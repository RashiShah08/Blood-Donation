"""Integration tests for everything a hospital does."""

import re
from datetime import date, timedelta

import pytest

from bloodconnect.extensions import db
from bloodconnect.models import BloodRequest, Notification, Pledge, utcnow
from tests.conftest import (
    FAR_LOCATION,
    SAFE_ANSWERS,
    RecordingMailer,
    login_donor,
    login_hospital,
    notification_count,
    pledge_for,
    reload,
)


@pytest.fixture
def hospital(make_hospital):
    return make_hospital(name="Lakeview Hospital")


@pytest.fixture
def logged_in(client, hospital):
    login_hospital(client, hospital)
    return client


REQUEST_FORM = {
    "patient_name": "Kiran Joshi",
    "patient_gender": "Male",
    "patient_weight_kg": "72",
    "blood_group": "B+",
    "units_required": "3",
    "urgency": "critical",
    "clinical_notes": "Post-operative bleeding",
}


class TestRequests:
    def test_create_request(self, logged_in, hospital):
        response = logged_in.post("/hospital/requests/new", data=REQUEST_FORM)
        blood_request = db.session.query(BloodRequest).one()
        assert response.headers["Location"].endswith(f"/hospital/requests/{blood_request.id}")
        assert (blood_request.hospital_id, blood_request.blood_group, blood_request.units_required) == (
            hospital.id,
            "B+",
            3,
        )
        assert blood_request.status == "open" and blood_request.units_pledged == 0

        body = logged_in.get(f"/hospital/requests/{blood_request.id}").get_data(as_text=True)
        assert "Kiran Joshi" in body and "Post-operative bleeding" in body
        assert "Compatible: B+, B-, O+, O-" in body

    @pytest.mark.parametrize(
        "overrides, message",
        [
            ({"units_required": "0"}, "Units required must be between 1 and 50."),
            ({"units_required": "51"}, "Units required must be between 1 and 50."),
            ({"units_required": "1.5"}, "Units required must be a whole number."),
            ({"blood_group": "X"}, "Select a valid blood group."),
            ({"urgency": "whenever"}, "Select a valid urgency."),
            ({"patient_name": ""}, "Patient name is required."),
            ({"clinical_notes": "x" * 501}, "Notes must be at most 500 characters."),
        ],
    )
    def test_validation(self, logged_in, overrides, message):
        response = logged_in.post("/hospital/requests/new", data={**REQUEST_FORM, **overrides})
        assert response.status_code == 422
        assert message in response.get_data(as_text=True)
        assert db.session.query(BloodRequest).count() == 0

    def test_close_as_fulfilled(self, logged_in, hospital, make_request):
        blood_request = make_request(hospital=hospital)
        logged_in.post(f"/hospital/requests/{blood_request.id}/close", data={"outcome": "fulfilled"})
        closed = reload(blood_request)
        assert closed.status == "fulfilled" and closed.closed_at is not None
        again = logged_in.post(
            f"/hospital/requests/{blood_request.id}/close", data={"outcome": "cancelled"}, follow_redirects=True
        )
        assert "already closed" in again.get_data(as_text=True)

    def test_cancel_releases_active_pledges(self, logged_in, hospital, make_request, make_donor):
        blood_request = make_request(hospital=hospital, blood_group="O-", units_required=2, units_pledged=1)
        donor = make_donor()
        db.session.add(Pledge(request_id=blood_request.id, donor_id=donor.id))
        db.session.commit()
        logged_in.post(f"/hospital/requests/{blood_request.id}/close", data={"outcome": "cancelled"})
        assert reload(blood_request).status == "cancelled"
        assert pledge_for(donor, blood_request).status == "cancelled"

    def test_invalid_close_outcome(self, logged_in, hospital, make_request):
        blood_request = make_request(hospital=hospital)
        logged_in.post(f"/hospital/requests/{blood_request.id}/close", data={"outcome": "deleted"})
        assert reload(blood_request).status == "open"


