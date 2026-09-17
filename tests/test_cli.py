"""Tests for the Flask CLI commands."""

from bloodconnect.extensions import db
from bloodconnect.models import BloodRequest, Donor, Hospital


def test_init_db(app):
    result = app.test_cli_runner().invoke(args=["init-db"])
    assert result.exit_code == 0 and "ready" in result.output


def test_seed_demo_refuses_outside_debug(app):
    result = app.test_cli_runner().invoke(args=["seed-demo"])
    assert result.exit_code != 0 and "Refusing" in result.output
    assert db.session.query(Hospital).count() == 0


def test_seed_demo_with_force_is_idempotent(app):
    runner = app.test_cli_runner()
    first = runner.invoke(args=["seed-demo", "--force"])
    assert first.exit_code == 0, first.output
    assert "citygeneral@example.com" in first.output
    counts = (
        db.session.query(Hospital).count(),
        db.session.query(Donor).count(),
        db.session.query(BloodRequest).count(),
    )
    assert counts == (2, 8, 1)

    second = runner.invoke(args=["seed-demo", "--force"])
    assert "already exists" in second.output
    assert db.session.query(Donor).count() == 8


def test_seeded_accounts_can_log_in(app):
    app.test_cli_runner().invoke(args=["seed-demo", "--force"])
    client = app.test_client()
    assert (
        client.post(
            "/hospital/login", data={"email": "citygeneral@example.com", "password": "demo-password"}
        ).status_code
        == 302
    )
    client.post("/logout")
    assert (
        client.post("/donor/login", data={"email": "donor1@example.com", "password": "demo-password"}).status_code
        == 302
    )
    assert "City General Hospital" in client.get("/donor/dashboard").get_data(as_text=True)


def test_verify_hospital(app, make_hospital):
    hospital = make_hospital(is_verified=False, email="verify@example.com")
    runner = app.test_cli_runner()
    assert runner.invoke(args=["verify-hospital", "nobody@example.com"]).exit_code != 0
    result = runner.invoke(args=["verify-hospital", "VERIFY@example.com"])
    assert result.exit_code == 0
    db.session.expire_all()
    assert db.session.get(Hospital, hospital.id).is_verified is True
