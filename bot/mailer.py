"""Sends sign-in links over SMTP (works with a Gmail app password, Resend/Brevo SMTP, etc.).

Env: SMTP_HOST, SMTP_PORT (587 STARTTLS or 465 SSL), SMTP_USER, SMTP_PASSWORD, EMAIL_FROM.
For local testing, EMAIL_DEV_LOG=1 logs the link instead of sending it."""
from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

log = logging.getLogger(__name__)


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def dev_log_mode() -> bool:
    return _env("EMAIL_DEV_LOG") == "1"


def configured() -> bool:
    return dev_log_mode() or all(_env(k) for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "EMAIL_FROM"))


def send_login_link(to: str, link: str, app_name: str = "Internship Tracker") -> None:
    if dev_log_mode():
        log.warning("EMAIL_DEV_LOG: sign-in link for %s: %s", to, link)
        return
    msg = EmailMessage()
    msg["Subject"] = f"Your sign-in link for {app_name}"
    msg["From"] = _env("EMAIL_FROM")
    msg["To"] = to
    msg.set_content(
        f"Click to sign in to {app_name}:\n\n{link}\n\n"
        "The link works once and expires in 15 minutes. If you didn't ask for it, ignore this email."
    )
    msg.add_alternative(
        f"""<p>Click to sign in to <b>{app_name}</b>:</p>
<p><a href="{link}" style="display:inline-block;padding:10px 18px;background:#2a78d6;color:#fff;border-radius:8px;text-decoration:none">Sign in</a></p>
<p style="color:#666;font-size:13px">The link works once and expires in 15 minutes. If you didn't ask for it, ignore this email.</p>""",
        subtype="html",
    )
    host, port = _env("SMTP_HOST"), int(_env("SMTP_PORT") or 587)
    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=context, timeout=20) as s:
            s.login(_env("SMTP_USER"), _env("SMTP_PASSWORD"))
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=20) as s:
            s.starttls(context=context)
            s.login(_env("SMTP_USER"), _env("SMTP_PASSWORD"))
            s.send_message(msg)
