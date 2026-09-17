"""Friendly error pages for browsers and JSON errors for API calls. Details are logged, never shown."""

import logging

from flask import Flask, jsonify, render_template, request
from flask_wtf.csrf import CSRFError
from werkzeug.exceptions import HTTPException

log = logging.getLogger(__name__)

MESSAGES = {
    400: ("Bad request", "Something about that request wasn't right. Please try again."),
    403: ("Access denied", "You don't have permission to view this page."),
    404: ("Page not found", "The page you're looking for doesn't exist or has moved."),
    405: ("Not allowed", "That action isn't allowed here."),
    413: ("Too large", "That request was too large."),
    429: ("Too many attempts", "You've tried that too many times. Please wait a minute and try again."),
    500: ("Something went wrong", "An unexpected error occurred. Please try again in a moment."),
}


def _is_api_request() -> bool:
    return "/api/" in request.path or request.accept_mimetypes.best == "application/json"


def _respond(code: int, message: str | None = None):
    title, default_message = MESSAGES.get(code, MESSAGES[500])
    message = message or default_message
    if _is_api_request():
        return jsonify(error=message), code
    return render_template("errors/error.html", code=code, title=title, message=message), code


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(CSRFError)
    def csrf_error(error):
        return _respond(400, "Your session expired or the form was out of date. Please reload the page and try again.")

    @app.errorhandler(HTTPException)
    def http_error(error: HTTPException):
        return _respond(error.code or 500)

    @app.errorhandler(Exception)
    def unexpected_error(error: Exception):
        log.exception("Unhandled error on %s %s", request.method, request.path)
        return _respond(500)
