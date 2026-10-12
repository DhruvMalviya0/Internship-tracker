"""Y Combinator's public jobs pages (ycombinator.com/jobs/role/...). The listing data is embedded in the page
(data-page JSON), so one request per configured page. Work at a Startup itself needs a login and isn't used."""
from __future__ import annotations

import html
import json
import re
from datetime import date, timedelta

from .base import FetchOutcome, Fetcher, Job, is_entry_level, is_internship

BASE = "https://www.ycombinator.com"
_PAGE_RE = re.compile(r'data-page="([^"]+)"')


def _created(text: str) -> str:
    m = re.match(r"(\d+)\s*(minute|hour|day|week|month|year)", text or "")
    if not m:
        return ""
    n, unit = int(m.group(1)), m.group(2)
    days = {"minute": 0, "hour": 0, "day": n, "week": 7 * n, "month": 30 * n, "year": 365 * n}[unit]
    return (date.today() - timedelta(days=days)).isoformat()


class YCombinatorFetcher(Fetcher):
    name = "ycombinator"

    def fetch(self, company, http, cache, progress):
        pages = company.options.get("pages") or ["software-engineer/remote"]
        junior = company.options.get("include_junior_roles", False)
        out = FetchOutcome(company, http_status=200)
        seen: set = set()
        errors = []
        for page in pages:
            url = f"{BASE}/jobs/role/{page.strip('/')}"
            progress(f"Reading {url}")
            res = http.get_text(url, accept="text/html,application/xhtml+xml")
            if not res.ok:
                errors.append(f"{page}: {res.error}")
                continue
            m = _PAGE_RE.search(res.data.decode("utf-8", "replace"))
            if not m:
                errors.append(f"{page}: page format changed (no job data found)")
                continue
            try:
                postings = json.loads(html.unescape(m.group(1)))["props"].get("jobPostings") or []
            except (ValueError, KeyError):
                errors.append(f"{page}: could not parse job data")
                continue
            for j in postings:
                if j.get("id") in seen:
                    continue
                seen.add(j.get("id"))
                out.postings += 1
                title, jtype = j.get("title") or "", j.get("type") or ""
                exp = str(j.get("minExperience") or "")
                if not (is_internship(title, jtype) or (junior and (is_entry_level(title) or exp == "0"))):
                    continue
                job_url = BASE + j["url"] if (j.get("url") or "").startswith("/") else (j.get("url") or "")
                loc = j.get("location") or ""
                skills = ", ".join(s.get("name", "") if isinstance(s, dict) else str(s) for s in j.get("skills") or [])
                desc = "\n".join(x for x in (
                    f"{j.get('companyName')} (YC {j.get('companyBatchName')}): {j.get('companyOneLiner') or ''}",
                    f"Role: {j.get('prettyRole') or ''} {('/ ' + j['roleSpecificType']) if j.get('roleSpecificType') else ''}",
                    f"Visa: {j['visa']}" if j.get("visa") else "",
                    f"Skills: {skills}" if skills else "",
                    f"Salary: {j['salaryRange']}" if j.get("salaryRange") else "",
                ) if x)
                posted = _created(j.get("createdAt") or "")
                out.jobs.append(Job(
                    source=self.name,
                    company=j.get("companyName") or "Unknown company",
                    ats_id=str(j.get("id") or ""),
                    title=title,
                    url=job_url,
                    apply_url=job_url,
                    location=loc,
                    workplace="remote" if re.search(r"\bremote\b", loc, re.I) else "",
                    employment_type=jtype,
                    description=desc,
                    posted_at=posted,
                    src_updated=posted,
                    compensation=j.get("salaryRange") or "",
                    via="Y Combinator jobs",
                ))
        if errors:
            out.warning = "Some pages failed: " + "; ".join(errors)
            out.complete = False
            if not out.postings:
                out.error = out.warning
        return out
