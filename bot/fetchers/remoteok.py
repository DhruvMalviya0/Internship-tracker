"""Remote OK public API (remoteok.com/api). Its terms: link back to the job's Remote OK URL and credit
Remote OK as the source; the tracker keeps both on every job."""
from __future__ import annotations

from ..textutil import html_to_text
from .base import FetchOutcome, Fetcher, Job, is_entry_level, is_internship

API = "https://remoteok.com/api"


class RemoteOKFetcher(Fetcher):
    name = "remoteok"

    def fetch(self, company, http, cache, progress):
        res = http.get_json(API)
        if not res.ok or not isinstance(res.data, list):
            return FetchOutcome(company, http_status=res.status, error=res.error or "Unexpected response")
        postings = [j for j in res.data if isinstance(j, dict) and j.get("position")]  # first item is the legal notice
        junior = company.options.get("include_junior_roles", False)
        out = FetchOutcome(company, postings=len(postings), http_status=200, warning=self.empty_board_warning(len(postings)))
        for j in postings:
            title = j.get("position") or ""
            tags = ", ".join(j.get("tags") or [])
            if not (is_internship(title, tags) or (junior and is_entry_level(title))):
                continue
            lo, hi = j.get("salary_min"), j.get("salary_max")
            loc = (j.get("location") or "").strip()
            out.jobs.append(Job(
                source=self.name,
                company=j.get("company") or "Unknown company",
                ats_id=str(j.get("id") or ""),
                title=title,
                url=j.get("url") or "",
                apply_url=j.get("apply_url") or j.get("url") or "",
                location=loc or "Remote",
                workplace="remote",
                employment_type=tags,
                description=html_to_text(j.get("description") or "") + (f"\n\nTags: {tags}" if tags else ""),
                posted_at=j.get("date") or "",
                src_updated=j.get("date") or "",
                compensation=f"USD {lo:,}-{hi:,} / year" if lo and hi else "",
                via="Remote OK",
                location_is_eligibility=bool(loc),
            ))
        return out
