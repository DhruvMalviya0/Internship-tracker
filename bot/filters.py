"""Decides whether an internship is remote, open to someone in India, and how its hours and pay look.
Every rejection returns a reason code plus the exact text that triggered it, so the website can show why."""
from __future__ import annotations

import re
from dataclasses import dataclass

from .fetchers.base import Job, is_internship
from .textutil import compile_terms, snippet

REASON_LABELS = {
    "not_internship": "Not an internship",
    "not_remote": "Not remote",
    "restricted_phrase": "Eligibility / location restriction",
    "region_restricted": "Region restricted",
    "low_score": "Low match score",
    "duplicate": "Duplicate listing",
}

REMOTE_TYPES_OK = {"Remote", "Remote (from description)"}

_DESC_REMOTE_RE = re.compile(
    r"\b(fully remote|100% remote|remote[- ]first|remote position|remote role|remote internship|"
    r"work from anywhere|work remotely from anywhere|this (?:role|position|internship) is remote)\b",
    re.I,
)
_LOC_REMOTE_RE = re.compile(r"\b(remote|anywhere|work from home|wfh|distributed|virtual)\b", re.I)

# Place names that mean "you must be here". Anything matching in the location/title (after
# removing allowed regions) means the role is not open to someone living in India.
_FOREIGN_RE = re.compile(
    r"\b(united states|usa|u\.s\.a?\.?|us|america|americas|north america|latam|latin america|canada|mexico|"
    r"brazil|argentina|colombia|chile|united kingdom|uk|england|scotland|ireland|europe|european union|eu|"
    r"emea|dach|germany|france|spain|portugal|italy|netherlands|belgium|poland|switzerland|austria|nordics|"
    r"sweden|norway|denmark|finland|estonia|romania|greece|israel|uae|dubai|saudi|turkey|egypt|nigeria|kenya|"
    r"south africa|australia|new zealand|anz|japan|korea|china|hong kong|taiwan|singapore|philippines|"
    r"vietnam|indonesia|malaysia|thailand|pakistan|bangladesh|"
    r"new york|nyc|san francisco|sf|bay area|seattle|austin|boston|chicago|los angeles|denver|atlanta|miami|"
    r"toronto|vancouver|montreal|london|berlin|munich|paris|amsterdam|dublin|madrid|barcelona|lisbon|"
    r"stockholm|warsaw|zurich|tel aviv|sydney|melbourne|tokyo|seoul|"
    r"pst|pdt|edt|cst|mst|bst|cet)\b",
    re.I,
)
_US_STATE_RE = re.compile(
    r",\s*(AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|"
    r"NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC)\b"
)
_INDIA_RE = re.compile(
    r"\b(india|bengaluru|bangalore|mumbai|delhi|new delhi|ncr|gurgaon|gurugram|noida|hyderabad|pune|chennai|"
    r"kolkata|ahmedabad|jaipur|indore|bhopal|kochi|chandigarh|coimbatore)\b",
    re.I,
)
_INDIA_CODES = {"in", "ind", "india"}

_HOURS_RE = re.compile(
    r"(\d{1,2})\s*(?:(?:-|–|to)\s*(\d{1,2})\s*)?\+?\s*(?:hours|hrs|hr|h)\b\s*(?:per|/|a|each|every)?\s*(?:week|wk)\b",
    re.I,
)
_PART_TIME_RE = re.compile(r"\bpart[- ]time\b", re.I)
_FULL_TIME_RE = re.compile(r"\bfull[- ]time\b|\bfulltime\b", re.I)

_UNPAID_RE = re.compile(
    r"\b(unpaid|volunteer(?:ing)? (?:role|position|basis)|no stipend|without (?:a )?stipend|not paid|"
    r"equity[- ]only|for academic credit only)\b",
    re.I,
)
_PAID_RE = re.compile(
    r"\b(stipend|paid internship|paid position|hourly rate|per hour|/\s?hr|/\s?hour|per month|/\s?month|lpa)\b|"
    r"(?:\$|usd|₹|inr|rs\.?|€|eur|£|gbp)\s?\d[\d,.]*\s?k?|\d[\d,.]*\s?k?\s?(?:usd|inr|eur|gbp)\b",
    re.I,
)


@dataclass
class RegionResult:
    label: str
    reject_detail: str = ""


def workplace_type(job: Job) -> str:
    wp = (job.workplace or "").strip().lower().replace("_", "-")
    if wp in ("onsite", "on-site", "in-office", "office"):
        return "On-site"
    if wp == "hybrid":
        return "Hybrid"
    if wp == "remote" or job.is_remote is True:
        return "Remote"
    loc = job.location or ""
    if re.search(r"\bhybrid\b", loc, re.I):
        return "Hybrid"
    if _LOC_REMOTE_RE.search(loc) or _LOC_REMOTE_RE.search(job.title or ""):
        return "Remote"
    if _DESC_REMOTE_RE.search(job.description or ""):
        return "Remote (from description)"
    return "On-site" if loc.strip() else "Unknown"


