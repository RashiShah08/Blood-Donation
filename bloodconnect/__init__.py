"""BloodConnect: connects hospitals that need blood with nearby compatible donors."""

import logging
import secrets
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask

from .config import build_config, normalize_database_url
from .domain.blood import BLOOD_GROUPS
from .extensions import csrf, db, limiter
from .security import init_security
from .services.notifications import Mailer

log = logging.getLogger(__name__)


def create_app(overrides: dict | None = None, *, load_env: bool = True) -> Flask:
    if load_env:
        load_dotenv()
    app = Flask(__name__, instance_relative_config=True)
    app.config.update(build_config())
    if overrides:
        app.config.update(overrides)

    _configure_static_caching(app)
    _configure_logging(app)
    _ensure_secret_key(app)
    _configure_database(app)

    db.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)
    app.extensions["mailer"] = Mailer(app.config)
    init_security(app)

    from .auth import bp as auth_bp
    from .cli import register_cli
    from .donor import bp as donor_bp
    from .errors import register_error_handlers
    from .hospital import bp as hospital_bp
    from .main import bp as main_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(donor_bp)
    app.register_blueprint(hospital_bp)
    register_error_handlers(app)
    register_cli(app)
    _register_template_helpers(app)

    with app.app_context():
        db.create_all()
    return app


def _configure_database(app: Flask) -> None:
    url = app.config.get("SQLALCHEMY_DATABASE_URI")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Start PostgreSQL (`docker compose up -d`) and add "
            "DATABASE_URL=postgresql://bloodconnect:bloodconnect@127.0.0.1:5432/bloodconnect to .env."
        )
    url = normalize_database_url(url)
    if not url.startswith("postgresql+"):
        raise RuntimeError("BloodConnect requires PostgreSQL: DATABASE_URL must start with postgresql://")
    app.config["SQLALCHEMY_DATABASE_URI"] = url


def _configure_static_caching(app: Flask) -> None:
    """Static URLs carry a version from the file's timestamp, so they can be cached for a year
    and still update the moment a file changes."""
    if not app.config.get("SEND_FILE_MAX_AGE_DEFAULT"):  # Flask seeds this key with None
        app.config["SEND_FILE_MAX_AGE_DEFAULT"] = timedelta(days=365)
    versions: dict[str, str] = {}

    @app.url_defaults
    def add_static_version(endpoint: str, values: dict) -> None:
        if endpoint != "static" or "filename" not in values:
            return
        filename = values["filename"]
        if app.debug or filename not in versions:
            path = Path(app.static_folder or "") / filename
            try:
                versions[filename] = str(int(path.stat().st_mtime))
            except OSError:
                return
        values["v"] = versions[filename]


def _configure_logging(app: Flask) -> None:
    level = getattr(logging, app.config.get("LOG_LEVEL", "INFO"), logging.INFO)
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _ensure_secret_key(app: Flask) -> None:
    if app.config.get("SECRET_KEY"):
        return
    if app.debug or app.testing:
        app.config["SECRET_KEY"] = secrets.token_hex(32)
        log.warning("FLASK_SECRET_KEY is not set; using a temporary key (sessions reset on restart).")
        return
    raise RuntimeError(
        "FLASK_SECRET_KEY is not set. Generate one with "
        '`python -c "import secrets; print(secrets.token_hex(32))"` and add it to .env, '
        "or set FLASK_DEBUG=1 for local development."
    )


def _register_template_helpers(app: Flask) -> None:
    @app.context_processor
    def globals_for_templates():
        return {
            "BLOOD_GROUPS": BLOOD_GROUPS,
            "BLOOD_GROUPS_PAIRS": [(group, group) for group in BLOOD_GROUPS],
            "current_year": date.today().year,
        }

    @app.template_filter("km")
    def format_km(value: float) -> str:
        return f"{value:.1f} km" if value >= 1 else f"{value * 1000:.0f} m"

    @app.template_filter("timeago")
    def time_ago(value: datetime) -> str:
        from .models import utcnow

        seconds = max(0, int((utcnow() - value).total_seconds()))
        for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
            if seconds >= size:
                count = seconds // size
                return f"{count} {unit}{'s' if count != 1 else ''} ago"
        return "just now"

    @app.template_filter("datefmt")
    def format_date(value: date | datetime | None) -> str:
        return value.strftime("%d %b %Y") if value else "—"
