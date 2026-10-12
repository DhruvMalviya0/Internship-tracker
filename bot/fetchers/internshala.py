"""Internshala work-from-home internship listings.

Internshala has no public feed. Its robots.txt allows these listing and detail pages and its terms don't
mention automated access, so the bot reads them politely: one listing page per category per run, and the
detail page (schema.org JobPosting data + stipend) only for internships it hasn't seen before, capped per
run. Nothing is submitted and no login is used."""
from __future__ import annotations

import re

from ..jsonld import fields_from_jobposting, find_jobposting
from ..textutil import normalize_url
from .base import FetchOutcome, Fetcher, Job

BASE = "https://internshala.com"
_HREF_RE = re.compile(r"data-href='(/internship/detail/[^']+)'")
_STIPEND_RE = re.compile(
    r"Stipend[\s\S]{0,400}?(₹\s?[\d,]+(?:\s*-\s*[\d,]+)?(?:\s*/\s*\w+)?|Unpaid|Performance based[^<]{0,40})", re.I)
_HTML = "text/html,application/xhtml+xml"


def _job_from_detail(url: str, page: str) -> Job | None:
    jp = find_jobposting(page)
    if not jp:
        return None
    f = fields_from_jobposting(jp, url)
    m = _STIPEND_RE.search(page)
    stipend = re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
    description = f["description"]
    compensation = ""
    if stipend.lower() == "unpaid":
        description += "\n\nStipend: Unpaid"
    elif stipend:
        compensation = stipend
        description += f"\n\nStipend: {stipend}"
    if "volunteer" in f["employment_type"].lower() and not compensation:
        description += "\n\nListed as a volunteer role (unpaid)."
    return Job(
        source="internshala",
        company=f["company"] or "Unknown company",
        ats_id=url.rstrip("/").rsplit("/", 1)[-1],
        title=f["role"],
        url=url,
        apply_url=url,
        location=f["location"] or "Remote (India)",
        workplace=f["workplace"] or "remote",
        employment_type=f["employment_type"] or "Internship",
        description=description,
        posted_at=f["posted_at"],
        src_updated=f["posted_at"],
        compensation=compensation,
        via="Internshala",
        company_url=f["company_url"],
    )


class InternshalaFetcher(Fetcher):
    name = "internshala"

    def fetch(self, company, http, cache, progress):
        cats = company.options.get("categories") or ["front-end-development"]
        cap = int(company.options.get("max_new_details_per_run", 60) or 60)
        out = FetchOutcome(company, http_status=200)
        seen: set[str] = set()
        errors = []
        for cat in cats:
            listing = f"{BASE}/internships/work-from-home-{cat.strip().strip('/')}-internships/"
            progress(f"Reading listing: {cat}")
            res = http.get_text(listing, accept=_HTML)
            if not res.ok:
                errors.append(f"{cat}: {res.error}")
                continue
            page = res.data.decode("utf-8", "replace")
            for href in dict.fromkeys(_HREF_RE.findall(page)):
                url = BASE + href
                if url in seen:
                    continue
                seen.add(url)
                out.postings += 1
                cached = (cache or {}).get(normalize_url(url)) or {}
                if cached.get("job"):
                    out.jobs.append(Job.from_dict(cached["job"]))
                    out.details_cached += 1
                    continue
                if cache is None or out.details_fetched >= cap:
                    continue
                progress(f"Reading details: {href.rsplit('/', 1)[-1][:70]}")
                d = http.get_text(url, accept=_HTML)
                out.details_fetched += 1
                job = _job_from_detail(url, d.data.decode("utf-8", "replace")) if d.ok else None
                if job:
                    out.jobs.append(job)
        if out.details_fetched >= cap:
            out.warning = f"Read {cap} new internships (the per-run cap); the rest will be read on the next run."
            out.complete = False
        if errors:
            out.warning = (out.warning + " " if out.warning else "") + "Some listings failed: " + "; ".join(errors)
            out.complete = False
            if not out.postings:
                out.error = "; ".join(errors)
        return out
