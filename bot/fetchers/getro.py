"""VC / accelerator portfolio job boards hosted on Getro (Techstars, Antler, ...). The board's search page embeds
the matching jobs in its __NEXT_DATA__ JSON, so each (board, query) is one request returning the top 20 matches."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from .base import FetchOutcome, Fetcher, Job, is_entry_level, is_internship, maybe_eligible

_NEXT_RE = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)
_WORK_MODES = {"remote": "remote", "hybrid": "hybrid", "on_site": "onsite", "onsite": "onsite"}


def _iso(ts) -> str:
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError):
        return ""


class GetroFetcher(Fetcher):
    name = "vcboards"

    def fetch(self, company, http, cache, progress):
        boards = company.options.get("boards") or []
        queries = company.options.get("queries") or ["intern"]
        junior = company.options.get("include_junior_roles", False)
        out = FetchOutcome(company, http_status=200)
        seen: set = set()
        errors = []
        for board in boards:
            name, base = (board.get("name") or "").strip(), (board.get("url") or "").rstrip("/")
            if not base:
                continue
            for q in queries:
                progress(f"Searching {name} for “{q}”")
                res = http.get_text(f"{base}/jobs", params={"q": q}, accept="text/html,application/xhtml+xml")
                if not res.ok:
                    errors.append(f"{name}: {res.error}")
                    continue
                m = _NEXT_RE.search(res.data.decode("utf-8", "replace"))
                try:
                    found = json.loads(m.group(1))["props"]["pageProps"]["initialState"]["jobs"]["found"] if m else None
                except (ValueError, KeyError, TypeError):
                    found = None
                if found is None:
                    errors.append(f"{name}: not a Getro board, or its format changed")
                    continue
                for j in found:
                    if j.get("id") in seen:
                        continue
                    seen.add(j.get("id"))
                    out.postings += 1
                    title = j.get("title") or ""
                    seniority = j.get("seniority") or ""
                    mode = _WORK_MODES.get(j.get("workMode") or "", "")
                    where = " / ".join(j.get("locations") or [])
                    if not (is_internship(title, seniority) or (junior and (is_entry_level(title) or seniority == "entry_level"))):
                        continue
                    if mode != "remote" and not maybe_eligible(where):
                        continue
                    org = j.get("organization") or {}
                    lo, hi = j.get("compensationAmountMinCents"), j.get("compensationAmountMaxCents")
                    comp = ""
                    if lo and hi and j.get("compensationPublic"):
                        comp = f"{j.get('compensationCurrency') or ''} {lo / 100:,.0f}-{hi / 100:,.0f} / {(j.get('compensationPeriod') or '').replace('_', ' ')}".strip()
                    skills = ", ".join(j.get("skills") or [])
                    out.jobs.append(Job(
                        source=self.name,
                        company=org.get("name") or "Unknown company",
                        ats_id=str(j.get("id") or ""),
                        title=title,
                        url=j.get("url") or f"{base}/jobs",
                        apply_url=j.get("url") or f"{base}/jobs",
                        location=("Remote" + (f" ({where})" if where else "")) if mode == "remote" else where,
                        workplace=mode,
                        employment_type="Internship" if seniority == "internship" else seniority,
                        description=f"From the {name} portfolio job board." + (f"\n\nSkills: {skills}" if skills else ""),
                        posted_at=_iso(j.get("createdAt")),
                        src_updated=_iso(j.get("createdAt")),
                        compensation=comp,
                        via=f"{name} portfolio",
                        company_url=f"https://{org['domain']}" if org.get("domain") else "",
                    ))
        if errors:
            out.warning = "Some boards failed: " + "; ".join(errors)
            out.complete = False
            if not out.postings:
                out.error = out.warning
        return out
