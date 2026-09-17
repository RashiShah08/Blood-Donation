"""Live trip progress from donors and the hospital operations console."""

from datetime import timedelta

import pytest

from bloodconnect.extensions import db
from bloodconnect.models import Notification, Pledge, utcnow
from tests.conftest import SAFE_ANSWERS, login_donor, login_hospital, pledge_for, reload


@pytest.fixture
def hospital(make_hospital):
    return make_hospital(name="Harbour Hospital")


@pytest.fixture
def pledge(client, hospital, make_request, make_donor):
    blood_request = make_request(hospital=hospital, blood_group="O-", units_required=2)
    donor = make_donor(name="Ananya Rao", blood_group="O-")
    login_donor(client, donor)
    client.post(f"/donor/requests/{blood_request.id}/pledge", data=SAFE_ANSWERS)
    return pledge_for(donor, blood_request)


class TestProgressApi:
    def test_records_eta_and_distance(self, client, pledge):
        response = client.post(
            f"/donor/api/pledges/{pledge.id}/progress", json={"distance_km": 3.456, "eta_minutes": 11.6}
        )
        assert response.status_code == 200
        assert response.get_json() == {"status": "pledged", "eta_minutes": 12}
        updated = reload(pledge)
        assert (updated.distance_remaining_km, updated.eta_minutes) == (3.46, 12)
        assert updated.progress_updated_at is not None

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"distance_km": -1, "eta_minutes": 5},
            {"distance_km": 5, "eta_minutes": 2000},
            {"distance_km": 5000, "eta_minutes": 5},
            {"distance_km": True, "eta_minutes": 5},
            {"distance_km": "5", "eta_minutes": 5},
            {"distance_km": 5},
        ],
    )
    def test_rejects_invalid_values(self, client, pledge, body):
        assert client.post(f"/donor/api/pledges/{pledge.id}/progress", json=body).status_code == 400
        assert reload(pledge).eta_minutes is None

    def test_other_donors_cannot_report(self, client, pledge, make_donor):
        client.post("/logout")
        login_donor(client, make_donor(blood_group="O-"))
        response = client.post(f"/donor/api/pledges/{pledge.id}/progress", json={"distance_km": 1, "eta_minutes": 3})
        assert response.status_code == 404

    def test_inactive_pledge_is_rejected(self, client, pledge):
        client.post(f"/donor/pledges/{pledge.id}/cancel")
        response = client.post(f"/donor/api/pledges/{pledge.id}/progress", json={"distance_km": 1, "eta_minutes": 3})
        assert response.status_code == 409

    def test_requires_login(self, client, pledge):
        client.post("/logout")
        response = client.post(f"/donor/api/pledges/{pledge.id}/progress", json={"distance_km": 1, "eta_minutes": 3})
        assert response.status_code == 401

    def test_arrival_sets_eta_to_zero(self, client, pledge):
        client.post(f"/donor/api/pledges/{pledge.id}/arrived")
        updated = reload(pledge)
        assert (updated.status, updated.eta_minutes, updated.distance_remaining_km) == ("arrived", 0, 0.0)

    def test_hospital_request_page_shows_eta(self, client, hospital, pledge):
        client.post(f"/donor/api/pledges/{pledge.id}/progress", json={"distance_km": 2, "eta_minutes": 7})
        client.post("/logout")
        login_hospital(client, hospital)
        body = client.get(f"/hospital/requests/{pledge.request_id}").get_data(as_text=True)
        assert "7 min" in body


