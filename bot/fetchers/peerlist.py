"""Peerlist jobs via its public jobs API (peerlist.io/api/v1/jobs), newest first."""
from __future__ import annotations

from ..textutil import html_to_text
from .base import FetchOutcome, Fetcher, Job, is_entry_level, is_internship

API = "https://peerlist.io/api/v1/jobs"


class PeerlistFetcher(Fetcher):
    name = "peerlist"

    def fetch(self, company, http, cache, progress):
        pages = max(1, int(company.options.get("pages", 2) or 2))
        junior = company.options.get("include_junior_roles", False)
        out = FetchOutcome(company, http_status=200)
        seen: set = set()
        for page in range(1, pages + 1):
            progress(f"Reading page {page}")
            res = http.get_json(API, params={"page": page})
            if not res.ok:
                if page == 1:
                    return FetchOutcome(company, http_status=res.status, error=res.error)
                out.warning, out.complete = f"Page {page} failed: {res.error}", False
                break
            jobs = ((res.data or {}).get("data") or {}).get("jobs") or []
            if not jobs:
                break
            for j in jobs:
                jid = j.get("jobId") or j.get("applyLink")
                if jid in seen:
                    continue
                seen.add(jid)
                out.postings += 1
                title, jtype = j.get("jobTitle") or "", j.get("jobType") or ""
                if not (is_internship(title, jtype) or (junior and is_entry_level(title))):
                    continue
                pref = j.get("locationPreference") or ""
                pref = ", ".join(pref) if isinstance(pref, list) else str(pref)
                places = " / ".join(", ".join(x for x in (l.get("city"), l.get("country")) if x)
                                    for l in j.get("location") or [] if isinstance(l, dict))
                remote = "remote" in pref.lower()
                company_obj = j.get("company") if isinstance(j.get("company"), dict) else {}
                sal = j.get("salary") if isinstance(j.get("salary"), dict) else {}
                comp = ""
                if sal.get("min") or sal.get("max"):
                    comp = f"{sal.get('currency') or ''} {sal.get('min') or ''}-{sal.get('max') or ''} {sal.get('period') or ''}".strip()
                skills = ", ".join(s.get("name", "") if isinstance(s, dict) else str(s) for s in j.get("skills") or [])
                desc = html_to_text(j.get("jobDescription") or "")
                if j.get("internshipDuration"):
                    desc += f"\n\nDuration: {j['internshipDuration']}"
                if skills:
                    desc += f"\n\nSkills: {skills}"
                link = j.get("applyLink") or ""
                out.jobs.append(Job(
                    source=self.name,
                    company=company_obj.get("name") or j.get("companyDomain") or "Unknown company",
                    ats_id=str(j.get("jobId") or ""),
                    title=title,
                    url=link,
                    apply_url=link,
                    location=("Remote" + (f" ({places})" if places else "")) if remote else places,
                    workplace="remote" if remote else (pref.lower() if pref.lower() in ("hybrid", "onsite") else ""),
                    employment_type=jtype,
                    description=desc,
                    posted_at=j.get("publishedAt") or "",
                    src_updated=j.get("publishedAt") or "",
                    compensation=comp,
                    via="Peerlist",
                    company_url=f"https://{j['companyDomain']}" if j.get("companyDomain") else "",
                ))
        return out
