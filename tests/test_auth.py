"""Integration tests for registration, login, logout and password reset."""

import re
from datetime import date, timedelta

import pytest

from bloodconnect import auth
from bloodconnect.extensions import db
from bloodconnect.models import Donor, Hospital
from tests.conftest import DONOR_PASSWORD, HOSPITAL_PASSWORD, login_donor, login_hospital


def donor_form(**overrides):
    form = {
        "name": "Asha Verma",
        "email": "asha@example.com",
        "phone": "",
        "date_of_birth": "1995-04-12",
        "gender": "Female",
        "weight_kg": "58",
        "blood_group": "B+",
        "health_issues": "none",
        "latitude": "19.12",
        "longitude": "72.87",
        "password": "correct-horse-1",
        "confirm_password": "correct-horse-1",
        "consent": "on",
    }
    form.update(overrides)
    return form


def hospital_form(**overrides):
    form = {
        "name": "Sunrise Hospital",
        "hospital_type": "private",
        "email": "desk@sunrise.example",
        "phone": "+91 22 5555 0101",
        "address": "12 Link Road",
        "city": "Mumbai",
        "state": "Maharashtra",
        "pincode": "400053",
        "country": "India",
        "latitude": "19.13",
        "longitude": "72.83",
        "password": "hospital-secret-1",
        "confirm_password": "hospital-secret-1",
        "consent": "on",
    }
    form.update(overrides)
    return form


class TestDonorRegistration:
    def test_success_hashes_password_and_logs_in(self, client):
        response = client.post("/donor/register", data=donor_form())
        assert response.status_code == 302
        assert response.headers["Location"].endswith("/donor/dashboard")

        donor = db.session.query(Donor).filter_by(email="asha@example.com").one()
        assert donor.password_hash != "correct-horse-1"
        assert donor.password_hash.startswith(("scrypt:", "pbkdf2:"))
        assert donor.check_password("correct-horse-1")
        assert donor.date_of_birth == date(1995, 4, 12)
        assert client.get("/donor/dashboard").status_code == 200

    @pytest.mark.parametrize(
        "overrides, field_message",
        [
            ({"name": ""}, "Full name is required."),
            ({"email": "not-an-email"}, "Enter a valid email address."),
            ({"blood_group": "Z+"}, "Select a valid blood group."),
            ({"weight_kg": "abc"}, "Weight must be a number."),
            ({"gender": "Robot"}, "Select a valid gender."),
            ({"latitude": "", "longitude": ""}, "Choose a location on the map"),
            ({"confirm_password": "something-else"}, "Passwords don&#39;t match."),
            ({"password": "short", "confirm_password": "short"}, "at least 8 characters"),
            ({"consent": ""}, "Please agree to the terms"),
            ({"date_of_birth": "2999-01-01"}, "can&#39;t be in the future"),
        ],
    )
    def test_invalid_input_is_rejected_with_message(self, client, overrides, field_message):
        response = client.post("/donor/register", data=donor_form(**overrides))
        assert response.status_code == 422
        assert field_message in response.get_data(as_text=True)
        assert db.session.query(Donor).count() == 0

    def test_under_18_cannot_register(self, client):
        dob = (date.today() - timedelta(days=365 * 17)).isoformat()
        response = client.post("/donor/register", data=donor_form(date_of_birth=dob))
        assert response.status_code == 422
        assert "at least 18" in response.get_data(as_text=True)

    def test_duplicate_email_is_case_insensitive(self, client, make_donor):
        make_donor(email="asha@example.com")
        response = client.post("/donor/register", data=donor_form(email="ASHA@example.com"))
        assert response.status_code == 422
        assert "already exists" in response.get_data(as_text=True)

    def test_form_is_refilled_but_password_is_not_echoed(self, client):
        response = client.post("/donor/register", data=donor_form(email="bad"))
        body = response.get_data(as_text=True)
        assert 'value="Asha Verma"' in body
        assert "correct-horse-1" not in body


class TestHospitalRegistration:
    def test_success(self, client):
        response = client.post("/hospital/register", data=hospital_form())
        assert response.status_code == 302
        hospital = db.session.query(Hospital).one()
        assert hospital.is_verified is True  # verification not required by default
        assert client.get("/hospital/dashboard").status_code == 200

    def test_requires_verification_when_configured(self, app, client):
        app.config["REQUIRE_HOSPITAL_VERIFICATION"] = True
        client.post("/hospital/register", data=hospital_form())
        assert db.session.query(Hospital).one().is_verified is False

    def test_duplicate_name_is_case_insensitive(self, client, make_hospital):
        make_hospital(name="Sunrise Hospital")
        response = client.post("/hospital/register", data=hospital_form(name="sunrise hospital"))
        assert response.status_code == 422
        assert "already registered" in response.get_data(as_text=True)

    def test_invalid_pincode_and_type(self, client):
        response = client.post("/hospital/register", data=hospital_form(pincode="!!", hospital_type="spaceport"))
        body = response.get_data(as_text=True)
        assert response.status_code == 422
        assert "Enter a valid pincode." in body and "Select a valid hospital type." in body


