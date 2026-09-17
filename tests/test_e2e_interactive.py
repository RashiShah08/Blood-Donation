"""Browser tests for the interactive features: widgets, live updates, keyboard control and layout."""

import re
from datetime import date, timedelta

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

from bloodconnect.extensions import db  # noqa: E402
from bloodconnect.models import BloodRequest, Donor, Hospital, Pledge  # noqa: E402
from tests.test_e2e_browser import SAFE, live_server, session, ui_login  # noqa: E402, F401

pytestmark = pytest.mark.e2e

POLL_TIMEOUT = 25_000  # live pages poll every 10 to 15 seconds


def make_world(live_server, name, *, requests=(("O-", "high"),), donor_group="O-"):  # noqa: F811
    """Create a hospital, its requests and a nearby donor. Returns emails and ids."""
    with live_server["app"].app_context():
        hospital = Hospital(
            name=f"{name} Hospital",
            email=f"{name.lower()}@hospital.example",
            phone="+91 22 5555 0100",
            address="1 Test Road",
            city="Mumbai",
            state="MH",
            pincode="400069",
            hospital_type="private",
            latitude=19.1136,
            longitude=72.8697,
            is_verified=True,
        )
        hospital.set_password("hospital-pass-123")
        db.session.add(hospital)
        db.session.flush()
        request_ids = []
        for group, urgency in requests:
            blood_request = BloodRequest(
                hospital_id=hospital.id,
                patient_name=f"{name} Patient",
                patient_gender="Male",
                blood_group=group,
                units_required=2,
                urgency=urgency,
            )
            db.session.add(blood_request)
            db.session.flush()
            request_ids.append(blood_request.id)
        donor = Donor(
            name=f"{name} Donor",
            email=f"{name.lower()}@donor.example",
            date_of_birth=date.today() - timedelta(days=365 * 30),
            gender="Male",
            weight_kg=70,
            blood_group=donor_group,
            health_issues="none",
            latitude=19.1236,
            longitude=72.8697,
        )
        donor.set_password("donor-pass-123")
        db.session.add(donor)
        db.session.commit()
        return {
            "hospital_email": hospital.email,
            "donor_email": donor.email,
            "hospital_id": hospital.id,
            "donor_id": donor.id,
            "request_ids": request_ids,
        }


def test_compatibility_explorer(session):  # noqa: F811
    page = session().page
    page.goto("/")
    ab_positive = page.get_by_role("button", name="AB+", exact=True)
    ab_positive.click()
    expect(ab_positive).to_have_attribute("aria-pressed", "true")
    explorer = page.locator("[data-compat-explorer]")
    expect(explorer.locator("[data-explorer-summary]")).to_contain_text("universal red cell recipient")
    expect(explorer.locator('[data-flow="receive_from"] .chip-group.is-match')).to_have_count(8)
    expect(explorer.locator('[data-flow="give_to"] .chip-group.is-match')).to_have_count(1)

    page.keyboard.press("ArrowLeft")  # moves to B- in the 4 x 2 grid
    b_negative = page.get_by_role("button", name="B-", exact=True)
    expect(b_negative).to_be_focused()
    expect(b_negative).to_have_attribute("aria-pressed", "true")
    expect(explorer.locator('[data-flow="give_to"] .chip-group.is-match')).to_have_text(["B+", "B-", "AB+", "AB-"])


def test_quick_eligibility_check(session):  # noqa: F811
    page = session().page
    page.goto("/")
    verdict = page.locator("[data-verdict]")
    expect(verdict).to_have_attribute("data-state", "ok")

    page.get_by_label(re.compile(r"^Age")).fill("16")
    expect(verdict).to_have_attribute("data-state", "wait")
    expect(verdict).to_contain_text("Not just yet")
    expect(verdict).to_contain_text("at least 18")
    expect(page.locator('[data-output="age"]')).to_have_text("16")

    page.get_by_label(re.compile(r"^Age")).fill("30")
    page.get_by_label("Gender").select_option("Female")
    page.get_by_label("Last blood donation").select_option("60")
    expect(verdict).to_contain_text("120 days")

    page.get_by_label("Last blood donation").select_option("")
    expect(verdict).to_have_attribute("data-state", "ok")
    expect(verdict).to_contain_text("You could likely donate")


