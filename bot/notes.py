"""Drafts a short tailored note and a block of prefilled answers. These are drafts for you to edit;
nothing here is ever sent anywhere."""
from __future__ import annotations

import re

from .fetchers.base import Job


def pick_project(profile: dict, job: Job, matched: list[str]) -> dict | None:
    projects = [p for p in profile.get("projects", []) if p.get("name")]
    if not projects:
        return None
    text = f"{job.title}\n{job.description}".lower()
    matched_low = {m.lower() for m in matched}

    def relevance(p: dict) -> tuple:
        tags = [t.lower() for t in p.get("tags", [])]
        tag_hits = sum(1 for t in tags if t in matched_low or re.search(rf"(?<!\w){re.escape(t)}(?!\w)", text))
        return (tag_hits, bool(p.get("url")))

    return max(projects, key=relevance)


def _join(items: list[str]) -> str:
    items = [i for i in items if i]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + f" and {items[-1]}"


def build_note(profile: dict, job: Job, matched: list[str], project: dict | None, night_ok: bool) -> str:
    me = profile.get("preferred_name") or profile.get("name", "")
    full = profile.get("name", "")
    who = f"{me} ({full})" if full and me and full != me else (me or full)
    f = profile["filters"]
    top = [s["name"] for s in sorted(profile.get("skills", []), key=lambda s: -s.get("weight", 1))]
    skills = _join((matched + [t for t in top if t not in matched])[:3])

    body = [
        f"I'm {who}, a student developer and designer from India applying for the {job.title} role. "
        f"Most of my work is in {skills}."
    ]
    if project:
        link = f" ({project['url']})" if project.get("url") else " [add link]"
        summary = project.get("summary", "").rstrip(".")
        body.append(f"The closest thing I've built to this role is {project['name']}{link}: {summary}.")
    hours = f"{f.get('hours_min', 20)}-{f.get('hours_max', 35)} hours a week"
    overlap = " and I'm happy to work US hours" if night_ok else ""
    body.append(f"I can commit {hours} remotely{overlap}.")
    body.append(f"I'd love to bring that mix of engineering and design craft to {job.company}.")
    signature = profile.get("drafts", {}).get("signature") or me
    return f"Hi {job.company} team,\n\n{' '.join(body)}\n\nThanks for considering me,\n{signature}"


def build_answers(profile: dict, job: Job, project: dict | None, region_label: str) -> str:
    c = profile.get("contact", {})
    f = profile["filters"]
    d = profile.get("drafts", {})
    loc = profile.get("location", {})
    blank = "[fill in]"
    rows = [
        ("Full name", profile.get("name") or blank),
        ("Preferred name", profile.get("preferred_name") or blank),
        ("Email", c.get("email") or blank),
        ("Phone", c.get("phone") or blank),
        ("Location", f"{loc.get('city', '') + ', ' if loc.get('city') else ''}{loc.get('country', 'India')} "
                     f"({loc.get('timezone', 'IST, UTC+5:30')}), remote"),
        ("Work authorization", d.get("work_authorization")
         or "Based in India. Not authorized to work in the US/EU; available for remote internships "
            "(contractor arrangement is fine)."),
        ("Weekly availability", f"{f.get('hours_min', 20)}-{f.get('hours_max', 35)} hours/week"),
        ("Time-zone overlap", "Comfortable with US hours (night shifts in IST)" if f.get("night_shift_ok") else "IST working hours"),
        ("Start date", d.get("start_date") or blank),
        ("Expected stipend", d.get("expected_stipend") or blank),
        ("Portfolio", c.get("portfolio") or blank),
        ("GitHub", c.get("github") or blank),
        ("LinkedIn", c.get("linkedin") or blank),
        ("Most relevant project", f"{project['name']} {project.get('url') or '[add link]'}" if project else blank),
        (f"Why {job.company}?", f"[1-2 lines on what draws you to {job.company} specifically]"),
    ]
    if region_label == "Unspecified":
        rows.append(("Check before applying", "The posting doesn't say which countries are eligible; confirm India is OK."))
    width = max(len(k) for k, _ in rows)
    return "\n".join(f"{k.ljust(width)} : {v}" for k, v in rows)
