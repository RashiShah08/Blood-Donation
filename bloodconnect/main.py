"""Public information pages, health check and redirects from the old URLs."""

from flask import Blueprint, jsonify, redirect, render_template, url_for
from sqlalchemy import text

from .domain.blood import BLOOD_GROUPS, donor_groups_for, recipient_groups_for
from .domain.eligibility import (
    DEFAULT_INTERVAL_DAYS,
    DONATION_INTERVAL_DAYS,
    MAX_AGE_YEARS,
    MIN_AGE_YEARS,
    MIN_WEIGHT_KG,
)
from .extensions import db

bp = Blueprint("main", __name__)

ELIGIBILITY_FACTS = {
    "min_age": MIN_AGE_YEARS,
    "max_age": MAX_AGE_YEARS,
    "min_weight": MIN_WEIGHT_KG,
    "male_interval": DONATION_INTERVAL_DAYS["Male"],
    "female_interval": DONATION_INTERVAL_DAYS["Female"],
}

# The same rules the server enforces, handed to the interactive "could you donate?" widget.
QUICK_CHECK_RULES = {
    "min_age": MIN_AGE_YEARS,
    "max_age": MAX_AGE_YEARS,
    "min_weight": MIN_WEIGHT_KG,
    "interval_days": {**DONATION_INTERVAL_DAYS, "Other": DEFAULT_INTERVAL_DAYS},
}


def compatibility_map() -> dict[str, dict[str, list[str]]]:
    return {
        group: {"give_to": list(recipient_groups_for(group)), "receive_from": list(donor_groups_for(group))}
        for group in BLOOD_GROUPS
    }


@bp.get("/")
def home():
    return render_template("main/home.html", compat=compatibility_map(), rules=QUICK_CHECK_RULES)


@bp.get("/how-it-works")
def how_it_works():
    compatibility = {group: donor_groups_for(group) for group in BLOOD_GROUPS}
    return render_template("main/how_it_works.html", compatibility=compatibility, compat=compatibility_map())


@bp.get("/for-donors")
def for_donors():
    return render_template("main/for_donors.html", facts=ELIGIBILITY_FACTS, rules=QUICK_CHECK_RULES)


@bp.get("/for-patients")
def for_patients():
    return render_template("main/for_patients.html")


@bp.get("/faq")
def faq():
    return render_template("main/faq.html", facts=ELIGIBILITY_FACTS)


@bp.get("/about")
def about():
    return render_template("main/about.html")


@bp.get("/terms")
def terms():
    return render_template("main/terms.html")


@bp.get("/privacy")
def privacy():
    return render_template("main/privacy.html")


@bp.get("/healthz")
def health():
    # Liveness only: hosting platforms call this every few seconds, and a database query here
    # would keep a scale-to-zero database awake around the clock.
    return jsonify(status="ok")


@bp.get("/readyz")
def ready():
    db.session.execute(text("SELECT 1"))
    return jsonify(status="ok", database="ok")


# Old URLs from the first version of the site, kept working with permanent redirects.
LEGACY_REDIRECTS = {
    "/donor_login": "auth.donor_login",
    "/hospitallogin": "auth.hospital_login",
    "/donordashboard": "donor.dashboard",
    "/hospitaldashboard": "hospital.dashboard",
    "/reward": "donor.impact",
    "/map.html": "donor.dashboard",
    "/proximity.html": "hospital.dashboard",
    "/about_us": "main.about",
    "/FAQ": "main.faq",
    "/for_donor": "main.for_donors",
    "/for_patients": "main.for_patients",
    "/howitworks": "main.how_it_works",
    "/terms_conditions": "main.terms",
}


def _make_redirect(endpoint: str):
    def legacy_redirect():
        return redirect(url_for(endpoint), code=301)

    return legacy_redirect


for _index, (_path, _endpoint) in enumerate(LEGACY_REDIRECTS.items()):
    bp.add_url_rule(_path, f"legacy_redirect_{_index}", _make_redirect(_endpoint))
