"""schema.org JobPosting helpers (the structured data job pages publish for Google Jobs).
Shared by the link importer and the Internshala feed."""
from __future__ import annotations

import html
import json
import re

from .textutil import html_to_text

COUNTRY_NAMES = {"IN": "India", "US": "United States", "GB": "United Kingdom", "UK": "United Kingdom",
                 "CA": "Canada", "DE": "Germany", "SG": "Singapore", "AU": "Australia"}
_JSONLD_RE = re.compile(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S | re.I)


def meta(page: str, prop: str) -> str:
    m = re.search(rf'<meta[^>]+(?:property|name)=["\']{re.escape(prop)}["\'][^>]+content=["\']([^"\']*)["\']', page, re.I) \
        or re.search(rf'<meta[^>]+content=["\']([^"\']*)["\'][^>]+(?:property|name)=["\']{re.escape(prop)}["\']', page, re.I)
    return html.unescape(m.group(1)).strip() if m else ""


def find_jobposting(page: str) -> dict | None:
    for block in _JSONLD_RE.findall(page):
        try:
            data = json.loads(block.strip())
        except ValueError:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            item = stack.pop(0)
            if not isinstance(item, dict):
                continue
            types = item.get("@type")
            types = types if isinstance(types, list) else [types]
            if "JobPosting" in types:
                return item
            if isinstance(item.get("@graph"), list):
                stack.extend(item["@graph"])
    return None


def place_names(value) -> list[str]:
    """Flattens schema.org Place/Country/PostalAddress shapes into readable place names."""
    out = []
    for v in value if isinstance(value, list) else [value]:
        if isinstance(v, str):
            out.append(COUNTRY_NAMES.get(v, v))
        elif isinstance(v, dict):
            addr = v.get("address") if isinstance(v.get("address"), dict) else v
            parts = [addr[k] for k in ("addressLocality", "addressRegion") if isinstance(addr.get(k), str) and addr[k]]
            country = addr.get("addressCountry")
            if isinstance(country, dict):
                country = country.get("name")
            if isinstance(country, str) and country:
                parts.append(COUNTRY_NAMES.get(country, country))
            if not parts and isinstance(v.get("name"), str):
                parts.append(COUNTRY_NAMES.get(v["name"], v["name"]))
            if parts:
                out.append(", ".join(parts))
    return [o for o in out if o]


def salary(base) -> str:
    if not isinstance(base, dict):
        return ""
    cur = base.get("currency") or ""
    val = base.get("value")
    if isinstance(val, dict):
        lo, hi, unit = val.get("minValue"), val.get("maxValue"), (val.get("unitText") or "").lower()
        amount = val.get("value")
        if lo is not None and hi is not None and lo != hi:
            text = f"{lo:,}-{hi:,}" if isinstance(lo, (int, float)) and isinstance(hi, (int, float)) else f"{lo}-{hi}"
        else:
            text = str(amount if amount is not None else lo if lo is not None else hi or "")
        return f"{cur} {text}{' / ' + unit if unit else ''}".strip() if text else ""
    return f"{cur} {val}".strip() if val else ""


def fields_from_jobposting(jp: dict, url: str) -> dict:
    org = jp.get("hiringOrganization") or {}
    org_name = org.get("name") if isinstance(org, dict) else str(org)
    org_url = (org.get("sameAs") or org.get("url") or "") if isinstance(org, dict) else ""
    if isinstance(org_url, list):
        org_url = org_url[0] if org_url else ""
    remote = str(jp.get("jobLocationType") or "").upper() == "TELECOMMUTE"
    eligible = place_names(jp.get("applicantLocationRequirements"))
    places = place_names(jp.get("jobLocation"))
    location = ("Remote" + (f" ({', '.join(eligible)})" if eligible else "")) if remote else " / ".join(places)
    emp = jp.get("employmentType") or ""
    emp = ", ".join(emp) if isinstance(emp, list) else str(emp)
    own_url = jp.get("url") if isinstance(jp.get("url"), str) and jp.get("url", "").startswith("http") else url
    return {
        "role": html.unescape(str(jp.get("title") or "")).strip(),
        "company": html.unescape(str(org_name or "")).strip(),
        "company_url": org_url if isinstance(org_url, str) else "",
        "url": url,
        "apply_url": own_url,
        "location": location,
        "workplace": "remote" if remote else ("onsite" if places else ""),
        "employment_type": emp.replace("_", "-").title(),
        "compensation": salary(jp.get("baseSalary")),
        "description": html_to_text(html.unescape(str(jp.get("description") or ""))),
        "posted_at": str(jp.get("datePosted") or ""),
    }
