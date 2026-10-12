"""Himalayas remote jobs via its public search API (himalayas.app/jobs/api/search).
Two searches per query: roles open worldwide, and roles open to candidates in India."""
from __future__ import annotations

from datetime import datetime, timezone

from ..textutil import html_to_text
from .base import FetchOutcome, Fetcher, Job, is_entry_level, is_internship

API = "https://himalayas.app/jobs/api/search"


def _iso(ts) -> str:
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError):
        return str(ts or "")


class HimalayasFetcher(Fetcher):
    name = "himalayas"

    def fetch(self, company, http, cache, progress):
        queries = company.options.get("queries") or ["intern"]
        junior = company.options.get("include_junior_roles", False)
        out = FetchOutcome(company, http_status=200)
        seen: set = set()
        errors = []
        for q in queries:
            for scope in ({"worldwide": "true"}, {"country": "India"}):
                progress(f"Searching “{q}” ({'worldwide' if 'worldwide' in scope else 'India'})")
                res = http.get_json(API, params={"q": q, "limit": 20, **scope})
                if not res.ok:
                    errors.append(f"{q}: {res.error}")
                    continue
                for j in res.data.get("jobs") or []:
                    key = j.get("guid") or j.get("applicationLink")
                    if not key or key in seen:
                        continue
                    seen.add(key)
                    out.postings += 1
                    title = j.get("title") or ""
                    emp = j.get("employmentType") or ""
                    seniority = ", ".join(j.get("seniority") or [])
                    if not (is_internship(title, emp) or (junior and (is_entry_level(title) or "Entry" in seniority))):
                        continue
                    places = j.get("locationRestrictions") or []
                    lo, hi = j.get("minSalary"), j.get("maxSalary")
                    comp = ""
                    if lo or hi:
                        comp = f"{j.get('currency') or ''} {lo or ''}{'-' + str(hi) if hi and hi != lo else ''} / {j.get('salaryPeriod') or 'year'}".strip()
                    tz = ", ".join(f"UTC{t:+g}" if isinstance(t, (int, float)) else str(t)
                                   for t in j.get("timezoneRestrictions") or [])
                    desc = html_to_text(j.get("description") or j.get("excerpt") or "")
                    if tz:
                        desc += f"\n\nTime zones: {tz}"
                    out.jobs.append(Job(
                        source=self.name,
                        company=j.get("companyName") or "Unknown company",
                        ats_id=key.rstrip("/").rsplit("/", 1)[-1],
                        title=title,
                        url=key,
                        apply_url=j.get("applicationLink") or key,
                        location=", ".join(places) if places else "Worldwide",
                        workplace="remote",
                        employment_type=f"{emp}{', ' + seniority if seniority else ''}",
                        description=desc,
                        posted_at=_iso(j.get("pubDate")),
                        src_updated=_iso(j.get("pubDate")),
                        compensation=comp,
                        via="Himalayas",
                        location_is_eligibility=True,
                    ))
        if errors:
            out.warning = "Some searches failed: " + "; ".join(errors)
            out.complete = False
            if not out.postings:
                out.error = out.warning
        return out
