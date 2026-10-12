"""Community-maintained internship lists on GitHub, read from raw.githubusercontent.com:

- SimplifyJobs (e.g. SimplifyJobs/Summer2027-Internships): the repo's own structured file,
  .github/scripts/listings.json, which the README tables are generated from.
- speedyapply (e.g. speedyapply/2027-SWE-College-Jobs/INTERN_INTL.md): markdown tables of
  Company | Position | Location | Posting | Age.

Both lists hold thousands of mostly US-only roles, so only listings whose location could be open to someone in
India (remote / worldwide / India...) are passed on to the filters; the rest are just counted."""
from __future__ import annotations

import html
import re
from datetime import date, datetime, timedelta, timezone

from .base import FetchOutcome, Fetcher, Job, is_entry_level, is_internship, maybe_eligible

RAW = "https://raw.githubusercontent.com/{path}"


def _iso_epoch(ts) -> str:
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError):
        return ""


class SimplifyFetcher(Fetcher):
    name = "simplify"

    def fetch(self, company, http, cache, progress):
        repos = company.options.get("repos") or ["SimplifyJobs/Summer2027-Internships"]
        junior = company.options.get("include_junior_roles", False)
        out = FetchOutcome(company, http_status=200)
        errors = []
        for repo in repos:
            branch = "dev"
            progress(f"Reading {repo}")
            res = http.get_json(RAW.format(path=f"{repo}/{branch}/.github/scripts/listings.json"))
            if not res.ok or not isinstance(res.data, list):
                errors.append(f"{repo}: {res.error or 'unexpected format'}")
                continue
            internships_repo = "intern" in repo.lower()
            for x in res.data:
                if not x.get("active") or not x.get("is_visible", True):
                    continue
                out.postings += 1
                locations = x.get("locations") or []
                where = " / ".join(locations)
                title = x.get("title") or ""
                if not maybe_eligible(where):
                    continue
                if not (internships_repo or is_internship(title) or (junior and is_entry_level(title))):
                    continue
                extra = [f"Listed in {repo} ({x.get('category') or 'uncategorized'})"]
                if x.get("terms"):
                    extra.append("Terms: " + ", ".join(x["terms"]))
                if x.get("sponsorship"):
                    extra.append(f"Sponsorship: {x['sponsorship']}")
                out.jobs.append(Job(
                    source=self.name,
                    company=x.get("company_name") or "Unknown company",
                    ats_id=str(x.get("id") or ""),
                    title=title,
                    url=x.get("url") or "",
                    apply_url=x.get("url") or "",
                    location=where,
                    workplace="remote" if re.search(r"\bremote\b", where, re.I) else "",
                    employment_type="Internship" if internships_repo else "",
                    description="\n".join(extra),
                    posted_at=_iso_epoch(x.get("date_posted")),
                    src_updated=_iso_epoch(x.get("date_updated")),
                    via="SimplifyJobs (GitHub)",
                    location_is_eligibility=True,
                ))
        if errors:
            out.warning = "Some lists failed: " + "; ".join(errors)
            out.complete = False
            if not out.postings:
                out.error = out.warning
        return out


_CELL_LINK = re.compile(r'href="([^"]+)"')
_TAG = re.compile(r"<[^>]+>")


def _cell_text(cell: str) -> str:
    return html.unescape(_TAG.sub("", cell)).strip()


def parse_markdown_tables(text: str) -> list[dict]:
    """Rows of every markdown table in the file, keyed by lower-cased header; also keeps raw cells."""
    rows, header = [], None
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            header = None
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if header is None:
            header = [_cell_text(c).lower() for c in cells]
            continue
        if all(set(c) <= set("-: ") for c in cells):
            continue
        if len(cells) != len(header):
            continue
        rows.append({"_raw": dict(zip(header, cells)), **{h: _cell_text(c) for h, c in zip(header, cells)}})
    return rows


def _age_to_date(age: str) -> str:
    m = re.match(r"(\d+)\s*([dwmh])", age or "")
    if not m:
        return ""
    n, unit = int(m.group(1)), m.group(2)
    days = {"h": 0, "d": n, "w": 7 * n, "m": 30 * n}[unit]
    return (date.today() - timedelta(days=days)).isoformat()


class SpeedyApplyFetcher(Fetcher):
    name = "speedyapply"

    def fetch(self, company, http, cache, progress):
        files = company.options.get("files") or ["speedyapply/2027-SWE-College-Jobs/main/INTERN_INTL.md"]
        junior = company.options.get("include_junior_roles", False)
        out = FetchOutcome(company, http_status=200)
        errors = []
        seen: set[str] = set()
        for path in files:
            progress(f"Reading {path}")
            res = http.get_text(RAW.format(path=path), accept="text/plain, text/markdown, */*")
            if not res.ok:
                errors.append(f"{path}: {res.error}")
                continue
            intern_file = "intern" in path.lower()
            for row in parse_markdown_tables(res.data.decode("utf-8", "replace")):
                title = row.get("position") or row.get("role") or ""
                company_name = row.get("company") or ""
                if not title or not company_name:
                    continue
                links = _CELL_LINK.findall(row["_raw"].get("posting") or row["_raw"].get("apply") or "")
                url = html.unescape(links[0]) if links else ""
                if not url or url in seen:
                    continue
                seen.add(url)
                out.postings += 1
                where = row.get("location") or ""
                if not maybe_eligible(where):
                    continue
                if not (intern_file or is_internship(title) or (junior and is_entry_level(title))):
                    continue
                site = _CELL_LINK.findall(row["_raw"].get("company") or "")
                posted = _age_to_date(row.get("age") or "")
                out.jobs.append(Job(
                    source=self.name,
                    company=company_name,
                    ats_id=url.rstrip("/").rsplit("/", 1)[-1][:80],
                    title=title,
                    url=url,
                    apply_url=url,
                    location=where,
                    workplace="remote" if re.search(r"\bremote\b", where, re.I) else "",
                    employment_type="Internship" if intern_file else "",
                    description=f"Listed in {path.split('/main/')[0]}" + (f"; salary: {row['salary']}" if row.get("salary") else ""),
                    posted_at=posted,
                    src_updated=posted,
                    via="speedyapply (GitHub)",
                    company_url=html.unescape(site[0]) if site else "",
                ))
        if errors:
            out.warning = "Some files failed: " + "; ".join(errors)
            out.complete = False
            if not out.postings:
                out.error = out.warning
        return out
