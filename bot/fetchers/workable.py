"""Workable public careers widget API."""
from __future__ import annotations

from urllib.parse import quote

from ..textutil import html_to_text
from .base import FetchOutcome, Fetcher, Job, is_internship

API = "https://apply.workable.com/api/v1/widget/accounts/{token}"


class WorkableFetcher(Fetcher):
    name = "workable"

    def fetch(self, company, http, cache, progress):
        res = http.get_json(API.format(token=quote(company.board_token)), params={"details": "true"})
        if not res.ok:
            hint = " (account not found)" if res.status == 404 else ""
            return FetchOutcome(company, http_status=res.status, error=res.error + hint)
        postings = res.data.get("jobs") or []
        out = FetchOutcome(company, postings=len(postings), http_status=200, warning=self.empty_board_warning(len(postings)))

        for j in postings:
            title = j.get("title") or ""
            if not is_internship(title, j.get("employment_type") or ""):
                continue
            locs = j.get("locations") or [{"city": j.get("city"), "region": j.get("state"), "country": j.get("country")}]
            loc_text = " / ".join(
                ", ".join(x for x in (l.get("city"), l.get("region"), l.get("country")) if x) for l in locs
            )
            if j.get("telecommuting"):
                loc_text = f"Remote ({loc_text})" if loc_text else "Remote"
            out.jobs.append(Job(
                source=self.name,
                company=company.name,
                ats_id=j.get("shortcode") or "",
                title=title,
                url=j.get("url") or j.get("shortlink") or "",
                apply_url=j.get("application_url") or j.get("url") or "",
                location=loc_text,
                is_remote=True if j.get("telecommuting") else None,
                countries=[l.get("countryCode") for l in locs if l.get("countryCode")],
                employment_type=j.get("employment_type") or "",
                description=html_to_text(j.get("description") or ""),
                posted_at=j.get("published_on") or j.get("created_at") or "",
                src_updated=j.get("published_on") or "",
                department=j.get("department") or "",
            ))
        return out
