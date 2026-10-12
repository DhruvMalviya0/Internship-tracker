"""Import one internship from a link you paste (Internshala, Wellfound, Unstop, an HN post, a company page...).

Only that single page is requested, when you ask for it, with robots.txt respected and the bot's User-Agent.
Most job pages publish schema.org JobPosting data (it's what Google Jobs reads), so the fields come from there;
otherwise page metadata, otherwise you fill the form. Sites that need a login or block automated requests
simply fall back to the form; nothing tries to get around that."""
from __future__ import annotations

import html
import re
from urllib.parse import urlsplit

from .fetchers.base import Job
from .jsonld import fields_from_jobposting, find_jobposting, meta
from .fetchers.hackernews import ITEM as HN_ITEM, parse_comment
from .http_client import HttpClient, RobotsDisallowed
from .textutil import html_to_text

PLATFORMS = {
    "internshala.com": "Internshala",
    "wellfound.com": "Wellfound",
    "angel.co": "Wellfound",
    "workatastartup.com": "YC Work at a Startup",
    "news.ycombinator.com": "Hacker News",
    "weworkremotely.com": "We Work Remotely",
    "remotive.com": "Remotive",
    "remote.co": "Remote.co",
    "arc.dev": "Arc.dev",
    "joinhandshake.com": "Handshake",
    "wayup.com": "WayUp",
    "theforage.com": "Forage",
    "otta.com": "Otta",
    "welcometothejungle.com": "Otta / Welcome to the Jungle",
    "unstop.com": "Unstop",
    "linkedin.com": "LinkedIn",
    "greenhouse.io": "Greenhouse",
    "lever.co": "Lever",
    "ashbyhq.com": "Ashby",
    "workable.com": "Workable",
    "summerofcode.withgoogle.com": "Google Summer of Code",
    "outreachy.org": "Outreachy",
    "mlh.io": "MLH Fellowship",
    "lfx.linuxfoundation.org": "LFX Mentorship",
}
_HN_ID_RE = re.compile(r"[?&]id=(\d+)")
_LEVER_RE = re.compile(r"^/([^/]+)/([0-9a-f-]{36})")
_GH_RE = re.compile(r"^/([^/]+)/jobs/(\d+)")
_ASHBY_RE = re.compile(r"^/([^/]+)/([0-9a-f-]{36})")