class TestLogin:
    def test_donor_login_success(self, client, make_donor):
        donor = make_donor()
        response = login_donor(client, donor)
        assert response.status_code == 302 and response.headers["Location"].endswith("/donor/dashboard")

    def test_email_is_case_insensitive(self, client, make_donor):
        make_donor(email="mixed@example.com")
        response = client.post("/donor/login", data={"email": "MIXED@Example.com", "password": DONOR_PASSWORD})
        assert response.status_code == 302

    def test_wrong_password_and_unknown_email_look_identical(self, client, make_donor):
        donor = make_donor()
        wrong = client.post("/donor/login", data={"email": donor.email, "password": "nope-nope"})
        unknown = client.post("/donor/login", data={"email": "ghost@example.com", "password": "nope-nope"})
        assert wrong.status_code == unknown.status_code == 401
        assert "Incorrect email or password." in wrong.get_data(as_text=True)
        assert "Incorrect email or password." in unknown.get_data(as_text=True)

    def test_hospital_login(self, client, make_hospital):
        hospital = make_hospital()
        assert login_hospital(client, hospital).headers["Location"].endswith("/hospital/dashboard")
        assert login_hospital(client, hospital, "wrong-password").status_code == 401

    def test_donor_credentials_do_not_work_for_hospital_login(self, client, make_donor):
        donor = make_donor()
        assert (
            client.post("/hospital/login", data={"email": donor.email, "password": DONOR_PASSWORD}).status_code == 401
        )

    def test_safe_next_redirect_is_followed(self, client, make_donor):
        donor = make_donor()
        response = client.post(
            "/donor/login?next=/donor/impact", data={"email": donor.email, "password": DONOR_PASSWORD}
        )
        assert response.headers["Location"] == "/donor/impact"

    @pytest.mark.parametrize(
        "target", ["https://evil.example/phish", "//evil.example", "/\\evil.example", "javascript:alert(1)"]
    )
    def test_open_redirects_are_blocked(self, client, make_donor, target):
        donor = make_donor()
        response = client.post(
            "/donor/login", query_string={"next": target}, data={"email": donor.email, "password": DONOR_PASSWORD}
        )
        assert response.headers["Location"].endswith("/donor/dashboard")

    def test_logged_in_donor_visiting_login_is_sent_to_dashboard(self, client, make_donor):
        login_donor(client, make_donor())
        assert client.get("/donor/login").headers["Location"].endswith("/donor/dashboard")

    def test_switching_accounts_clears_previous_session(self, client, make_donor, make_hospital):
        login_donor(client, make_donor())
        login_hospital(client, make_hospital())
        assert client.get("/donor/dashboard").status_code == 302
        assert client.get("/hospital/dashboard").status_code == 200


class TestLogout:
    def test_logout_requires_post(self, client):
        assert client.get("/logout").status_code == 405

    def test_logout_ends_session(self, client, make_donor):
        login_donor(client, make_donor())
        response = client.post("/logout", follow_redirects=True)
        assert "You&#39;ve been logged out." in response.get_data(as_text=True)
        assert client.get("/donor/dashboard").status_code == 302


class TestPasswordReset:
    def _reset_link(self, mailer):
        match = re.search(r"http://localhost(/\S+/reset-password/\S+)", mailer.outbox[-1].body)
        assert match, mailer.outbox[-1].body
        return match.group(1)

    def test_full_reset_flow(self, client, mailer, make_donor):
        donor = make_donor()
        response = client.post("/donor/forgot-password", data={"email": donor.email})
        assert response.status_code == 302
        assert len(mailer.outbox) == 1 and mailer.outbox[0].to == donor.email

        link = self._reset_link(mailer)
        assert client.get(link).status_code == 200
        response = client.post(link, data={"password": "brand-new-pass-1", "confirm_password": "brand-new-pass-1"})
        assert response.headers["Location"].endswith("/donor/login")

        assert login_donor(client, donor, "brand-new-pass-1").status_code == 302
        client.post("/logout")
        assert login_donor(client, donor, DONOR_PASSWORD).status_code == 401

    def test_link_only_works_once(self, client, mailer, make_donor):
        donor = make_donor()
        client.post("/donor/forgot-password", data={"email": donor.email})
        link = self._reset_link(mailer)
        client.post(link, data={"password": "brand-new-pass-1", "confirm_password": "brand-new-pass-1"})
        again = client.get(link)
        assert again.status_code == 302 and "forgot-password" in again.headers["Location"]

    def test_unknown_email_gets_same_response_and_no_email(self, client, mailer):
        response = client.post("/donor/forgot-password", data={"email": "ghost@example.com"}, follow_redirects=True)
        assert "If an account exists" in response.get_data(as_text=True)
        assert mailer.outbox == []

    def test_expired_token_is_rejected(self, client, mailer, make_hospital, monkeypatch):
        hospital = make_hospital()
        client.post("/hospital/forgot-password", data={"email": hospital.email})
        link = self._reset_link(mailer)
        monkeypatch.setattr(auth, "RESET_TOKEN_MAX_AGE_SECONDS", -1)
        assert "forgot-password" in client.get(link).headers["Location"]

    def test_tampered_or_wrong_kind_token_is_rejected(self, client, mailer, make_donor):
        donor = make_donor()
        client.post("/donor/forgot-password", data={"email": donor.email})
        link = self._reset_link(mailer)
        assert "forgot-password" in client.get(link + "x").headers["Location"]
        assert "forgot-password" in client.get(link.replace("/donor/", "/hospital/")).headers["Location"]

    def test_new_password_is_validated(self, client, mailer, make_hospital):
        hospital = make_hospital()
        client.post("/hospital/forgot-password", data={"email": hospital.email})
        link = self._reset_link(mailer)
        response = client.post(link, data={"password": "short", "confirm_password": "short"})
        assert response.status_code == 422
        assert login_hospital(client, hospital, HOSPITAL_PASSWORD).status_code == 302
