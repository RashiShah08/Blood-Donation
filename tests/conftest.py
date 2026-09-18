"""Shared fixtures: an app backed by a PostgreSQL test database, factories and helpers."""

import itertools
import os
from datetime import date, timedelta

import pytest
import sqlalchemy
from sqlalchemy import text
from sqlalchemy.engine import make_url

from bloodconnect import create_app
from bloodconnect.config import normalize_database_url
from bloodconnect.extensions import db
from bloodconnect.models import BloodRequest, Donor, Hospital, Notification, Pledge
from bloodconnect.services.notifications import SendReport

HOSPITAL_LOCATION = (19.1136, 72.8697)  # Andheri East, Mumbai
NEAR_LOCATION = (19.1236, 72.8697)  # ~1.1 km north of the hospital
FAR_LOCATION = (18.5204, 73.8567)  # Pune, ~120 km away
DONOR_PASSWORD = "donor-pass-123"
HOSPITAL_PASSWORD = "hospital-pass-123"
SAFE_ANSWERS = {**{f"q{i}": "no" for i in range(1, 11)}, "q11": "yes"}

# Wiped on every test, so it must be a dedicated test database (see db/init and docker-compose.yml).
TEST_DATABASE_URL = os.getenv(
    # 127.0.0.1 rather than localhost: on Windows, localhost tries IPv6 first and each connection stalls ~2 s.
    "TEST_DATABASE_URL",
    "postgresql://bloodconnect:bloodconnect@127.0.0.1:5432/bloodconnect_test",
)
TABLES = "login_throttles, notifications, pledges, blood_requests, donors, hospitals"

TEST_CONFIG = {
    "TESTING": True,
    "SECRET_KEY": "test-secret-key",
    "SQLALCHEMY_DATABASE_URI": TEST_DATABASE_URL,
    "WTF_CSRF_ENABLED": False,
    "RATELIMIT_ENABLED": False,
    "MAIL_SUPPRESS_SEND": True,
    "EMAIL_ADDRESS": "alerts@example.com",
    "EMAIL_PASSWORD": "not-used",
    "ORS_API_KEY": None,
    "PUBLIC_BASE_URL": None,
    "REQUIRE_HOSPITAL_VERIFICATION": False,
}

_counter = itertools.count(1)


@pytest.fixture(scope="session", autouse=True)
def postgres_test_database():
    url = make_url(normalize_database_url(TEST_DATABASE_URL))
    if not (url.database or "").endswith("test"):
        pytest.exit(f"Refusing to run: TEST_DATABASE_URL database {url.database!r} must end in 'test'.", returncode=1)
    engine = sqlalchemy.create_engine(url)
    try:
        # Rebuild the schema once per run so the test database always matches the current models.
        db.metadata.drop_all(engine)
        db.metadata.create_all(engine)
    except sqlalchemy.exc.OperationalError as error:
        pytest.exit(
            f"Can't connect to the test database ({url.render_as_string(hide_password=True)}).\n"
            "Start PostgreSQL with `docker compose up -d`, or set TEST_DATABASE_URL.\n"
            f"{error.orig}",
            returncode=1,
        )
    finally:
        engine.dispose()


def reset_database():
    """Empty every table and restart ids, so each test starts from a clean database."""
    db.session.remove()
    db.session.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))
    db.session.commit()


def build_app(**overrides):
    app = create_app({**TEST_CONFIG, **overrides}, load_env=False)
    with app.app_context():
        reset_database()
    return app


def dispose(app):
    """Close the app's database connections so tests don't exhaust PostgreSQL's connection limit."""
    with app.app_context():
        db.session.remove()
        db.engine.dispose()


@pytest.fixture
def app():
    app = build_app()
    with app.app_context():
        yield app
        db.session.remove()
    dispose(app)


@pytest.fixture
def client(app):
    return app.test_client()


class RecordingMailer:
    """Stands in for the SMTP mailer; records messages and can simulate failures."""

    def __init__(self, config, fail_for=()):
        self.config = config
        self.fail_for = set(fail_for)
        self.outbox = []

    def send(self, emails):
        report = SendReport()
        for email in emails:
            if email.to in self.fail_for:
                report.failed.append(email.to)
            else:
                report.sent.append(email.to)
                self.outbox.append(email)
        return report


@pytest.fixture
def mailer(app):
    recording = RecordingMailer(app.config)
    app.extensions["mailer"] = recording
    return recording


@pytest.fixture
def make_hospital(app):
    def _make(**overrides) -> Hospital:
        n = next(_counter)
        values = {
            "name": f"Test Hospital {n}",
            "email": f"hospital{n}@example.com",
            "phone": "+91 22 5555 0100",
            "address": "1 Main Road",
            "city": "Mumbai",
            "state": "Maharashtra",
            "pincode": "400069",
            "country": "India",
            "hospital_type": "government",
            "latitude": HOSPITAL_LOCATION[0],
            "longitude": HOSPITAL_LOCATION[1],
            "is_verified": True,
        }
        values.update(overrides)
        hospital = Hospital(**values)
        hospital.set_password(HOSPITAL_PASSWORD)
        db.session.add(hospital)
        db.session.commit()
        return hospital

    return _make


@pytest.fixture
def make_donor(app):
    def _make(**overrides) -> Donor:
        n = next(_counter)
        values = {
            "name": f"Donor {n}",
            "email": f"donor{n}@example.com",
            "phone": "+91 98765 43210",
            "date_of_birth": date.today() - timedelta(days=365 * 30),
            "gender": "Male",
            "weight_kg": 70,
            "blood_group": "O-",
            "health_issues": "none",
            "latitude": NEAR_LOCATION[0],
            "longitude": NEAR_LOCATION[1],
            "is_available": True,
        }
        values.update(overrides)
        donor = Donor(**values)
        donor.set_password(DONOR_PASSWORD)
        db.session.add(donor)
        db.session.commit()
        return donor

    return _make


@pytest.fixture
def make_request(app, make_hospital):
    def _make(hospital=None, **overrides) -> BloodRequest:
        hospital = hospital or make_hospital()
        values = {
            "hospital_id": hospital.id,
            "patient_name": "Confidential Patient",
            "patient_gender": "Female",
            "patient_weight_kg": 60,
            "blood_group": "A+",
            "units_required": 2,
            "urgency": "high",
            "clinical_notes": "Private clinical note",
        }
        values.update(overrides)
        blood_request = BloodRequest(**values)
        db.session.add(blood_request)
        db.session.commit()
        return blood_request

    return _make


def login_donor(client, donor, password=DONOR_PASSWORD):
    return client.post("/donor/login", data={"email": donor.email, "password": password})


def login_hospital(client, hospital, password=HOSPITAL_PASSWORD):
    return client.post("/hospital/login", data={"email": hospital.email, "password": password})


def reload(instance):
    db.session.expire_all()
    return db.session.get(type(instance), instance.id)


def pledge_for(donor, blood_request):
    return db.session.query(Pledge).filter_by(donor_id=donor.id, request_id=blood_request.id).one_or_none()


def notification_count(blood_request):
    return db.session.query(Notification).filter_by(request_id=blood_request.id).count()
