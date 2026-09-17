"""Access control, CSRF, rate limiting, headers, XSS escaping and privacy guarantees."""

import re

import pytest

from bloodconnect.extensions import db
from bloodconnect.main import LEGACY_REDIRECTS
from bloodconnect.models import Pledge
from tests.conftest import (
    DONOR_PASSWORD,
    FAR_LOCATION,
    SAFE_ANSWERS,
    build_app,
    dispose,
    login_donor,
    login_hospital,
    reload,
)

DONOR_PAGES = [
    "/donor/dashboard",
    "/donor/impact",
    "/donor/profile",
    "/donor/requests/1",
    "/donor/requests/1/pledge",
    "/donor/pledges/1/directions",
]
HOSPITAL_PAGES = ["/hospital/dashboard", "/hospital/requests/new", "/hospital/requests/1"]
PUBLIC_PAGES = [
    "/",
    "/how-it-works",
    "/for-donors",
    "/for-patients",
    "/faq",
    "/about",
    "/terms",
    "/privacy",
    "/donor/login",
    "/hospital/login",
    "/donor/register",
    "/hospital/register",
    "/donor/forgot-password",
    "/hospital/forgot-password",
]


class TestAnonymousAccess:
    @pytest.mark.parametrize("path", DONOR_PAGES)
    def test_donor_pages_redirect_to_login_with_next(self, client, path):
        response = client.get(path)
        assert response.status_code == 302
        assert "/donor/login?next=" in response.headers["Location"]

    @pytest.mark.parametrize("path", HOSPITAL_PAGES)
    def test_hospital_pages_redirect_to_login(self, client, path):
        response = client.get(path)
        assert response.status_code == 302
        assert "/hospital/login" in response.headers["Location"]

    @pytest.mark.parametrize(
        "method, path",
        [
            ("get", "/donor/api/pledges/1/route?lat=1&lng=1"),
            ("post", "/donor/api/pledges/1/arrived"),
            ("get", "/hospital/api/requests/1/donors?radius=5"),
            ("post", "/hospital/api/requests/1/notify"),
        ],
    )
    def test_api_returns_json_401(self, client, method, path):
        response = getattr(client, method)(path, json={} if method == "post" else None)
        assert response.status_code == 401
        assert response.get_json() == {"error": "Please log in to continue."}

    @pytest.mark.parametrize("path", PUBLIC_PAGES)
    def test_public_pages_render(self, client, path):
        response = client.get(path)
        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert body.lstrip().startswith("<!DOCTYPE html>")
        assert '<html lang="en">' in body and 'name="viewport"' in body
        assert body.count("<h1") == 1


class TestRoleSeparation:
    def test_donor_cannot_use_hospital_area(self, client, make_donor):
        login_donor(client, make_donor())
        assert client.get("/hospital/dashboard").status_code == 302
        assert client.get("/hospital/api/requests/1/donors?radius=5").status_code == 401

    def test_hospital_cannot_use_donor_area(self, client, make_hospital):
        login_hospital(client, make_hospital())
        assert client.get("/donor/dashboard").status_code == 302


class TestHospitalIsolation:
    """A hospital must never see or change another hospital's requests (the old app's IDOR)."""

    @pytest.fixture
    def other_request(self, make_hospital, make_request, make_donor):
        other = make_request(hospital=make_hospital(), blood_group="O-")
        donor = make_donor(blood_group="O-")
        pledge = Pledge(request_id=other.id, donor_id=donor.id)
        other.units_pledged = 1
        db.session.add(pledge)
        db.session.commit()
        return other, pledge

    def test_cannot_view_or_query_other_requests(self, client, make_hospital, other_request):
        blood_request, _ = other_request
        login_hospital(client, make_hospital())
        assert client.get(f"/hospital/requests/{blood_request.id}").status_code == 404
        assert client.get(f"/hospital/api/requests/{blood_request.id}/donors?radius=5").status_code == 404
        assert client.post(f"/hospital/api/requests/{blood_request.id}/notify", json={"radius": 5}).status_code == 404
        assert (
            client.post(f"/hospital/requests/{blood_request.id}/close", data={"outcome": "cancelled"}).status_code
            == 404
        )
        assert reload(blood_request).status == "open"

    def test_cannot_record_outcome_for_other_pledge(self, client, make_hospital, other_request):
        _, pledge = other_request
        login_hospital(client, make_hospital())
        client.post(f"/hospital/pledges/{pledge.id}/outcome", data={"outcome": "donated"})
        assert reload(pledge).status == "pledged"

    def test_no_session_means_no_fallback_hospital(self, client, make_hospital):
        """Regression: the old dashboard logged anonymous visitors in as the first hospital."""
        make_hospital()
        assert client.get("/hospital/dashboard").status_code == 302


