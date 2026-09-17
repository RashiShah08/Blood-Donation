"""Unit tests for email delivery, routing and the pledge service."""

import io
import json
import smtplib
import urllib.error
from datetime import date

import pytest

from bloodconnect.extensions import db
from bloodconnect.services import pledges, routing
from bloodconnect.services.matching import find_donors_near
from bloodconnect.services.notifications import Mailer, OutgoingEmail
from tests.conftest import SAFE_ANSWERS, reload

MAIL_CONFIG = {
    "MAIL_SUPPRESS_SEND": False,
    "SMTP_HOST": "smtp.test",
    "SMTP_PORT": 465,
    "SMTP_TIMEOUT_SECONDS": 5,
    "EMAIL_ADDRESS": "alerts@example.com",
    "EMAIL_PASSWORD": "app-password",
    "MAIL_FROM_NAME": "BloodConnect",
}


class FakeSMTP:
    def __init__(self, fail_for=(), fail_login=False):
        self.fail_for = set(fail_for)
        self.fail_login = fail_login
        self.connections = 0
        self.logins = 0
        self.messages = []
        self.args = None

    def __call__(self, host, port, timeout):
        self.connections += 1
        self.args = (host, port, timeout)
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def login(self, user, password):
        if self.fail_login:
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")
        self.logins += 1

    def send_message(self, message):
        if message["To"] in self.fail_for:
            raise smtplib.SMTPRecipientsRefused({message["To"]: (550, b"no such user")})
        self.messages.append(message)


def emails(*recipients):
    return [OutgoingEmail(to, f"Subject {to}", "Body") for to in recipients]


class TestMailer:
    def test_sends_batch_over_one_connection(self):
        smtp = FakeSMTP()
        report = Mailer(MAIL_CONFIG, smtp).send(emails("a@example.com", "b@example.com"))
        assert report.sent == ["a@example.com", "b@example.com"] and report.failed == []
        assert (smtp.connections, smtp.logins) == (1, 1)
        assert smtp.args == ("smtp.test", 465, 5)
        assert smtp.messages[0]["From"] == "BloodConnect <alerts@example.com>"

    def test_individual_failures_are_reported(self):
        report = Mailer(MAIL_CONFIG, FakeSMTP(fail_for={"b@example.com"})).send(
            emails("a@example.com", "b@example.com")
        )
        assert report.sent == ["a@example.com"] and report.failed == ["b@example.com"]

    def test_connection_failure_fails_everything(self):
        report = Mailer(MAIL_CONFIG, FakeSMTP(fail_login=True)).send(emails("a@example.com", "b@example.com"))
        assert report.sent == [] and report.failed == ["a@example.com", "b@example.com"]

    def test_network_error_fails_everything(self):
        def refuse(*args):
            raise ConnectionRefusedError("down")

        report = Mailer(MAIL_CONFIG, refuse).send(emails("a@example.com"))
        assert report.failed == ["a@example.com"]

    def test_suppressed_sending_is_simulated(self):
        smtp = FakeSMTP()
        report = Mailer({**MAIL_CONFIG, "MAIL_SUPPRESS_SEND": True}, smtp).send(emails("a@example.com"))
        assert report.simulated and report.sent == ["a@example.com"]
        assert smtp.connections == 0

    def test_empty_batch(self):
        smtp = FakeSMTP()
        assert Mailer(MAIL_CONFIG, smtp).send([]).sent == []
        assert smtp.connections == 0


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


ORS_PAYLOAD = {
    "features": [
        {
            "properties": {"summary": {"distance": 2500.0, "duration": 420.0}},
            "geometry": {"coordinates": [[72.8697, 19.1236], [72.8700, 19.1180], [72.8697, 19.1136]]},
        }
    ]
}


