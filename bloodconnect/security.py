"""Session login, access control and HTTP security headers."""

from functools import wraps
from urllib.parse import urlsplit

from flask import Flask, g, jsonify, redirect, request, session, url_for

from .extensions import db
from .models import Donor, Hospital

DONOR, HOSPITAL = "donor", "hospital"

CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        # Leaflet positions map tiles with inline styles.
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: https://tile.openstreetmap.org",
        "connect-src 'self'",
        "font-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
)


def login(kind: str, account_id: int) -> None:
    session.clear()  # new session on login prevents session fixation
    session.permanent = True
    session["account_kind"] = kind
    session["account_id"] = account_id


def logout() -> None:
    session.clear()


def is_safe_redirect(target: str | None) -> bool:
    """Only allow redirects to paths on this site (blocks open redirects)."""
    if not target or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return False
    parts = urlsplit(target)
    return not parts.scheme and not parts.netloc


def _wants_json() -> bool:
    return request.path.startswith(("/api/", "/donor/api/", "/hospital/api/")) or request.is_json


def _login_required(kind: str, login_endpoint: str):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            account = g.donor if kind == DONOR else g.hospital
            if account is None:
                if _wants_json():
                    return jsonify(error="Please log in to continue."), 401
                next_path = request.full_path.rstrip("?") if request.method == "GET" else None
                return redirect(url_for(login_endpoint, next=next_path))
            return view(*args, **kwargs)

        return wrapped

    return decorator


donor_required = _login_required(DONOR, "auth.donor_login")
hospital_required = _login_required(HOSPITAL, "auth.hospital_login")


def init_security(app: Flask) -> None:
    @app.before_request
    def load_account():
        g.donor = g.hospital = None
        kind, account_id = session.get("account_kind"), session.get("account_id")
        if kind == DONOR:
            g.donor = db.session.get(Donor, account_id)
        elif kind == HOSPITAL:
            g.hospital = db.session.get(Hospital, account_id)
        if kind and g.donor is None and g.hospital is None:
            session.clear()  # the account was deleted

    @app.after_request
    def set_security_headers(response):
        headers = response.headers
        headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        headers.setdefault("Permissions-Policy", "geolocation=(self), camera=(), microphone=()")
        if app.config.get("SESSION_COOKIE_SECURE"):
            headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        # Pages are personal or carry a CSRF token; only static files are cacheable.
        if response.mimetype == "text/html":
            headers.setdefault("Cache-Control", "no-store")
        return response

    @app.context_processor
    def inject_account():
        return {"current_donor": g.get("donor"), "current_hospital": g.get("hospital")}