class TestDonorIsolation:
    def test_cannot_touch_another_donors_pledge(self, client, make_donor, make_request):
        blood_request = make_request(blood_group="O-")
        owner = make_donor(blood_group="O-")
        login_donor(client, owner)
        client.post(f"/donor/requests/{blood_request.id}/pledge", data=SAFE_ANSWERS)
        pledge = db.session.query(Pledge).one()
        client.post("/logout")

        login_donor(client, make_donor(blood_group="O-"))
        assert client.get(f"/donor/pledges/{pledge.id}/directions").status_code == 404
        assert client.get(f"/donor/api/pledges/{pledge.id}/route?lat=19&lng=72").status_code == 404
        assert client.post(f"/donor/api/pledges/{pledge.id}/arrived").status_code == 409
        client.post(f"/donor/pledges/{pledge.id}/cancel")
        assert reload(pledge).status == "pledged"

    def test_incompatible_request_is_hidden(self, client, make_donor, make_request):
        blood_request = make_request(blood_group="O-")
        login_donor(client, make_donor(blood_group="A+"))
        assert client.get(f"/donor/requests/{blood_request.id}").status_code == 404
        assert client.get(f"/donor/requests/{blood_request.id}/pledge").status_code == 404


class TestCsrf:
    @pytest.fixture
    def csrf_app(self):
        # No app context is held open here: like a real server, every request must get a
        # fresh context, otherwise Flask-WTF's per-request token cache on `g` leaks between requests.
        app = build_app(WTF_CSRF_ENABLED=True)
        yield app
        dispose(app)

    @staticmethod
    def token_from(response):
        return re.search(r'name="csrf-token" content="([^"]+)"', response.get_data(as_text=True)).group(1)

    def test_form_post_without_token_is_rejected(self, csrf_app):
        client = csrf_app.test_client()
        response = client.post("/donor/login", data={"email": "a@example.com", "password": "whatever-1"})
        assert response.status_code == 400
        assert "session expired" in response.get_data(as_text=True)

    def test_form_post_with_token_is_processed(self, csrf_app):
        client = csrf_app.test_client()
        token = self.token_from(client.get("/donor/login"))
        response = client.post(
            "/donor/login", data={"email": "a@example.com", "password": "whatever-1", "csrf_token": token}
        )
        assert response.status_code == 401

    def test_json_api_requires_header(self, csrf_app):
        from bloodconnect.models import BloodRequest, Hospital
        from tests.conftest import HOSPITAL_LOCATION

        with csrf_app.app_context():
            hospital = Hospital(
                name="H",
                email="h@example.com",
                phone="+91 22 5555 0000",
                address="Road 1",
                city="Mumbai",
                state="MH",
                pincode="400001",
                hospital_type="private",
                latitude=HOSPITAL_LOCATION[0],
                longitude=HOSPITAL_LOCATION[1],
                is_verified=True,
            )
            hospital.set_password("hospital-pass-123")
            db.session.add(hospital)
            db.session.flush()
            db.session.add(
                BloodRequest(
                    hospital_id=hospital.id,
                    patient_name="P",
                    patient_gender="Male",
                    blood_group="A+",
                    units_required=1,
                    urgency="low",
                )
            )
            db.session.commit()

        client = csrf_app.test_client()
        token = self.token_from(client.get("/hospital/login"))
        client.post(
            "/hospital/login", data={"email": "h@example.com", "password": "hospital-pass-123", "csrf_token": token}
        )
        rejected = client.post("/hospital/api/requests/1/notify", json={"radius": 5})
        assert rejected.status_code == 400 and "error" in rejected.get_json()
        # Logging in starts a new session, so pages rendered after login carry a new token.
        stale = client.post("/hospital/api/requests/1/notify", json={"radius": 5}, headers={"X-CSRFToken": token})
        assert stale.status_code == 400
        fresh_token = self.token_from(client.get("/hospital/requests/1"))
        accepted = client.post(
            "/hospital/api/requests/1/notify", json={"radius": 5}, headers={"X-CSRFToken": fresh_token}
        )
        assert accepted.status_code == 200


