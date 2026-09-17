"""Integration tests for everything a donor does."""

from datetime import date, timedelta

import pytest

from bloodconnect.extensions import db
from bloodconnect.models import Donor, Pledge
from tests.conftest import (
    DONOR_PASSWORD,
    FAR_LOCATION,
    SAFE_ANSWERS,
    login_donor,
    login_hospital,
    pledge_for,
    reload,
)


@pytest.fixture
def donor(make_donor):
    return make_donor(blood_group="O-", name="Neha Kulkarni")


@pytest.fixture
def logged_in(client, donor):
    login_donor(client, donor)
    return client


class TestDashboard:
    def test_shows_compatible_requests_only(self, logged_in, make_hospital, make_request, make_donor):
        compatible = make_request(hospital=make_hospital(name="Compatible Care"), blood_group="A+")
        body = logged_in.get("/donor/dashboard").get_data(as_text=True)
        assert "Compatible Care" in body and f"/donor/requests/{compatible.id}" in body

        client = logged_in
        client.post("/logout")
        a_positive = make_donor(blood_group="A+")
        login_donor(client, a_positive)
        make_request(hospital=make_hospital(name="Needs O Negative"), blood_group="O-")
        body = client.get("/donor/dashboard").get_data(as_text=True)
        assert "Needs O Negative" not in body
        assert "Compatible Care" in body  # A+ can give to A+

    def test_hides_far_full_closed_and_already_pledged_requests(self, logged_in, donor, make_hospital, make_request):
        make_request(
            hospital=make_hospital(name="Far Away Clinic", latitude=FAR_LOCATION[0], longitude=FAR_LOCATION[1])
        )
        make_request(hospital=make_hospital(name="Fully Pledged"), units_required=1, units_pledged=1)
        make_request(hospital=make_hospital(name="Closed Request"), status="fulfilled")
        pledged = make_request(hospital=make_hospital(name="My Pledge Hospital"))
        db.session.add(Pledge(request_id=pledged.id, donor_id=donor.id))
        db.session.commit()

        body = logged_in.get("/donor/dashboard").get_data(as_text=True)
        assert "Far Away Clinic" not in body
        assert "Fully Pledged" not in body
        assert "Closed Request" not in body
        assert "Your pledges" in body and "My Pledge Hospital" in body
        assert body.count("My Pledge Hospital") == 1  # only in the pledges section

    def test_readiness_ring_counts_down_to_next_eligible_date(self, client, make_donor):
        donor = make_donor(gender="Male", last_donation_date=date.today() - timedelta(days=30))
        login_donor(client, donor)
        body = client.get("/donor/dashboard").get_data(as_text=True)
        assert "60 days until you can donate again" in body
        assert "--p: 33" in body  # 30 of 90 days

    def test_readiness_ring_is_full_without_a_recent_donation(self, logged_in):
        body = logged_in.get("/donor/dashboard").get_data(as_text=True)
        assert 'aria-label="Ready to donate"' in body
        assert "--p: 100" in body

    def test_requests_are_sorted_by_urgency(self, logged_in, make_hospital, make_request):
        make_request(hospital=make_hospital(name="Low Priority"), urgency="low")
        make_request(hospital=make_hospital(name="Critical Case"), urgency="critical")
        body = logged_in.get("/donor/dashboard").get_data(as_text=True)
        assert body.index("Critical Case") < body.index("Low Priority")

    def test_empty_state(self, logged_in):
        assert "There are no open requests" in logged_in.get("/donor/dashboard").get_data(as_text=True)

    def test_availability_toggle(self, logged_in, donor, make_request):
        make_request()
        logged_in.post("/donor/availability", data={"available": "no"})
        assert reload(donor).is_available is False
        body = logged_in.get("/donor/dashboard").get_data(as_text=True)
        assert "You're marked as not available" in body
        logged_in.post("/donor/availability", data={"available": "yes"})
        assert reload(donor).is_available is True

    def test_shows_eligibility_problems(self, client, make_donor):
        donor = make_donor(last_donation_date=date.today() - timedelta(days=10))
        login_donor(client, donor)
        assert "Your last donation was too recent" in client.get("/donor/dashboard").get_data(as_text=True)


