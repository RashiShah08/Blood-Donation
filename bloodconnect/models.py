"""Database models (PostgreSQL)."""

from datetime import UTC, date, datetime

from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from werkzeug.security import check_password_hash, generate_password_hash

from .domain import eligibility
from .extensions import db

GENDERS = ("Male", "Female", "Other")
HEALTH_ISSUES = {
    "none": "None",
    "bp": "Blood pressure",
    "cholesterol": "Cholesterol",
    "diabetes": "Diabetes",
    "other": "Other",
}
HOSPITAL_TYPES = {"private": "Private", "government": "Government", "clinic": "Clinic", "blood_bank": "Blood bank"}
URGENCY_LEVELS = ("critical", "high", "medium", "low")

REQUEST_OPEN, REQUEST_FULFILLED, REQUEST_CANCELLED = "open", "fulfilled", "cancelled"
PLEDGE_PLEDGED, PLEDGE_ARRIVED, PLEDGE_DONATED = "pledged", "arrived", "donated"
PLEDGE_CANCELLED, PLEDGE_NO_SHOW = "cancelled", "no_show"
ACTIVE_PLEDGE_STATUSES = (PLEDGE_PLEDGED, PLEDGE_ARRIVED)


def utcnow() -> datetime:
    """Naive UTC timestamp; all `timestamp` columns store UTC."""
    return datetime.now(UTC).replace(tzinfo=None)


class PasswordMixin:
    password_hash: Mapped[str] = mapped_column(String(255))

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


class Donor(PasswordMixin, db.Model):
    __tablename__ = "donors"
    __table_args__ = (
        CheckConstraint("weight_kg > 0", name="ck_donor_weight_positive"),
        CheckConstraint("latitude BETWEEN -90 AND 90", name="ck_donor_latitude"),
        CheckConstraint("longitude BETWEEN -180 AND 180", name="ck_donor_longitude"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(20))
    date_of_birth: Mapped[date]
    gender: Mapped[str] = mapped_column(String(10))
    weight_kg: Mapped[float]
    blood_group: Mapped[str] = mapped_column(String(3), index=True)
    health_issues: Mapped[str] = mapped_column(String(20), default="none")
    latitude: Mapped[float]
    longitude: Mapped[float]
    is_available: Mapped[bool] = mapped_column(default=True)
    last_donation_date: Mapped[date | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    pledges: Mapped[list["Pledge"]] = relationship(back_populates="donor", cascade="all, delete-orphan")
    notifications: Mapped[list["Notification"]] = relationship(back_populates="donor", cascade="all, delete-orphan")

    def age(self, today: date) -> int:
        return eligibility.age_on(self.date_of_birth, today)

    def next_eligible_date(self) -> date | None:
        return eligibility.next_eligible_date(self.gender, self.last_donation_date)

    def profile_eligibility(self, today: date) -> eligibility.EligibilityResult:
        return eligibility.check_profile(
            date_of_birth=self.date_of_birth,
            weight_kg=self.weight_kg,
            gender=self.gender,
            last_donation_date=self.last_donation_date,
            today=today,
        )


class Hospital(PasswordMixin, db.Model):
    __tablename__ = "hospitals"
    __table_args__ = (
        CheckConstraint("latitude BETWEEN -90 AND 90", name="ck_hospital_latitude"),
        CheckConstraint("longitude BETWEEN -180 AND 180", name="ck_hospital_longitude"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    phone: Mapped[str] = mapped_column(String(20))
    address: Mapped[str] = mapped_column(String(255))
    city: Mapped[str] = mapped_column(String(80))
    state: Mapped[str] = mapped_column(String(80))
    pincode: Mapped[str] = mapped_column(String(10))
    country: Mapped[str] = mapped_column(String(80), default="India")
    hospital_type: Mapped[str] = mapped_column(String(20))
    latitude: Mapped[float]
    longitude: Mapped[float]
    is_verified: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    requests: Mapped[list["BloodRequest"]] = relationship(back_populates="hospital", cascade="all, delete-orphan")


class BloodRequest(db.Model):
    __tablename__ = "blood_requests"
    __table_args__ = (
        CheckConstraint("units_required BETWEEN 1 AND 50", name="ck_request_units_required"),
        CheckConstraint("units_pledged >= 0 AND units_pledged <= units_required", name="ck_request_units_pledged"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id", ondelete="CASCADE"), index=True)
    patient_name: Mapped[str] = mapped_column(String(120))
    patient_gender: Mapped[str] = mapped_column(String(10))
    patient_weight_kg: Mapped[float | None]
    blood_group: Mapped[str] = mapped_column(String(3), index=True)
    units_required: Mapped[int]
    units_pledged: Mapped[int] = mapped_column(default=0)
    urgency: Mapped[str] = mapped_column(String(10))
    clinical_notes: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(12), default=REQUEST_OPEN, index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    closed_at: Mapped[datetime | None]

    hospital: Mapped[Hospital] = relationship(back_populates="requests")
    pledges: Mapped[list["Pledge"]] = relationship(back_populates="request", cascade="all, delete-orphan")
    notifications: Mapped[list["Notification"]] = relationship(back_populates="request", cascade="all, delete-orphan")

    @property
    def units_remaining(self) -> int:
        return max(0, self.units_required - self.units_pledged)

    @property
    def is_open(self) -> bool:
        return self.status == REQUEST_OPEN


class Pledge(db.Model):
    """A donor's commitment to donate for a request."""

    __tablename__ = "pledges"
    __table_args__ = (UniqueConstraint("request_id", "donor_id", name="uq_pledge_request_donor"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("blood_requests.id", ondelete="CASCADE"), index=True)
    donor_id: Mapped[int] = mapped_column(ForeignKey("donors.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(12), default=PLEDGE_PLEDGED)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    # Trip progress reported by the donor's directions page. Only ETA and distance are
    # stored, never the donor's coordinates.
    eta_minutes: Mapped[int | None]
    distance_remaining_km: Mapped[float | None]
    progress_updated_at: Mapped[datetime | None]

    request: Mapped[BloodRequest] = relationship(back_populates="pledges")
    donor: Mapped[Donor] = relationship(back_populates="pledges")

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE_PLEDGE_STATUSES


class LoginThrottle(db.Model):
    """Failed logins for one account email, shared by every app process (see services/throttle.py)."""

    __tablename__ = "login_throttles"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)  # sha256 of "kind:email"
    failures: Mapped[int] = mapped_column(default=0)
    window_started_at: Mapped[datetime] = mapped_column(default=utcnow)
    locked_until: Mapped[datetime | None]


class Notification(db.Model):
    """One alert email per donor per request, so donors are never spammed."""

    __tablename__ = "notifications"
    __table_args__ = (UniqueConstraint("request_id", "donor_id", name="uq_notification_request_donor"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("blood_requests.id", ondelete="CASCADE"), index=True)
    donor_id: Mapped[int] = mapped_column(ForeignKey("donors.id", ondelete="CASCADE"), index=True)
    sent_at: Mapped[datetime] = mapped_column(default=utcnow)

    request: Mapped[BloodRequest] = relationship(back_populates="notifications")
    donor: Mapped[Donor] = relationship(back_populates="notifications")