class TestRateLimiting:
    def test_login_is_rate_limited(self):
        app = build_app(RATELIMIT_ENABLED=True, RATELIMIT_STORAGE_URI="memory://")
        with app.app_context():
            client = app.test_client()
            statuses = [
                client.post("/donor/login", data={"email": "x@example.com", "password": "guess-guess"}).status_code
                for _ in range(11)
            ]
            assert statuses[:10] == [401] * 10
            assert statuses[10] == 429
            assert "Too many attempts" in client.post("/donor/login", data={}).get_data(as_text=True)
        dispose(app)


class TestHeadersAndErrors:
    def test_security_headers(self, client):
        headers = client.get("/").headers
        assert "script-src 'self'" in headers["Content-Security-Policy"]
        assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
        assert headers["X-Frame-Options"] == "DENY"
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
        assert "Strict-Transport-Security" not in headers

    def test_hsts_when_cookies_are_secure(self):
        app = build_app(SESSION_COOKIE_SECURE=True)
        with app.app_context():
            assert "max-age" in app.test_client().get("/").headers["Strict-Transport-Security"]
        dispose(app)

    def test_personal_pages_are_not_cached(self, client, make_donor):
        login_donor(client, make_donor())
        assert client.get("/donor/dashboard").headers["Cache-Control"] == "no-store"

    def test_pages_with_a_csrf_token_are_not_cached(self, client):
        # Login pages are anonymous but carry a CSRF token, so they must not be stored either.
        assert client.get("/donor/login").headers["Cache-Control"] == "no-store"

    def test_static_files_are_versioned_and_cacheable(self, client):
        body = client.get("/").get_data(as_text=True)
        match = re.search(r"/static/css/app\.css\?v=(\d+)", body)
        assert match, "the stylesheet URL carries no version"
        response = client.get(f"/static/css/app.css?v={match.group(1)}")
        assert response.status_code == 200
        assert "max-age=31536000" in response.headers["Cache-Control"]

    def test_session_cookie_flags(self, client, make_donor):
        response = login_donor(client, make_donor())
        cookie = response.headers["Set-Cookie"]
        assert "HttpOnly" in cookie and "SameSite=Lax" in cookie

    def test_html_and_json_404(self, client):
        page = client.get("/does-not-exist")
        assert page.status_code == 404 and "Page not found" in page.get_data(as_text=True)
        api = client.get("/hospital/api/unknown")
        assert api.status_code == 404 and api.get_json()["error"]

    def test_unexpected_errors_hide_details(self, app, client, monkeypatch):
        def explode():
            raise RuntimeError("secret internal detail")

        monkeypatch.setitem(app.view_functions, "main.about", explode)
        response = client.get("/about")
        assert response.status_code == 500
        assert "secret internal detail" not in response.get_data(as_text=True)

    @pytest.mark.parametrize("old_path, endpoint", sorted(LEGACY_REDIRECTS.items()))
    def test_legacy_urls_redirect_permanently(self, app, client, old_path, endpoint):
        from flask import url_for

        response = client.get(old_path)
        assert response.status_code == 301
        with app.test_request_context():
            assert response.headers["Location"].endswith(url_for(endpoint))

    def test_health_check(self, client):
        assert client.get("/healthz").get_json() == {"status": "ok"}