_ONSITE_PHRASE_RE = re.compile(r"on-?site|in[- ]office|office|relocat|hybrid", re.I)


def onsite_allowed(job: Job, remote_type: str, profile: dict) -> bool:
    """True for an on-site/hybrid role in a place listed in filters.onsite_ok_locations (e.g. your city)."""
    rx = compile_terms(profile["filters"].get("onsite_ok_locations", []))
    return bool(rx and remote_type in ("On-site", "Hybrid", "Unknown") and rx.search(f"{job.title} | {job.location}"))


def find_reject_phrase(job: Job, profile: dict, allow_onsite: bool = False) -> str:
    """Returns a snippet around the first restriction phrase found, or ''. allow_onsite ignores phrases about
    working on-site / in an office / relocating, because the place is one you said is fine."""
    f = profile["filters"]
    full = f"{job.title}\n{job.location}\n{job.description}"
    phrases = f.get("reject_phrases", [])
    if allow_onsite:
        phrases = [p for p in phrases if not _ONSITE_PHRASE_RE.search(p)]
    rx = compile_terms(phrases)
    if rx and (m := rx.search(full)):
        return f"“{m.group(0)}”: {snippet(full, m.start(), m.end())}"
    loc_rx = None if allow_onsite else compile_terms(f.get("location_reject_terms", []))
    if loc_rx and (m := loc_rx.search(f"{job.title} | {job.location}")):
        return f"“{m.group(0)}” in location “{job.location}”"
    return ""


def region(job: Job, profile: dict) -> RegionResult:
    allowed = [a.lower() for a in profile["filters"].get("allowed_regions", [])]
    where = f"{job.title} | {job.location}"
    low = where.lower()

    if _INDIA_RE.search(where):
        return RegionResult("India")
    for term in allowed:
        if re.search(rf"(?<![\w]){re.escape(term)}(?![\w])", low):
            return RegionResult("APAC" if term in ("apac", "asia", "asia-pacific") else term.title())

    stripped = re.sub(r"\b(remote|hybrid|anywhere|work from home|wfh|distributed|virtual)\b", " ", where, flags=re.I)
    if m := (_FOREIGN_RE.search(stripped) or _US_STATE_RE.search(stripped)):
        return RegionResult("Restricted", f"location “{job.location or job.title}” ({m.group(0).strip(', ')})")

    codes = {c.strip().lower() for c in job.countries if c}
    if codes and not codes & _INDIA_CODES:
        return RegionResult("Restricted", f"country {', '.join(sorted(c.upper() for c in codes))}")
    if job.location_is_eligibility:
        # Job feeds state where candidates must live; any place we didn't recognize as allowed is a restriction.
        place = re.sub(r"\b(remote|anywhere|work from home|wfh|distributed|virtual|only)\b|[-–(),/|]", " ", job.location, flags=re.I)
        if re.search(r"[A-Za-z]{2,}", place):
            return RegionResult("Restricted", f"open to “{job.location}” only")
    return RegionResult("Unspecified")


def hours(job: Job, profile: dict) -> tuple[float | None, str]:
    """(hours per week if stated, label). Label vocabulary matches the tracker's Hours column."""
    text = f"{job.title}\n{job.employment_type}\n{job.description}"
    m = _HOURS_RE.search(text)
    if m:
        lo = float(m.group(1))
        hi = float(m.group(2)) if m.group(2) else lo
        if 1 <= lo <= 80 and 1 <= hi <= 80:
            f = profile["filters"]
            return _hours_label(lo, hi, f.get("hours_min", 20), f.get("hours_max", 35))
    if _PART_TIME_RE.search(f"{job.employment_type} {job.title}") or _PART_TIME_RE.search(job.description or ""):
        return None, "Under target (part-time)"
    if _FULL_TIME_RE.search(job.employment_type or "") or re.search(r"fulltime", job.employment_type or "", re.I):
        return 40.0, "Slightly over (full-time ~40h)"
    return None, "Hours unknown"


def _hours_label(lo: float, hi: float, hmin: float = 20, hmax: float = 35) -> tuple[float, str]:
    shown = f"{lo:g}" if lo == hi else f"{lo:g}-{hi:g}"
    if hi < hmin:
        return hi, f"Under target ({shown}h)"
    if lo > hmax + 10:
        return lo, f"Over ({shown}h)"
    if hi > hmax:
        return hi, f"Slightly over ({shown}h)"
    return hi, f"In range ({shown}h)"


def paid(job: Job) -> str:
    if job.compensation:
        return "Yes"
    text = f"{job.title}\n{job.description}"
    if _UNPAID_RE.search(text):
        return "No"
    if _PAID_RE.search(text):
        return "Yes"
    return "Unknown"


__all__ = [
    "REASON_LABELS", "REMOTE_TYPES_OK", "RegionResult", "find_reject_phrase", "hours", "is_internship", "onsite_allowed",
    "paid", "region", "workplace_type",
]
