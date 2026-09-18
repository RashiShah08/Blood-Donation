"""Registration, login, logout and password reset for donors and hospitals."""

from datetime import date

from flask import Blueprint, current_app, flash, g, redirect, render_template, request, url_for
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import func, select
from werkzeug.security import check_password_hash, generate_password_hash

from . import security
from .domain.blood import BLOOD_GROUPS, recipient_groups_for
from .domain.eligibility import MIN_AGE_YEARS, age_on
from .extensions import db, limiter
from .models import GENDERS, HEALTH_ISSUES, HOSPITAL_TYPES, Donor, Hospital
from .services import throttle
from .services.notifications import password_reset_email
from .validation import FormValidator

bp = Blueprint("auth", __name__)

ACCOUNT_MODELS = {security.DONOR: Donor, security.HOSPITAL: Hospital}
DASHBOARDS = {security.DONOR: "donor.dashboard", security.HOSPITAL: "hospital.dashboard"}
LOGIN_ENDPOINTS = {security.DONOR: "auth.donor_login", security.HOSPITAL: "auth.hospital_login"}
RESET_TOKEN_MAX_AGE_SECONDS = 3600
# Compared against when an email isn't registered, so response time doesn't reveal which emails exist.
_DUMMY_HASH = generate_password_hash("not-a-real-password")

LOGIN_LIMIT = "10 per minute; 50 per hour"


def _email_taken(model, email: str) -> bool:
    return db.session.scalar(select(model.id).where(func.lower(model.email) == email)) is not None


def _redirect_after_login(kind: str):
    target = request.args.get("next")
    if security.is_safe_redirect(target):
        return redirect(target)
    return redirect(url_for(DASHBOARDS[kind]))


def _login(kind: str):
    model = ACCOUNT_MODELS[kind]
    template = f"auth/{kind}_login.html"
    if request.method == "GET":
        if (kind == security.DONOR and g.donor) or (kind == security.HOSPITAL and g.hospital):
            return redirect(url_for(DASHBOARDS[kind]))
        return render_template(template, email="")

    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    wait = throttle.minutes_locked(kind, email) if email else 0
    if wait:
        error = (
            f"Too many failed attempts. Try again in {wait} minute{'s' if wait != 1 else ''}, or reset your password."
        )
        return render_template(template, email=email, error=error), 429
    account = db.session.scalar(select(model).where(func.lower(model.email) == email)) if email else None
    if account is None:
        check_password_hash(_DUMMY_HASH, password)
    elif account.check_password(password):
        throttle.clear(kind, email)
        security.login(kind, account.id)
        return _redirect_after_login(kind)
    if email:
        throttle.record_failure(kind, email)
    return render_template(template, email=email, error="Incorrect email or password."), 401


@bp.route("/donor/login", methods=["GET", "POST"])
@limiter.limit(LOGIN_LIMIT, methods=["POST"])
def donor_login():
    return _login(security.DONOR)


@bp.route("/hospital/login", methods=["GET", "POST"])
@limiter.limit(LOGIN_LIMIT, methods=["POST"])
def hospital_login():
    return _login(security.HOSPITAL)


@bp.post("/logout")
def logout():
    security.logout()
    flash("You've been logged out.", "success")
    return redirect(url_for("main.home"))


@bp.route("/donor/register", methods=["GET", "POST"])
@limiter.limit("5 per minute; 30 per hour", methods=["POST"])
def donor_register():
    context = {
        "genders": GENDERS,
        "health_issues": HEALTH_ISSUES,
        "form": {},
        "errors": {},
        "give_to": {group: list(recipient_groups_for(group)) for group in BLOOD_GROUPS},
    }
    if request.method == "GET":
        return render_template("auth/donor_register.html", **context)

    today = date.today()
    v = FormValidator(request.form)
    v.text("name", "Full name", min_len=2, max_len=120)
    email = v.email()
    v.phone(required=False)
    dob = v.past_date("date_of_birth", "Date of birth", today=today)
    v.choice("gender", "Gender", GENDERS)
    v.number("weight_kg", "Weight", minimum=20, maximum=250)
    v.blood_group()
    v.choice("health_issues", "Health condition", HEALTH_ISSUES)
    v.coordinates()
    v.new_password()
    if dob and "date_of_birth" not in v.errors and age_on(dob, today) < MIN_AGE_YEARS:
        v.errors["date_of_birth"] = f"You must be at least {MIN_AGE_YEARS} years old to register as a donor."
    if request.form.get("consent") != "on":
        v.errors["consent"] = "Please agree to the terms and privacy policy."
    if email and "email" not in v.errors and _email_taken(Donor, email):
        v.errors["email"] = "An account with this email already exists."

    if not v.is_valid:
        return render_template("auth/donor_register.html", **{**context, "form": request.form, "errors": v.errors}), 422

    data = v.data
    donor = Donor(
        name=data["name"],
        email=data["email"],
        phone=data["phone"],
        date_of_birth=data["date_of_birth"],
        gender=data["gender"],
        weight_kg=data["weight_kg"],
        blood_group=data["blood_group"],
        health_issues=data["health_issues"],
        latitude=data["latitude"],
        longitude=data["longitude"],
    )
    donor.set_password(data["password"])
    db.session.add(donor)
    db.session.commit()
    security.login(security.DONOR, donor.id)
    flash("Welcome to BloodConnect! Your donor account is ready.", "success")
    return redirect(url_for("donor.dashboard"))


