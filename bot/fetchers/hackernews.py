"""Hacker News "Ask HN: Who is hiring?" threads, read through the public HN Search API (hn.algolia.com),
which returns a whole thread in one request. Each top-level comment is one company's post, conventionally
starting with a header line like "Company | Role | Location | REMOTE | Salary | URL"."""
from __future__ import annotations

import html
import re
from urllib.parse import urlsplit

from ..textutil import html_to_text
from .base import ENTRY_RE, INTERN_RE, FetchOutcome, Fetcher, Job

SEARCH = "https://hn.algolia.com/api/v1/search_by_date"
ITEM = "https://hn.algolia.com/api/v1/items/{id}"

_ROLE_RE = re.compile(
    r"\b(engineer\w*|developer|designer|intern\w*|scientist|analyst|architect|programmer|full[- ]?stack|"
    r"front[- ]?end|back[- ]?end|devops|sre|product|design|ml|ai|data|research\w*|swe|co-?op|graduate|junior)\b",
    re.I,
)
_PLACE_RE = re.compile(
    r"\b(remote|onsite|on-site|hybrid|in[- ]person|in[- ]office|worldwide|anywhere|global|time ?zones?|"
    r"utc|gmt|est|pst|cet|ist|us|usa|eu|emea|apac|europe|india|canada|uk)\b|,\s*[A-Z]{2}\b",
    re.I,
)
_HREF_RE = re.compile(r'href="([^"]+)"')
_ATS_HOSTS = ("greenhouse.io", "lever.co", "ashbyhq.com", "workable.com", "notion.site", "docs.google.com",
              "forms.gle", "bit.ly", "ycombinator.com", "workatastartup.com", "linkedin.com", "wellfound.com")


def _links(raw_html: str) -> list[str]:
    return [html.unescape(u) for u in _HREF_RE.findall(raw_html or "") if u.startswith("http")]


def parse_comment(c: dict) -> Job | None:
    """Turns one HN comment (Algolia item dict) into a Job; None for deleted/empty comments."""
    raw = c.get("text") or ""
    if not raw.strip():
        return None
    head_html = raw.split("<p>", 1)[0]
    head_text = html_to_text(head_html).strip()
    header = re.sub(r"\s+", " ", head_text.split("\n", 1)[0]).strip()[:300]
    segments = [s for s in (re.sub(r"https?://\S+", "", seg).strip() for seg in re.split(r"\s*\|\s*", header)) if s]
    company = (segments[0] if segments else "Unknown company")[:80]
    rest = segments[1:]
    roles = [s for s in rest if _ROLE_RE.search(s) and not s.lower().startswith("http")]
    places = [s for s in rest if _PLACE_RE.search(s) and s not in roles]
    title = ", ".join(roles)[:140] or "Open roles (see post)"

    if re.search(r"\b(no|not) remote\b", header, re.I):
        workplace = "onsite"
    elif re.search(r"\bremote\b", header, re.I):
        workplace = "remote"
    elif re.search(r"\bhybrid\b", header, re.I):
        workplace = "hybrid"
    elif re.search(r"\b(onsite|on-site|in[- ]person|in[- ]office)\b", header, re.I):
        workplace = "onsite"
    else:
        workplace = ""

    links = _links(raw)
    company_url = next((u for u in links if not any(h in (urlsplit(u).hostname or "") for h in _ATS_HOSTS)), "")
    item_url = f"https://news.ycombinator.com/item?id={c.get('id')}"
    description = html_to_text(raw)
    employment = "Internship" if INTERN_RE.search(description) else ("Junior / entry-level" if ENTRY_RE.search(description) else "")
    return Job(
        source="hackernews",
        company=company,
        ats_id=str(c.get("id") or ""),
        title=title,
        url=item_url,
        apply_url=links[0] if links else item_url,
        location=" / ".join(places) or " | ".join(rest)[:160],
        workplace=workplace,
        employment_type=employment,
        description=description,
        posted_at=c.get("created_at") or "",
        src_updated=c.get("created_at") or "",
        via="Hacker News",
        company_url=company_url,
    )


class HackerNewsFetcher(Fetcher):
    name = "hackernews"

    def fetch(self, company, http, cache, progress):
        months = max(1, int(company.options.get("months", 1) or 1))
        junior = company.options.get("include_junior_roles", False)
        res = http.get_json(SEARCH, params={"tags": "story,author_whoishiring", "hitsPerPage": 12})
        if not res.ok:
            return FetchOutcome(company, http_status=res.status, error=res.error or "HN search failed")
        threads = [h for h in res.data.get("hits") or [] if "who is hiring" in (h.get("title") or "").lower()][:months]
        if not threads:
            return FetchOutcome(company, http_status=200, warning="No 'Who is hiring?' thread found.")

        out = FetchOutcome(company, http_status=200)
        for t in threads:
            progress(f"Reading “{t.get('title')}” ({t.get('num_comments', '?')} comments)")
            item = http.get_json(ITEM.format(id=t["objectID"]))
            if not item.ok:
                out.warning = f"Could not load {t.get('title')}: {item.error}"
                out.complete = False
                continue
            for c in item.data.get("children") or []:
                job = parse_comment(c)
                if job is None:
                    continue
                out.postings += 1
                if job.employment_type == "Internship" or (junior and job.employment_type):
                    out.jobs.append(job)
        return out
