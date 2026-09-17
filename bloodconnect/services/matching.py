"""Find compatible donors near a hospital, and open requests near a donor."""

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select

from ..domain import geo
from ..domain.blood import donor_groups_for, recipient_groups_for
from ..extensions import db
from ..models import (
    ACTIVE_PLEDGE_STATUSES,
    REQUEST_OPEN,
    URGENCY_LEVELS,
    BloodRequest,
    Donor,
    Hospital,
    Notification,
    Pledge,
)


@dataclass
class DonorMatch:
    donor: Donor
    distance_km: float


@dataclass
class RequestMatch:
    request: BloodRequest
    distance_km: float


def _interval_ok(donor: Donor, today: date) -> bool:
    next_date = donor.next_eligible_date()
    return next_date is None or next_date <= today


def find_donors_near(blood_request: BloodRequest, radius_km: float, today: date) -> list[DonorMatch]:
    """Available, compatible donors within radius_km of the hospital, nearest first."""
    hospital = blood_request.hospital
    min_lat, max_lat, min_lon, max_lon = geo.bounding_box(hospital.latitude, hospital.longitude, radius_km)
    stmt = select(Donor).where(
        Donor.blood_group.in_(donor_groups_for(blood_request.blood_group)),
        Donor.is_available.is_(True),
        Donor.latitude.between(min_lat, max_lat),
    )
    if min_lon is not None:
        stmt = stmt.where(Donor.longitude.between(min_lon, max_lon))

    matches = []
    for donor in db.session.scalars(stmt):
        distance = geo.haversine_km(hospital.latitude, hospital.longitude, donor.latitude, donor.longitude)
        if distance <= radius_km and _interval_ok(donor, today):
            matches.append(DonorMatch(donor, distance))
    matches.sort(key=lambda match: match.distance_km)
    return matches


def search_with_expansion(
    blood_request: BloodRequest, radii: tuple[float, ...], start_radius: float, today: date
) -> tuple[float, list[DonorMatch]]:
    """Search at start_radius, widening through the configured radii until someone is found."""
    candidates = sorted({r for r in radii if r >= start_radius} | {start_radius})
    for radius in candidates:
        matches = find_donors_near(blood_request, radius, today)
        if matches:
            return radius, matches
    return candidates[-1], []


def notified_donor_ids(request_id: int) -> set[int]:
    return set(db.session.scalars(select(Notification.donor_id).where(Notification.request_id == request_id)))


def active_pledge_donor_ids(request_id: int) -> set[int]:
    stmt = select(Pledge.donor_id).where(Pledge.request_id == request_id, Pledge.status.in_(ACTIVE_PLEDGE_STATUSES))
    return set(db.session.scalars(stmt))


def open_requests_for_donor(donor: Donor, max_distance_km: float) -> list[RequestMatch]:
    """Open requests this donor can help with: compatible, not fully pledged, not already pledged."""
    already_pledged = select(Pledge.request_id).where(
        Pledge.donor_id == donor.id, Pledge.status.in_(ACTIVE_PLEDGE_STATUSES + ("donated",))
    )
    stmt = (
        select(BloodRequest)
        .join(Hospital)
        .where(
            BloodRequest.status == REQUEST_OPEN,
            BloodRequest.blood_group.in_(recipient_groups_for(donor.blood_group)),
            BloodRequest.units_pledged < BloodRequest.units_required,
            BloodRequest.id.not_in(already_pledged),
        )
    )
    matches = []
    for blood_request in db.session.scalars(stmt):
        hospital = blood_request.hospital
        distance = geo.haversine_km(donor.latitude, donor.longitude, hospital.latitude, hospital.longitude)
        if distance <= max_distance_km:
            matches.append(RequestMatch(blood_request, distance))
    urgency_rank = {level: rank for rank, level in enumerate(URGENCY_LEVELS)}
    matches.sort(key=lambda m: (urgency_rank.get(m.request.urgency, len(URGENCY_LEVELS)), m.distance_km))
    return matches
