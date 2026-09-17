"""Donor area: dashboard, pledging, directions, recognition and profile."""

from datetime import date

from flask import Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template, request, url_for
from sqlalchemy import select

from . import security
from .domain import geo
from .domain.blood import can_donate, recipient_groups_for
from .domain.eligibility import QUESTIONS, donation_interval_days
from .domain.recognition import BADGES, earned_badges, next_badge
from .extensions import db, limiter
from .models import (
    ACTIVE_PLEDGE_STATUSES,
    GENDERS,
    HEALTH_ISSUES,
    PLEDGE_DONATED,
    BloodRequest,
    Pledge,
)
from .security import donor_required
from .services import matching, pledges, routing
from .validation import FormValidator

bp = Blueprint("donor", __name__, url_prefix="/donor")


def _donor_pledges(statuses) -> list[Pledge]:
    stmt = (
        select(Pledge)
        .where(Pledge.donor_id == g.donor.id, Pledge.status.in_(statuses))
        .order_by(Pledge.created_at.desc())
    )
    return list(db.session.scalars(stmt))


def _distance_to(blood_request: BloodRequest) -> float:
    hospital = blood_request.hospital
    return geo.haversine_km(g.donor.latitude, g.donor.longitude, hospital.latitude, hospital.longitude)


@bp.get("/dashboard")
@donor_required
def dashboard():
    today = date.today()
    donor = g.donor
    donation_count = len(_donor_pledges((PLEDGE_DONATED,)))
    radius = current_app.config["DONOR_REQUEST_RADIUS_KM"]
    return render_template(
        "donor/dashboard.html",
        donor=donor,
        age=donor.age(today),
        eligibility=donor.profile_eligibility(today),
        next_eligible=donor.next_eligible_date(),
        can_give_to=recipient_groups_for(donor.blood_group),
        matches=matching.open_requests_for_donor(donor, radius) if donor.is_available else [],
        active_pledges=_donor_pledges(ACTIVE_PLEDGE_STATUSES),
        radius_km=radius,
        health_issues=HEALTH_ISSUES,
        readiness=_readiness(donor, today),
        donation_count=donation_count,
        badges_earned=len(earned_badges(donation_count)),
        badges_total=len(BADGES),
        upcoming_badge=next_badge(donation_count),
    )


def _readiness(donor, today: date) -> dict:
    """How far the donor is through the rest interval since their last donation, for the dashboard ring."""
    next_date = donor.next_eligible_date()
    if donor.last_donation_date is None or next_date is None or next_date <= today:
        return {"ready": True, "percent": 100, "days_left": 0, "next_date": None}
    interval = donation_interval_days(donor.gender)
    elapsed = (today - donor.last_donation_date).days
    return {
        "ready": False,
        "percent": max(0, min(100, round(elapsed * 100 / interval))),
        "days_left": (next_date - today).days,
        "next_date": next_date,
    }


def _request_json(match) -> dict:
    blood_request = match.request
    hospital = blood_request.hospital
    return {
        "id": blood_request.id,
        "hospital": hospital.name,
        "city": hospital.city,
        "blood_group": blood_request.blood_group,
        "urgency": blood_request.urgency,
        "units_remaining": blood_request.units_remaining,
        "distance_km": round(match.distance_km, 2),
        "posted_at": blood_request.created_at.isoformat(timespec="seconds") + "Z",
        "url": url_for("donor.request_detail", request_id=blood_request.id),
    }


@bp.get("/api/feed")
@donor_required
@limiter.limit("60 per minute")
def feed():
    """Live data for the donor dashboard: matching requests and the donor's active pledges."""
    donor = g.donor
    radius = current_app.config["DONOR_REQUEST_RADIUS_KM"]
    matches = matching.open_requests_for_donor(donor, radius) if donor.is_available else []
    return jsonify(
        available=donor.is_available,
        radius_km=radius,
        requests=[_request_json(match) for match in matches],
        pledges=[
            {"id": p.id, "status": p.status, "request_id": p.request_id, "request_status": p.request.status}
            for p in _donor_pledges(ACTIVE_PLEDGE_STATUSES)
        ],
    )


