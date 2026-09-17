"""End-to-end browser tests: a real Chromium drives the real UI against a live server.

Run with: pytest -m e2e   (requires `playwright install chromium`)
"""

import base64
import re
import threading
from datetime import date, timedelta

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

from bloodconnect.extensions import db  # noqa: E402
from bloodconnect.models import BloodRequest, Donor, Hospital, Pledge  # noqa: E402
from tests.conftest import build_app, dispose  # noqa: E402

pytestmark = pytest.mark.e2e

HOSPITAL_POSITION = {"latitude": 19.1136, "longitude": 72.8697}
DONOR_POSITION = {"latitude": 19.1236, "longitude": 72.8697}
# The journey test runs in Delhi so accounts seeded in Mumbai by other tests don't appear in it.
JOURNEY_HOSPITAL_POSITION = {"latitude": 28.6139, "longitude": 77.2090}
JOURNEY_DONOR_POSITION = {"latitude": 28.6239, "longitude": 77.2090}
SAFE = {**{f"q{i}": "no" for i in range(1, 11)}, "q11": "yes"}
PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)
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
]


@pytest.fixture(scope="module")
def live_server():
    # Same PostgreSQL test database as the other tests; build_app empties it first.
    app = build_app(WTF_CSRF_ENABLED=True, SECRET_KEY="e2e-secret-key")  # exercise the real CSRF flow
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield {"url": f"http://127.0.0.1:{server.server_port}", "app": app}
    server.shutdown()
    dispose(app)


class Session:
    """A browser context with console/CSP error and network tracking."""

    def __init__(self, context, base_url):
        self.context = context
        self.page = context.new_page()
        self.errors = []
        self.foreign_requests = []
        self.page.on("console", lambda msg: self.errors.append(msg.text) if msg.type == "error" else None)
        self.page.on("pageerror", lambda exc: self.errors.append(str(exc)))
        self.page.on("dialog", lambda dialog: dialog.accept())

        def track(request):
            if not request.url.startswith((base_url, "data:", "https://tile.openstreetmap.org/")):
                self.foreign_requests.append(request.url)

        self.page.on("request", track)


@pytest.fixture
def session(browser, live_server):
    contexts = []

    def _open(geolocation=None, viewport=None) -> Session:
        context = browser.new_context(
            base_url=live_server["url"],
            geolocation=geolocation,
            permissions=["geolocation"] if geolocation else [],
            viewport=viewport or {"width": 1280, "height": 900},
        )
        # Serve blank map tiles so tests don't depend on the internet.
        context.route(
            "https://tile.openstreetmap.org/**",
            lambda route: route.fulfill(status=200, content_type="image/png", body=PNG_1PX),
        )
        contexts.append(context)
        return Session(context, live_server["url"])

    yield _open
    for context in contexts:
        context.close()


def seed(live_server, *, hospital_name, donor_email, blood_group="O-", units=1, **donor_overrides):
    with live_server["app"].app_context():
        hospital = Hospital(
            name=hospital_name,
            email=f"{donor_email}.hospital@example.com",
            phone="+91 22 5555 0100",
            address="1 Test Road",
            city="Mumbai",
            state="MH",
            pincode="400069",
            hospital_type="private",
            latitude=HOSPITAL_POSITION["latitude"],
            longitude=HOSPITAL_POSITION["longitude"],
            is_verified=True,
        )
        hospital.set_password("hospital-pass-123")
        db.session.add(hospital)
        db.session.flush()
        blood_request = BloodRequest(
            hospital_id=hospital.id,
            patient_name="Seeded Patient",
            patient_gender="Male",
            blood_group=blood_group,
            units_required=units,
            urgency="high",
        )
        donor_values = {
            "name": "Seeded Donor",
            "email": donor_email,
            "date_of_birth": date.today() - timedelta(days=365 * 30),
            "gender": "Male",
            "weight_kg": 70,
            "blood_group": "O-",
            "health_issues": "none",
            "latitude": DONOR_POSITION["latitude"],
            "longitude": DONOR_POSITION["longitude"],
        }
        donor_values.update(donor_overrides)
        donor = Donor(**donor_values)
        donor.set_password("donor-pass-123")
        db.session.add_all([blood_request, donor])
        db.session.commit()
        return blood_request.id


def ui_login(page, kind, email, password, *, expect_success=True):
    page.goto(f"/{kind}/login")
    page.get_by_label("Email").fill(email)
    page.get_by_label("Password", exact=True).fill(password)
    page.get_by_role("button", name="Log in").click()
    # Wait for the login POST to land: WebKit starts the navigation later than Chromium does.
    if expect_success:
        page.wait_for_url(lambda url: "/login" not in url)


