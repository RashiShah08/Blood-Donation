"""Email delivery and donor alerts."""

import json
import logging
import smtplib
import ssl
import urllib.error
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


def _post_json(url: str, payload: dict, headers: dict, timeout: int) -> int:
    request = urllib.request.Request(  # noqa: S310 (fixed https URL)
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={**headers, "Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return response.status


class Mailer:
    """Sends a batch of emails, over one SMTP connection or through Brevo's HTTPS API.

    Brevo is used when BREVO_API_KEY is set, for hosts that block SMTP ports. With
    MAIL_SUPPRESS_SEND (the default when no credentials are configured) emails are
    logged instead of sent, so the app can be demoed without an account.
    """

    def __init__(self, config: dict, smtp_factory: Callable | None = None, http_post: Callable | None = None):
        self.config = config
        self.smtp_factory = smtp_factory or self._connect
        self.http_post = http_post or _post_json

    def _connect(self, host: str, port: int, timeout: int):
        context = ssl.create_default_context()
        if port == 465:
            return smtplib.SMTP_SSL(host, port, timeout=timeout, context=context)
        server = smtplib.SMTP(host, port, timeout=timeout)
        server.starttls(context=context)
        return server

    def _build(self, email: OutgoingEmail) -> EmailMessage:
        message = EmailMessage()
        message["From"] = formataddr((self.config["MAIL_FROM_NAME"], self.config["EMAIL_ADDRESS"]))
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
