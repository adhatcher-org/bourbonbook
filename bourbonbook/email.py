from __future__ import annotations

import asyncio
import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from html import escape
from pathlib import Path
from typing import Protocol

from bourbonbook.config import Settings

logger = logging.getLogger(__name__)
EMAIL_TEMPLATES = Path(__file__).parent / "templates" / "email"


@dataclass(frozen=True)
class OutgoingEmail:
    recipient: str
    subject: str
    text: str
    html: str


class EmailSender(Protocol):
    async def send(self, message: OutgoingEmail) -> None: ...


class MemoryEmailSender:
    def __init__(self) -> None:
        self.messages: list[OutgoingEmail] = []

    async def send(self, message: OutgoingEmail) -> None:
        self.messages.append(message)


class CaptureEmailSender(MemoryEmailSender):
    async def send(self, message: OutgoingEmail) -> None:
        await super().send(message)
        logger.info(
            "Captured email delivery recipient_domain=%s", message.recipient.rpartition("@")[2]
        )


class SMTPEmailSender:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def send(self, message: OutgoingEmail) -> None:
        await asyncio.to_thread(self._send_sync, message)

    def _send_sync(self, outgoing: OutgoingEmail) -> None:
        settings = self.settings
        message = EmailMessage()
        message["Subject"] = outgoing.subject
        message["From"] = f"{settings.smtp_from_name} <{settings.smtp_from_email}>"
        message["To"] = outgoing.recipient
        message.set_content(outgoing.text)
        message.add_alternative(outgoing.html, subtype="html")
        smtp_class = smtplib.SMTP_SSL if settings.smtp_tls_mode == "ssl" else smtplib.SMTP
        with smtp_class(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
            if settings.smtp_tls_mode == "starttls":
                smtp.starttls(context=ssl.create_default_context())
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password or "")
            smtp.send_message(message)


def create_email_sender(settings: Settings) -> EmailSender:
    return (
        SMTPEmailSender(settings)
        if settings.email_delivery_mode == "smtp"
        else CaptureEmailSender()
    )


def link_message(recipient: str, purpose: str, url: str, expiry: str) -> OutgoingEmail:
    verification = purpose == "verify_email"
    action = "Verify your email" if verification else "Reset your password"
    subject = f"{action} for Bourbon Book"
    template_name = "verification" if verification else "password_reset"
    text = (
        (EMAIL_TEMPLATES / f"{template_name}.txt")
        .read_text()
        .format(action=action, url=url, expiry=expiry)
    )
    html = (
        (EMAIL_TEMPLATES / f"{template_name}.html")
        .read_text()
        .format(action=escape(action), url=escape(url, quote=True), expiry=escape(expiry))
    )
    return OutgoingEmail(recipient, subject, text, html)


def security_message(recipient: str) -> OutgoingEmail:
    return OutgoingEmail(
        recipient,
        "Your Bourbon Book password changed",
        "Your password was changed. If this was not you, contact your administrator.",
        "<p>Your password was changed. If this was not you, contact your administrator.</p>",
    )