@pytest.mark.parametrize("path", PUBLIC_PAGES)
def test_public_pages_are_clean_and_accessible(session, path):
    browser_session = session()
    page = browser_session.page
    response = page.goto(path)
    assert response.status == 200
    expect(page.locator("h1")).to_have_count(1)
    expect(page).to_have_title(re.compile("BloodConnect"))
    assert page.get_attribute("html", "lang") == "en"

    unlabeled = page.evaluate(
        """() => [...document.querySelectorAll('input:not([type=hidden]), select, textarea')]
              .filter((el) => !el.labels || el.labels.length === 0).map((el) => el.name)"""
    )
    assert unlabeled == []
    missing_alt = page.evaluate("() => [...document.querySelectorAll('img:not([alt])')].map((img) => img.src)")
    assert missing_alt == []
    page.wait_for_load_state("networkidle")
    assert browser_session.errors == []
    assert browser_session.foreign_requests == []


def test_mobile_navigation(session):
    page = session(viewport={"width": 390, "height": 844}).page
    page.goto("/")
    # A CSS locator, because role locators skip hidden elements and would match the footer link instead.
    faq_link = page.locator('#site-nav a[href="/faq"]')
    expect(faq_link).to_be_hidden()
    menu = page.get_by_role("button", name="Menu")
    menu.click()
    expect(menu).to_have_attribute("aria-expanded", "true")
    expect(faq_link).to_be_visible()
    page.keyboard.press("Escape")
    expect(menu).to_have_attribute("aria-expanded", "false")
    expect(faq_link).to_be_hidden()
    # No horizontal scrolling on a phone.
    assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")


def test_skip_link_moves_focus_to_main(session):
    page = session().page
    page.goto("/faq")
    skip = page.get_by_role("link", name="Skip to main content")
    if page.context.browser.browser_type.name == "webkit":
        skip.focus()  # Safari only tabs to form controls by default, so reach the link directly.
    else:
        page.keyboard.press("Tab")
        expect(skip).to_be_focused()
    page.keyboard.press("Enter")
    expect(page).to_have_url(re.compile("#main$"))
    expect(page.locator("#main")).to_be_focused()


def test_javascript_helpers(session):
    page = session().page
    page.goto("/")
    distance = page.evaluate("() => window.BloodConnect.distanceKm(18.9398, 72.8355, 18.5204, 73.8567)")
    assert 115 < distance < 125
    assert page.evaluate("() => window.BloodConnect.formatDistance(0.25)") == "250 m"
    assert page.evaluate("() => window.BloodConnect.formatDistance(3.456)") == "3.5 km"


def test_registration_errors_are_shown_accessibly(session):
    page = session().page
    page.goto("/donor/register")
    page.get_by_role("button", name="Create donor account").click()
    expect(page.get_by_role("alert")).to_contain_text("Please fix")
    expect(page.get_by_label("Full name")).to_have_attribute("aria-invalid", "true")
    expect(page.get_by_text("Full name is required.")).to_be_visible()
    expect(page.locator("#location-error")).to_be_visible()


def test_login_failure_logout_and_404(session, live_server):
    seed(live_server, hospital_name="Login Test Hospital", donor_email="login-test@example.com")
    page = session().page
    ui_login(page, "donor", "login-test@example.com", "wrong-password", expect_success=False)
    expect(page.get_by_role("alert")).to_contain_text("Incorrect email or password.")

    ui_login(page, "donor", "login-test@example.com", "donor-pass-123")
    expect(page).to_have_url(re.compile("/donor/dashboard$"))
    page.get_by_role("button", name="Log out").click()
    expect(page.locator(".flashes").get_by_role("status")).to_contain_text("You've been logged out.")

    page.goto("/this-page-does-not-exist")
    expect(page.get_by_role("heading", name="Page not found")).to_be_visible()


