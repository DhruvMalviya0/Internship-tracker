"""Sends sign-in links and account notices over SMTP (works with a Gmail app password, Resend/Brevo SMTP, etc.).

Env: SMTP_HOST, SMTP_PORT (587 STARTTLS or 465 SSL), SMTP_USER, SMTP_PASSWORD, EMAIL_FROM.
For local testing, EMAIL_DEV_LOG=1 logs emails instead of sending them."""
from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

log = logging.getLogger(__name__)

PLACEHOLDERS = {"", "-", "none", "todo", "tbd", "x"}


def _env(name: str) -> str:
    """Env var value; placeholders like "-" (Render forms may require some value) count as unset."""
    value = os.environ.get(name, "").strip()
    return "" if value.lower() in PLACEHOLDERS else value


def dev_log_mode() -> bool:
    return _env("EMAIL_DEV_LOG") == "1"


def configured() -> bool:
    return dev_log_mode() or all(_env(k) for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "EMAIL_FROM"))


def send(to: str, subject: str, text: str, html: str) -> None:
    if dev_log_mode():
        log.warning("EMAIL_DEV_LOG: email to %s: %s\n%s", to, subject, text)
        return
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = _env("EMAIL_FROM")
    msg["To"] = to
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
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


def _button(link: str, label: str) -> str:
    return (f'<p><a href="{link}" style="display:inline-block;padding:10px 18px;background:#2a78d6;color:#fff;'
            f'border-radius:8px;text-decoration:none">{label}</a></p>')


def send_login_link(to: str, link: str, app_name: str = "Internship Tracker") -> None:
    send(to, f"Your sign-in link for {app_name}",
         f"Click to sign in to {app_name}:\n\n{link}\n\n"
         "The link works once and expires in 15 minutes. If you didn't ask for it, ignore this email.",
         f"<p>Click to sign in to <b>{app_name}</b>:</p>{_button(link, 'Sign in')}"
         '<p style="color:#666;font-size:13px">The link works once and expires in 15 minutes. '
         "If you didn't ask for it, ignore this email.</p>")


def send_pause_notice(to: str, name: str, days: int, link: str, app_name: str = "Internship Tracker") -> None:
    hello = f"Hi {name}," if name else "Hi,"
    send(to, f"Your daily internship search is paused ({app_name})",
         f"{hello}\n\nYou haven't opened {app_name} in {days} days, so we've paused the daily search for your "
         "account. Your tracked internships, notes and drafts are all kept.\n\n"
         f"Sign in any time to turn it back on:\n{link}\n",
         f"<p>{hello}</p><p>You haven't opened <b>{app_name}</b> in {days} days, so we've paused the daily search "
         "for your account. Your tracked internships, notes and drafts are all kept.</p>"
         f"<p>Sign in any time and it switches back on automatically:</p>{_button(link, 'Turn my search back on')}")
