"""We Work Remotely category RSS feeds (https://weworkremotely.com/categories/<slug>.rss).
Every listing is remote; the <region> element says where candidates must be."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import quote, urlsplit

from ..textutil import html_to_text
from .base import FetchOutcome, Fetcher, Job, is_entry_level, is_internship

FEED = "https://weworkremotely.com/categories/{slug}.rss"
_URL_RE = re.compile(r'URL:\s*</strong>\s*<a href="(https?://[^"]+)"', re.I)
_SHORTENERS = {"bit.ly", "t.co", "tinyurl.com", "goo.gl", "lnkd.in", "ow.ly", "buff.ly", "rebrand.ly"}


def _iso(rfc822: str) -> str:
    try:
        return parsedate_to_datetime(rfc822).isoformat(timespec="seconds")
    except (TypeError, ValueError):
        return ""


def _company_url(description_html: str) -> str:
    m = _URL_RE.search(description_html or "")
    if not m:
        return ""
    host = (urlsplit(m.group(1)).hostname or "").lower().removeprefix("www.")
    return "" if host in _SHORTENERS else m.group(1)


class WeWorkRemotelyFetcher(Fetcher):
    name = "weworkremotely"

    def fetch(self, company, http, cache, progress):
        slugs = [s.strip() for s in company.board_token.split(",") if s.strip() and s.strip() != "all"]
        slugs = slugs or ["remote-front-end-programming-jobs"]
        junior = company.options.get("include_junior_roles", False)
        out = FetchOutcome(company, http_status=200)
        seen_links: set[str] = set()
        errors = []

        for slug in slugs:
            progress(f"Reading feed {slug}")
            res = http.get_text(FEED.format(slug=quote(slug)))
            if not res.ok:
                errors.append(f"{slug}: {res.error}{' (unknown category)' if res.status == 404 else ''}")
                continue
            try:
                items = ET.fromstring(res.data).findall("./channel/item")
            except ET.ParseError as exc:
                errors.append(f"{slug}: could not parse RSS ({exc})")
                continue
            for it in items:
                link = (it.findtext("link") or it.findtext("guid") or "").strip()
                if not link or link in seen_links:
                    continue
                seen_links.add(link)
                out.postings += 1
                raw_title = (it.findtext("title") or "").strip()
                company_name, _, title = raw_title.partition(": ")
                if not title:
                    company_name, title = "Unknown company", raw_title
                job_type = (it.findtext("type") or "").strip()
                if not (is_internship(title, job_type) or (junior and is_entry_level(title))):
                    continue
                region = (it.findtext("region") or "").strip()
                country = (it.findtext("country") or "").strip()
                description_html = it.findtext("description") or ""
                skills = (it.findtext("skills") or "").strip()
                description = html_to_text(description_html)
                if skills:
                    description += f"\n\nSkills: {skills}"
                published = _iso(it.findtext("pubDate") or "")
                out.jobs.append(Job(
                    source=self.name,
                    company=company_name.strip(),
                    ats_id=link.rstrip("/").rsplit("/", 1)[-1],
                    title=title.strip(),
                    url=link,
                    apply_url=link,
                    location=", ".join(x for x in (region, country) if x),
                    workplace="remote",
                    employment_type=job_type,
                    description=description,
                    posted_at=published,
                    src_updated=published,
                    department=(it.findtext("category") or "").strip(),
                    via="We Work Remotely",
                    company_url=_company_url(description_html),
                    location_is_eligibility=True,
                ))

        if errors and not out.postings:
            out.error = "; ".join(errors)
            out.http_status = None
        elif errors:
            out.warning = "Some feeds failed: " + "; ".join(errors)
            out.complete = False
        elif out.postings == 0:
            out.warning = self.empty_board_warning(0)
        return out