def test_full_journey_register_request_alert_pledge_navigate_donate(session, live_server):
    # 1. A donor registers using the map's "use my location" button.
    donor = session(geolocation=JOURNEY_DONOR_POSITION)
    d = donor.page
    d.goto("/donor/register")
    d.get_by_label("Full name").fill("Priya Desai")
    d.get_by_label("Email").fill("priya.journey@example.com")
    d.get_by_label("Date of birth").fill("1994-06-15")
    d.get_by_label("Gender").select_option("Female")
    d.get_by_label("Blood group").select_option("O-")
    d.get_by_label("Weight (kg)").fill("60")
    d.get_by_label("Ongoing health condition").select_option("none")
    d.get_by_role("button", name="Use my current location").click()
    expect(d.locator(".picker-status")).to_contain_text("Location set")
    d.get_by_label("Password", exact=True).fill("strong-pass-123")
    d.get_by_label("Confirm password").fill("strong-pass-123")
    d.get_by_label(re.compile("I agree")).check()
    d.get_by_role("button", name="Create donor account").click()
    expect(d).to_have_url(re.compile("/donor/dashboard$"))
    expect(d.get_by_text("There are no open requests")).to_be_visible()

    # 2. A hospital registers and creates a request.
    hospital = session(geolocation=JOURNEY_HOSPITAL_POSITION)
    h = hospital.page
    h.goto("/hospital/register")
    h.get_by_label("Hospital name").fill("Sunrise Journey Hospital")
    h.get_by_label("Type").select_option("government")
    h.get_by_label("Email").fill("sunrise.journey@example.com")
    h.get_by_label("Phone number").fill("+91 22 5555 0199")
    h.get_by_label("Street address").fill("12 Link Road")
    h.get_by_label("City").fill("Mumbai")
    h.get_by_label("State").fill("Maharashtra")
    h.get_by_label("Pincode").fill("400069")
    h.get_by_role("button", name="Use my current location").click()
    expect(h.locator(".picker-status")).to_contain_text("Location set")
    h.get_by_label("Password", exact=True).fill("hospital-pass-123")
    h.get_by_label("Confirm password").fill("hospital-pass-123")
    h.get_by_label(re.compile("authorised")).check()
    h.get_by_role("button", name="Register hospital").click()
    expect(h).to_have_url(re.compile("/hospital/dashboard$"))

    h.get_by_role("link", name="New blood request").click()
    h.get_by_label("Patient name").fill("Journey Patient")
    h.get_by_label("Gender").select_option("Male")
    h.get_by_label("Blood group").select_option("A+")
    h.get_by_label("Units required").fill("1")
    h.get_by_label("Urgency").select_option("high")
    h.get_by_role("button", name="Create request").click()
    expect(h).to_have_url(re.compile(r"/hospital/requests/\d+$"))

    # 3. The hospital finds the donor on the map and alerts them.
    request_url = h.url
    summary = h.locator("[data-summary]")
    expect(summary).to_contain_text("1 compatible donor within 5 km")
    expect(h.locator("path.leaflet-interactive")).to_have_count(2)  # radius circle + donor dot
    h.get_by_role("button", name=re.compile("Alert 1 donor")).click()
    expect(h.locator(".toast")).to_contain_text("Demo mode")
    expect(h.locator("[data-notified-count]")).to_have_text("1")
    expect(summary).to_contain_text("0 not alerted yet")
    expect(h.get_by_role("button", name="No new donors to alert")).to_be_disabled()

    # 4. The donor sees the request, which hides patient details.
    d.reload()
    d.get_by_role("link", name="I can help").click()
    expect(d.get_by_role("heading", name="Sunrise Journey Hospital")).to_be_visible()
    expect(d.get_by_text("Journey Patient")).to_have_count(0)
    d.get_by_role("link", name="Check eligibility and pledge").click()

    # 5. Unanswered questions are caught in the browser.
    d.get_by_role("button", name="Confirm my pledge").click()
    expect(d.locator(".toast")).to_contain_text("11 remaining questions")
    expect(d).to_have_url(re.compile("/pledge$"))

    for key, value in SAFE.items():
        d.locator(f'input[name="{key}"][value="{value}"]').check()
    expect(d.locator("[data-progress-count]")).to_have_text("11")
    d.get_by_role("button", name="Confirm my pledge").click()
    expect(d).to_have_url(re.compile(r"/donor/pledges/\d+/directions$"))
    expect(d.get_by_role("status").first).to_contain_text("Your pledge is confirmed")

    # 6. Live directions: route, distance, then arrival.
    expect(d.locator("[data-arrival]")).to_be_hidden()
    d.get_by_role("button", name="Start").click()
    expect(d.locator('[data-stat="distance"]')).to_have_text(re.compile(r"^\d+(\.\d)? (km|m)$"))
    expect(d.locator("[data-route-source]")).to_contain_text("Straight-line estimate")
    expect(d.locator("path.leaflet-interactive")).to_have_count(1)  # the route line

    # 6b. The hospital's live ops console shows the donor's ETA, never their location.
    h.goto("/hospital/ops")
    expect(h.locator("[data-live]")).to_have_text("LIVE")
    expect(h.locator(".queue-item")).to_contain_text("A+")
    expect(h.locator(".eta-item", has_text="Priya Desai")).to_contain_text(re.compile(r"\d+ min"), timeout=15000)
    expect(h.locator('[data-summary-key="on_the_way"]')).to_have_text("1")

    d.get_by_role("button", name="Pause tracking").click()
    donor.context.set_geolocation(JOURNEY_HOSPITAL_POSITION)
    d.get_by_role("button", name="Start").click()
    expect(d.locator("[data-arrival]")).to_be_visible()
    expect(d.locator("[data-trip-status]")).to_have_text("You've arrived.")

    # 7. The hospital sees the donor has arrived and records the donation.
    with live_server["app"].app_context():
        for _ in range(50):
            db.session.expire_all()
            if db.session.query(Pledge).filter_by(status="arrived").count():
                break
            d.wait_for_timeout(100)
    h.goto(request_url)
    row = h.get_by_role("row", name=re.compile("Priya Desai"))
    expect(row).to_contain_text("Arrived")
    expect(row).to_contain_text("priya.journey@example.com")
    row.get_by_role("button", name="Donated").click()
    expect(h.get_by_role("status").first).to_contain_text("Donation recorded")
    expect(h.get_by_role("row", name=re.compile("Priya Desai"))).to_contain_text("Donated")

    # 8. The donor's impact page shows the donation and a badge.
    d.goto("/donor/impact")
    expect(d.locator('[data-kpi="donations"] .kpi-value')).to_have_text("1")
    expect(d.locator(".badge-card.is-earned")).to_contain_text("First Drop")
    expect(d.get_by_text("Certificate of appreciation").first).to_be_visible()

    assert donor.errors == [] and hospital.errors == []
    assert donor.foreign_requests == [] and hospital.foreign_requests == []


