"""Hospital area: dashboard, blood requests, donor search, alerts and pledge outcomes."""

import statistics
from datetime import date, timedelta

from flask import Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template, request, url_for
from sqlalchemy import func, select

from .domain import geo
from .domain.blood import BLOOD_GROUPS, donor_groups_for
from .extensions import db, limiter
from .models import (
    ACTIVE_PLEDGE_STATUSES,
    GENDERS,
    REQUEST_FULFILLED,
    REQUEST_OPEN,
    URGENCY_LEVELS,
    BloodRequest,
    Notification,
    Pledge,
    utcnow,
)
from .security import hospital_required
from .services import matching, pledges
from .services import ops as ops_service
from .services.notifications import notify_donors
from .validation import FormValidator

bp = Blueprint("hospital", __name__, url_prefix="/hospital")


def _owned_request_or_404(request_id: int) -> BloodRequest:
    blood_request = db.session.get(BloodRequest, request_id)
    if blood_request is None or blood_request.hospital_id != g.hospital.id:
        abort(404)
    return blood_request


def _dashboard_stats(hospital_id: int) -> dict:
    counts = dict(
        db.session.execute(
            select(BloodRequest.status, func.count())
            .where(BloodRequest.hospital_id == hospital_id)
            .group_by(BloodRequest.status)
        ).all()
    )
    units_needed = db.session.scalar(
        select(func.coalesce(func.sum(BloodRequest.units_required - BloodRequest.units_pledged), 0)).where(
            BloodRequest.hospital_id == hospital_id, BloodRequest.status == REQUEST_OPEN
        )
    )
    active_pledges = db.session.scalar(
        select(func.count(Pledge.id))
        .join(BloodRequest)
        .where(BloodRequest.hospital_id == hospital_id, Pledge.status.in_(ACTIVE_PLEDGE_STATUSES))
    )
    closed = sum(count for status, count in counts.items() if status != REQUEST_OPEN)
    fulfilled = counts.get(REQUEST_FULFILLED, 0)

    # Minutes between a donor being alerted and pledging, for this hospital's requests.
    response_rows = db.session.execute(
        select(Pledge.created_at, Notification.sent_at)
        .join(BloodRequest, Pledge.request_id == BloodRequest.id)
        .join(Notification, (Notification.request_id == Pledge.request_id) & (Notification.donor_id == Pledge.donor_id))
        .where(BloodRequest.hospital_id == hospital_id)
    ).all()
    response_minutes = [
        (pledged_at - sent_at).total_seconds() / 60 for pledged_at, sent_at in response_rows if pledged_at >= sent_at
    ]
    return {
        "open_requests": counts.get(REQUEST_OPEN, 0),
        "units_needed": units_needed,
        "active_pledges": active_pledges,
        "fulfilment_rate": round(fulfilled / closed * 100) if closed else None,
        "median_response_minutes": round(statistics.median(response_minutes)) if response_minutes else None,
        "pledge_trend": _pledge_trend(hospital_id),
    }


TREND_DAYS = 14


def _pledge_trend(hospital_id: int) -> list[dict]:
    """Pledges per day (UTC) for the last two weeks, oldest first, including days with none."""
    today = utcnow().date()
    start = today - timedelta(days=TREND_DAYS - 1)
    created = db.session.scalars(
        select(Pledge.created_at)
        .join(BloodRequest)
        .where(BloodRequest.hospital_id == hospital_id, Pledge.created_at >= start)
    ).all()
    per_day = {start + timedelta(days=offset): 0 for offset in range(TREND_DAYS)}
    for created_at in created:
        if created_at.date() in per_day:
            per_day[created_at.date()] += 1
    peak = max(per_day.values()) or 1
    return [{"day": day, "count": count, "height": round(count / peak * 100)} for day, count in per_day.items()]


