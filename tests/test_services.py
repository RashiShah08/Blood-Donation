"""Unit tests for email delivery, routing and the pledge service."""

import base64
import email
import io
import json
import smtplib
import urllib.error
import urllib.parse
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


GMAIL_CONFIG = {
    **MAIL_CONFIG,
    "GMAIL_CLIENT_ID": "123.apps.googleusercontent.com",
    "GMAIL_CLIENT_SECRET": "GOCSPX-test",
    "GMAIL_REFRESH_TOKEN": "1//refresh",
    "GMAIL_SENDER": "bloodconnectapp@gmail.com",
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

    def test_brevo_sends_each_email_over_https(self):
        calls = []

        def post(url, payload, headers, timeout):
            calls.append((url, payload, headers, timeout))
            return 201

        brevo = {**MAIL_CONFIG, "BREVO_API_KEY": "xkeysib-test", "MAIL_FROM_EMAIL": "alerts@example.org"}
        smtp = FakeSMTP()
        report = Mailer(brevo, smtp, post).send(emails("a@example.com", "b@example.com"))
        assert report.sent == ["a@example.com", "b@example.com"] and report.failed == []
        assert smtp.connections == 0  # SMTP is never used when Brevo is configured
        url, payload, headers, timeout = calls[0]
        assert url == "https://api.brevo.com/v3/smtp/email"
        assert headers == {"api-key": "xkeysib-test"} and timeout == 5
        assert payload == {
            "sender": {"name": "BloodConnect", "email": "alerts@example.org"},
            "to": [{"email": "a@example.com"}],
            "subject": "Subject a@example.com",
            "textContent": "Body",
        }

    def test_brevo_failures_are_reported_per_email(self):
        def post(url, payload, headers, timeout):
            recipient = payload["to"][0]["email"]
            if recipient == "down@example.com":
                raise urllib.error.URLError("connection refused")
            return 400 if recipient == "bad@example.com" else 201

        brevo = {**MAIL_CONFIG, "BREVO_API_KEY": "xkeysib-test", "MAIL_FROM_EMAIL": "alerts@example.org"}
        report = Mailer(brevo, FakeSMTP(), post).send(emails("ok@example.com", "bad@example.com", "down@example.com"))
        assert report.sent == ["ok@example.com"]
        assert report.failed == ["bad@example.com", "down@example.com"]

    def test_gmail_api_sends_with_one_token_per_batch(self):
        calls = []

        def google(url, data, headers, timeout):
            calls.append((url, data, headers))
            if url == "https://oauth2.googleapis.com/token":
                return 200, {"access_token": "ya29.token", "expires_in": 3599}
            return 200, {"id": "msg-1"}

        smtp = FakeSMTP()
        mailer = Mailer(GMAIL_CONFIG, smtp, gmail_http=google)
        report = mailer.send(emails("a@example.com", "b@example.com"))
        assert report.sent == ["a@example.com", "b@example.com"] and report.failed == []
        assert smtp.connections == 0
        token_calls = [call for call in calls if call[0].endswith("/token")]
        assert len(token_calls) == 1
        form = urllib.parse.parse_qs(token_calls[0][1].decode())
        assert form["grant_type"] == ["refresh_token"] and form["refresh_token"] == ["1//refresh"]

        send_url, body, headers = calls[1]
        assert send_url == "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
        assert headers["Authorization"] == "Bearer ya29.token"
        message = email.message_from_bytes(base64.urlsafe_b64decode(json.loads(body)["raw"]))
        assert message["From"] == "BloodConnect <bloodconnectapp@gmail.com>"
        assert message["To"] == "a@example.com" and message["Subject"] == "Subject a@example.com"

        # The access token is reused for the next batch until it is about to expire.
        mailer.send(emails("c@example.com"))
        assert len([call for call in calls if call[0].endswith("/token")]) == 1

    def test_gmail_api_rejected_credentials_fail_the_batch_without_sending(self):
        calls = []

        def google(url, data, headers, timeout):
            calls.append(url)
            return 400, {"error": "invalid_grant", "error_description": "Token has been expired or revoked."}

        report = Mailer(GMAIL_CONFIG, FakeSMTP(), gmail_http=google).send(emails("a@example.com", "b@example.com"))
        assert report.sent == [] and report.failed == ["a@example.com", "b@example.com"]
        assert calls == ["https://oauth2.googleapis.com/token"]

    def test_gmail_api_failures_are_reported_per_email(self):
        def google(url, data, headers, timeout):
            if url.endswith("/token"):
                return 200, {"access_token": "ya29.token", "expires_in": 3599}
            to = email.message_from_bytes(base64.urlsafe_b64decode(json.loads(data)["raw"]))["To"]
            if to == "down@example.com":
                raise urllib.error.URLError("connection reset")
            if to == "bad@example.com":
                return 400, {"error": {"code": 400, "message": "Invalid To header"}}
            return 200, {"id": "ok"}

        report = Mailer(GMAIL_CONFIG, FakeSMTP(), gmail_http=google).send(
            emails("ok@example.com", "bad@example.com", "down@example.com")
        )
        assert report.sent == ["ok@example.com"]
        assert report.failed == ["bad@example.com", "down@example.com"]

    def test_gmail_api_takes_priority_over_other_providers(self):
        used = []

        def google(url, data, headers, timeout):
            used.append("gmail")
            return 200, ({"access_token": "t", "expires_in": 3599} if url.endswith("/token") else {"id": "x"})

        def brevo(*args):
            used.append("brevo")
            return 201

        config = {**GMAIL_CONFIG, "BREVO_API_KEY": "xkeysib-test", "MAIL_FROM_EMAIL": "x@example.org"}
        Mailer(config, FakeSMTP(), brevo, google).send(emails("a@example.com"))
        assert "brevo" not in used and "gmail" in used

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
