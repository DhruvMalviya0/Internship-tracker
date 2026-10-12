"""Loads and saves profile.json and companies.csv. All paths are relative to the project root."""
from __future__ import annotations

import copy
import csv
import io
import json
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROFILE_PATH = ROOT / "profile.json"
COMPANIES_PATH = ROOT / "companies.csv"
DATA_DIR = ROOT / "data"
LOG_DIR = ROOT / "logs"
DB_PATH = DATA_DIR / "state.db"
DEMO_DB_PATH = DATA_DIR / "demo.db"

ATS_TYPES = ("greenhouse", "lever", "ashby", "workable")
# Aggregator feeds: jobs from any company, configured in profile.json "feeds" rather than companies.csv.
FEED_TYPES = {
    "internshala": "Internshala",
    "unstop": "Unstop",
    "ycombinator": "Y Combinator jobs",
    "vcboards": "VC portfolio boards",
    "peerlist": "Peerlist",
    "himalayas": "Himalayas",
    "remoteok": "Remote OK",
    "remotive": "Remotive",
    "weworkremotely": "We Work Remotely",
    "hackernews": "Hacker News",
    "simplify": "SimplifyJobs (GitHub)",
    "speedyapply": "speedyapply (GitHub)",
}
COMPANY_FIELDS = ["name", "category", "ats", "board_token", "website", "careers_url", "has_ai_screening", "enabled"]
AI_SCREENING_VALUES = ("Unknown", "Yes", "No")

# Tuning knobs that rarely change. profile.json overrides any of these.
DEFAULTS = {
    "filters": {
        "remote_only": True,
        "night_shift_ok": True,
        "hours_min": 20,
        "hours_max": 35,
        "require_paid": True,
        "min_score": 20,
        "allowed_regions": ["worldwide", "anywhere", "global", "india", "apac", "asia"],
        "reject_phrases": [],
        "location_reject_terms": ["on-site", "onsite", "in-office", "in office", "hybrid"],
        "onsite_ok_locations": [],
        "include_junior_roles": False,
        "us_hours_phrases": [],
    },
    "ai_screening_terms": [],
    "feeds": {
        "include_junior_roles": False,
        "remotive": {"enabled": True, "min_hours_between_fetches": 6},
        "weworkremotely": {"enabled": True, "categories": ["remote-front-end-programming-jobs"], "min_hours_between_fetches": 1},
        "hackernews": {"enabled": True, "months": 1, "min_hours_between_fetches": 6},
        "internshala": {"enabled": True, "categories": ["front-end-development"], "max_new_details_per_run": 60,
                        "min_hours_between_fetches": 6},
        "unstop": {"enabled": True, "search_terms": ["frontend"], "min_hours_between_fetches": 6},
        "ycombinator": {"enabled": True, "pages": ["software-engineer/remote"], "min_hours_between_fetches": 6},
        "vcboards": {"enabled": True, "boards": [], "queries": ["intern"], "min_hours_between_fetches": 12},
        "peerlist": {"enabled": True, "pages": 2, "min_hours_between_fetches": 6},
        "himalayas": {"enabled": True, "queries": ["intern"], "min_hours_between_fetches": 6},
        "remoteok": {"enabled": True, "min_hours_between_fetches": 6},
        "simplify": {"enabled": True, "repos": ["SimplifyJobs/Summer2027-Internships"], "min_hours_between_fetches": 6},
        "speedyapply": {"enabled": True, "files": ["speedyapply/2027-SWE-College-Jobs/main/INTERN_INTL.md"],
                        "min_hours_between_fetches": 6},
    },
    "goals": {"weekly_min": 10, "weekly_max": 15},
    "drafts": {"followup_days": 7, "start_date": "Immediately", "expected_stipend": ""},
    "red_flags": {"new_domain_days": 180},
    "llm_scoring": {"enabled": False},
    "network": {
        "user_agent": "InternshipAssistant/1.0 (personal job-search tool; polite, rate-limited)",
        "min_delay_seconds": 1.0,
        "timeout_seconds": 30,
        "max_workers": 6,
        "retries": 3,
    },
}


@dataclass
class Company:
    name: str
    category: str
    ats: str
    board_token: str
    website: str = ""
    careers_url: str = ""
    has_ai_screening: str = "Unknown"
    enabled: bool = True
    options: dict = field(default_factory=dict)  # feed settings; never written to companies.csv

    @property
    def is_feed(self) -> bool:
        return self.ats in FEED_TYPES

    def to_row(self) -> dict:
        row = asdict(self)
        row.pop("options", None)
        row["enabled"] = "yes" if self.enabled else "no"
        return row


