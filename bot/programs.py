"""Watches seasonal open-source programs so you know when applications open.

- Google Summer of Code: the program's public API (summerofcode.withgoogle.com/api/program/) with every
  milestone date.
- LFX Mentorship (CNCF projects): the term table in github.com/cncf/mentoring (LFX's own site blocks bots).

Results are stored in the `programs` table and shown on the Platforms page."""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone

from . import db
from .http_client import HttpClient, RobotsDisallowed

GSOC_API = "https://summerofcode.withgoogle.com/api/program/"
LFX_README = "https://raw.githubusercontent.com/cncf/mentoring/main/programs/lfx-mentorship/README.md"
CHECK_EVERY_HOURS = 24

SCHEMA = """
CREATE TABLE IF NOT EXISTS programs (
    key TEXT PRIMARY KEY,
    name TEXT,
    status TEXT,
    detail TEXT,
    next_label TEXT,
    next_date TEXT,
    url TEXT,
    checked_at TEXT,
    error TEXT
);
"""


def _dt(s: str) -> datetime | None:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


def gsoc(http: HttpClient) -> dict:
    res = http.get_json(GSOC_API)
    if not res.ok or not isinstance(res.data, list) or not res.data:
        raise RuntimeError(res.error or "unexpected response")
    latest = max(res.data, key=lambda p: p.get("slug", ""))
    now = datetime.now(timezone.utc)
    milestones = sorted((m for m in latest.get("milestones") or [] if _dt(m.get("datetime"))),
                        key=lambda m: _dt(m["datetime"]))
    upcoming = [m for m in milestones if _dt(m["datetime"]) > now and not m.get("project_size")]
    proposals_open = next((m for m in milestones if m.get("name") == "contributor_proposals_open"), None)
    phase = (latest.get("phase") or "").replace("_", " ")
    applying = latest.get("phase") in ("contributor_proposals_open", "student_application_period")
    if latest.get("is_active") and not applying:
        hint = ""
        if proposals_open:
            d = _dt(proposals_open["datetime"])
            hint = (f" Next year's program isn't announced yet; in {latest['slug']} contributor proposals opened "
                    f"{d:%B %d} (expect a similar window).")
        status, detail = f"GSoC {latest['slug']}: {phase}", f"Applications are closed for {latest['slug']}.{hint}"
    elif applying:
        status, detail = f"GSoC {latest['slug']}: proposals OPEN", "Contributor proposals are open now. Apply on the site."
    else:
        status, detail = f"GSoC {latest['slug']}: {phase or 'archived'}", "Waiting for the next program to be announced."
    nxt = upcoming[0] if upcoming else None
    return {"key": "gsoc", "name": "Google Summer of Code", "status": status, "detail": detail,
            "next_label": nxt["label"] if nxt else "", "next_date": nxt["datetime"][:10] if nxt else "",
            "url": "https://summerofcode.withgoogle.com/"}


def lfx(http: HttpClient) -> dict:
    res = http.get_text(LFX_README, accept="text/plain, */*")
    if not res.ok:
        raise RuntimeError(res.error)
    text = res.data.decode("utf-8", "replace")
    rows = []
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 3 and re.fullmatch(r"\d{4}", cells[0]):
            rows.append(cells)
    if not rows:
        raise RuntimeError("term table not found (format changed?)")
    year, term, status = rows[0][:3]
    open_now = re.search(r"open|accepting", status, re.I)
    return {"key": "lfx", "name": "LFX Mentorship (CNCF)", "status": f"{year} {term}: {status} (per the CNCF repo)",
            "detail": ("Mentee applications look OPEN. Apply on the LFX site." if open_now else
                       "Three terms a year (Mar–May, Jun–Aug, Sep–Nov); mentee applications usually open a few weeks "
                       "before each term starts. Check the LFX site for project lists."),
            "next_label": "", "next_date": "", "url": "https://mentorship.lfx.linuxfoundation.org/"}


CHECKS = [gsoc, lfx]


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def due(conn: sqlite3.Connection) -> bool:
    ensure_schema(conn)
    row = conn.execute("SELECT MIN(checked_at) t FROM programs").fetchone()
    if not row or not row["t"]:
        return True
    return (datetime.now() - datetime.fromisoformat(row["t"])).total_seconds() > CHECK_EVERY_HOURS * 3600


def check_all(conn: sqlite3.Connection, http: HttpClient) -> list[dict]:
    ensure_schema(conn)
    results = []
    for fn in CHECKS:
        try:
            r = fn(http)
            r["error"] = ""
        except (RuntimeError, RobotsDisallowed, ValueError, KeyError) as exc:
            r = {"key": fn.__name__, "name": fn.__name__, "status": "", "detail": "", "next_label": "",
                 "next_date": "", "url": "", "error": str(exc)}
            prev = conn.execute("SELECT * FROM programs WHERE key=?", (fn.__name__,)).fetchone()
            if prev:  # keep the last good status, just record the error
                r = {**dict(prev), "error": str(exc)}
        r["checked_at"] = db.now_iso()
        conn.execute(
            "INSERT INTO programs(key, name, status, detail, next_label, next_date, url, checked_at, error) "
            "VALUES (:key, :name, :status, :detail, :next_label, :next_date, :url, :checked_at, :error) "
            "ON CONFLICT(key) DO UPDATE SET name=excluded.name, status=excluded.status, detail=excluded.detail, "
            "next_label=excluded.next_label, next_date=excluded.next_date, url=excluded.url, "
            "checked_at=excluded.checked_at, error=excluded.error", r)
        results.append(r)
    conn.commit()
    return results


def all_status(conn: sqlite3.Connection) -> dict:
    ensure_schema(conn)
    return {r["key"]: dict(r) for r in conn.execute("SELECT * FROM programs")}