def test_ineligible_answers_block_the_pledge(session, live_server):
    request_id = seed(live_server, hospital_name="Eligibility Hospital", donor_email="ineligible@example.com")
    page = session().page
    ui_login(page, "donor", "ineligible@example.com", "donor-pass-123")
    page.goto(f"/donor/requests/{request_id}/pledge")
    for key, value in {**SAFE, "q1": "yes"}.items():
        page.locator(f'input[name="{key}"][value="{value}"]').check()
    page.get_by_role("button", name="Confirm my pledge").click()
    alert = page.get_by_role("alert")
    expect(alert).to_contain_text("you can't donate right now")
    expect(alert).to_contain_text("recent illness")
    expect(alert).to_be_focused()
    expect(page.locator('input[name="q1"][value="yes"]')).to_be_checked()
    with live_server["app"].app_context():
        assert db.session.get(BloodRequest, request_id).units_pledged == 0


def test_cancel_pledge_from_dashboard(session, live_server):
    request_id = seed(live_server, hospital_name="Cancel Test Hospital", donor_email="cancel@example.com")
    page = session().page
    ui_login(page, "donor", "cancel@example.com", "donor-pass-123")
    page.goto(f"/donor/requests/{request_id}/pledge")
    for key, value in SAFE.items():
        page.locator(f'input[name="{key}"][value="{value}"]').check()
    page.get_by_role("button", name="Confirm my pledge").click()
    page.wait_for_url(re.compile(r"/directions$"))
    page.goto("/donor/dashboard")
    expect(page.get_by_role("heading", name="Your pledges")).to_be_visible()
    page.get_by_role("button", name="Cancel pledge").click()  # confirm dialog is accepted
    expect(page.get_by_role("status").first).to_contain_text("Your pledge has been cancelled")
    with live_server["app"].app_context():
        assert db.session.get(BloodRequest, request_id).units_pledged == 0


def test_markup_in_data_is_never_executed(session, live_server):
    payload = '<img src=x onerror="window.__xss=1">Evil Hospital'
    seed(live_server, hospital_name=payload, donor_email="xss@example.com")
    page = session().page
    ui_login(page, "donor", "xss@example.com", "donor-pass-123")
    expect(page.get_by_text(payload)).to_be_visible()
    assert page.evaluate("() => window.__xss") is None


def test_csrf_protects_json_api_from_other_sites(session, live_server):
    seed(live_server, hospital_name="CSRF Hospital", donor_email="csrf@example.com")
    page = session().page
    ui_login(page, "hospital", "csrf@example.com.hospital@example.com", "hospital-pass-123")
    # WebKit refuses the cross-site request itself; other engines send it and the server rejects it.
    status = page.evaluate(
        """async () => {
             try {
               return (await fetch('/hospital/api/requests/1/notify', {
                 method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{"radius": 5}'
               })).status;
             } catch { return 'blocked by the browser'; }
           }"""
    )
    assert status in (400, "blocked by the browser")