def feed_sources(profile: dict) -> list[Company]:
    """The enabled aggregator feeds as pseudo-companies, so the pipeline can fetch them like boards."""
    feeds = profile.get("feeds", {})
    out = []
    for ats, label in FEED_TYPES.items():
        cfg = feeds.get(ats) or {}
        if not cfg.get("enabled", False):
            continue
        token = ",".join(cfg.get("categories", [])) or "all"
        out.append(Company(label, "Job feed", ats, token, enabled=True,
                           options={**cfg, "include_junior_roles": bool(feeds.get("include_junior_roles"))}))
    return out


def deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def validate_profile(profile: dict, require_skills: bool = True) -> list[str]:
    """Returns a list of human-readable problems; empty means valid."""
    errors = []
    if not isinstance(profile, dict):
        return ["Profile must be a JSON object."]
    skills = profile.get("skills")
    if not isinstance(skills, list) or (require_skills and not skills):
        errors.append("'skills' must be a list" + (" with at least one skill." if require_skills else "."))
    else:
        for i, s in enumerate(skills):
            if not isinstance(s, dict) or not str(s.get("name", "")).strip():
                errors.append(f"skills[{i}] needs a 'name'.")
            elif not isinstance(s.get("weight", 1), (int, float)):
                errors.append(f"skills[{i}].weight must be a number.")
    if not isinstance(profile.get("projects", []), list):
        errors.append("'projects' must be a list.")
    f = profile.get("filters", {})
    if not isinstance(f, dict):
        errors.append("'filters' must be an object.")
    else:
        hmin, hmax = f.get("hours_min", 20), f.get("hours_max", 35)
        if not isinstance(hmin, (int, float)) or not isinstance(hmax, (int, float)) or hmin > hmax:
            errors.append("filters.hours_min must be a number no larger than filters.hours_max.")
        ms = f.get("min_score", 0)
        if not isinstance(ms, (int, float)) or not 0 <= ms <= 100:
            errors.append("filters.min_score must be between 0 and 100.")
    return errors


def load_profile(path: Path = PROFILE_PATH) -> dict:
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    errors = validate_profile(raw)
    if errors:
        raise ValueError(f"{path.name}: " + " ".join(errors))
    return deep_merge(DEFAULTS, raw)


def read_profile_raw(path: Path = PROFILE_PATH) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def atomic_write_text(path: Path, text: str, backup: bool = True) -> None:
    path = Path(path)
    if backup and path.exists():
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def save_profile(profile: dict, path: Path = PROFILE_PATH) -> None:
    errors = validate_profile(profile)
    if errors:
        raise ValueError(" ".join(errors))
    atomic_write_text(path, json.dumps(profile, indent=2, ensure_ascii=False) + "\n")


def _truthy(value: str) -> bool:
    return str(value).strip().lower() not in ("no", "false", "0", "off", "n")


def company_from_row(row: dict) -> Company:
    ats = str(row.get("ats", "")).strip().lower()
    ai = str(row.get("has_ai_screening", "") or "Unknown").strip().capitalize()
    return Company(
        name=str(row.get("name", "")).strip(),
        category=str(row.get("category", "")).strip(),
        ats=ats,
        board_token=str(row.get("board_token", "")).strip(),
        website=str(row.get("website", "")).strip(),
        careers_url=str(row.get("careers_url", "")).strip(),
        has_ai_screening=ai if ai in AI_SCREENING_VALUES else "Unknown",
        enabled=_truthy(row.get("enabled", "yes") or "yes"),
    )


def validate_companies(companies: list[Company]) -> list[str]:
    errors, seen = [], set()
    for i, c in enumerate(companies, start=1):
        if not c.name:
            errors.append(f"Row {i}: name is required.")
        if c.ats not in ATS_TYPES:
            errors.append(f"Row {i} ({c.name or '?'}): ats must be one of {', '.join(ATS_TYPES)}.")
        if not c.board_token:
            errors.append(f"Row {i} ({c.name or '?'}): board_token is required.")
        key = (c.ats, c.board_token.lower())
        if c.board_token and key in seen:
            errors.append(f"Row {i} ({c.name}): duplicate {c.ats} board '{c.board_token}'.")
        seen.add(key)
    return errors


def load_companies(path: Path = COMPANIES_PATH) -> list[Company]:
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rows = [r for r in csv.DictReader(fh) if any((v or "").strip() for v in r.values())]
    companies = [company_from_row(r) for r in rows]
    errors = validate_companies(companies)
    if errors:
        raise ValueError(f"{path.name}: " + " ".join(errors))
    return companies


def save_companies(companies: list[Company], path: Path = COMPANIES_PATH) -> None:
    errors = validate_companies(companies)
    if errors:
        raise ValueError(" ".join(errors))
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=COMPANY_FIELDS, lineterminator="\n")
    writer.writeheader()
    for c in companies:
        writer.writerow(c.to_row())
    atomic_write_text(path, buf.getvalue())
