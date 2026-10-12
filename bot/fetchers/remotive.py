"""Remotive public API (https://remotive.com/api-documentation).

Their terms: link back to the Remotive job URL, credit Remotive as the source, and call at most ~4 times a day.
The tracker keeps the Remotive URL and source on every job, and the pipeline enforces
feeds.remotive.min_hours_between_fetches. The free API returns a small, 24h-delayed sample and ignores
category/search filters, so one unfiltered call per run is all that's useful."""
from __future__ import annotations

from ..textutil import html_to_text
from .base import FetchOutcome, Fetcher, Job, is_entry_level, is_internship

API = "https://remotive.com/api/remote-jobs"


class RemotiveFetcher(Fetcher):
    name = "remotive"

    def fetch(self, company, http, cache, progress):
        res = http.get_json(API)
        if not res.ok or not isinstance(res.data, dict):
            return FetchOutcome(company, http_status=res.status, error=res.error or "Unexpected response")
        postings = res.data.get("jobs") or []
        out = FetchOutcome(company, postings=len(postings), http_status=200, warning=self.empty_board_warning(len(postings)))
        junior = company.options.get("include_junior_roles", False)

        for j in postings:
            title = j.get("title") or ""
            job_type = (j.get("job_type") or "").replace("_", "-")
            if not (is_internship(title, job_type) or (junior and is_entry_level(title))):
                continue
            out.jobs.append(Job(
                source=self.name,
                company=j.get("company_name") or "Unknown company",
                ats_id=str(j.get("id") or ""),
                title=title,
                url=j.get("url") or "",
                apply_url=j.get("url") or "",
                location=j.get("candidate_required_location") or "",
                workplace="remote",
                employment_type=job_type,
                description=html_to_text(j.get("description") or ""),
                posted_at=j.get("publication_date") or "",
                src_updated=j.get("publication_date") or "",
                compensation=j.get("salary") or "",
                department=j.get("category") or "",
                via="Remotive",
                location_is_eligibility=True,
            ))
        return out
