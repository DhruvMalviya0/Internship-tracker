"""Lever public Postings API (one call returns every posting with its description)."""
from __future__ import annotations

from datetime import datetime
from urllib.parse import quote

from ..textutil import html_to_text
from .base import FetchOutcome, Fetcher, Job, is_internship

API = "https://api.lever.co/v0/postings/{token}"


def _ms_to_iso(ms) -> str:
    try:
        return datetime.fromtimestamp(int(ms) / 1000).isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError):
        return ""


def _salary(p: dict) -> str:
    s = p.get("salaryRange") or {}
    if s.get("min") is None or s.get("max") is None:
        return ""
    interval = (s.get("interval") or "").replace("-", " ")
    return f"{s.get('currency', '')} {s['min']:,}-{s['max']:,} {interval}".strip()


class LeverFetcher(Fetcher):
    name = "lever"

    def fetch(self, company, http, cache, progress):
        res = http.get_json(API.format(token=quote(company.board_token)), params={"mode": "json"})
        if not res.ok or not isinstance(res.data, list):
            hint = " (board token not found)" if res.status == 404 else ""
            return FetchOutcome(company, http_status=res.status, error=(res.error or "Unexpected response") + hint)
        postings = res.data
        out = FetchOutcome(company, postings=len(postings), http_status=200, warning=self.empty_board_warning(len(postings)))

        for p in postings:
            cats = p.get("categories") or {}
            title = p.get("text") or ""
            commitment = cats.get("commitment") or ""
            if not is_internship(title, commitment):
                continue
            sections = [p.get("descriptionPlain") or html_to_text(p.get("description") or "")]
            for block in p.get("lists") or []:
                sections.append(f"{block.get('text', '')}\n{html_to_text(block.get('content') or '')}")
            sections.append(p.get("additionalPlain") or "")
            locations = cats.get("allLocations") or [cats.get("location") or ""]
            out.jobs.append(Job(
                source=self.name,
                company=company.name,
                ats_id=p.get("id") or "",
                title=title,
                url=p.get("hostedUrl") or "",
                apply_url=p.get("applyUrl") or p.get("hostedUrl") or "",
                location=" / ".join(l for l in locations if l),
                workplace=p.get("workplaceType") or "",
                countries=[p["country"]] if p.get("country") else [],
                employment_type=commitment,
                description="\n\n".join(s.strip() for s in sections if s and s.strip()),
                posted_at=_ms_to_iso(p.get("createdAt")),
                src_updated=str(p.get("updatedAt") or p.get("createdAt") or ""),
                compensation=_salary(p),
                department=cats.get("team") or cats.get("department") or "",
            ))
        return out