def test_registration_live_preview_and_password_strength(session):  # noqa: F811
    browser_session = session(geolocation={"latitude": 19.12, "longitude": 72.87})
    page = browser_session.page
    page.goto("/donor/register")
    card = page.locator("[data-donor-card]")
    progress = page.locator("[data-completeness-count]")
    expect(progress).to_have_text("0 of 6 done")

    page.get_by_label("Full name").fill("Meera Joshi")
    page.get_by_label("Email").fill("meera@example.com")
    expect(card.locator('[data-card="name"]')).to_have_text("Meera Joshi")
    expect(progress).to_have_text("1 of 6 done")

    page.get_by_label("Blood group").select_option("O-")
    expect(card.locator('[data-card="blood_group"]')).to_have_text("O-")
    expect(card.locator('[data-card="give_to"] .chip-group')).to_have_count(8)

    page.get_by_role("button", name="Use my current location").click()
    expect(card.locator('[data-card="location"]')).to_have_text("Home area set")

    password = page.get_by_label("Password", exact=True)
    password.fill("abc")
    expect(page.locator("[data-strength-text]")).to_have_text("5 more characters needed")
    password.fill("Str0ng!Passphrase")
    expect(page.locator("[data-strength-text]")).to_have_text("Strong password")
    assert browser_session.errors == []


def test_faq_search_and_table_of_contents(session):  # noqa: F811
    page = session().page
    page.goto("/faq")
    expect(page.locator('.toc a[href="#using"]')).to_be_visible()
    search = page.get_by_label("Search questions")
    search.fill("piercing")
    expect(page.locator(".faq details:visible")).to_have_count(1)
    expect(page.locator(".faq details:visible")).to_have_attribute("open", "")
    expect(page.get_by_role("heading", name="Requesting blood")).to_be_hidden()
    search.fill("zzzz")
    expect(page.get_by_text("No questions match your search.")).to_be_visible()
    search.fill("")
    expect(page.locator(".faq details:visible")).to_have_count(10)


def test_donor_feed_updates_live_and_availability_switch(session, live_server):  # noqa: F811
    world = make_world(live_server, "Feed")
    browser_session = session()
    page = browser_session.page
    ui_login(page, "donor", world["donor_email"], "donor-pass-123")
    expect(page.locator("[data-request-list] .request-card")).to_have_count(1)
    expect(page.locator("[data-live-feed][data-ready]")).to_have_count(1)
    expect(page.locator("[data-pledge-banner]")).to_be_hidden()  # regression: CSS display beat [hidden]

    # A hospital posts a new request while the donor watches the dashboard.
    with live_server["app"].app_context():
        db.session.add(
            BloodRequest(
                hospital_id=world["hospital_id"],
                patient_name="Late Patient",
                patient_gender="Female",
                blood_group="AB+",
                units_required=1,
                urgency="critical",
            )
        )
        db.session.commit()
    expect(page.locator("[data-request-list] .request-card")).to_have_count(2, timeout=POLL_TIMEOUT)
    expect(page.locator(".toast").first).to_contain_text("New request near you")
    expect(page.locator("[data-request-list] .request-card").first).to_contain_text("Critical urgency")

    switch = page.get_by_role("switch")
    switch.click()
    expect(switch).to_have_attribute("aria-checked", "false")
    expect(page.get_by_text("You're marked as not available")).to_be_visible()
    expect(page.locator("[data-request-list] .request-card")).to_have_count(0)
    with live_server["app"].app_context():
        assert db.session.get(Donor, world["donor_id"]).is_available is False
    switch.click()
    expect(switch).to_have_attribute("aria-checked", "true")
    expect(page.locator("[data-request-list] .request-card")).to_have_count(2)
    assert page.url.endswith("/donor/dashboard")  # no page reloads
    assert browser_session.errors == []