@bp.post("/api/availability")
@donor_required
def availability_api():
    data = request.get_json(silent=True) or {}
    if not isinstance(data.get("available"), bool):
        return jsonify(error="Send available: true or false."), 400
    g.donor.is_available = data["available"]
    db.session.commit()
    return jsonify(available=g.donor.is_available)


@bp.post("/availability")
@donor_required
def set_availability():
    g.donor.is_available = request.form.get("available") == "yes"
    db.session.commit()
    if g.donor.is_available:
        flash("You're available. We'll show you requests you can help with.", "success")
    else:
        flash("You're marked unavailable, so you won't get alerts until you switch this back on.", "success")
    return redirect(url_for("donor.dashboard"))


def _get_request_or_404(request_id: int) -> BloodRequest:
    blood_request = db.session.get(BloodRequest, request_id)
    if blood_request is None:
        abort(404)
    return blood_request


def _existing_pledge(request_id: int) -> Pledge | None:
    return db.session.scalar(select(Pledge).where(Pledge.request_id == request_id, Pledge.donor_id == g.donor.id))


@bp.get("/requests/<int:request_id>")
@donor_required
def request_detail(request_id: int):
    blood_request = _get_request_or_404(request_id)
    pledge = _existing_pledge(request_id)
    compatible = can_donate(g.donor.blood_group, blood_request.blood_group)
    # Donors only see requests they could act on (or already pledged for).
    if not compatible and pledge is None:
        abort(404)
    return render_template(
        "donor/request_detail.html",
        blood_request=blood_request,
        pledge=pledge,
        distance_km=_distance_to(blood_request),
        compatible=compatible,
        eligibility=g.donor.profile_eligibility(date.today()),
    )


@bp.route("/requests/<int:request_id>/pledge", methods=["GET", "POST"])
@donor_required
@limiter.limit("20 per hour", methods=["POST"])
def pledge(request_id: int):
    blood_request = _get_request_or_404(request_id)
    if not can_donate(g.donor.blood_group, blood_request.blood_group):
        abort(404)
    context = {
        "blood_request": blood_request,
        "questions": QUESTIONS,
        "distance_km": _distance_to(blood_request),
        "answers": {},
        "error": None,
        "reasons": [],
    }
    if request.method == "GET":
        existing = _existing_pledge(request_id)
        if existing is not None and existing.is_active:
            return redirect(url_for("donor.directions", pledge_id=existing.id))
        return render_template("donor/pledge.html", **context)

    answers = {q.key: request.form.get(q.key, "") for q in QUESTIONS}
    try:
        new_pledge = pledges.create_pledge(g.donor, request_id, answers, date.today())
    except pledges.PledgeError as error:
        context.update(answers=answers, error=error.message, reasons=error.reasons)
        return render_template("donor/pledge.html", **context), 422
    flash("Thank you! Your pledge is confirmed. The hospital can now see you're coming.", "success")
    return redirect(url_for("donor.directions", pledge_id=new_pledge.id))


@bp.post("/pledges/<int:pledge_id>/cancel")
@donor_required
def cancel_pledge(pledge_id: int):
    try:
        pledges.cancel_pledge(g.donor, pledge_id)
        flash("Your pledge has been cancelled. Thanks for letting the hospital know.", "success")
    except pledges.PledgeError as error:
        flash(error.message, "error")
    return redirect(url_for("donor.dashboard"))


@bp.get("/pledges/<int:pledge_id>/directions")
@donor_required
def directions(pledge_id: int):
    pledge = pledges.get_donor_pledge(g.donor, pledge_id)
    if pledge is None:
        abort(404)
    return render_template("donor/directions.html", pledge=pledge, hospital=pledge.request.hospital)


@bp.get("/api/pledges/<int:pledge_id>/route")
@donor_required
@limiter.limit("30 per minute")
def route(pledge_id: int):
    pledge = pledges.get_donor_pledge(g.donor, pledge_id)
    if pledge is None:
        return jsonify(error="Pledge not found."), 404
    lat = request.args.get("lat", type=float)
    lng = request.args.get("lng", type=float)
    if lat is None or lng is None or not geo.is_valid_coordinate(lat, lng):
        return jsonify(error="A valid current location is required."), 400
    hospital = pledge.request.hospital
    result = routing.get_route((lat, lng), (hospital.latitude, hospital.longitude), current_app.config["ORS_API_KEY"])
    return jsonify(route=result.to_dict())