class TestOutputEscapingAndPrivacy:
    def test_hospital_supplied_text_is_escaped_for_donors(self, client, make_hospital, make_request, make_donor):
        hospital = make_hospital(name='<script>alert("x")</script> General')
        blood_request = make_request(hospital=hospital, blood_group="O-")
        login_donor(client, make_donor(blood_group="O-"))
        for path in ["/donor/dashboard", f"/donor/requests/{blood_request.id}"]:
            body = client.get(path).get_data(as_text=True)
            assert '<script>alert("x")</script>' not in body
            assert "&lt;script&gt;" in body

    def test_donors_never_see_patient_details(self, client, make_request, make_donor):
        blood_request = make_request(blood_group="O-", patient_name="Ravi Secret", clinical_notes="Very private note")
        login_donor(client, make_donor(blood_group="O-"))
        for path in [
            "/donor/dashboard",
            f"/donor/requests/{blood_request.id}",
            f"/donor/requests/{blood_request.id}/pledge",
        ]:
            body = client.get(path).get_data(as_text=True)
            assert "Ravi Secret" not in body and "Very private note" not in body

    def test_donor_search_api_exposes_no_identity(self, client, make_hospital, make_request, make_donor):
        hospital = make_hospital()
        blood_request = make_request(hospital=hospital, blood_group="A+")
        donor = make_donor(
            blood_group="O-", name="Hidden Name", email="hidden@example.com", latitude=19.123456, longitude=72.876543
        )
        login_hospital(client, hospital)
        data = client.get(f"/hospital/api/requests/{blood_request.id}/donors?radius=5").get_json()
        raw = str(data)
        assert (
            "Hidden Name" not in raw
            and "hidden@example.com" not in raw
            and str(donor.id) not in [str(v) for d in data["donors"] for v in d.values()]
        )
        assert data["donors"][0]["approx_lat"] == 19.12 and data["donors"][0]["approx_lng"] == 72.88
        assert set(data["donors"][0]) == {
            "blood_group",
            "distance_km",
            "approx_lat",
            "approx_lng",
            "notified",
            "pledged",
        }

    @pytest.mark.parametrize("path", PUBLIC_PAGES)
    def test_pages_only_load_local_assets(self, client, path):
        body = client.get(path).get_data(as_text=True)
        for src in re.findall(r'<script[^>]*src="([^"]+)"', body):
            assert src.startswith("/static/"), src
        for href in re.findall(r'<link[^>]*rel="stylesheet"[^>]*href="([^"]+)"', body):
            assert href.startswith("/static/"), href
        assert "googleusercontent" not in body and "cdn.tailwindcss" not in body
        assert "<script>" not in body  # no inline scripts (CSP script-src 'self')

    @pytest.mark.parametrize("path", PUBLIC_PAGES)
    def test_no_paid_donation_or_false_claims(self, client, path):
        body = client.get(path).get_data(as_text=True).lower()
        for phrase in ("₹", "cashback", "gift card", "1-800", "24/7", "110 pounds", "56 days"):
            assert phrase not in body, f"{phrase!r} found on {path}"

    def test_passwords_never_appear_in_pages(self, client, make_donor):
        donor = make_donor()
        login_donor(client, donor)
        for path in ["/donor/dashboard", "/donor/profile"]:
            body = client.get(path).get_data(as_text=True)
            assert DONOR_PASSWORD not in body and donor.password_hash not in body

    def test_far_donor_location_is_not_leaked_to_hospital_page(self, client, make_hospital, make_request, make_donor):
        hospital = make_hospital()
        blood_request = make_request(hospital=hospital)
        make_donor(latitude=FAR_LOCATION[0], longitude=FAR_LOCATION[1])
        login_hospital(client, hospital)
        body = client.get(f"/hospital/requests/{blood_request.id}").get_data(as_text=True)
        assert str(FAR_LOCATION[0]) not in body
