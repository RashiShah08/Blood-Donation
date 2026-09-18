"""Flask CLI commands: `flask --app app <command>`."""

from datetime import date, timedelta

import click
from flask import Flask, current_app
from sqlalchemy import select
from sqlalchemy.engine import make_url

from .extensions import db
from .models import BloodRequest, Donor, Hospital

DEMO_PASSWORD = "demo-password"

# Hospitals and donors around Mumbai, so the map and matching work out of the box.
DEMO_HOSPITALS = [
    ("City General Hospital", "citygeneral@example.com", "Andheri East", 19.1136, 72.8697, "government"),
    ("Lakeside Blood Bank", "lakeside@example.com", "Powai", 19.1197, 72.9051, "blood_bank"),
]
DEMO_DONORS = [
    ("Aarav Mehta", "O-", "Male", 19.1180, 72.8650),
    ("Diya Sharma", "A+", "Female", 19.1070, 72.8760),
    ("Kabir Rao", "B+", "Male", 19.1300, 72.8900),
    ("Isha Patel", "O+", "Female", 19.0990, 72.8550),
    ("Rohan Iyer", "AB-", "Male", 19.1450, 72.9100),
    ("Meera Nair", "A-", "Female", 19.1600, 72.8400),
    ("Vikram Singh", "O+", "Male", 19.0760, 72.8777),
    ("Sara Khan", "B-", "Female", 19.2000, 72.9700),
]


def register_cli(app: Flask) -> None:
    @app.cli.command("init-db")
    def init_db():
        """Create any missing database tables."""
        db.create_all()
        click.echo("Database tables are ready.")

    @app.cli.command("seed-demo")
    @click.option("--force", is_flag=True, help="Allow seeding when FLASK_DEBUG is off.")
    @click.option("--allow-remote-database", is_flag=True, help="Allow seeding a database that isn't on this machine.")
    def seed_demo(force: bool, allow_remote_database: bool):
        """Add demo hospitals, donors and one open request."""
        if not current_app.debug and not force:
            raise click.ClickException("Refusing to add demo accounts outside debug mode (use --force).")
        # Demo accounts share one published password: never put them on a public database by accident.
        host = make_url(current_app.config["SQLALCHEMY_DATABASE_URI"]).host or ""
        if host not in {"127.0.0.1", "localhost", "::1"} and not allow_remote_database:
            raise click.ClickException(
                f"Refusing to add demo accounts to the remote database at {host}: they share a published "
                "password. Use --allow-remote-database only for a private demo."
            )
        if db.session.scalar(select(Hospital).where(Hospital.email == DEMO_HOSPITALS[0][1])):
            click.echo("Demo data already exists.")
            return

        hospitals = []
        for name, email, area, lat, lon, kind in DEMO_HOSPITALS:
            hospital = Hospital(
                name=name,
                email=email,
                phone="+91 22 5555 0100",
                address=f"{area} Main Road",
                city="Mumbai",
                state="Maharashtra",
                pincode="400069",
                country="India",
                hospital_type=kind,
                latitude=lat,
                longitude=lon,
                is_verified=True,
            )
            hospital.set_password(DEMO_PASSWORD)
            hospitals.append(hospital)
        db.session.add_all(hospitals)

        for index, (name, group, gender, lat, lon) in enumerate(DEMO_DONORS, start=1):
            donor = Donor(
                name=name,
                email=f"donor{index}@example.com",
                date_of_birth=date.today() - timedelta(days=365 * 28),
                gender=gender,
                weight_kg=62,
                blood_group=group,
                health_issues="none",
                latitude=lat,
                longitude=lon,
            )
            donor.set_password(DEMO_PASSWORD)
            db.session.add(donor)

        db.session.flush()
        db.session.add(
            BloodRequest(
                hospital_id=hospitals[0].id,
                patient_name="Demo Patient",
                patient_gender="Female",
                patient_weight_kg=58,
                blood_group="A+",
                units_required=2,
                urgency="high",
                clinical_notes="Scheduled surgery tomorrow morning.",
            )
        )
        db.session.commit()
        click.echo(f"Added {len(DEMO_HOSPITALS)} hospitals and {len(DEMO_DONORS)} donors.")
        click.echo(f"Hospital login: {DEMO_HOSPITALS[0][1]} / {DEMO_PASSWORD}")
        click.echo(f"Donor login:    donor1@example.com / {DEMO_PASSWORD}")

    @app.cli.command("verify-hospital")
    @click.argument("email")
    def verify_hospital(email: str):
        """Mark a hospital account as verified."""
        hospital = db.session.scalar(select(Hospital).where(Hospital.email == email.strip().lower()))
        if hospital is None:
            raise click.ClickException(f"No hospital with email {email}")
        hospital.is_verified = True
        db.session.commit()
        click.echo(f"{hospital.name} is now verified.")
