"""Unit tests for form validation and configuration loading."""

import ssl
from datetime import date

import pytest
from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from bloodconnect import _configure_database, config, create_app
from bloodconnect.validation import FormValidator
from tests.conftest import TEST_DATABASE_URL, dispose

TODAY = date(2026, 9, 14)


class TestFormValidator:
    def test_text_collapses_whitespace_and_checks_length(self):
        v = FormValidator({"name": "  Asha   Verma ", "short": "a", "long": "x" * 10})
        assert v.text("name", "Name") == "Asha Verma"
        v.text("short", "Short", min_len=2)
        v.text("long", "Long", max_len=5)
        v.text("missing", "Missing")
        v.text("optional", "Optional", required=False)
        assert set(v.errors) == {"short", "long", "missing"}

    def test_email_is_lowercased_and_validated(self):
        v = FormValidator({"email": " Asha@Example.COM "})
        assert v.email() == "asha@example.com" and v.is_valid
        for bad in ["", "no-at-sign", "a@b", "a b@c.com", "x" * 250 + "@example.com"]:
            invalid = FormValidator({"email": bad})
            invalid.email()
            assert "email" in invalid.errors

    def test_new_password_rules(self):
        short = FormValidator({"password": "short", "confirm_password": "short"})
        short.new_password()
        assert "password" in short.errors
        mismatch = FormValidator({"password": "long-enough-1", "confirm_password": "different-1"})
        mismatch.new_password()
        assert "confirm_password" in mismatch.errors
        ok = FormValidator({"password": "long-enough-1", "confirm_password": "long-enough-1"})
        ok.new_password()
        assert ok.is_valid

    @pytest.mark.parametrize(
        "phone, valid", [("+91 98765 43210", True), ("(022) 555-0100", True), ("12", False), ("call me", False)]
    )
    def test_phone(self, phone, valid):
        v = FormValidator({"phone": phone})
        v.phone()
        assert v.is_valid is valid

    def test_optional_phone_may_be_blank(self):
        v = FormValidator({"phone": ""})
        assert v.phone(required=False) is None and v.is_valid

    def test_number_parsing_and_ranges(self):
        v = FormValidator({"a": "12.5", "b": "abc", "c": "0", "d": "3.5", "e": "7"})
        assert v.number("a", "A", minimum=1, maximum=20) == 12.5
        v.number("b", "B", minimum=1, maximum=20)
        v.number("c", "C", minimum=1, maximum=20)
        v.number("d", "D", minimum=1, maximum=20, integer=True)
        assert v.number("e", "E", minimum=1, maximum=20, integer=True) == 7
        assert set(v.errors) == {"b", "c", "d"}

    def test_past_date(self):
        v = FormValidator({"ok": "2000-01-31", "future": "2027-01-01", "bad": "31/01/2000", "old": "1800-01-01"})
        assert v.past_date("ok", "OK", today=TODAY) == date(2000, 1, 31)
        v.past_date("future", "Future", today=TODAY)
        v.past_date("bad", "Bad", today=TODAY)
        v.past_date("old", "Old", today=TODAY)
        assert v.past_date("none", "None", today=TODAY, required=False) is None
        assert set(v.errors) == {"future", "bad", "old"}

    @pytest.mark.parametrize(
        "lat, lon, valid",
        [("19.12", "72.87", True), ("", "", False), ("91", "72", False), ("19", "abc", False), ("nan", "72", False)],
    )
    def test_coordinates(self, lat, lon, valid):
        v = FormValidator({"latitude": lat, "longitude": lon})
        v.coordinates()
        assert v.is_valid is valid
        if not valid:
            assert "location" in v.errors

    def test_choice_and_blood_group(self):
        v = FormValidator({"gender": "Female", "blood_group": " ab- ", "bad_gender": "X", "bad_group": "Q"})
        assert v.choice("gender", "Gender", ["Male", "Female"]) == "Female"
        assert v.blood_group() == "AB-"
        v.choice("bad_gender", "Gender", ["Male", "Female"])
        v.blood_group("bad_group")
        assert set(v.errors) == {"bad_gender", "bad_group"}

    def test_pincode(self):
        good, bad = FormValidator({"pincode": "400069"}), FormValidator({"pincode": "<script>"})
        good.pincode()
        bad.pincode()
        assert good.is_valid and not bad.is_valid


