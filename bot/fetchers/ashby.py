"""Ashby public Job Posting API (isRemote / workplaceType / compensation included)."""
from __future__ import annotations

from urllib.parse import quote

from ..textutil import html_to_text
from .base import FetchOutcome, Fetcher, Job, is_internship

API = "https://api.ashbyhq.com/posting-api/job-board/{token}"


class AshbyFetcher(Fetcher):
    name = "ashby"

    def fetch(self, company, http, cache, progress):
        res = http.get_json(API.format(token=quote(company.board_token)), params={"includeCompensation": "true"})
        if not res.ok:
            hint = " (board token not found)" if res.status == 404 else ""
            return FetchOutcome(company, http_status=res.status, error=res.error + hint)
        postings = [j for j in res.data.get("jobs") or [] if j.get("isListed", True)]
        out = FetchOutcome(company, postings=len(postings), http_status=200, warning=self.empty_board_warning(len(postings)))

        for j in postings:
            title = j.get("title") or ""
            if not is_internship(title, j.get("employmentType") or ""):
                continue
            locations = [j.get("location") or ""] + [s.get("location") or "" for s in j.get("secondaryLocations") or []]
            country = (((j.get("address") or {}).get("postalAddress") or {}).get("addressCountry")) or ""
            comp = j.get("compensation") or {}
            out.jobs.append(Job(
                source=self.name,
                company=company.name,
                ats_id=j.get("id") or "",
                title=title,
                url=j.get("jobUrl") or "",
                apply_url=j.get("applyUrl") or j.get("jobUrl") or "",
                location=" / ".join(l for l in locations if l),
                workplace=j.get("workplaceType") or "",
                is_remote=j.get("isRemote"),
                countries=[country] if country else [],
                employment_type=j.get("employmentType") or "",
                description=j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml") or ""),
                posted_at=j.get("publishedAt") or "",
                src_updated=j.get("publishedAt") or "",
                compensation=comp.get("compensationTierSummary") or comp.get("scrapeableCompensationSalarySummary") or "",
                department=j.get("department") or "",
            ))
        return out