def test_pledge_questionnaire_by_keyboard(session, live_server):  # noqa: F811
    world = make_world(live_server, "Keys")
    page = session().page
    ui_login(page, "donor", world["donor_email"], "donor-pass-123")
    page.goto(f"/donor/requests/{world['request_ids'][0]}/pledge")
    expect(page.locator(".questions > li").first).to_have_class(re.compile("is-current"))
    for _ in range(10):
        page.keyboard.press("n")
    page.keyboard.press("y")
    expect(page.locator("[data-progress-count]")).to_have_text("11")
    expect(page.locator("[data-ring-value]")).to_have_text("11/11")
    expect(page.locator('input[name="q11"][value="yes"]')).to_be_checked()
    page.get_by_role("button", name="Confirm my pledge").click()
    expect(page).to_have_url(re.compile(r"/donor/pledges/\d+/directions$"))


def test_command_palette(session, live_server):  # noqa: F811
    world = make_world(live_server, "Palette")
    page = session().page
    ui_login(page, "hospital", world["hospital_email"], "hospital-pass-123")
    expect(page).to_have_url(re.compile("/hospital/dashboard$"))
    expect(page.locator("dialog[data-palette][data-ready]")).to_have_count(1)  # script has loaded

    page.keyboard.press("Control+k")
    dialog = page.locator("dialog[data-palette]")
    expect(dialog).to_be_visible()
    expect(page.get_by_role("combobox")).to_be_focused()
    expect(dialog.get_by_role("option", name=re.compile(f"Request #{world['request_ids'][0]}"))).to_be_visible()

    page.keyboard.type("live ops")
    expect(dialog.get_by_role("option")).to_have_count(1)
    page.keyboard.press("Enter")
    expect(page).to_have_url(re.compile("/hospital/ops$"))

    page.goto("/hospital/dashboard")
    page.get_by_role("button", name=re.compile("Search or jump")).click()
    page.keyboard.type("nothing-matches-this")
    expect(dialog).to_contain_text("No results")
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()


def test_request_form_live_preview(session, live_server):  # noqa: F811
    world = make_world(live_server, "Preview")
    page = session().page
    ui_login(page, "hospital", world["hospital_email"], "hospital-pass-123")
    page.goto("/hospital/requests/new")
    preview = page.locator("[data-request-preview]")
    page.get_by_label("Blood group").select_option("B-")
    expect(preview.locator('[data-preview="compat"] .chip-group.is-match')).to_have_text(["B-", "O-"])
    expect(preview.locator('[data-preview="blood_group"]')).to_have_text("B-")
    page.get_by_label("Units required").fill("3")
    expect(preview.locator('[data-preview="units"]')).to_have_text("3 units")
    page.get_by_label("Urgency").select_option("critical")
    expect(preview.locator('[data-preview="urgency"]')).to_have_text("Critical urgency")


def test_hospital_dashboard_shows_new_pledges_live(session, live_server):  # noqa: F811
    world = make_world(live_server, "Pulse")
    page = session().page
    ui_login(page, "hospital", world["hospital_email"], "hospital-pass-123")
    # Wait for the first live refresh, so the new pledge arrives after the baseline snapshot.
    expect(page.locator("[data-hospital-live][data-live-ready]")).to_have_count(1)
    with live_server["app"].app_context():
        db.session.add(Pledge(request_id=world["request_ids"][0], donor_id=world["donor_id"]))
        db.session.commit()
    expect(page.locator(".toast").first).to_contain_text("Pulse Donor pledged", timeout=POLL_TIMEOUT)
    expect(page.locator("[data-activity-list] li").first).to_contain_text("Pulse Donor pledged")
    expect(page.locator('[data-kpi="active_pledges"] .kpi-value')).to_have_text("1")


