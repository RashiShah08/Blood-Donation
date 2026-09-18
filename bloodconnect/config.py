"""Application configuration, read from environment variables (see .env.example)."""

import os
from datetime import timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

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


# libpq connection options that pg8000 does not understand; TLS is configured separately.
LIBPQ_ONLY_OPTIONS = {"sslmode", "channel_binding"}
TLS_SSLMODES = {"require", "verify-ca", "verify-full"}


def split_tls_options(url: str) -> tuple[str, bool]:
    """Remove libpq-only query options from a database URL and report whether TLS is required.

    Hosted PostgreSQL (for example Neon) hands out URLs ending in ?sslmode=require. pg8000
    rejects that option, so it is removed here and TLS is switched on through the driver.
    """
    parts = urlsplit(url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    sslmode = next((value for key, value in query if key == "sslmode"), "")
    kept = [(key, value) for key, value in query if key not in LIBPQ_ONLY_OPTIONS]
    return urlunsplit(parts._replace(query=urlencode(kept))), sslmode.lower() in TLS_SSLMODES


def build_config() -> dict:
    database_url = os.getenv("DATABASE_URL", "").strip()
    secret_key = os.getenv("FLASK_SECRET_KEY", "").strip()
    email_address = os.getenv("EMAIL_ADDRESS", "").strip() or None
    email_password = os.getenv("EMAIL_PASSWORD", "").strip() or None
    smtp_configured = bool(email_address and email_password)
    brevo_api_key = os.getenv("BREVO_API_KEY", "").strip() or None
    gmail_client_id = os.getenv("GMAIL_CLIENT_ID", "").strip() or None
    gmail_client_secret = os.getenv("GMAIL_CLIENT_SECRET", "").strip() or None
    gmail_refresh_token = os.getenv("GMAIL_REFRESH_TOKEN", "").strip() or None
    gmail_api_configured = bool(gmail_client_id and gmail_client_secret and gmail_refresh_token)

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
        # Brevo sends over HTTPS, for hosts that block SMTP ports (such as Render's free tier).
        "BREVO_API_KEY": brevo_api_key,
        "MAIL_FROM_EMAIL": os.getenv("MAIL_FROM_EMAIL", "").strip() or email_address,
        # Google's Gmail API with a send-only OAuth token (see scripts/google_gmail_token.py).
        "GMAIL_CLIENT_ID": gmail_client_id,
        "GMAIL_CLIENT_SECRET": gmail_client_secret,
        "GMAIL_REFRESH_TOKEN": gmail_refresh_token,
        "GMAIL_SENDER": os.getenv("GMAIL_SENDER", "").strip() or email_address,
        # Without email credentials, emails are logged instead of sent (demo mode).
        "MAIL_SUPPRESS_SEND": env_bool(
            "MAIL_SUPPRESS_SEND", default=not (smtp_configured or brevo_api_key or gmail_api_configured)
        ),
        # Number of reverse proxies in front of the app (1 on Render); 0 when served directly.
        "TRUST_PROXY_HOPS": env_int("TRUST_PROXY_HOPS", 0),
        "ORS_API_KEY": os.getenv("ORS_API_KEY", "").strip() or None,
        # Where security researchers should report issues (shown in /.well-known/security.txt).
        "SECURITY_CONTACT": os.getenv(
            "SECURITY_CONTACT", "https://github.com/RashiShah08/Blood-Donation/security/advisories/new"
        ).strip(),
        "PUBLIC_BASE_URL": os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/") or None,
        "REQUIRE_HOSPITAL_VERIFICATION": env_bool("REQUIRE_HOSPITAL_VERIFICATION"),
        "SEARCH_RADII_KM": (2, 5, 10, 25),
        "DONOR_REQUEST_RADIUS_KM": env_int("DONOR_REQUEST_RADIUS_KM", 50),
        "MAX_NOTIFICATIONS_PER_SEND": 50,
        # Sites allowed to show the public information pages in a frame (CSP frame-ancestors).
        # Empty blocks framing everywhere.
        "PUBLIC_FRAME_ANCESTORS": os.getenv("PUBLIC_FRAME_ANCESTORS", "https: http://localhost:*").strip() or None,
        "LOG_LEVEL": os.getenv("LOG_LEVEL", "INFO").strip().upper(),
    }
