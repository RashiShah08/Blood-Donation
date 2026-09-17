"""Server-side form validation. Browser checks are a convenience; these are the rules."""

import re
from collections.abc import Iterable, Mapping
from datetime import date, datetime

from .domain import geo
from .domain.blood import normalize_blood_group

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^\+?[0-9 ()-]{7,20}$")
PINCODE_RE = re.compile(r"^[A-Za-z0-9 -]{3,10}$")
MIN_PASSWORD_LENGTH = 8


class FormValidator:
    """Collects cleaned values and per-field error messages from a submitted form."""

    def __init__(self, form: Mapping[str, str]):
        self.form = form
        self.data: dict = {}
        self.errors: dict[str, str] = {}

    @property
    def is_valid(self) -> bool:
        return not self.errors

    def _raw(self, key: str) -> str:
        value = self.form.get(key, "")
        return value.strip() if isinstance(value, str) else ""

    def text(self, key: str, label: str, *, required: bool = True, min_len: int = 1, max_len: int = 255) -> str | None:
        value = " ".join(self._raw(key).split())
        if not value:
            if required:
                self.errors[key] = f"{label} is required."
            self.data[key] = None
            return None
        if len(value) < min_len:
            self.errors[key] = f"{label} must be at least {min_len} characters."
        elif len(value) > max_len:
            self.errors[key] = f"{label} must be at most {max_len} characters."
        self.data[key] = value
        return value

    def email(self, key: str = "email") -> str | None:
        value = self._raw(key).lower()
        if not value:
            self.errors[key] = "Email is required."
        elif len(value) > 254 or not EMAIL_RE.match(value):
            self.errors[key] = "Enter a valid email address."
        self.data[key] = value
        return value

    def new_password(self, key: str = "password", confirm_key: str = "confirm_password") -> str | None:
        value = self.form.get(key, "")
        if len(value) < MIN_PASSWORD_LENGTH:
            self.errors[key] = f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
        elif len(value) > 128:
            self.errors[key] = "Password must be at most 128 characters."
        elif value != self.form.get(confirm_key, ""):
            self.errors[confirm_key] = "Passwords don't match."
        self.data[key] = value
        return value

    def phone(self, key: str = "phone", *, required: bool = True) -> str | None:
        value = self._raw(key)
        if not value:
            if required:
                self.errors[key] = "Phone number is required."
            self.data[key] = None
            return None
        if not PHONE_RE.match(value):
            self.errors[key] = "Enter a valid phone number."
        self.data[key] = value
        return value

    def pincode(self, key: str = "pincode") -> str | None:
        value = self._raw(key)
        if not PINCODE_RE.match(value):
            self.errors[key] = "Enter a valid pincode."
        self.data[key] = value
        return value

    def choice(self, key: str, label: str, choices: Iterable[str]) -> str | None:
        value = self._raw(key)
        if value not in set(choices):
            self.errors[key] = f"Select a valid {label.lower()}."
        self.data[key] = value
        return value

    def blood_group(self, key: str = "blood_group") -> str | None:
        value = normalize_blood_group(self._raw(key))
        if value is None:
            self.errors[key] = "Select a valid blood group."
        self.data[key] = value
        return value

    def number(
        self, key: str, label: str, *, minimum: float, maximum: float, required: bool = True, integer: bool = False
    ) -> float | int | None:
        raw = self._raw(key)
        if not raw:
            if required:
                self.errors[key] = f"{label} is required."
            self.data[key] = None
            return None
        try:
            value = int(raw) if integer else float(raw)
        except ValueError:
            self.errors[key] = f"{label} must be a {'whole ' if integer else ''}number."
            self.data[key] = None
            return None
        if not (minimum <= value <= maximum):
            self.errors[key] = f"{label} must be between {minimum:g} and {maximum:g}."
        self.data[key] = value
        return value

    def past_date(self, key: str, label: str, *, today: date, required: bool = True) -> date | None:
        raw = self._raw(key)
        if not raw:
            if required:
                self.errors[key] = f"{label} is required."
            self.data[key] = None
            return None
        try:
            value = datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            self.errors[key] = f"{label} must be a valid date."
            self.data[key] = None
            return None
        if value > today or value.year < 1900:
            self.errors[key] = f"{label} can't be in the future."
        self.data[key] = value
        return value

    def coordinates(self, lat_key: str = "latitude", lon_key: str = "longitude") -> tuple[float, float] | None:
        try:
            lat = float(self._raw(lat_key))
            lon = float(self._raw(lon_key))
        except ValueError:
            lat = lon = None
        if lat is None or not geo.is_valid_coordinate(lat, lon):
            self.errors["location"] = "Choose a location on the map or use your current location."
            self.data[lat_key] = self.data[lon_key] = None
            return None
        self.data[lat_key], self.data[lon_key] = lat, lon
        return lat, lon
