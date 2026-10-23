"""Sends the three emails (review alert, weekly summary, failure notice) plus login
delivery. Uses Resend or SMTP when configured; otherwise, or when no recipient is
set, writes the email to the outbox folder (.eml + .html). Every email is logged
in the database either way, so enabling real sending later is a config change."""
from __future__ import annotations

import base64
import re
import smtplib
import time
from datetime import datetime
from email.message import EmailMessage
from email.utils import parseaddr
from pathlib import Path

import httpx
from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.retry import RetryError, with_retry
from src.settings import settings

TEMPLATES = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"),
                        autoescape=select_autoescape(["html"]))


def render(template: str, **context) -> str:
    return TEMPLATES.get_template(template).render(app_url=settings.app_url, **context)


class Mailer:
    def __init__(self, store=None, sleep=time.sleep, transport: httpx.BaseTransport | None = None):
        self.store = store
        self.sleep = sleep
        self.transport = transport

    @property
    def provider(self) -> str:
        if settings.email_provider == "smtp" and settings.smtp_host:
            return "smtp"
        if settings.resend_api_key and settings.email_provider in ("", "resend"):
            return "resend"
        return "outbox"

    def send(self, kind: str, recipients: list[str], subject: str, html: str,
             attachments: list[tuple[str, bytes]] | None = None, run_id: int | None = None,
             keep_body: bool = True) -> str:
        """Send (or write to the outbox). Returns SENT, OUTBOX or FAILED.
        keep_body=False keeps the body out of the database (used for login emails)."""
        attachments = attachments or []
        recipients = [r for r in recipients if r]
        status, detail = "OUTBOX", ""
        if recipients and self.provider != "outbox":
            try:
                detail = with_retry(lambda: self._deliver(recipients, subject, html, attachments),
                                    label=f"email:{kind}", sleep=self.sleep)
                status = "SENT"
            except RetryError as exc:
                status, detail = "FAILED", _scrub(str(exc))
        else:
            detail = "No recipient configured" if not recipients else "No email provider configured"
        if status != "SENT":
            path = self._write_outbox(kind, recipients, subject, html, attachments)
            detail = f"{detail}. Saved to outbox: {path.name}" if path else detail
        if self.store is not None:
            self.store.log_email(kind, recipients, subject, status, detail,
                                 html if keep_body else "", run_id)
        return status

    # -- providers ---------------------------------------------------------
    def _deliver(self, recipients, subject, html, attachments) -> str:
        if self.provider == "smtp":
            msg = _build_message(settings.email_from, recipients, subject, html, attachments)
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
                smtp.starttls()
                if settings.smtp_user:
                    smtp.login(settings.smtp_user, settings.smtp_password)
                smtp.send_message(msg)
            return "Sent via SMTP"
        payload = {"from": settings.email_from, "to": recipients, "subject": subject, "html": html}
        if attachments:
            payload["attachments"] = [{"filename": n, "content": base64.b64encode(b).decode()}
                                      for n, b in attachments]
        with httpx.Client(transport=self.transport, timeout=30) as client:
            resp = client.post("https://api.resend.com/emails", json=payload,
                               headers={"Authorization": f"Bearer {settings.resend_api_key}"})
        if resp.status_code >= 300:
            raise RuntimeError(f"Resend returned {resp.status_code}: {resp.text[:300]}")
        return f"Sent via Resend (id {resp.json().get('id', '?')})"

    def _write_outbox(self, kind, recipients, subject, html, attachments) -> Path | None:
        try:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            base = settings.outbox_dir / f"{stamp}-{kind}"
            msg = _build_message(settings.email_from, recipients or ["(no recipient set)"], subject, html, attachments)
            base.with_suffix(".eml").write_bytes(bytes(msg))
            base.with_suffix(".html").write_text(html, encoding="utf-8")
            return base.with_suffix(".eml")
        except OSError:
            return None


def _build_message(sender, recipients, subject, html, attachments) -> EmailMessage:
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = sender, ", ".join(recipients), subject
    msg.set_content(re.sub(r"<[^>]+>", "", html))
    msg.add_alternative(html, subtype="html")
    for name, data in attachments:
        msg.add_attachment(data, maintype="application",
                           subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet", filename=name)
    return msg


def _scrub(text: str) -> str:
    """Keep secrets out of logged error messages."""
    for secret in (settings.resend_api_key, settings.smtp_password):
        if secret:
            text = text.replace(secret, "***")
    return text


def sender_address() -> str:
    return parseaddr(settings.email_from)[1]