@bp.route("/hospital/register", methods=["GET", "POST"])
@limiter.limit("5 per minute; 30 per hour", methods=["POST"])
def hospital_register():
    context = {"hospital_types": HOSPITAL_TYPES, "form": {}, "errors": {}}
    if request.method == "GET":
        return render_template("auth/hospital_register.html", **context)

    v = FormValidator(request.form)
    name = v.text("name", "Hospital name", min_len=3, max_len=160)
    email = v.email()
    v.phone()
    v.text("address", "Address", min_len=5, max_len=255)
    v.text("city", "City", max_len=80)
    v.text("state", "State", max_len=80)
    v.pincode()
    v.text("country", "Country", max_len=80)
    v.choice("hospital_type", "Hospital type", HOSPITAL_TYPES)
    v.coordinates()
    v.new_password()
    if request.form.get("consent") != "on":
        v.errors["consent"] = "Please agree to the terms and privacy policy."
    if email and "email" not in v.errors and _email_taken(Hospital, email):
        v.errors["email"] = "An account with this email already exists."
    if name and "name" not in v.errors:
        if db.session.scalar(select(Hospital.id).where(func.lower(Hospital.name) == name.lower())):
            v.errors["name"] = "A hospital with this name is already registered."

    if not v.is_valid:
        return render_template(
            "auth/hospital_register.html", **{**context, "form": request.form, "errors": v.errors}
        ), 422

    data = v.data
    hospital = Hospital(
        name=data["name"],
        email=data["email"],
        phone=data["phone"],
        address=data["address"],
        city=data["city"],
        state=data["state"],
        pincode=data["pincode"],
        country=data["country"],
        hospital_type=data["hospital_type"],
        latitude=data["latitude"],
        longitude=data["longitude"],
        is_verified=not current_app.config["REQUIRE_HOSPITAL_VERIFICATION"],
    )
    hospital.set_password(data["password"])
    db.session.add(hospital)
    db.session.commit()
    security.login(security.HOSPITAL, hospital.id)
    flash("Your hospital account is ready.", "success")
    return redirect(url_for("hospital.dashboard"))


def _reset_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.secret_key, salt="bloodconnect-password-reset")


def _fingerprint(account) -> str:
    # Changes when the password changes, so a reset link only works once.
    return account.password_hash[-16:]


@bp.route("/<any(donor, hospital):kind>/forgot-password", methods=["GET", "POST"])
@limiter.limit("5 per minute; 20 per hour", methods=["POST"])
def forgot_password(kind: str):
    if request.method == "GET":
        return render_template("auth/forgot_password.html", kind=kind)

    email = request.form.get("email", "").strip().lower()
    model = ACCOUNT_MODELS[kind]
    account = db.session.scalar(select(model).where(func.lower(model.email) == email)) if email else None
    if account is not None:
        token = _reset_serializer().dumps({"kind": kind, "id": account.id, "fp": _fingerprint(account)})
        link = url_for("auth.reset_password", kind=kind, token=token, _external=True)
        base = current_app.config.get("PUBLIC_BASE_URL")
        if base:
            link = base + url_for("auth.reset_password", kind=kind, token=token)
        current_app.extensions["mailer"].send([password_reset_email(account.email, account.name, link)])
    flash("If an account exists for that email, we've sent a link to reset the password.", "success")
    return redirect(url_for(LOGIN_ENDPOINTS[kind]))


@bp.route("/<any(donor, hospital):kind>/reset-password/<token>", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def reset_password(kind: str, token: str):
    try:
        payload = _reset_serializer().loads(token, max_age=RESET_TOKEN_MAX_AGE_SECONDS)
    except (SignatureExpired, BadSignature):
        payload = None
    account = db.session.get(ACCOUNT_MODELS[kind], payload["id"]) if payload and payload.get("kind") == kind else None
    if account is None or payload.get("fp") != _fingerprint(account):
        flash("That reset link is invalid or has expired. Please request a new one.", "error")
        return redirect(url_for("auth.forgot_password", kind=kind))

    if request.method == "GET":
        return render_template("auth/reset_password.html", kind=kind, token=token, errors={})

    v = FormValidator(request.form)
    v.new_password()
    if not v.is_valid:
        return render_template("auth/reset_password.html", kind=kind, token=token, errors=v.errors), 422
    account.set_password(v.data["password"])
    db.session.commit()
    throttle.clear(kind, account.email)  # a successful reset lifts any lockout
    flash("Your password has been updated. Please log in.", "success")
    return redirect(url_for(LOGIN_ENDPOINTS[kind]))
