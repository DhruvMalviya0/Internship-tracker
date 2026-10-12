"""Unstop internships via its public search API (unstop.com/api/public/opportunity/search-result)."""
from __future__ import annotations

from ..textutil import html_to_text
from .base import FetchOutcome, Fetcher, Job

API = "https://unstop.com/api/public/opportunity/search-result"


def _pay(jd: dict) -> tuple[str, str]:
    """(compensation, note for the description)."""
    if (jd.get("paid_unpaid") or "").lower() == "unpaid":
        return "", "Stipend: Unpaid"
    lo, hi = jd.get("min_salary"), jd.get("max_salary")
    if lo or hi:
        rng = f"{lo:,}-{hi:,}" if lo and hi and lo != hi else f"{lo or hi:,}"
        text = f"INR {rng} / {jd.get('pay_in') or 'month'}"
        return text, f"Stipend: {text}"
    return "", ""


class UnstopFetcher(Fetcher):
    name = "unstop"

    def fetch(self, company, http, cache, progress):
        terms = company.options.get("search_terms") or ["frontend"]
        out = FetchOutcome(company, http_status=200)
        seen: set = set()
        errors = []
        for term in terms:
            progress(f"Searching “{term}”")
            res = http.get_json(API, params={"opportunity": "internships", "page": 1, "per_page": 50,
                                             "oppstatus": "open", "searchTerm": term})
            if not res.ok:
                errors.append(f"{term}: {res.error}")
                continue
            for x in ((res.data or {}).get("data") or {}).get("data") or []:
                if x.get("id") in seen:
                    continue
                seen.add(x.get("id"))
                out.postings += 1
                jd = x.get("jobDetail") if isinstance(x.get("jobDetail"), dict) else {}
                wfh = (jd.get("type") or "").lower() == "wfh" or (x.get("region") or "").lower() == "online"
                places = [", ".join(p for p in (l.get("city"), l.get("country")) if p) for l in x.get("locations") or []]
                compensation, pay_note = _pay(jd)
                skills = ", ".join(dict.fromkeys(s.get("skill_name") or s.get("skill") or "" for s in x.get("required_skills") or []))
                description = html_to_text(x.get("details") or "")
                description += "".join(f"\n\n{t}" for t in (pay_note, f"Skills: {skills}" if skills else "") if t)
                url = f"https://unstop.com/{x.get('public_url') or ''}".rstrip("/")
                out.jobs.append(Job(
                    source=self.name,
                    company=((x.get("organisation") or {}).get("name") or "Unknown company").strip(),
                    ats_id=str(x.get("id") or ""),
                    title=x.get("title") or "",
                    url=url,
                    apply_url=url,
                    location="Remote (India)" if wfh else " / ".join(places),
                    workplace="remote" if wfh else "onsite",
                    employment_type=f"Internship, {(jd.get('timing') or '').replace('_', '-')}".strip(", "),
                    description=description,
                    posted_at=x.get("approved_date") or x.get("updated_at") or "",
                    src_updated=x.get("updated_at") or "",
                    compensation=compensation,
                    via="Unstop",
                ))
        if errors:
            out.warning = "Some searches failed: " + "; ".join(errors)
            out.complete = False
            if not out.postings:
                out.error = out.warning
        return out