class TestPledging:
    def test_request_detail_and_questionnaire(self, logged_in, make_request):
        blood_request = make_request()
        detail = logged_in.get(f"/donor/requests/{blood_request.id}").get_data(as_text=True)
        assert "Check eligibility and pledge" in detail
        form = logged_in.get(f"/donor/requests/{blood_request.id}/pledge").get_data(as_text=True)
        assert form.count('type="radio"') == 22
        assert "Your answers aren't stored" in form

    def test_successful_pledge(self, logged_in, donor, make_request):
        blood_request = make_request(units_required=2)
        response = logged_in.post(f"/donor/requests/{blood_request.id}/pledge", data=SAFE_ANSWERS)
        pledge = pledge_for(donor, blood_request)
        assert response.status_code == 302
        assert response.headers["Location"].endswith(f"/donor/pledges/{pledge.id}/directions")
        assert pledge.status == "pledged"
        assert reload(blood_request).units_pledged == 1

    @pytest.mark.parametrize(
        "answers, message",
        [
            ({**SAFE_ANSWERS, "q4": "yes"}, "Malaria in the past 3 months."),
            ({**SAFE_ANSWERS, "q11": "no"}, "minimum gap between donations"),
            ({k: v for k, v in SAFE_ANSWERS.items() if k != "q7"}, "Please answer every question."),
        ],
    )
    def test_ineligible_answers_do_not_pledge(self, logged_in, donor, make_request, answers, message):
        blood_request = make_request()
        response = logged_in.post(f"/donor/requests/{blood_request.id}/pledge", data=answers)
        assert response.status_code == 422
        assert message in response.get_data(as_text=True)
        assert pledge_for(donor, blood_request) is None
        assert reload(blood_request).units_pledged == 0

    def test_answers_are_kept_after_an_error(self, logged_in, make_request):
        blood_request = make_request()
        body = logged_in.post(
            f"/donor/requests/{blood_request.id}/pledge", data={**SAFE_ANSWERS, "q2": "yes"}
        ).get_data(as_text=True)
        assert 'name="q2" value="yes" required checked' in body

    @pytest.mark.parametrize(
        "donor_overrides, message",
        [
            ({"last_donation_date": date.today() - timedelta(days=30)}, "too recent"),
            ({"weight_kg": 40}, "at least 45 kg"),
            ({"date_of_birth": date.today() - timedelta(days=365 * 70)}, "18 to 65"),
            ({"is_available": False}, "Mark yourself as available"),
        ],
    )
    def test_profile_rules_are_enforced_on_the_server(self, client, make_donor, make_request, donor_overrides, message):
        donor = make_donor(**donor_overrides)
        blood_request = make_request()
        login_donor(client, donor)
        response = client.post(f"/donor/requests/{blood_request.id}/pledge", data=SAFE_ANSWERS)
        assert response.status_code == 422
        assert message in response.get_data(as_text=True)

    def test_cannot_pledge_twice(self, logged_in, donor, make_request):
        blood_request = make_request(units_required=3)
        logged_in.post(f"/donor/requests/{blood_request.id}/pledge", data=SAFE_ANSWERS)
        again = logged_in.post(f"/donor/requests/{blood_request.id}/pledge", data=SAFE_ANSWERS)
        assert again.status_code == 422 and "already pledged" in again.get_data(as_text=True)
        assert reload(blood_request).units_pledged == 1
        # Visiting the questionnaire again goes straight to directions.
        assert "/directions" in logged_in.get(f"/donor/requests/{blood_request.id}/pledge").headers["Location"]

    def test_units_are_never_over_pledged(self, client, make_donor, make_request):
        blood_request = make_request(units_required=1, blood_group="O-")
        first, second = make_donor(), make_donor()
        login_donor(client, first)
        assert client.post(f"/donor/requests/{blood_request.id}/pledge", data=SAFE_ANSWERS).status_code == 302
        client.post("/logout")
        login_donor(client, second)
        response = client.post(f"/donor/requests/{blood_request.id}/pledge", data=SAFE_ANSWERS)
        assert response.status_code == 422 and "already has enough donors" in response.get_data(as_text=True)
        assert reload(blood_request).units_pledged == 1

    def test_closed_request_cannot_be_pledged(self, logged_in, make_request):
        blood_request = make_request(status="cancelled")
        response = logged_in.post(f"/donor/requests/{blood_request.id}/pledge", data=SAFE_ANSWERS)
        assert response.status_code == 422 and "no longer open" in response.get_data(as_text=True)

    def test_cancel_releases_unit_and_allows_repledge(self, logged_in, donor, make_request):
        blood_request = make_request(units_required=1)
        logged_in.post(f"/donor/requests/{blood_request.id}/pledge", data=SAFE_ANSWERS)
        pledge = pledge_for(donor, blood_request)
        logged_in.post(f"/donor/pledges/{pledge.id}/cancel")
        assert reload(pledge).status == "cancelled"
        assert reload(blood_request).units_pledged == 0
        assert logged_in.post(f"/donor/pledges/{pledge.id}/cancel", follow_redirects=True).status_code == 200

        assert logged_in.post(f"/donor/requests/{blood_request.id}/pledge", data=SAFE_ANSWERS).status_code == 302
        assert reload(pledge).status == "pledged" and reload(blood_request).units_pledged == 1

    def test_last_unit_donor_keeps_directions(self, logged_in, donor, make_request):
        """Regression: in the old app the donor who filled the last unit lost the map button."""
        blood_request = make_request(units_required=1)
        logged_in.post(f"/donor/requests/{blood_request.id}/pledge", data=SAFE_ANSWERS)
        pledge = pledge_for(donor, blood_request)
        body = logged_in.get("/donor/dashboard").get_data(as_text=True)
        assert f"/donor/pledges/{pledge.id}/directions" in body


