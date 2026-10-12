"""Greenhouse public Job Board API. The list call is light; details are fetched only for internships, and
only when the posting's updated_at changed since the last run."""
from __future__ import annotations

from urllib.parse import quote

from ..textutil import html_to_text, normalize_url
from .base import FetchOutcome, Fetcher, Job, is_internship

API = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"


def _pay_ranges(detail: dict) -> str:
    out = []
    for r in detail.get("pay_input_ranges") or []:
        lo, hi = r.get("min_cents"), r.get("max_cents")
        cur = r.get("currency_type") or ""
        if lo is not None and hi is not None:
            out.append(f"{cur} {lo / 100:,.0f}-{hi / 100:,.0f}".strip())
    return "; ".join(out)


class GreenhouseFetcher(Fetcher):
    name = "greenhouse"

    def fetch(self, company, http, cache, progress):
        base = API.format(token=quote(company.board_token))
        res = http.get_json(base)
        if not res.ok:
            hint = " (board token not found)" if res.status == 404 else ""
            return FetchOutcome(company, http_status=res.status, error=res.error + hint)
        postings = res.data.get("jobs") or []
        out = FetchOutcome(company, postings=len(postings), http_status=200, warning=self.empty_board_warning(len(postings)))

        for p in postings:
            title = p.get("title") or ""
            if not is_internship(title):
                continue
            url = p.get("absolute_url") or ""
            cached = cache.get(normalize_url(url)) if cache is not None else None
            updated = p.get("updated_at") or ""
            if cache is None:  # quick board check: titles only, no detail pages
                description, comp = "", ""
            elif cached and cached.get("src_updated") == updated and cached.get("description"):
                description, comp = cached["description"], cached.get("compensation", "")
                out.details_cached += 1
            else:
                progress(f"Reading details: {title}")
                d = http.get_json(f"{base}/{p.get('id')}", params={"pay_transparency": "true"})
                out.details_fetched += 1
                if d.ok:
                    description = html_to_text(d.data.get("content") or "", unescape_first=True)
                    comp = _pay_ranges(d.data)
                else:
                    description, comp = "", ""
                    progress(f"Could not load details for {title} ({d.error})")
            location = (p.get("location") or {}).get("name") or ""
            out.jobs.append(Job(
                source=self.name,
                company=company.name,
                ats_id=str(p.get("id") or ""),
                title=title,
                url=url,
                apply_url=url,
                location=location,
                description=description,
                posted_at=p.get("first_published") or updated,
                src_updated=updated,
                compensation=comp,
                ai_disclaimer=bool(p.get("ai_disclaimer") or p.get("include_ai_disclaimer")),
                department=", ".join(d.get("name", "") for d in p.get("departments") or []),
            ))
        return out