@bp.get("/dashboard")
@hospital_required
def dashboard():
    hospital = g.hospital
    open_requests = db.session.scalars(
        select(BloodRequest)
        .where(BloodRequest.hospital_id == hospital.id, BloodRequest.status == REQUEST_OPEN)
        .order_by(BloodRequest.created_at.desc())
    ).all()
    closed_requests = db.session.scalars(
        select(BloodRequest)
        .where(BloodRequest.hospital_id == hospital.id, BloodRequest.status != REQUEST_OPEN)
        .order_by(BloodRequest.closed_at.desc())
        .limit(10)
    ).all()
    return render_template(
        "hospital/dashboard.html",
        hospital=hospital,
        stats=_dashboard_stats(hospital.id),
        open_requests=open_requests,
        closed_requests=closed_requests,
        activity=ops_service.recent_activity(hospital, limit=10),
    )


@bp.route("/requests/new", methods=["GET", "POST"])
@hospital_required
@limiter.limit("30 per hour", methods=["POST"])
def new_request():
    context = {
        "genders": GENDERS,
        "urgency_levels": URGENCY_LEVELS,
        "form": {},
        "errors": {},
        "compat": {group: list(donor_groups_for(group)) for group in BLOOD_GROUPS},
    }
    if request.method == "GET":
        return render_template("hospital/request_form.html", **context)

    v = FormValidator(request.form)
    v.text("patient_name", "Patient name", min_len=2, max_len=120)
    v.choice("patient_gender", "Patient gender", GENDERS)
    v.number("patient_weight_kg", "Patient weight", minimum=1, maximum=250, required=False)
    v.blood_group()
    v.number("units_required", "Units required", minimum=1, maximum=50, integer=True)
    v.choice("urgency", "Urgency", URGENCY_LEVELS)
    v.text("clinical_notes", "Notes", required=False, max_len=500)
    if not v.is_valid:
        return render_template(
            "hospital/request_form.html", **{**context, "form": request.form, "errors": v.errors}
        ), 422

    data = v.data
    blood_request = BloodRequest(
        hospital_id=g.hospital.id,
        patient_name=data["patient_name"],
        patient_gender=data["patient_gender"],
        patient_weight_kg=data["patient_weight_kg"],
        blood_group=data["blood_group"],
        units_required=data["units_required"],
        urgency=data["urgency"],
        clinical_notes=data["clinical_notes"],
    )
    db.session.add(blood_request)
    db.session.commit()
    flash("Request created. Now find nearby donors and alert them.", "success")
    return redirect(url_for("hospital.request_detail", request_id=blood_request.id))


@bp.get("/requests/<int:request_id>")
@hospital_required
def request_detail(request_id: int):
    blood_request = _owned_request_or_404(request_id)
    pledge_list = sorted(blood_request.pledges, key=lambda p: (not p.is_active, p.created_at))
    return render_template(
        "hospital/request_detail.html",
        blood_request=blood_request,
        pledges=pledge_list,
        compatible_groups=donor_groups_for(blood_request.blood_group),
        radii=current_app.config["SEARCH_RADII_KM"],
        notified_count=len(blood_request.notifications),
    )


def _requested_radius() -> float | None:
    radius = request.values.get("radius", type=float)
    if radius is None and request.is_json:
        radius = (request.get_json(silent=True) or {}).get("radius")
    return radius if radius in current_app.config["SEARCH_RADII_KM"] else None


@bp.get("/api/requests/<int:request_id>/donors")
@hospital_required
@limiter.limit("60 per minute")
def donors_api(request_id: int):
    blood_request = _owned_request_or_404(request_id)
    radius = _requested_radius()
    if radius is None:
        return jsonify(error="Choose one of the available search radii."), 400
    today = date.today()
    if request.args.get("expand") == "1":
        radius, found = matching.search_with_expansion(
            blood_request, current_app.config["SEARCH_RADII_KM"], radius, today
        )
    else:
        found = matching.find_donors_near(blood_request, radius, today)

    notified = matching.notified_donor_ids(request_id)
    pledged = matching.active_pledge_donor_ids(request_id)
    hospital = blood_request.hospital
    donors = []
    for match in found:
        # Only an approximate position and no identity: hospitals see who pledged, not who lives where.
        approx_lat, approx_lng = geo.approximate(match.donor.latitude, match.donor.longitude)
        donors.append(
            {
                "blood_group": match.donor.blood_group,
                "distance_km": round(match.distance_km, 1),
                "approx_lat": approx_lat,
                "approx_lng": approx_lng,
                "notified": match.donor.id in notified,
                "pledged": match.donor.id in pledged,
            }
        )
    return jsonify(
        radius_km=radius,
        hospital={"name": hospital.name, "lat": hospital.latitude, "lng": hospital.longitude},
        request={
            "status": blood_request.status,
            "blood_group": blood_request.blood_group,
            "units_remaining": blood_request.units_remaining,
        },
        donors=donors,
        notifiable=sum(1 for d in donors if not d["notified"] and not d["pledged"]),
    )


