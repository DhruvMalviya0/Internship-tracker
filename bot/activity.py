"""Pauses the daily search for accounts nobody has used in a while (cloud mode), and emails them once.

Env: INACTIVE_DAYS (default 14), PUBLIC_URL (for the sign-in link), SMTP_* (to send the email)."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from . import db, mailer
from .mailer import _env as env

log = logging.getLogger(__name__)


def inactive_days() -> int:
    try:
        return max(1, int(env("INACTIVE_DAYS") or 14))
    except ValueError:
        return 14


def apply_inactivity(conn, users: list[dict]) -> tuple[list[dict], list[str]]:
    """Returns (users to search for, notes for the run log). Pauses newly inactive accounts and emails them."""
    days = inactive_days()
    cutoff = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    active, notes = [], []
    newly, still = 0, 0
    for u in users:
        if u.get("paused_at"):
            still += 1
            continue
        if db.last_activity(u) and db.last_activity(u) < cutoff:
            sent = False
            if mailer.configured():
                link = (env("PUBLIC_URL") or "").rstrip("/") + "/login"
                try:
                    mailer.send_pause_notice(u["email"], (u.get("name") or "").split(" ")[0], days, link,
                                             env("APP_NAME") or "Internship Tracker")
                    sent = True
                except Exception:  # a mail problem must not stop the run
                    log.exception("Could not send the pause email to user #%s", u["id"])
            db.pause_user(conn, u["id"], sent)
            newly += 1
            continue
        active.append(u)
    if newly:
        notes.append(f"Paused the daily search for {newly} account{'s' if newly != 1 else ''} inactive for "
                     f"{days}+ days{' (emailed)' if mailer.configured() else ' (email not set up, so no notice sent)'}")
    if still:
        notes.append(f"{still} paused account{'s' if still != 1 else ''} skipped until they sign in again")
    return active, notes