class TestOpsConsole:
    def test_page_requires_a_hospital(self, client, make_donor):
        assert "/hospital/login" in client.get("/hospital/ops").headers["Location"]
        assert client.get("/hospital/api/ops").status_code == 401
        login_donor(client, make_donor())
        assert client.get("/hospital/ops").status_code == 302

    def test_page_renders(self, client, hospital):
        login_hospital(client, hospital)
        response = client.get("/hospital/ops")
        body = response.get_data(as_text=True)
        assert response.status_code == 200
        assert 'data-surface="ops"' in body
        assert 'data-api-url="/hospital/api/ops"' in body
        assert "LIVE OPS" in body

    def test_snapshot(self, client, hospital, make_hospital, make_request, make_donor):
        low = make_request(hospital=hospital, urgency="low", blood_group="A+", units_required=1)
        critical = make_request(
            hospital=hospital, urgency="critical", blood_group="O-", units_required=3, units_pledged=3
        )
        make_request(hospital=hospital, status="fulfilled")
        make_request(hospital=make_hospital(), patient_name="Other Hospital Patient")

        near, stale, here = (
            make_donor(name="Near Donor"),
            make_donor(name="Stale Donor"),
            make_donor(name="Arrived Donor"),
        )
        now = utcnow()
        db.session.add_all(
            [
                Pledge(
                    request_id=critical.id,
                    donor_id=near.id,
                    eta_minutes=6,
                    distance_remaining_km=2.34,
                    progress_updated_at=now,
                ),
                Pledge(
                    request_id=critical.id,
                    donor_id=stale.id,
                    eta_minutes=3,
                    distance_remaining_km=1.0,
                    progress_updated_at=now - timedelta(minutes=25),
                ),
                Pledge(
                    request_id=critical.id,
                    donor_id=here.id,
                    status="arrived",
                    eta_minutes=0,
                    distance_remaining_km=0.0,
                    progress_updated_at=now,
                ),
                Notification(request_id=critical.id, donor_id=near.id, sent_at=now),
                Notification(request_id=critical.id, donor_id=stale.id, sent_at=now),
            ]
        )
        db.session.commit()

        login_hospital(client, hospital)
        data = client.get("/hospital/api/ops").get_json()

        assert data["hospital"]["name"] == "Harbour Hospital"
        assert data["summary"] == {"open_requests": 2, "units_needed": 1, "on_the_way": 2, "arrived": 1}
        assert [r["id"] for r in data["requests"]] == [critical.id, low.id]  # most urgent first
        assert "Other Hospital Patient" not in str(data)

        first = data["requests"][0]
        assert first["detail_url"] == f"/hospital/requests/{critical.id}"
        assert first["donors_url"] == f"/hospital/api/requests/{critical.id}/donors"
        assert first["notify_url"] == f"/hospital/api/requests/{critical.id}/notify"
        assert first["notified"] == 2
        people = [(p["donor_name"], p["status"], p["eta_minutes"], p["distance_km"]) for p in first["pledges"]]
        assert people == [
            ("Arrived Donor", "arrived", 0, 0.0),
            ("Near Donor", "pledged", 6, 2.3),
            ("Stale Donor", "pledged", None, None),  # progress older than 10 minutes is hidden
        ]

        kinds = [event["kind"] for event in data["activity"]]
        assert "alerted" in kinds and "request" in kinds and "arrived" in kinds
        alert_event = next(e for e in data["activity"] if e["kind"] == "alerted")
        assert alert_event["text"] == f"2 donors alerted for request #{critical.id}"

    def test_snapshot_never_contains_donor_coordinates(self, client, hospital, make_request, make_donor):
        blood_request = make_request(hospital=hospital, blood_group="O-")
        donor = make_donor(latitude=19.123456, longitude=72.876543)
        db.session.add(Pledge(request_id=blood_request.id, donor_id=donor.id))
        db.session.commit()
        login_hospital(client, hospital)
        raw = client.get("/hospital/api/ops").get_data(as_text=True)
        assert "19.123456" not in raw and "72.876543" not in raw

    def test_empty_snapshot(self, client, hospital):
        login_hospital(client, hospital)
        data = client.get("/hospital/api/ops").get_json()
        assert data["requests"] == [] and data["activity"] == []
        assert data["summary"] == {"open_requests": 0, "units_needed": 0, "on_the_way": 0, "arrived": 0}


def test_fonts_are_served_locally(client):
    css = client.get("/static/css/app.css").get_data(as_text=True)
    for font in ("plus-jakarta-sans", "geist", "geist-mono"):
        assert f"../fonts/{font}.woff2" in css
        response = client.get(f"/static/fonts/{font}.woff2")
        assert response.status_code == 200 and len(response.data) > 10_000
        response.close()


@pytest.mark.parametrize(
    "path, surface",
    [
        ("/", "community"),
        ("/donor/login", "community"),
        ("/hospital/login", "product"),
        ("/hospital/register", "product"),
    ],
)
def test_pages_use_the_right_surface(client, path, surface):
    assert f'data-surface="{surface}"' in client.get(path).get_data(as_text=True)
