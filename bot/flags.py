"""AI-screening, red-flag and night-shift detection, plus a cached domain-age check via RDAP."""
from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from .config import Company
from .fetchers.base import Job
from .http_client import HttpClient, RobotsDisallowed
from .textutil import compile_terms

log = logging.getLogger(__name__)

_FEE_RE = re.compile(
    r"\b(registration|training|joining|processing|application|onboarding|security|certification)\s+"
    r"(fee|fees|deposit|charges?)\b|\bpay (?:a|the|an) (?:fee|deposit)\b|\brefundable deposit\b",
    re.I,
)
_OFFPLATFORM_RE = re.compile(r"\b(whatsapp|telegram)\b|wa\.me/|t\.me/", re.I)
_FREE_EMAIL_RE = re.compile(r"[\w.+-]+@(gmail|yahoo|outlook|hotmail|proton|protonmail|rediffmail|ymail)\.\w+", re.I)
_INTERVIEW_RE = re.compile(r"\binterview", re.I)
_SECOND_LEVEL = {"co", "com", "org", "net", "ac", "gov", "edu"}


def ai_screening(job: Job, company: Company, profile: dict) -> list[str]:
    reasons = []
    rx = compile_terms(profile.get("ai_screening_terms", []))
    if rx:
        found = sorted({m.group(0).strip() for m in rx.finditer(f"{job.title}\n{job.description}")}, key=str.lower)
        reasons += [f"Mentions “{t}”" for t in found]
    if job.ai_disclaimer:
        reasons.append("Greenhouse posting carries an AI-use disclaimer")
    if company.has_ai_screening == "Yes":
        reasons.append(f"{company.name} is marked as using AI screening (Companies page)")
    return reasons


def night_shift(job: Job, profile: dict) -> bool:
    rx = compile_terms(profile["filters"].get("us_hours_phrases", []))
    return bool(rx and rx.search(f"{job.location}\n{job.description}"))


def red_flags(job: Job, company: Company, paid: str, domain_age_days: int | None, profile: dict,
              whitelisted: bool = True) -> list[str]:
    """whitelisted=False for companies found via a job feed: they have no website on file by design,
    so that is reported as a warning by the pipeline instead of a red flag."""
    text = f"{job.title}\n{job.description}"
    flags = []
    if m := _FEE_RE.search(text):
        flags.append(f"Asks for a fee (“{m.group(0)}”)")
    if m := _OFFPLATFORM_RE.search(text):
        flags.append(f"Contact via {m.group(0).strip('/').replace('wa.me', 'WhatsApp').replace('t.me', 'Telegram')}")
    if m := _FREE_EMAIL_RE.search(text):
        flags.append(f"Applications to a personal email ({m.group(0)})")
    if whitelisted and not company.website:
        flags.append("No company website on file")
    limit = profile.get("red_flags", {}).get("new_domain_days", 180)
    if domain_age_days is not None and domain_age_days < limit:
        flags.append(f"Company domain registered only {domain_age_days} days ago")
    if paid == "No" and not _INTERVIEW_RE.search(text):
        flags.append("Unpaid and no interview mentioned")
    return flags


def registrable_domain(website: str) -> str:
    if not website:
        return ""
    host = urlsplit(website if "//" in website else f"https://{website}").hostname or ""
    labels = host.lower().removeprefix("www.").split(".")
    if len(labels) >= 3 and labels[-2] in _SECOND_LEVEL and len(labels[-1]) == 2:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:]) if len(labels) >= 2 else ""


class DomainChecker:
    """Domain registration age from rdap.org, cached in SQLite for 30 days so each domain is checked rarely."""

    def __init__(self, conn: sqlite3.Connection, http: HttpClient | None, ttl_days: int = 30):
        self.conn, self.http, self.ttl = conn, http, timedelta(days=ttl_days)
        self._memo: dict[str, int | None] = {}

    def age_days(self, website: str) -> int | None:
        domain = registrable_domain(website)
        if not domain:
            return None
        if domain in self._memo:
            return self._memo[domain]
        row = self.conn.execute("SELECT registered, checked_at FROM domain_cache WHERE domain=?", (domain,)).fetchone()
        registered = None
        if row and datetime.fromisoformat(row["checked_at"]) > datetime.now() - self.ttl:
            registered = row["registered"]
        elif self.http is not None:
            registered = self._lookup(domain)
            self.conn.execute(
                "INSERT INTO domain_cache(domain, registered, checked_at) VALUES (?,?,?) "
                "ON CONFLICT(domain) DO UPDATE SET registered=excluded.registered, checked_at=excluded.checked_at",
                (domain, registered, datetime.now().isoformat(timespec="seconds")),
            )
        age = None
        if registered:
            try:
                reg = datetime.fromisoformat(registered.replace("Z", "+00:00"))
                age = (datetime.now(timezone.utc) - reg).days
            except ValueError:
                age = None
        self._memo[domain] = age
        return age

    def _lookup(self, domain: str) -> str | None:
        try:
            res = self.http.get_json(f"https://rdap.org/domain/{domain}")
        except RobotsDisallowed as exc:
            log.info("RDAP skipped for %s: %s", domain, exc)
            return None
        if not res.ok:
            return None
        for event in res.data.get("events") or []:
            if event.get("eventAction") == "registration":
                return event.get("eventDate")
        return None
