"""Server side of the interactive features: live donor feed, availability API and widget data."""

import json
import re

import pytest

from bloodconnect.extensions import db
from bloodconnect.models import Pledge
from tests.conftest import login_donor, login_hospital, reload


def attribute_json(body: str, name: str):
    match = re.search(rf"{name}='([^']*)'", body)
    assert match, f"{name} not found"
    return json.loads(match.group(1))


class TestDonorFeed:
    def test_requires_login(self, client):
        assert client.get("/donor/api/feed").status_code == 401

    def test_lists_compatible_requests_without_patient_details(self, client, make_hospital, make_request, make_donor):
        hospital = make_hospital(name="Feed Hospital", city="Mumbai")
        blood_request = make_request(
            hospital=hospital, blood_group="A+", units_required=3, urgency="critical", patient_name="Secret Patient"
        )
        make_request(hospital=make_hospital(), blood_group="O-")  # O+ can't give to O-
        login_donor(client, make_donor(blood_group="O+"))

        data = client.get("/donor/api/feed").get_json()
        assert data["available"] is True and data["radius_km"] == 50
        assert [r["id"] for r in data["requests"]] == [blood_request.id]
        item = data["requests"][0]
        assert item["hospital"] == "Feed Hospital" and item["city"] == "Mumbai"
        assert (item["blood_group"], item["urgency"], item["units_remaining"]) == ("A+", "critical", 3)
        assert item["url"] == f"/donor/requests/{blood_request.id}"
        assert item["posted_at"].endswith("Z") and 1.0 < item["distance_km"] < 1.3
        assert "Secret Patient" not in json.dumps(data)

    def test_unavailable_donor_gets_no_requests(self, client, make_request, make_donor):
        make_request(blood_group="O-")
        login_donor(client, make_donor(is_available=False))
        data = client.get("/donor/api/feed").get_json()
        assert data["available"] is False and data["requests"] == []

    def test_includes_active_pledges(self, client, make_request, make_donor):
        blood_request = make_request(blood_group="O-")
        donor = make_donor()
        pledge = Pledge(request_id=blood_request.id, donor_id=donor.id, status="arrived")
        db.session.add(pledge)
        db.session.commit()
        login_donor(client, donor)
        data = client.get("/donor/api/feed").get_json()
        assert data["pledges"] == [
            {"id": pledge.id, "status": "arrived", "request_id": blood_request.id, "request_status": "open"}
        ]
        assert data["requests"] == []  # already pledged requests aren't offered again


class TestAvailabilityApi:
    def test_toggles(self, client, make_donor):
        donor = make_donor()
        login_donor(client, donor)
        assert client.post("/donor/api/availability", json={"available": False}).get_json() == {"available": False}
        assert reload(donor).is_available is False
        assert client.post("/donor/api/availability", json={"available": True}).get_json() == {"available": True}
        assert reload(donor).is_available is True

    @pytest.mark.parametrize("body", [{}, {"available": "yes"}, {"available": 1}, {"available": None}])
    def test_rejects_non_boolean(self, client, make_donor, body):
        login_donor(client, make_donor())
        assert client.post("/donor/api/availability", json=body).status_code == 400

    def test_requires_login(self, client):
        assert client.post("/donor/api/availability", json={"available": True}).status_code == 401


class TestWidgetData:
    def test_home_explorer_and_quick_check(self, client):
        body = client.get("/").get_data(as_text=True)
        compat = attribute_json(body, "data-compat")
        assert compat["O-"]["give_to"] == ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"]
        assert compat["AB+"]["receive_from"] == ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"]
        assert compat["A-"] == {"give_to": ["A+", "A-", "AB+", "AB-"], "receive_from": ["A-", "O-"]}
        rules = attribute_json(body, "data-rules")
        assert rules == {
            "min_age": 18,
            "max_age": 65,
            "min_weight": 45,
            "interval_days": {"Male": 90, "Female": 120, "Other": 120},
        }
        # The page renders a sensible default before JavaScript runs.
        assert 'data-group="O-" aria-pressed="true"' in body
        assert "universal red cell donor" in body

    def test_how_it_works_has_explorer_and_table(self, client):
        body = client.get("/how-it-works").get_data(as_text=True)
        assert "data-compat-explorer" in body and "<table>" in body

    def test_for_donors_has_quick_check(self, client):
        assert "data-quick-check" in client.get("/for-donors").get_data(as_text=True)

    def test_donor_registration_preview_data(self, client):
        give_to = attribute_json(client.get("/donor/register").get_data(as_text=True), "data-give-to")
        assert give_to["AB+"] == ["AB+"] and len(give_to["O-"]) == 8

    def test_request_form_preview_data(self, client, make_hospital):
        login_hospital(client, make_hospital())
        compat = attribute_json(client.get("/hospital/requests/new").get_data(as_text=True), "data-compat")
        assert compat["A+"] == ["A+", "A-", "O+", "O-"] and compat["O-"] == ["O-"]

    @pytest.mark.parametrize(
        "path", ["/how-it-works", "/for-donors", "/for-patients", "/faq", "/about", "/terms", "/privacy"]
    )
    def test_on_this_page_links_point_to_real_sections(self, client, path):
        body = client.get(path).get_data(as_text=True)
        toc = re.search(r"<nav class=\"toc\".*?</nav>", body, re.DOTALL).group(0)
        anchors = re.findall(r'href="#([^"]+)"', toc)
        assert anchors
        for anchor in anchors:
            assert f'id="{anchor}"' in body, f"#{anchor} missing on {path}"

    def test_faq_has_search(self, client):
        body = client.get("/faq").get_data(as_text=True)
        assert 'id="faq-search"' in body and '<label for="faq-search">' in body


class TestAppPages:
    def test_donor_dashboard_live_hooks(self, client, make_donor):
        login_donor(client, make_donor())
        body = client.get("/donor/dashboard").get_data(as_text=True)
        assert 'data-feed-url="/donor/api/feed"' in body
        assert 'role="switch" aria-checked="true"' in body
        assert 'data-api-url="/donor/api/availability"' in body

    def test_hospital_shell_has_palette_and_live_activity(self, client, make_hospital, make_request, make_donor):
        hospital = make_hospital()
        blood_request = make_request(hospital=hospital, blood_group="O-")
        donor = make_donor(name="Activity Donor")
        db.session.add(Pledge(request_id=blood_request.id, donor_id=donor.id))
        db.session.commit()
        login_hospital(client, hospital)
        body = client.get("/hospital/dashboard").get_data(as_text=True)
        commands = attribute_json(body, "data-commands")
        assert {"label": "Live ops console", "url": "/hospital/ops"}.items() <= next(
            c for c in commands if c["label"] == "Live ops console"
        ).items()
        assert any(c["url"] == "/hospital/requests/new" for c in commands)
        assert "data-palette" in body and 'aria-keyshortcuts="Control+K"' in body
        assert f"Activity Donor pledged for request #{blood_request.id}" in body

    def test_ops_page_has_shortcuts_and_tools(self, client, make_hospital):
        login_hospital(client, make_hospital())
        body = client.get("/hospital/ops").get_data(as_text=True)
        assert "data-shortcuts" in body and "data-sound" in body and "data-fullscreen" in body