class TestConfig:
    @pytest.mark.parametrize(
        "url, expected",
        [
            ("postgres://u:p@h:5432/db", "postgresql+pg8000://u:p@h:5432/db"),
            ("postgresql://u:p@h/db", "postgresql+pg8000://u:p@h/db"),
            ("postgresql+psycopg://u@h/db", "postgresql+psycopg://u@h/db"),
        ],
    )
    def test_normalize_database_url(self, url, expected):
        assert config.normalize_database_url(url) == expected

    def test_placeholder_secret_is_ignored(self, monkeypatch):
        monkeypatch.setenv("FLASK_SECRET_KEY", "replace_with_a_long_random_string")
        assert config.build_config()["SECRET_KEY"] is None

    def test_email_is_simulated_without_credentials(self, monkeypatch):
        monkeypatch.delenv("EMAIL_ADDRESS", raising=False)
        monkeypatch.delenv("EMAIL_PASSWORD", raising=False)
        monkeypatch.delenv("MAIL_SUPPRESS_SEND", raising=False)
        assert config.build_config()["MAIL_SUPPRESS_SEND"] is True
        monkeypatch.setenv("EMAIL_ADDRESS", "a@example.com")
        monkeypatch.setenv("EMAIL_PASSWORD", "secret")
        assert config.build_config()["MAIL_SUPPRESS_SEND"] is False

    @pytest.mark.parametrize(
        "value, expected", [("1", True), ("true", True), ("ON", True), ("0", False), ("no", False), ("", False)]
    )
    def test_env_bool(self, monkeypatch, value, expected):
        monkeypatch.setenv("SOME_FLAG", value)
        assert config.env_bool("SOME_FLAG") is expected

    def test_invalid_integer_env_raises(self, monkeypatch):
        monkeypatch.setenv("SMTP_PORT", "abc")
        with pytest.raises(RuntimeError):
            config.build_config()

    def test_production_refuses_to_start_without_secret(self, monkeypatch):
        monkeypatch.delenv("FLASK_SECRET_KEY", raising=False)
        monkeypatch.delenv("FLASK_DEBUG", raising=False)
        with pytest.raises(RuntimeError, match="FLASK_SECRET_KEY"):
            create_app({"SQLALCHEMY_DATABASE_URI": TEST_DATABASE_URL}, load_env=False)

    def test_debug_mode_uses_temporary_secret(self, monkeypatch):
        monkeypatch.delenv("FLASK_SECRET_KEY", raising=False)
        app = create_app({"SQLALCHEMY_DATABASE_URI": TEST_DATABASE_URL, "DEBUG": True}, load_env=False)
        assert len(app.config["SECRET_KEY"]) == 64
        dispose(app)

    def test_database_url_is_required(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        with pytest.raises(RuntimeError, match="DATABASE_URL is not set"):
            create_app({"SECRET_KEY": "k"}, load_env=False)

    @pytest.mark.parametrize("url", ["sqlite:///bloodconnect.db", "mysql://u:p@localhost/db"])
    def test_only_postgresql_is_accepted(self, url):
        with pytest.raises(RuntimeError, match="requires PostgreSQL"):
            create_app({"SECRET_KEY": "k", "SQLALCHEMY_DATABASE_URI": url}, load_env=False)

    def test_postgres_url_uses_pure_python_driver(self):
        app = create_app({"SECRET_KEY": "k", "SQLALCHEMY_DATABASE_URI": TEST_DATABASE_URL}, load_env=False)
        assert app.config["SQLALCHEMY_DATABASE_URI"].startswith("postgresql+pg8000://")
        dispose(app)

    @pytest.mark.parametrize(
        "url, expected_url, tls",
        [
            (
                "postgresql+pg8000://u:p@ep-x.neon.tech/db?sslmode=require&channel_binding=require",
                "postgresql+pg8000://u:p@ep-x.neon.tech/db",
                True,
            ),
            ("postgresql+pg8000://u:p@h/db?sslmode=disable", "postgresql+pg8000://u:p@h/db", False),
            (
                "postgresql+pg8000://u:p@h/db?application_name=bc",
                "postgresql+pg8000://u:p@h/db?application_name=bc",
                False,
            ),
            ("postgresql+pg8000://u:p@h/db", "postgresql+pg8000://u:p@h/db", False),
        ],
    )
    def test_libpq_tls_options_are_translated_for_pg8000(self, url, expected_url, tls):
        assert config.split_tls_options(url) == (expected_url, tls)

    def test_hosted_database_url_turns_on_tls(self):
        app = Flask("tls-test")
        app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql://u:p@ep-x.neon.tech/db?sslmode=require"
        app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True}
        _configure_database(app)
        assert app.config["SQLALCHEMY_DATABASE_URI"] == "postgresql+pg8000://u:p@ep-x.neon.tech/db"
        options = app.config["SQLALCHEMY_ENGINE_OPTIONS"]
        assert options["pool_pre_ping"] is True
        assert isinstance(options["connect_args"]["ssl_context"], ssl.SSLContext)

    def test_brevo_key_turns_on_real_sending(self, monkeypatch):
        for name in ("EMAIL_ADDRESS", "EMAIL_PASSWORD", "MAIL_SUPPRESS_SEND", "MAIL_FROM_EMAIL"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("BREVO_API_KEY", "xkeysib-test")
        monkeypatch.setenv("MAIL_FROM_EMAIL", "alerts@example.org")
        settings = config.build_config()
        assert settings["MAIL_SUPPRESS_SEND"] is False
        assert settings["MAIL_FROM_EMAIL"] == "alerts@example.org"

    def test_gmail_api_credentials_turn_on_real_sending(self, monkeypatch):
        for name in ("EMAIL_PASSWORD", "BREVO_API_KEY", "MAIL_SUPPRESS_SEND", "GMAIL_SENDER", "GMAIL_REFRESH_TOKEN"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("EMAIL_ADDRESS", "bloodconnectapp@gmail.com")
        monkeypatch.setenv("GMAIL_CLIENT_ID", "123.apps.googleusercontent.com")
        monkeypatch.setenv("GMAIL_CLIENT_SECRET", "GOCSPX-test")
        assert config.build_config()["MAIL_SUPPRESS_SEND"] is True  # incomplete: still demo mode
        monkeypatch.setenv("GMAIL_REFRESH_TOKEN", "1//refresh")
        settings = config.build_config()
        assert settings["MAIL_SUPPRESS_SEND"] is False
        assert settings["GMAIL_SENDER"] == "bloodconnectapp@gmail.com"  # falls back to EMAIL_ADDRESS

    def test_proxy_headers_are_trusted_only_when_configured(self):
        direct = create_app({"SECRET_KEY": "k", "SQLALCHEMY_DATABASE_URI": TEST_DATABASE_URL}, load_env=False)
        assert not isinstance(direct.wsgi_app, ProxyFix)
        dispose(direct)
        proxied = create_app(
            {"SECRET_KEY": "k", "SQLALCHEMY_DATABASE_URI": TEST_DATABASE_URL, "TRUST_PROXY_HOPS": 1}, load_env=False
        )
        assert isinstance(proxied.wsgi_app, ProxyFix)
        with proxied.test_request_context(
            "/", headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": "bc.example"}
        ):
            from flask import request

            assert request.url_root == "http://localhost/"  # test contexts bypass WSGI middleware
        response = proxied.test_client().get(
            "/donor/login", headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": "bc.example"}
        )
        assert response.status_code == 200
        dispose(proxied)

    def test_debug_is_off_by_default(self, monkeypatch):
        monkeypatch.delenv("FLASK_DEBUG", raising=False)
        assert config.build_config()["DEBUG"] is False