class TestDonorSearch:
    @pytest.fixture
    def scenario(self, hospital, make_request, make_donor):
        blood_request = make_request(hospital=hospital, blood_group="A+")
        donors = {
            "o_neg_near": make_donor(blood_group="O-", latitude=19.1236, longitude=72.8697),  # ~1.1 km
            "a_pos_mid": make_donor(blood_group="A+", latitude=19.1436, longitude=72.8697),  # ~3.3 km
            "b_pos_near": make_donor(blood_group="B+", latitude=19.1186, longitude=72.8697),  # incompatible
            "unavailable": make_donor(blood_group="O+", is_available=False),
            "too_recent": make_donor(blood_group="O+", last_donation_date=date.today() - timedelta(days=5)),
            "far": make_donor(blood_group="O-", latitude=FAR_LOCATION[0], longitude=FAR_LOCATION[1]),
            "a_neg_8km": make_donor(blood_group="A-", latitude=19.1856, longitude=72.8697),  # ~8 km
        }
        return blood_request, donors

    def test_filters_and_sorts(self, logged_in, scenario):
        blood_request, _ = scenario
        data = logged_in.get(f"/hospital/api/requests/{blood_request.id}/donors?radius=5").get_json()
        assert data["radius_km"] == 5
        assert [d["blood_group"] for d in data["donors"]] == ["O-", "A+"]
        assert data["donors"][0]["distance_km"] < data["donors"][1]["distance_km"]
        assert data["notifiable"] == 2
        assert data["request"] == {"status": "open", "blood_group": "A+", "units_remaining": 2}

        wider = logged_in.get(f"/hospital/api/requests/{blood_request.id}/donors?radius=10").get_json()
        assert [d["blood_group"] for d in wider["donors"]] == ["O-", "A+", "A-"]

    def test_expand_widens_until_donors_found(self, logged_in, hospital, make_request, make_donor):
        blood_request = make_request(hospital=hospital, blood_group="A+")
        make_donor(blood_group="A-", latitude=19.1856, longitude=72.8697)  # ~8 km
        data = logged_in.get(f"/hospital/api/requests/{blood_request.id}/donors?radius=2&expand=1").get_json()
        assert data["radius_km"] == 10 and len(data["donors"]) == 1

    def test_expand_with_nobody_stops_at_widest_radius(self, logged_in, hospital, make_request):
        """Regression: the old search looped forever when nobody was within range."""
        blood_request = make_request(hospital=hospital)
        data = logged_in.get(f"/hospital/api/requests/{blood_request.id}/donors?radius=2&expand=1").get_json()
        assert data["radius_km"] == 25 and data["donors"] == []

    @pytest.mark.parametrize("radius", ["", "3", "abc", "100"])
    def test_rejects_unknown_radius(self, logged_in, hospital, make_request, radius):
        blood_request = make_request(hospital=hospital)
        assert logged_in.get(f"/hospital/api/requests/{blood_request.id}/donors?radius={radius}").status_code == 400

    def test_flags_notified_and_pledged_donors(self, logged_in, scenario):
        blood_request, donors = scenario
        db.session.add(Notification(request_id=blood_request.id, donor_id=donors["o_neg_near"].id))
        db.session.add(Pledge(request_id=blood_request.id, donor_id=donors["a_pos_mid"].id))
        db.session.commit()
        data = logged_in.get(f"/hospital/api/requests/{blood_request.id}/donors?radius=5").get_json()
        assert [(d["notified"], d["pledged"]) for d in data["donors"]] == [(True, False), (False, True)]
        assert data["notifiable"] == 0


