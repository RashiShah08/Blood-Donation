"""Live snapshot for the hospital operations console: open requests, donors on the way and recent activity."""

from datetime import datetime

from sqlalchemy import select

from ..extensions import db
from ..models import (
    PLEDGE_ARRIVED,
    REQUEST_OPEN,
    URGENCY_LEVELS,
    BloodRequest,
    Hospital,
    Notification,
    Pledge,
    utcnow,
)

ACTIVITY_LIMIT = 30
# An ETA older than this is hidden: the donor probably closed the directions page.
STALE_PROGRESS_MINUTES = 10

PLEDGE_EVENT_TEXT = {
    "pledged": "{name} pledged for request #{request_id}",
    "arrived": "{name} arrived for request #{request_id}",
    "donated": "{name} donated for request #{request_id}",
    "cancelled": "{name} cancelled their pledge for request #{request_id}",
    "no_show": "{name} didn't arrive for request #{request_id}",
}


def _iso(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") + "Z" if value else None


def _pledge_json(pledge: Pledge, now: datetime) -> dict:
    fresh = (
        pledge.progress_updated_at is not None
        and (now - pledge.progress_updated_at).total_seconds() <= STALE_PROGRESS_MINUTES * 60
    )
    distance = pledge.distance_remaining_km if fresh else None
    return {
        "id": pledge.id,
        "donor_name": pledge.donor.name,
        "blood_group": pledge.donor.blood_group,
        "phone": pledge.donor.phone,
        "status": pledge.status,
        "eta_minutes": pledge.eta_minutes if fresh else None,
        "distance_km": round(distance, 1) if distance is not None else None,
        "progress_updated_at": _iso(pledge.progress_updated_at),
        "pledged_at": _iso(pledge.created_at),
    }


def _sort_pledges(item: dict) -> tuple:
    """Arrived donors first, then by live ETA; donors without a fresh ETA go last."""
    eta = item["eta_minutes"] if item["eta_minutes"] is not None else 10**6
    return (item["status"] != PLEDGE_ARRIVED, eta, item["pledged_at"])


def recent_activity(hospital: Hospital, limit: int = ACTIVITY_LIMIT) -> list[dict]:
    """Newest-first events (request opened/closed, pledges, alert batches). `at` is a naive UTC datetime."""
    events: list[dict] = []

    recent_requests = db.session.scalars(
        select(BloodRequest)
        .where(BloodRequest.hospital_id == hospital.id)
        .order_by(BloodRequest.created_at.desc())
        .limit(ACTIVITY_LIMIT)
    )
    for blood_request in recent_requests:
        events.append(
            {
                "at": blood_request.created_at,
                "kind": "request",
                "request_id": blood_request.id,
                "text": f"Request #{blood_request.id} opened: {blood_request.units_required} "
                f"{'unit' if blood_request.units_required == 1 else 'units'} of {blood_request.blood_group}",
            }
        )
        if blood_request.closed_at:
            events.append(
                {
                    "at": blood_request.closed_at,
                    "kind": blood_request.status,
                    "request_id": blood_request.id,
                    "text": f"Request #{blood_request.id} {blood_request.status}",
                }
            )

    recent_pledges = db.session.scalars(
        select(Pledge)
        .join(BloodRequest)
        .where(BloodRequest.hospital_id == hospital.id)
        .order_by(Pledge.updated_at.desc())
        .limit(ACTIVITY_LIMIT)
    )
    for pledge in recent_pledges:
        template = PLEDGE_EVENT_TEXT.get(pledge.status)
        if template is None:
            continue
        events.append(
            {
                "at": pledge.created_at if pledge.status == "pledged" else pledge.updated_at,
                "kind": pledge.status,
                "request_id": pledge.request_id,
                "text": template.format(name=pledge.donor.name, request_id=pledge.request_id),
            }
        )

    # Alerts are sent in batches, so group them by request and minute.
    alert_rows = db.session.execute(
        select(Notification.request_id, Notification.sent_at)
        .join(BloodRequest)
        .where(BloodRequest.hospital_id == hospital.id)
        .order_by(Notification.sent_at.desc())
        .limit(500)
    ).all()
    batches: dict[tuple[int, datetime], list[datetime]] = {}
    for request_id, sent_at in alert_rows:
        batches.setdefault((request_id, sent_at.replace(second=0, microsecond=0)), []).append(sent_at)
    for (request_id, _minute), times in batches.items():
        count = len(times)
        events.append(
            {
                "at": max(times),
                "kind": "alerted",
                "request_id": request_id,
                "text": f"{count} {'donor' if count == 1 else 'donors'} alerted for request #{request_id}",
            }
        )

    events.sort(key=lambda event: event["at"], reverse=True)
    return events[:limit]


def build_snapshot(hospital: Hospital) -> dict:
    now = utcnow()
    urgency_rank = {level: rank for rank, level in enumerate(URGENCY_LEVELS)}
    open_requests = sorted(
        db.session.scalars(
            select(BloodRequest).where(BloodRequest.hospital_id == hospital.id, BloodRequest.status == REQUEST_OPEN)
        ),
        key=lambda r: (urgency_rank.get(r.urgency, len(URGENCY_LEVELS)), r.created_at),
    )

    requests, on_the_way, arrived = [], 0, 0
    for blood_request in open_requests:
        active = sorted((_pledge_json(p, now) for p in blood_request.pledges if p.is_active), key=_sort_pledges)
        arrived += sum(1 for p in active if p["status"] == PLEDGE_ARRIVED)
        on_the_way += sum(1 for p in active if p["status"] != PLEDGE_ARRIVED)
        requests.append(
            {
                "id": blood_request.id,
                "patient_name": blood_request.patient_name,
                "blood_group": blood_request.blood_group,
                "urgency": blood_request.urgency,
                "units_required": blood_request.units_required,
                "units_pledged": blood_request.units_pledged,
                "units_remaining": blood_request.units_remaining,
                "notified": len(blood_request.notifications),
                "created_at": _iso(blood_request.created_at),
                "pledges": active,
            }
        )

    return {
        "generated_at": _iso(now),
        "hospital": {"name": hospital.name, "lat": hospital.latitude, "lng": hospital.longitude},
        "summary": {
            "open_requests": len(requests),
            "units_needed": sum(r["units_remaining"] for r in requests),
            "on_the_way": on_the_way,
            "arrived": arrived,
        },
        "requests": requests,
        "activity": [{**event, "at": _iso(event["at"])} for event in recent_activity(hospital)],
    }
