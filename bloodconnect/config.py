"""Application configuration, read from environment variables (see .env.example)."""

import os
from datetime import timedelta

# Values copied straight from .env.example must never be used as a real secret.
PLACEHOLDER_SECRETS = {"", "replace_with_a_long_random_string", "change-me"}


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    value = os.getenv(name, "").strip()
    try:
        return int(value) if value else default
    except ValueError as exc:
        raise RuntimeError(f"Environment variable {name} must be an integer, got {value!r}") from exc


def normalize_database_url(url: str) -> str:
    """Use the pure-Python pg8000 driver for postgres:// and postgresql:// URLs.

    pg8000 needs no native libpq library, so it also works where native extensions are blocked
    (for example by Windows Application Control). A URL that already names a driver, such as
    postgresql+psycopg://, is left unchanged.
    """
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        url = "postgresql+pg8000://" + url[len("postgresql://") :]
    return url


def build_config() -> dict:
    database_url = os.getenv("DATABASE_URL", "").strip()
    secret_key = os.getenv("FLASK_SECRET_KEY", "").strip()
    email_address = os.getenv("EMAIL_ADDRESS", "").strip() or None
    email_password = os.getenv("EMAIL_PASSWORD", "").strip() or None
    smtp_configured = bool(email_address and email_password)

    return {
        "DEBUG": env_bool("FLASK_DEBUG"),
        "SECRET_KEY": None if secret_key in PLACEHOLDER_SECRETS else secret_key,
        # PostgreSQL is required; create_app validates and normalises this.
        "SQLALCHEMY_DATABASE_URI": database_url or None,
        "SQLALCHEMY_ENGINE_OPTIONS": {"pool_pre_ping": True},
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SAMESITE": "Lax",
        "SESSION_COOKIE_SECURE": env_bool("SESSION_COOKIE_SECURE"),
        "PERMANENT_SESSION_LIFETIME": timedelta(hours=12),
        "WTF_CSRF_TIME_LIMIT": None,  # token lives as long as the session
        "RATELIMIT_STORAGE_URI": os.getenv("RATELIMIT_STORAGE_URI", "memory://"),
        "RATELIMIT_HEADERS_ENABLED": True,
        "SMTP_HOST": os.getenv("SMTP_HOST", "smtp.gmail.com").strip(),
        "SMTP_PORT": env_int("SMTP_PORT", 465),
        "SMTP_TIMEOUT_SECONDS": 10,
        "EMAIL_ADDRESS": email_address,
        "EMAIL_PASSWORD": email_password,
        "MAIL_FROM_NAME": os.getenv("MAIL_FROM_NAME", "BloodConnect").strip(),
        # Without SMTP credentials emails are logged instead of sent (demo mode).
        "MAIL_SUPPRESS_SEND": env_bool("MAIL_SUPPRESS_SEND", default=not smtp_configured),
        "ORS_API_KEY": os.getenv("ORS_API_KEY", "").strip() or None,
        "PUBLIC_BASE_URL": os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/") or None,
        "REQUIRE_HOSPITAL_VERIFICATION": env_bool("REQUIRE_HOSPITAL_VERIFICATION"),
        "SEARCH_RADII_KM": (2, 5, 10, 25),
        "DONOR_REQUEST_RADIUS_KM": env_int("DONOR_REQUEST_RADIUS_KM", 50),
        "MAX_NOTIFICATIONS_PER_SEND": 50,
        "LOG_LEVEL": os.getenv("LOG_LEVEL", "INFO").strip().upper(),
    }
