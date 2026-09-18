"""Email delivery and donor alerts."""

import base64
import json
import logging
import smtplib
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formataddr

from ..extensions import db
from ..models import URGENCY_LEVELS, BloodRequest, Notification
from .matching import DonorMatch, active_pledge_donor_ids, notified_donor_ids

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class OutgoingEmail:
    to: str
    subject: str
    body: str


@dataclass
class SendReport:
    sent: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    simulated: bool = False


@dataclass
class NotifyOutcome:
    sent: int
    failed: int
    skipped: int
    simulated: bool

    @property
    def success(self) -> bool:
        return self.sent > 0 and self.failed == 0


BREVO_SEND_URL = "https://api.brevo.com/v3/smtp/email"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 (a URL, not a secret)
GMAIL_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"


class EmailProviderError(Exception):
    """The email provider refused the credentials, so nothing in the batch can be sent."""


def _post_json(url: str, payload: dict, headers: dict, timeout: int) -> int:
    request = urllib.request.Request(  # noqa: S310 (fixed https URL)
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={**headers, "Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return response.status
    except urllib.error.HTTPError as exc:
        # The provider's reason (for example "sender not verified") is what makes a failure fixable.
        log.warning("Email API refused a request: HTTP %s %s", exc.code, exc.read(500).decode("utf-8", "replace"))
        return exc.code


def _http_request(url: str, data: bytes, headers: dict, timeout: int) -> tuple[int, dict]:
    """POST and return the status and JSON body, including for error responses."""
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")  # noqa: S310 (fixed https URL)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as exc:
        status, raw = exc.code, exc.read()
    try:
        return status, json.loads(raw or b"{}")
    except ValueError:
        return status, {"error": raw[:200].decode("utf-8", "replace")}


def _google_error(body: dict) -> str:
    error = body.get("error")
    if isinstance(error, dict):  # Gmail API errors
        return str(error.get("message") or error.get("status") or error)
    return str(body.get("error_description") or error or body)


class Mailer:
    """Sends a batch of emails through the Gmail API, Brevo's API or one SMTP connection.

    The first configured provider wins, in that order. The Gmail API uses a send-only OAuth
    token, so a leaked credential can't read the mailbox. With MAIL_SUPPRESS_SEND (the default
    when nothing is configured) emails are logged instead of sent, so the app can be demoed.
    """

    def __init__(
        self,
        config: dict,
        smtp_factory: Callable | None = None,
        http_post: Callable | None = None,
        gmail_http: Callable | None = None,
    ):
        self.config = config
        self.smtp_factory = smtp_factory or self._connect
        self.http_post = http_post or _post_json
        self.gmail_http = gmail_http or _http_request
        self._gmail_token: tuple[str, float] | None = None  # access token and when it expires

    def _connect(self, host: str, port: int, timeout: int):
        context = ssl.create_default_context()
        if port == 465:
            return smtplib.SMTP_SSL(host, port, timeout=timeout, context=context)
        server = smtplib.SMTP(host, port, timeout=timeout)
        server.starttls(context=context)
        return server

    def _build(self, email: OutgoingEmail, sender: str | None = None) -> EmailMessage:
        message = EmailMessage()
        message["From"] = formataddr((self.config["MAIL_FROM_NAME"], sender or self.config["EMAIL_ADDRESS"]))
        message["To"] = email.to
        message["Subject"] = email.subject
        message.set_content(email.body)
        return message

    def send(self, emails: list[OutgoingEmail]) -> SendReport:
        if not emails:
            return SendReport()
        if self.config.get("MAIL_SUPPRESS_SEND"):
            for email in emails:
                log.info("Email sending disabled. Would send %r:\n%s", email.subject, email.body)
            return SendReport(sent=[e.to for e in emails], simulated=True)
        if self._gmail_api_configured():
            return self._send_with_gmail_api(emails)
        if self.config.get("BREVO_API_KEY"):
            return self._send_with_brevo(emails)

        report = SendReport()
        try:
            with self.smtp_factory(
                self.config["SMTP_HOST"], self.config["SMTP_PORT"], self.config["SMTP_TIMEOUT_SECONDS"]
            ) as server:
                server.login(self.config["EMAIL_ADDRESS"], self.config["EMAIL_PASSWORD"])
                for email in emails:
                    try:
                        server.send_message(self._build(email))
                        report.sent.append(email.to)
                    except smtplib.SMTPException:
                        log.warning("Failed to send an email (subject %r)", email.subject)
                        report.failed.append(email.to)
        except (smtplib.SMTPException, OSError):
            log.exception("SMTP connection failed")
            done = set(report.sent) | set(report.failed)
            report.failed.extend(e.to for e in emails if e.to not in done)
        return report

    def _send_with_brevo(self, emails: list[OutgoingEmail]) -> SendReport:
        report = SendReport()
        sender = {"name": self.config["MAIL_FROM_NAME"], "email": self.config["MAIL_FROM_EMAIL"]}
        headers = {"api-key": self.config["BREVO_API_KEY"]}
        for email in emails:
            payload = {
                "sender": sender,
                "to": [{"email": email.to}],
                "subject": email.subject,
                "textContent": email.body,
            }
            try:
                status = self.http_post(BREVO_SEND_URL, payload, headers, self.config["SMTP_TIMEOUT_SECONDS"])
            except (urllib.error.URLError, OSError, ValueError) as exc:
                log.warning("Brevo could not send an email (subject %r): %s", email.subject, exc)
                report.failed.append(email.to)
                continue
            (report.sent if 200 <= status < 300 else report.failed).append(email.to)
        return report

    def _gmail_api_configured(self) -> bool:
        return all(self.config.get(key) for key in ("GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN"))

    def _gmail_access_token(self) -> str:
        """A short-lived access token, reused until a minute before it expires."""
        if self._gmail_token and self._gmail_token[1] > time.monotonic() + 60:
            return self._gmail_token[0]
        form = urllib.parse.urlencode(
            {
                "client_id": self.config["GMAIL_CLIENT_ID"],
                "client_secret": self.config["GMAIL_CLIENT_SECRET"],
                "refresh_token": self.config["GMAIL_REFRESH_TOKEN"],
                "grant_type": "refresh_token",
            }
        ).encode("ascii")
        status, body = self.gmail_http(
            GOOGLE_TOKEN_URL,
            form,
            {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
            self.config["SMTP_TIMEOUT_SECONDS"],
        )
        if status != 200 or not body.get("access_token"):
            raise EmailProviderError(f"HTTP {status}: {_google_error(body)}")
        self._gmail_token = (body["access_token"], time.monotonic() + int(body.get("expires_in", 3600)))
        return self._gmail_token[0]

    def _send_with_gmail_api(self, emails: list[OutgoingEmail]) -> SendReport:
        report = SendReport()
        timeout = self.config["SMTP_TIMEOUT_SECONDS"]
        try:
            token = self._gmail_access_token()
        except (EmailProviderError, urllib.error.URLError, OSError, ValueError) as exc:
            log.error("Google refused the Gmail API credentials, so no email was sent: %s", exc)
            report.failed.extend(email.to for email in emails)
            return report
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        for email in emails:
            message = self._build(email, sender=self.config.get("GMAIL_SENDER"))
            raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
            try:
                status, body = self.gmail_http(GMAIL_SEND_URL, json.dumps({"raw": raw}).encode(), headers, timeout)
            except (urllib.error.URLError, OSError, ValueError) as exc:
                log.warning("Gmail API could not send an email (subject %r): %s", email.subject, exc)
                report.failed.append(email.to)
                continue
            if 200 <= status < 300:
                report.sent.append(email.to)
            else:
                if status == 401:
                    self._gmail_token = None  # fetch a fresh token next time
                log.warning(
                    "Gmail API could not send an email (subject %r): HTTP %s %s",
                    email.subject,
                    status,
                    _google_error(body),
                )
                report.failed.append(email.to)
        return report


def _alert_email(match: DonorMatch, blood_request: BloodRequest, link: str) -> OutgoingEmail:
    hospital = blood_request.hospital
    urgency = blood_request.urgency.capitalize()
    body = (
        f"Hi {match.donor.name},\n\n"
        f"{hospital.name} in {hospital.city} needs {blood_request.blood_group} blood, "
        f"and your blood group ({match.donor.blood_group}) is compatible.\n\n"
        f"Urgency: {urgency}\n"
        f"Units still needed: {blood_request.units_remaining}\n"
        f"Distance from your saved location: about {match.distance_km:.1f} km\n\n"
        f"If you can help, check your eligibility and pledge here:\n{link}\n\n"
        "If you can't donate right now, you don't need to do anything. To stop these alerts, "
        "mark yourself as unavailable on your dashboard.\n\n"
        "Thank you,\nBloodConnect"
    )
    subject = f"[{urgency}] {blood_request.blood_group} blood needed at {hospital.name}"
    return OutgoingEmail(match.donor.email, subject, body)


def notify_donors(
    mailer: Mailer,
    blood_request: BloodRequest,
    matches: list[DonorMatch],
    link_for_request: Callable[[int], str],
    limit: int,
) -> NotifyOutcome:
    """Email matched donors who haven't been alerted about (or pledged for) this request yet."""
    skip_ids = notified_donor_ids(blood_request.id) | active_pledge_donor_ids(blood_request.id)
    targets = [m for m in matches if m.donor.id not in skip_ids][:limit]
    skipped = len(matches) - len(targets)
    if not targets:
        return NotifyOutcome(sent=0, failed=0, skipped=skipped, simulated=bool(mailer.config.get("MAIL_SUPPRESS_SEND")))

    link = link_for_request(blood_request.id)
    report = mailer.send([_alert_email(m, blood_request, link) for m in targets])
    delivered = set(report.sent)
    for match in targets:
        if match.donor.email in delivered:
            db.session.add(Notification(request_id=blood_request.id, donor_id=match.donor.id))
    db.session.commit()
    log.info(
        "Request %s: %d alert(s) sent, %d failed, %d skipped",
        blood_request.id,
        len(report.sent),
        len(report.failed),
        skipped,
    )
    return NotifyOutcome(sent=len(report.sent), failed=len(report.failed), skipped=skipped, simulated=report.simulated)


def password_reset_email(to: str, name: str, link: str) -> OutgoingEmail:
    body = (
        f"Hi {name},\n\n"
        "Someone asked to reset the password for your BloodConnect account. "
        f"If it was you, choose a new password within the next hour:\n{link}\n\n"
        "If you didn't ask for this, you can ignore this email. Your password won't change.\n\n"
        "BloodConnect"
    )
    return OutgoingEmail(to, "Reset your BloodConnect password", body)


URGENCY_LABELS = {level: level.capitalize() for level in URGENCY_LEVELS}