def platform_for(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    for domain, name in PLATFORMS.items():
        if host == domain or host.endswith("." + domain):
            return name
    return host or "Other"


def lookup(url: str, http: HttpClient) -> dict:
    """Returns {"ok": bool, "message": str, "fields": {...}} for the add-internship form."""
    url = (url or "").strip()
    if not re.match(r"^https?://", url, re.I):
        return {"ok": False, "message": "Paste a full link starting with http:// or https://", "fields": {}}
    platform = platform_for(url)
    base = {"url": url, "apply_url": url, "platform": platform}
    try:
        if platform == "Hacker News" and (m := _HN_ID_RE.search(url)):
            res = http.get_json(HN_ITEM.format(id=m.group(1)))
            job = parse_comment(res.data) if res.ok else None
            if job is None:
                return {"ok": False, "message": "Couldn't read that HN post. Fill in the details below.", "fields": base}
            return {"ok": True, "message": "Read the Hacker News post. Check the role and location.",
                    "fields": {**base, **_job_fields(job)}}

        parts = urlsplit(url)
        if parts.hostname == "jobs.lever.co" and (m := _LEVER_RE.match(parts.path)):
            res = http.get_json(f"https://api.lever.co/v0/postings/{m.group(1)}/{m.group(2)}")
            if res.ok and isinstance(res.data, dict):
                cats = res.data.get("categories") or {}
                return {"ok": True, "message": "Read the posting from Lever's API.", "fields": {
                    **base, "role": res.data.get("text", ""), "company": m.group(1).replace("-", " ").title(),
                    "location": cats.get("location", ""), "workplace": res.data.get("workplaceType", ""),
                    "employment_type": cats.get("commitment", ""),
                    "description": res.data.get("descriptionPlain") or html_to_text(res.data.get("description") or ""),
                    "apply_url": res.data.get("applyUrl") or url}}

        if (parts.hostname or "").endswith("greenhouse.io") and (m := _GH_RE.search(parts.path)):
            res = http.get_json(f"https://boards-api.greenhouse.io/v1/boards/{m.group(1)}/jobs/{m.group(2)}")
            if res.ok:
                d = res.data
                return {"ok": True, "message": "Read the posting from Greenhouse's API.", "fields": {
                    **base, "role": d.get("title", ""), "company": d.get("company_name") or m.group(1).title(),
                    "location": (d.get("location") or {}).get("name", ""),
                    "description": html_to_text(d.get("content") or "", unescape_first=True),
                    "posted_at": d.get("first_published") or d.get("updated_at") or ""}}

        if parts.hostname == "jobs.ashbyhq.com" and (m := _ASHBY_RE.match(parts.path)):
            res = http.get_json(f"https://api.ashbyhq.com/posting-api/job-board/{m.group(1)}",
                                params={"includeCompensation": "true"})
            post = next((j for j in (res.data or {}).get("jobs", []) if j.get("id") == m.group(2)), None) if res.ok else None
            if post:
                comp = post.get("compensation") or {}
                return {"ok": True, "message": "Read the posting from Ashby's API.", "fields": {
                    **base, "role": post.get("title", ""), "company": m.group(1).replace("-", " ").title(),
                    "location": post.get("location", ""), "workplace": (post.get("workplaceType") or "").lower(),
                    "employment_type": post.get("employmentType", ""),
                    "compensation": comp.get("compensationTierSummary") or "",
                    "description": post.get("descriptionPlain") or "", "apply_url": post.get("applyUrl") or url,
                    "posted_at": post.get("publishedAt", "")}}

        res = http.get_text(url, accept="text/html,application/xhtml+xml")
    except RobotsDisallowed:
        return {"ok": False, "fields": base,
                "message": f"{platform}'s robots.txt doesn't allow automated requests to this page, so it wasn't fetched. "
                           "Fill in the details below (copy them from the page)."}
    if not res.ok:
        why = {401: "needs you to be logged in", 403: "blocks automated requests", 404: "returned 'not found'",
               429: "is rate-limiting requests"}.get(res.status, f"returned {res.error or 'an error'}")
        return {"ok": False, "fields": base,
                "message": f"{platform} {why}, so the details couldn't be read. Fill them in below."}

    page = res.data.decode("utf-8", "replace") if isinstance(res.data, bytes) else str(res.data)
    if re.search(r"<title>\s*(Just a moment|Attention Required|Access denied)", page, re.I):
        return {"ok": False, "fields": base,
                "message": f"{platform} shows a bot check to automated requests, so the details couldn't be read. "
                           "Fill them in below."}
    jp = find_jobposting(page)
    if jp:
        return {"ok": True, "message": f"Read the job details from {platform}. Check them before saving.",
                "fields": {**base, **{k: v for k, v in fields_from_jobposting(jp, url).items() if v}}}
    title_tag = re.search(r"<title>(.*?)</title>", page, re.S | re.I)
    title = meta(page, "og:title") or (html.unescape(title_tag.group(1)).strip() if title_tag else "")
    desc = meta(page, "og:description") or meta(page, "description")
    return {"ok": bool(title), "fields": {**base, "role": title[:160], "description": desc,
                                          "company": meta(page, "og:site_name")},
            "message": "This page has no structured job data; filled in what was available. "
                       "Check the role and company, and paste the description." if title else
                       "Couldn't find job details on this page. Fill them in below."}


def _job_fields(job: Job) -> dict:
    return {"role": job.title, "company": job.company, "company_url": job.company_url, "location": job.location,
            "workplace": job.workplace, "employment_type": job.employment_type, "description": job.description,
            "apply_url": job.apply_url, "posted_at": job.posted_at}


def job_from_fields(f: dict) -> Job:
    """Builds the Job for a manually added internship from the (possibly edited) form fields."""
    url = (f.get("url") or "").strip()
    platform = (f.get("platform") or "").strip() or (platform_for(url) if url else "Added by you")
    role = (f.get("role") or "").strip()
    company = (f.get("company") or "").strip()
    if not role or not company:
        raise ValueError("Role and company are required.")
    workplace = (f.get("workplace") or "").strip().lower()
    return Job(
        source="manual",
        company=company,
        ats_id="",
        title=role,
        url=url,
        apply_url=(f.get("apply_url") or url).strip(),
        location=(f.get("location") or "").strip(),
        workplace=workplace if workplace in ("remote", "hybrid", "onsite") else "",
        employment_type=(f.get("employment_type") or "").strip(),
        description=(f.get("description") or "").strip(),
        posted_at=(f.get("posted_at") or "").strip(),
        compensation=(f.get("compensation") or "").strip(),
        via=platform,
        company_url=(f.get("company_url") or "").strip(),
    )