@bp.post("/api/requests/<int:request_id>/notify")
@hospital_required
@limiter.limit("5 per minute; 30 per hour")
def notify_api(request_id: int):
    blood_request = _owned_request_or_404(request_id)
    if not blood_request.is_open:
        return jsonify(error="This request is closed."), 409
    if current_app.config["REQUIRE_HOSPITAL_VERIFICATION"] and not g.hospital.is_verified:
        return jsonify(error="Your hospital account must be verified before you can alert donors."), 403
    radius = _requested_radius()
    if radius is None:
        return jsonify(error="Choose one of the available search radii."), 400

    found = matching.find_donors_near(blood_request, radius, date.today())
    base_url = current_app.config.get("PUBLIC_BASE_URL")

    def link_for(rid: int) -> str:
        if base_url:
            return base_url + url_for("donor.request_detail", request_id=rid)
        return url_for("donor.request_detail", request_id=rid, _external=True)

    outcome = notify_donors(
        current_app.extensions["mailer"],
        blood_request,
        found,
        link_for,
        current_app.config["MAX_NOTIFICATIONS_PER_SEND"],
    )
    body = {
        "success": outcome.success,
        "sent": outcome.sent,
        "failed": outcome.failed,
        "skipped": outcome.skipped,
        "simulated": outcome.simulated,
    }
    if outcome.sent == 0 and outcome.failed == 0:
        body["message"] = "Everyone in this area has already been alerted or has pledged."
        return jsonify(body)
    if outcome.sent == 0:
        body["error"] = "We couldn't send any alerts. Please try again shortly."
        return jsonify(body), 502
    return jsonify(body)


@bp.post("/pledges/<int:pledge_id>/outcome")
@hospital_required
def pledge_outcome(pledge_id: int):
    outcome = request.form.get("outcome", "")
    try:
        pledge = pledges.set_pledge_outcome(g.hospital, pledge_id, outcome, date.today())
    except pledges.PledgeError as error:
        flash(error.message, "error")
        return redirect(request.referrer if request.referrer else url_for("hospital.dashboard"))
    flash(
        "Donation recorded. Thank you!" if outcome == "donated" else "Marked as not arrived; the unit is open again.",
        "success",
    )
    return redirect(url_for("hospital.request_detail", request_id=pledge.request_id))


@bp.post("/requests/<int:request_id>/close")
@hospital_required
def close_request(request_id: int):
    outcome = request.form.get("outcome", "")
    try:
        pledges.close_request(g.hospital, request_id, outcome)
    except pledges.PledgeError as error:
        if error.message == "Request not found.":
            abort(404)
        flash(error.message, "error")
        return redirect(url_for("hospital.request_detail", request_id=request_id))
    flash("Request marked as fulfilled." if outcome == REQUEST_FULFILLED else "Request cancelled.", "success")
    return redirect(url_for("hospital.dashboard"))


@bp.get("/ops")
@hospital_required
def ops():
    return render_template("hospital/ops.html", hospital=g.hospital, radii=current_app.config["SEARCH_RADII_KM"])


@bp.get("/api/ops")
@hospital_required
@limiter.limit("120 per minute")
def ops_api():
    snapshot = ops_service.build_snapshot(g.hospital)
    for item in snapshot["requests"]:
        item["detail_url"] = url_for("hospital.request_detail", request_id=item["id"])
        item["donors_url"] = url_for("hospital.donors_api", request_id=item["id"])
        item["notify_url"] = url_for("hospital.notify_api", request_id=item["id"])
    return jsonify(snapshot)