class TestDirections:
    @pytest.fixture
    def pledge(self, logged_in, donor, make_request):
        blood_request = make_request()
        logged_in.post(f"/donor/requests/{blood_request.id}/pledge", data=SAFE_ANSWERS)
        return pledge_for(donor, blood_request)

    def test_page_has_map_configuration(self, logged_in, pledge):
        body = logged_in.get(f"/donor/pledges/{pledge.id}/directions").get_data(as_text=True)
        assert 'data-hospital-lat="19.1136"' in body
        assert f"/donor/api/pledges/{pledge.id}/route" in body
        assert "ORS_API_KEY" not in body and "api_key" not in body

    def test_route_api_falls_back_to_straight_line(self, logged_in, pledge):
        data = logged_in.get(f"/donor/api/pledges/{pledge.id}/route?lat=19.1236&lng=72.8697").get_json()
        route = data["route"]
        assert route["source"] == "straight_line"
        assert route["coordinates"] == [[19.1236, 72.8697], [19.1136, 72.8697]]
        assert 1.0 < route["distance_km"] < 1.3

    @pytest.mark.parametrize("query", ["", "?lat=abc&lng=1", "?lat=95&lng=72", "?lat=19"])
    def test_route_api_validates_location(self, logged_in, pledge, query):
        response = logged_in.get(f"/donor/api/pledges/{pledge.id}/route{query}")
        assert response.status_code == 400

    def test_arrival(self, logged_in, pledge):
        response = logged_in.post(f"/donor/api/pledges/{pledge.id}/arrived")
        assert response.get_json() == {"status": "arrived"}
        assert logged_in.post(f"/donor/api/pledges/{pledge.id}/arrived").status_code == 200  # idempotent
        assert reload(pledge).status == "arrived"

    def test_arrival_after_cancel_is_rejected(self, logged_in, pledge):
        logged_in.post(f"/donor/pledges/{pledge.id}/cancel")
        assert logged_in.post(f"/donor/api/pledges/{pledge.id}/arrived").status_code == 409
        body = logged_in.get(f"/donor/pledges/{pledge.id}/directions").get_data(as_text=True)
        assert "live directions are turned off" in body


class TestImpactAndProfile:
    def test_impact_before_and_after_a_recorded_donation(self, client, donor, make_hospital, make_request):
        hospital = make_hospital()
        blood_request = make_request(hospital=hospital)
        login_donor(client, donor)
        body = client.get("/donor/impact").get_data(as_text=True)
        assert "No recorded donations yet" in body and "Certificate of appreciation" not in body

        client.post(f"/donor/requests/{blood_request.id}/pledge", data=SAFE_ANSWERS)
        pledge = pledge_for(donor, blood_request)
        client.post("/logout")
        login_hospital(client, hospital)
        client.post(f"/hospital/pledges/{pledge.id}/outcome", data={"outcome": "donated"})
        client.post("/logout")

        login_donor(client, donor)
        body = client.get("/donor/impact").get_data(as_text=True)
        assert "Certificate of appreciation" in body and "Neha Kulkarni" in body
        assert body.count("is-earned") == 1
        assert "₹" not in body

    def test_profile_update(self, logged_in, donor):
        form = {
            "name": "Neha K",
            "phone": "+91 90000 00000",
            "weight_kg": "61.5",
            "health_issues": "bp",
            "last_donation_date": (date.today() - timedelta(days=100)).isoformat(),
            "latitude": "19.2",
            "longitude": "72.9",
        }
        assert logged_in.post("/donor/profile", data=form).status_code == 302
        updated = reload(donor)
        assert (updated.name, updated.weight_kg, updated.health_issues, updated.latitude) == (
            "Neha K",
            61.5,
            "bp",
            19.2,
        )
        assert updated.next_eligible_date() == date.today() - timedelta(days=100) + timedelta(days=90)

    def test_profile_validation(self, logged_in, donor):
        response = logged_in.post(
            "/donor/profile",
            data={"name": "", "weight_kg": "999", "health_issues": "x", "latitude": "", "longitude": ""},
        )
        assert response.status_code == 422
        assert reload(donor).name == "Neha Kulkarni"

    def test_delete_account_requires_password(self, logged_in, donor):
        logged_in.post("/donor/account/delete", data={"password": "wrong-password"})
        assert reload(donor) is not None

    def test_delete_account_removes_data_and_releases_units(self, logged_in, donor, make_request):
        blood_request = make_request(units_required=1)
        logged_in.post(f"/donor/requests/{blood_request.id}/pledge", data=SAFE_ANSWERS)
        response = logged_in.post("/donor/account/delete", data={"password": DONOR_PASSWORD}, follow_redirects=True)
        assert "Your account and personal data have been deleted." in response.get_data(as_text=True)
        db.session.expire_all()
        assert db.session.get(Donor, donor.id) is None
        assert db.session.query(Pledge).count() == 0
        assert reload(blood_request).units_pledged == 0
        assert logged_in.get("/donor/dashboard").status_code == 302