class TestNotifications:
    def test_alerts_matching_donors_once(self, app, logged_in, hospital, make_request, make_donor, mailer):
        blood_request = make_request(hospital=hospital, blood_group="AB+", patient_name="Private Person")
        near = make_donor(blood_group="B-", email="near@example.com")
        make_donor(blood_group="A+", latitude=FAR_LOCATION[0], longitude=FAR_LOCATION[1])

        first = logged_in.post(f"/hospital/api/requests/{blood_request.id}/notify", json={"radius": 5})
        assert first.status_code == 200
        assert first.get_json() == {"success": True, "sent": 1, "failed": 0, "skipped": 0, "simulated": False}
        assert [email.to for email in mailer.outbox] == ["near@example.com"]
        email = mailer.outbox[0]
        assert "AB+ blood needed at Lakeview Hospital" in email.subject
        assert f"http://localhost/donor/requests/{blood_request.id}" in email.body
        assert "Private Person" not in email.body
        assert notification_count(blood_request) == 1

        second = logged_in.post(f"/hospital/api/requests/{blood_request.id}/notify", json={"radius": 5}).get_json()
        assert second["sent"] == 0 and second["skipped"] == 1 and "already been alerted" in second["message"]
        assert len(mailer.outbox) == 1
        assert near.id  # silence unused warning

    def test_public_base_url_is_used_in_links(self, app, logged_in, hospital, make_request, make_donor, mailer):
        app.config["PUBLIC_BASE_URL"] = "https://bloodconnect.example.org"
        blood_request = make_request(hospital=hospital, blood_group="O-")
        make_donor(blood_group="O-")
        logged_in.post(f"/hospital/api/requests/{blood_request.id}/notify", json={"radius": 5})
        assert f"https://bloodconnect.example.org/donor/requests/{blood_request.id}" in mailer.outbox[0].body

    def test_all_failures_report_error(self, app, logged_in, hospital, make_request, make_donor):
        """Regression: the old /send endpoint reported success even when every email failed."""
        blood_request = make_request(hospital=hospital, blood_group="O-")
        donor = make_donor(blood_group="O-")
        app.extensions["mailer"] = RecordingMailer(app.config, fail_for={donor.email})
        response = logged_in.post(f"/hospital/api/requests/{blood_request.id}/notify", json={"radius": 5})
        assert response.status_code == 502
        assert response.get_json()["success"] is False
        assert notification_count(blood_request) == 0  # failed donors can be retried

    def test_partial_failure_is_not_success(self, app, logged_in, hospital, make_request, make_donor):
        blood_request = make_request(hospital=hospital, blood_group="O-")
        failing = make_donor(blood_group="O-")
        make_donor(blood_group="O-")
        app.extensions["mailer"] = RecordingMailer(app.config, fail_for={failing.email})
        data = logged_in.post(f"/hospital/api/requests/{blood_request.id}/notify", json={"radius": 5}).get_json()
        assert (data["success"], data["sent"], data["failed"]) == (False, 1, 1)

    def test_demo_mode_is_reported(self, logged_in, hospital, make_request, make_donor):
        blood_request = make_request(hospital=hospital, blood_group="O-")
        make_donor(blood_group="O-")
        data = logged_in.post(f"/hospital/api/requests/{blood_request.id}/notify", json={"radius": 5}).get_json()
        assert data["simulated"] is True and data["sent"] == 1

    def test_closed_request_cannot_notify(self, logged_in, hospital, make_request, mailer):
        blood_request = make_request(hospital=hospital, status="fulfilled")
        assert (
            logged_in.post(f"/hospital/api/requests/{blood_request.id}/notify", json={"radius": 5}).status_code == 409
        )

    def test_unverified_hospital_cannot_notify_when_required(self, app, client, make_hospital, make_request, mailer):
        app.config["REQUIRE_HOSPITAL_VERIFICATION"] = True
        hospital = make_hospital(is_verified=False)
        blood_request = make_request(hospital=hospital)
        login_hospital(client, hospital)
        response = client.post(f"/hospital/api/requests/{blood_request.id}/notify", json={"radius": 5})
        assert response.status_code == 403
        assert "verified" in client.get("/hospital/dashboard").get_data(as_text=True)

    def test_invalid_radius(self, logged_in, hospital, make_request, mailer):
        blood_request = make_request(hospital=hospital)
        assert (
            logged_in.post(f"/hospital/api/requests/{blood_request.id}/notify", json={"radius": 7}).status_code == 400
        )


