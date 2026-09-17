"""Pledge lifecycle. Unit counts change inside single guarded UPDATE statements, so
two donors pledging for the last unit at the same moment can't both succeed."""

from collections.abc import Mapping
from datetime import date

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from ..domain.blood import can_donate
from ..domain.eligibility import evaluate_answers
from ..extensions import db
from ..models import (
    PLEDGE_ARRIVED,
    PLEDGE_CANCELLED,
    PLEDGE_DONATED,
    PLEDGE_NO_SHOW,
    PLEDGE_PLEDGED,
    REQUEST_CANCELLED,
    REQUEST_FULFILLED,
    REQUEST_OPEN,
    BloodRequest,
    Donor,
    Hospital,
    Pledge,
    utcnow,
)


class PledgeError(Exception):
    def __init__(self, message: str, reasons: list[str] | None = None):
        super().__init__(message)
        self.message = message
        self.reasons = reasons or []


def _release_unit(request_id: int) -> None:
    db.session.execute(
        update(BloodRequest)
        .where(BloodRequest.id == request_id, BloodRequest.units_pledged > 0)
        .values(units_pledged=BloodRequest.units_pledged - 1)
    )


def create_pledge(donor: Donor, request_id: int, answers: Mapping[str, str], today: date) -> Pledge:
    blood_request = db.session.get(BloodRequest, request_id)
    if blood_request is None or not blood_request.is_open:
        raise PledgeError("This request is no longer open.")
    if not can_donate(donor.blood_group, blood_request.blood_group):
        raise PledgeError("Your blood group isn't compatible with this request.")
    if not donor.is_available:
        raise PledgeError("Mark yourself as available before pledging.")

    profile = donor.profile_eligibility(today)
    if not profile.eligible:
        raise PledgeError("You can't donate right now.", profile.reasons)
    answers_result = evaluate_answers(answers)
    if not answers_result.eligible:
        raise PledgeError("Based on your answers, you can't donate right now.", answers_result.reasons)

    existing = db.session.scalar(select(Pledge).where(Pledge.request_id == request_id, Pledge.donor_id == donor.id))
    if existing is not None and existing.status not in (PLEDGE_CANCELLED, PLEDGE_NO_SHOW):
        raise PledgeError("You've already pledged for this request.")

    claimed = db.session.execute(
        update(BloodRequest)
        .where(
            BloodRequest.id == request_id,
            BloodRequest.status == REQUEST_OPEN,
            BloodRequest.units_pledged < BloodRequest.units_required,
        )
        .values(units_pledged=BloodRequest.units_pledged + 1)
    )
    if claimed.rowcount != 1:
        db.session.rollback()
        raise PledgeError("This request already has enough donors. Thank you for offering!")

    if existing is not None:
        existing.status = PLEDGE_PLEDGED
        pledge = existing
    else:
        pledge = Pledge(request_id=request_id, donor_id=donor.id)
        db.session.add(pledge)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        raise PledgeError("You've already pledged for this request.") from None
    return pledge


def get_donor_pledge(donor: Donor, pledge_id: int) -> Pledge | None:
    pledge = db.session.get(Pledge, pledge_id)
    return pledge if pledge is not None and pledge.donor_id == donor.id else None


def cancel_pledge(donor: Donor, pledge_id: int) -> Pledge:
    pledge = get_donor_pledge(donor, pledge_id)
    if pledge is None:
        raise PledgeError("Pledge not found.")
    if not pledge.is_active:
        raise PledgeError("This pledge can no longer be cancelled.")
    pledge.status = PLEDGE_CANCELLED
    if pledge.request.is_open:
        _release_unit(pledge.request_id)
    db.session.commit()
    return pledge


def mark_arrived(donor: Donor, pledge_id: int) -> Pledge:
    pledge = get_donor_pledge(donor, pledge_id)
    if pledge is None:
        raise PledgeError("Pledge not found.")
    if pledge.status == PLEDGE_PLEDGED:
        pledge.status = PLEDGE_ARRIVED
        pledge.eta_minutes, pledge.distance_remaining_km, pledge.progress_updated_at = 0, 0.0, utcnow()
        db.session.commit()
    elif pledge.status != PLEDGE_ARRIVED:
        raise PledgeError("This pledge is no longer active.")
    return pledge


def record_progress(donor: Donor, pledge_id: int, distance_km: float, eta_minutes: int) -> Pledge:
    """Store the donor's latest ETA so the hospital's live console can show who is close."""
    pledge = get_donor_pledge(donor, pledge_id)
    if pledge is None:
        raise PledgeError("Pledge not found.")
    if not pledge.is_active:
        raise PledgeError("This pledge is no longer active.")
    pledge.distance_remaining_km = distance_km
    pledge.eta_minutes = eta_minutes
    pledge.progress_updated_at = utcnow()
    db.session.commit()
    return pledge


def set_pledge_outcome(hospital: Hospital, pledge_id: int, outcome: str, today: date) -> Pledge:
    """Hospital records whether a pledged donor donated or didn't turn up."""
    pledge = db.session.get(Pledge, pledge_id)
    if pledge is None or pledge.request.hospital_id != hospital.id:
        raise PledgeError("Pledge not found.")
    if not pledge.is_active:
        raise PledgeError("This pledge has already been closed.")
    if outcome == PLEDGE_DONATED:
        pledge.status = PLEDGE_DONATED
        pledge.donor.last_donation_date = today
    elif outcome == PLEDGE_NO_SHOW:
        pledge.status = PLEDGE_NO_SHOW
        if pledge.request.is_open:
            _release_unit(pledge.request_id)
    else:
        raise PledgeError("Unknown outcome.")
    db.session.commit()
    return pledge


def close_request(hospital: Hospital, request_id: int, outcome: str) -> BloodRequest:
    blood_request = db.session.get(BloodRequest, request_id)
    if blood_request is None or blood_request.hospital_id != hospital.id:
        raise PledgeError("Request not found.")
    if not blood_request.is_open:
        raise PledgeError("This request is already closed.")
    if outcome not in (REQUEST_FULFILLED, REQUEST_CANCELLED):
        raise PledgeError("Unknown outcome.")
    blood_request.status = outcome
    blood_request.closed_at = utcnow()
    if outcome == REQUEST_CANCELLED:
        for pledge in blood_request.pledges:
            if pledge.is_active:
                pledge.status = PLEDGE_CANCELLED
    db.session.commit()
    return blood_request