@bp.post("/api/pledges/<int:pledge_id>/arrived")
@donor_required
def arrived(pledge_id: int):
    try:
        pledge = pledges.mark_arrived(g.donor, pledge_id)
    except pledges.PledgeError as error:
        return jsonify(error=error.message), 409
    return jsonify(status=pledge.status)


def _number_in_range(value: object, maximum: float) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if 0 <= value <= maximum else None


@bp.post("/api/pledges/<int:pledge_id>/progress")
@donor_required
@limiter.limit("20 per minute")
def progress(pledge_id: int):
    data = request.get_json(silent=True) or {}
    distance_km = _number_in_range(data.get("distance_km"), 1000)
    eta_minutes = _number_in_range(data.get("eta_minutes"), 24 * 60)
    if distance_km is None or eta_minutes is None:
        return jsonify(error="Send distance_km (0-1000) and eta_minutes (0-1440)."), 400
    try:
        pledge = pledges.record_progress(g.donor, pledge_id, round(distance_km, 2), round(eta_minutes))
    except pledges.PledgeError as error:
        return jsonify(error=error.message), 404 if error.message == "Pledge not found." else 409
    return jsonify(status=pledge.status, eta_minutes=pledge.eta_minutes)


@bp.get("/impact")
@donor_required
def impact():
    donations = _donor_pledges((PLEDGE_DONATED,))
    count = len(donations)
    return render_template(
        "donor/impact.html",
        donations=donations,
        donation_count=count,
        badges=BADGES,
        earned=earned_badges(count),
        upcoming=next_badge(count),
        next_eligible=g.donor.next_eligible_date(),
    )


@bp.route("/profile", methods=["GET", "POST"])
@donor_required
def profile():
    donor = g.donor
    context = {"genders": GENDERS, "health_issues": HEALTH_ISSUES, "errors": {}}
    if request.method == "GET":
        return render_template("donor/profile.html", form=_profile_form(donor), **context)

    v = FormValidator(request.form)
    v.text("name", "Full name", min_len=2, max_len=120)
    v.phone(required=False)
    v.number("weight_kg", "Weight", minimum=20, maximum=250)
    v.choice("health_issues", "Health condition", HEALTH_ISSUES)
    v.past_date("last_donation_date", "Last donation date", today=date.today(), required=False)
    v.coordinates()
    if not v.is_valid:
        return render_template("donor/profile.html", form=request.form, **{**context, "errors": v.errors}), 422

    data = v.data
    donor.name, donor.phone, donor.weight_kg = data["name"], data["phone"], data["weight_kg"]
    donor.health_issues, donor.last_donation_date = data["health_issues"], data["last_donation_date"]
    donor.latitude, donor.longitude = data["latitude"], data["longitude"]
    db.session.commit()
    flash("Your profile has been updated.", "success")
    return redirect(url_for("donor.dashboard"))


def _profile_form(donor) -> dict:
    return {
        "name": donor.name,
        "phone": donor.phone or "",
        "weight_kg": f"{donor.weight_kg:g}",
        "health_issues": donor.health_issues,
        "last_donation_date": donor.last_donation_date.isoformat() if donor.last_donation_date else "",
        "latitude": donor.latitude,
        "longitude": donor.longitude,
    }


@bp.post("/account/delete")
@donor_required
@limiter.limit("5 per hour")
def delete_account():
    donor = g.donor
    if not donor.check_password(request.form.get("password", "")):
        flash("That password is incorrect, so your account was not deleted.", "error")
        return redirect(url_for("donor.profile"))
    for active in _donor_pledges(ACTIVE_PLEDGE_STATUSES):
        pledges.cancel_pledge(donor, active.id)
    db.session.delete(donor)
    db.session.commit()
    security.logout()
    flash("Your account and personal data have been deleted.", "success")
    return redirect(url_for("main.home"))