class TestPledgeOutcomes:
    @pytest.fixture
    def pledge(self, client, hospital, make_request, make_donor):
        blood_request = make_request(hospital=hospital, blood_group="O-", units_required=2)
        donor = make_donor(name="Pledged Donor", phone="+91 91234 56789")
        login_donor(client, donor)
        client.post(f"/donor/requests/{blood_request.id}/pledge", data=SAFE_ANSWERS)
        client.post("/logout")
        login_hospital(client, hospital)
        return pledge_for(donor, blood_request)

    def test_hospital_sees_pledged_donor_contact(self, client, pledge):
        body = client.get(f"/hospital/requests/{pledge.request_id}").get_data(as_text=True)
        assert "Pledged Donor" in body and "+91 91234 56789" in body and pledge.donor.email in body

    def test_mark_donated_updates_donor(self, client, pledge):
        client.post(f"/hospital/pledges/{pledge.id}/outcome", data={"outcome": "donated"})
        updated = reload(pledge)
        assert updated.status == "donated"
        assert updated.donor.last_donation_date == date.today()
        assert updated.request.units_pledged == 1  # donated units stay counted

    def test_mark_no_show_releases_unit(self, client, pledge):
        client.post(f"/hospital/pledges/{pledge.id}/outcome", data={"outcome": "no_show"})
        assert reload(pledge).status == "no_show"
        assert reload(pledge).request.units_pledged == 0

    def test_outcome_can_only_be_recorded_once(self, client, pledge):
        client.post(f"/hospital/pledges/{pledge.id}/outcome", data={"outcome": "no_show"})
        response = client.post(
            f"/hospital/pledges/{pledge.id}/outcome", data={"outcome": "donated"}, follow_redirects=True
        )
        assert "already been closed" in response.get_data(as_text=True)
        assert reload(pledge).status == "no_show"

    def test_unknown_outcome(self, client, pledge):
        client.post(f"/hospital/pledges/{pledge.id}/outcome", data={"outcome": "paid"})
        assert reload(pledge).status == "pledged"

    def test_donated_donor_cannot_pledge_again_until_interval_passes(self, client, hospital, pledge, make_request):
        client.post(f"/hospital/pledges/{pledge.id}/outcome", data={"outcome": "donated"})
        client.post("/logout")
        another = make_request(hospital=hospital, blood_group="O-")
        login_donor(client, pledge.donor)
        response = client.post(f"/donor/requests/{another.id}/pledge", data=SAFE_ANSWERS)
        assert response.status_code == 422 and "too recent" in response.get_data(as_text=True)


class TestDashboard:
    def test_coverage_and_two_week_pledge_trend(self, logged_in, hospital, make_request, make_donor):
        needed = make_request(hospital=hospital, units_required=4, units_pledged=1, blood_group="A+")
        make_request(hospital=hospital, units_required=1)
        db.session.add(Pledge(request_id=needed.id, donor_id=make_donor().id, created_at=utcnow() - timedelta(days=2)))
        db.session.add(Pledge(request_id=needed.id, donor_id=make_donor().id, created_at=utcnow() - timedelta(days=20)))
        db.session.commit()

        body = logged_in.get("/hospital/dashboard").get_data(as_text=True)
        assert "<b>20%</b>" in body and "1 of 5 units pledged" in body
        assert 'aria-label="1 of 4 units pledged"' in body
        assert body.count('class="trend-bar"') == 14
        assert "1 total" in body  # the 20-day-old pledge is outside the window

    def test_statistics(self, logged_in, hospital, make_request, make_donor):
        open_one = make_request(hospital=hospital, units_required=3, units_pledged=1, blood_group="O-")
        make_request(hospital=hospital, units_required=2)
        make_request(hospital=hospital, status="fulfilled")
        make_request(hospital=hospital, status="cancelled")

        donor = make_donor()
        sent_at = utcnow() - timedelta(minutes=20)
        db.session.add(Notification(request_id=open_one.id, donor_id=donor.id, sent_at=sent_at))
        db.session.add(Pledge(request_id=open_one.id, donor_id=donor.id, created_at=sent_at + timedelta(minutes=10)))
        db.session.commit()

        body = logged_in.get("/hospital/dashboard").get_data(as_text=True)

        def kpi(key):
            match = re.search(rf'data-kpi="{key}".*?class="kpi-value"[^>]*>([^<]*)<', body, re.DOTALL)
            return match.group(1).strip()

        assert kpi("open_requests") == "2"
        assert kpi("units_needed") == "4"  # (3-1) + 2
        assert kpi("active_pledges") == "1"  # donors on the way
        assert kpi("median_response") == "10 min"
        assert "10 min" in body
        assert "50% of closed requests fulfilled" in body

    def test_empty_dashboard(self, logged_in):
        body = logged_in.get("/hospital/dashboard").get_data(as_text=True)
        assert "You have no open requests." in body
        assert "—" in body  # no response time yet
        assert "15 minutes" not in body