class TestRouting:
    @pytest.fixture(autouse=True)
    def clear_cache(self):
        routing._ors_route.cache_clear()
        yield
        routing._ors_route.cache_clear()

    def test_straight_line_without_key(self):
        route = routing.get_route((19.1236, 72.8697), (19.1136, 72.8697), None)
        assert route.source == "straight_line"
        assert route.duration_min == pytest.approx(route.distance_km / 30 * 60)

    def test_openrouteservice_success_swaps_coordinates(self, monkeypatch):
        calls = []

        def fake_urlopen(request, timeout):
            calls.append(request)
            return FakeResponse(json.dumps(ORS_PAYLOAD).encode())

        monkeypatch.setattr(routing.urllib.request, "urlopen", fake_urlopen)
        route = routing.get_route((19.1236, 72.8697), (19.1136, 72.8697), "secret-key")
        assert route.source == "openrouteservice"
        assert (route.distance_km, route.duration_min) == (2.5, 7.0)
        assert route.coordinates[0] == (19.1236, 72.8697)
        assert calls[0].get_header("Authorization") == "secret-key"
        assert "secret-key" not in calls[0].full_url  # key sent as a header, not in the URL

        routing.get_route((19.12361, 72.86971), (19.1136, 72.8697), "secret-key")
        assert len(calls) == 1  # GPS jitter reuses the cached route

    @pytest.mark.parametrize("error", [urllib.error.URLError("offline"), TimeoutError(), ValueError("bad json")])
    def test_failures_fall_back_to_straight_line(self, monkeypatch, error):
        def failing(request, timeout):
            raise error

        monkeypatch.setattr(routing.urllib.request, "urlopen", failing)
        assert routing.get_route((19.1236, 72.8697), (19.1136, 72.8697), "key").source == "straight_line"

    def test_unexpected_payload_falls_back(self, monkeypatch):
        monkeypatch.setattr(
            routing.urllib.request, "urlopen", lambda request, timeout: FakeResponse(b'{"features": []}')
        )
        assert routing.get_route((19.1236, 72.8697), (19.1136, 72.8697), "key").source == "straight_line"

    def test_to_dict_rounds(self):
        data = routing.Route(1.23456, 2.3456, ((1.0, 2.0),), "straight_line").to_dict()
        assert data == {
            "distance_km": 1.23,
            "duration_min": 2.3,
            "coordinates": [[1.0, 2.0]],
            "source": "straight_line",
        }


class TestPledgeService:
    def test_units_guard_holds_under_repeated_pledges(self, app, make_request, make_donor):
        blood_request = make_request(units_required=2, blood_group="O-")
        donors = [make_donor() for _ in range(5)]
        results = []
        for donor in donors:
            try:
                pledges.create_pledge(donor, blood_request.id, SAFE_ANSWERS, date.today())
                results.append("ok")
            except pledges.PledgeError as error:
                results.append(error.message)
        assert results.count("ok") == 2
        assert reload(blood_request).units_pledged == 2

    def test_errors_carry_reasons(self, app, make_request, make_donor):
        blood_request = make_request()
        with pytest.raises(pledges.PledgeError) as caught:
            pledges.create_pledge(make_donor(weight_kg=40), blood_request.id, SAFE_ANSWERS, date.today())
        assert caught.value.reasons == ["Donors must weigh at least 45 kg."]

    def test_missing_request(self, app, make_donor):
        with pytest.raises(pledges.PledgeError, match="no longer open"):
            pledges.create_pledge(make_donor(), 9999, SAFE_ANSWERS, date.today())

    def test_boundary_distance_is_included(self, app, make_hospital, make_request, make_donor):
        hospital = make_hospital(latitude=0.0, longitude=0.0)
        blood_request = make_request(hospital=hospital, blood_group="O-")
        # 0.0449 degrees of latitude is about 4.99 km
        make_donor(latitude=0.0449, longitude=0.0)
        make_donor(latitude=0.0451, longitude=0.0)
        matches = find_donors_near(blood_request, 5, date.today())
        assert len(matches) == 1 and matches[0].distance_km < 5
        db.session.rollback()
