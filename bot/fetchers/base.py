"""Normalized job shape that every source maps into, plus the fetcher interface."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Callable

from ..config import Company
from ..http_client import HttpClient

# Titles or employment types that mean "this is an internship-style role".
INTERN_RE = re.compile(
    r"\b(intern|interns|internship|internships|co-?op|apprentice(?:ship)?|trainee|working student|"
    r"summer (?:analyst|associate|student)|student (?:developer|engineer|designer|researcher))\b",
    re.I,
)


def is_internship(title: str, employment_type: str = "") -> bool:
    return bool(INTERN_RE.search(title or "") or re.search(r"\bintern", employment_type or "", re.I))


ENTRY_RE = re.compile(r"\b(junior|jr\.?|entry[- ]level|graduate|new grad|fresh(?:er|ers)?|early[- ]career|associate)\b", re.I)
SENIOR_RE = re.compile(r"\b(senior|sr\.?|lead|staff|principal|manager|head|director|architect|vp)\b", re.I)


_ELIGIBLE_HINT_RE = re.compile(
    r"\b(remote|worldwide|anywhere|global|international|india|apac|asia|work from home|wfh|online|distributed|"
    r"bengaluru|bangalore|mumbai|delhi|gurgaon|gurugram|noida|hyderabad|pune|chennai|kolkata|ahmedabad)\b",
    re.I,
)


def maybe_eligible(text: str) -> bool:
    """Cheap pre-check for big lists (thousands of US-only listings): could this location be open to someone in
    India? Anything passing still goes through the full filters; this just keeps the skipped list readable."""
    return bool(_ELIGIBLE_HINT_RE.search(text or ""))


def is_entry_level(title: str) -> bool:
    """Junior / graduate roles (used only for job feeds, when include_junior_roles is on)."""
    return bool(ENTRY_RE.search(title or "")) and not SENIOR_RE.search(title or "")


@dataclass
class Job:
    source: str
    company: str
    ats_id: str
    title: str
    url: str
    apply_url: str = ""
    location: str = ""
    workplace: str = ""            # remote / hybrid / onsite as reported by the ATS, if any
    is_remote: bool | None = None
    countries: list[str] = field(default_factory=list)
    employment_type: str = ""
    description: str = ""
    posted_at: str = ""
    src_updated: str = ""          # source's own "updated" marker, used to skip re-downloading details
    compensation: str = ""
    ai_disclaimer: bool = False
    department: str = ""
    via: str = ""                  # aggregator name ("Remotive") when the job came from a feed
    company_url: str = ""          # company website, when a feed provides one
    location_is_eligibility: bool = False  # feed location = where candidates must live; unknown places restrict

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Job":
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
        return cls(**known)


@dataclass
class FetchOutcome:
    company: Company
    jobs: list[Job] = field(default_factory=list)
    postings: int = 0              # all postings on the board
    http_status: int | None = None
    error: str = ""
    warning: str = ""
    details_fetched: int = 0
    details_cached: int = 0
    seconds: float = 0.0
    complete: bool = True          # False when part of a multi-request fetch failed (don't close missing jobs)

    @property
    def ok(self) -> bool:
        return not self.error


# cache: url_key -> {"src_updated", "description", "compensation"} from earlier runs.
# Passing None means "just check the board": fetchers skip any per-posting detail requests.
DetailCache = dict
Progress = Callable[[str], None]


class Fetcher:
    name = "base"

    def fetch(self, company: Company, http: HttpClient, cache: DetailCache, progress: Progress) -> FetchOutcome:
        raise NotImplementedError

    @staticmethod
    def empty_board_warning(postings: int) -> str:
        # Lever, Ashby and Workable answer 200 with an empty list for unknown boards, so 0 may mean a bad token.
        return "Board returned 0 postings. If this persists, check the board token." if postings == 0 else ""