def test_ops_console_keyboard_and_tools(session, live_server):  # noqa: F811
    world = make_world(live_server, "Console", requests=(("O-", "critical"), ("A+", "low")))
    page = session().page
    ui_login(page, "hospital", world["hospital_email"], "hospital-pass-123")
    page.goto("/hospital/ops")
    expect(page.locator("#ops[data-ready]")).to_have_count(1)
    items = page.locator(".queue-item")
    expect(items).to_have_count(2)
    expect(items.first).to_have_attribute("aria-pressed", "true")  # most urgent is selected first
    page.locator("body").click(position={"x": 5, "y": 300})
    page.keyboard.press("j")
    expect(items.nth(1)).to_have_attribute("aria-pressed", "true")
    expect(page).to_have_url(re.compile(f"#request-{world['request_ids'][1]}$"))
    page.keyboard.press("k")
    expect(items.first).to_have_attribute("aria-pressed", "true")

    page.keyboard.press("?")
    expect(page.locator("dialog[data-shortcuts]")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.locator("dialog[data-shortcuts]")).to_be_hidden()

    sound = page.locator("[data-sound]")
    sound.click()
    expect(sound).to_have_attribute("aria-pressed", "true")


@pytest.mark.parametrize("path", ["/", "/faq", "/donor/register"])
def test_wide_screens_use_the_space(session, path):  # noqa: F811
    page = session(viewport={"width": 1680, "height": 1000}).page
    page.goto(path)
    width = page.evaluate("() => document.querySelector('main .container').getBoundingClientRect().width")
    assert width > 1350, f"main container only {width}px wide on a 1680px screen"
    if path == "/donor/register":
        form = page.locator("#register-form").bounding_box()
        aside = page.locator("aside.sticky-aside").bounding_box()
        assert aside["x"] > form["x"] + form["width"] - 1  # side by side, not stacked
    if path == "/faq":
        expect(page.locator(".toc")).to_be_visible()


def test_phone_layout_stacks_without_horizontal_scroll(session):  # noqa: F811
    page = session(viewport={"width": 390, "height": 844}).page
    for path in ["/", "/donor/register", "/faq"]:
        page.goto(path)
        assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth"), path


# For each row of a two-column form grid, return the controls whose top edge or height differs.
MISALIGNED_FORM_ROWS = """() => {
  const problems = [];
  document.querySelectorAll('.grid').forEach((grid) => {
    const rows = {};
    [...grid.children].filter((el) => el.matches('.field')).forEach((field) => {
      const control = field.querySelector('input, select, textarea');
      if (!control) return;
      const box = control.getBoundingClientRect();
      const rowTop = Math.round(field.getBoundingClientRect().top);
      (rows[rowTop] ||= []).push({ name: control.name, top: Math.round(box.top), height: Math.round(box.height) });
    });
    Object.values(rows).forEach((row) => {
      if (row.length < 2) return;
      if (new Set(row.map((c) => c.top)).size > 1 || new Set(row.map((c) => c.height)).size > 1) problems.push(row);
    });
  });
  return problems;
}"""


def test_form_fields_line_up_in_every_row(session, live_server):  # noqa: F811
    world = make_world(live_server, "Aligned")
    page = session(viewport={"width": 1440, "height": 900}).page
    for path in ("/donor/register", "/hospital/register"):
        page.goto(path)
        assert page.evaluate(MISALIGNED_FORM_ROWS) == [], path

    ui_login(page, "donor", world["donor_email"], "donor-pass-123")
    page.goto("/donor/profile")
    assert page.evaluate(MISALIGNED_FORM_ROWS) == []

    hospital = session(viewport={"width": 1440, "height": 900}).page
    ui_login(hospital, "hospital", world["hospital_email"], "hospital-pass-123")
    hospital.goto("/hospital/requests/new")
    assert hospital.evaluate(MISALIGNED_FORM_ROWS) == []


def test_hospital_pages_never_scroll_sideways_on_phones(session, live_server):  # noqa: F811
    world = make_world(live_server, "Narrow")
    page = session(viewport={"width": 390, "height": 844}).page
    ui_login(page, "hospital", world["hospital_email"], "hospital-pass-123")
    for path in ("/hospital/dashboard", f"/hospital/requests/{world['request_ids'][0]}"):
        page.goto(path)
        page.wait_for_load_state("networkidle")
        assert page.evaluate("document.documentElement.scrollWidth") <= 390, path
