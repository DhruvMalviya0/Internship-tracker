"""Builds data/demo.db: clearly fake sample rows covering every status and flag, plus a sample bot run.
Companies are fictional and every link points at example.com."""
from __future__ import annotations

import json
import random
from datetime import date, datetime, timedelta
from pathlib import Path

from . import config, db, notes
from .fetchers.base import Job
from .textutil import sha1

ROWS = [
    # role, company, status, score, remote, region, hours, paid, ai, red, night, missing, applied_days_ago, followup_offset
    ("Creative Developer Intern", "Northwind Studio (sample)", "To Review", 88, "Remote", "Worldwide", "In range (20-30h)", "Yes", 0, [], 1, ["Blender"], None, None),
    ("Frontend Engineering Intern", "Lumen Labs (sample)", "Ready to Apply", 79, "Remote", "India", "In range (25h)", "Yes", 0, [], 0, ["Testing"], None, None),
    ("Design Engineer Intern", "Halcyon Apps (sample)", "Applied", 84, "Remote", "APAC", "Slightly over (full-time ~40h)", "Yes", 0, [], 0, ["Figma"], 3, 4),
    ("UI Engineer Intern", "Brightpath (sample)", "Applied", 71, "Remote", "Worldwide", "Hours unknown", "Unknown", 0, [], 1, ["Storybook", "Accessibility"], 12, -5),
    ("WebGL Intern", "Polygon Forge (sample)", "Follow-up Due", 92, "Remote", "Worldwide", "In range (20h)", "Yes", 0, [], 1, [], 10, -3),
    ("Frontend Intern", "Quillwork (sample)", "AI Interview / Assessment", 74, "Remote", "Anywhere", "In range (30h)", "Yes", 1, [], 1, ["TypeScript"], 9, 0),
    ("Product Design Intern", "Mosaic Health (sample)", "Interview Scheduled", 66, "Remote", "India", "In range (20-35h)", "Yes", 0, [], 0, ["Figma", "UX design"], 16, 5),
    ("Motion Design Intern", "Kite & Key (sample)", "Offer", 81, "Remote", "Worldwide", "In range (24h)", "Yes", 0, [], 1, ["Motion design"], 30, None),
    ("Software Engineering Intern", "Orbital Data (sample)", "Rejected", 47, "Remote", "APAC", "Slightly over (full-time ~40h)", "Yes", 1, [], 0, ["Python", "SQL", "Docker"], 24, None),
    ("Web Developer Intern", "Tidewater Co (sample)", "No Response", 58, "Remote", "Unspecified", "Hours unknown", "Unknown", 0, [], 1, ["Vue"], 40, None),
    ("Frontend Intern (Urgent!)", "QuickHire Global (sample)", "Skipped (red flag)", 63, "Remote", "Worldwide", "Hours unknown", "No", 0,
     ["Asks for a fee (“registration fee”)", "Contact via WhatsApp", "Company domain registered only 41 days ago"], 0, [], None, None),
    ("3D Web Intern", "Aurora XR (sample)", "To Review", 76, "Remote", "Worldwide", "Under target (12h)", "Yes", 1, [], 1, ["Blender"], None, None),
    ("Design Systems Intern", "Fernleaf (sample)", "To Review", 69, "Remote (from description)", "Unspecified", "Hours unknown", "Unknown", 0, [], 0, ["Storybook"], None, None),
    ("Frontend Intern", "Cinder Analytics (sample)", "Applied", 62, "Remote", "India", "In range (20h)", "Yes", 0, [], 0, ["Redux"], 5, 2),
    ("UI Developer Intern", "Parcel Post (sample)", "Applied", 55, "Remote", "APAC", "Hours unknown", "Yes", 1, [], 0, ["Angular"], 18, None),
    ("Creative Technologist Intern", "Signal Arts (sample)", "No Response", 73, "Remote", "Worldwide", "In range (25h)", "Yes", 0, [], 1, [], 33, None),
    ("Frontend Intern", "Delta Grid (sample)", "Rejected", 51, "Remote", "Worldwide", "Hours unknown", "Unknown", 1, [], 1, ["TypeScript", "GraphQL"], 20, None),
    ("Graphics Programming Intern", "Shader Shop (sample)", "Applied", 90, "Remote", "Worldwide", "In range (20-25h)", "Yes", 0, [], 1, ["C++"], 0, 7),
    ("Web Intern", "Linen & Loom (sample)", "Applied", 64, "Remote", "India", "Hours unknown", "Yes", 0, [], 0, [], 8, -1),
    ("Frontend Intern", "Unpaid Example (sample)", "To Review", 58, "Remote", "Worldwide", "Hours unknown", "No", 0, [], 0, ["Vue"], None, None),
]

DESCRIPTION = (
    "SAMPLE POSTING (demo data, not a real job).\n\n"
    "We're looking for an intern who loves building polished, animated interfaces with React, Tailwind and "
    "Framer Motion. Bonus points for React Three Fiber and GLSL shader work. You'll help maintain our design system.\n\n"
    "- Remote, {region}\n- {hours}\n- Paid: {paid}"
)


def build_demo(path: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)
    conn = db.connect(path)
    db.ensure_local_user(conn)
    profile = config.load_profile()
    today = date.today()
    rnd = random.Random(7)

    run_start = datetime.now() - timedelta(hours=5)
    conn.execute(
        "INSERT INTO runs(id, started_at, finished_at, heartbeat_at, status, trigger, args, phase, companies_total, "
        "companies_done, counts) VALUES (1,?,?,?,?,?,?,?,?,?,?)",
        (run_start.isoformat(timespec="seconds"), (run_start + timedelta(seconds=41)).isoformat(timespec="seconds"),
         (run_start + timedelta(seconds=41)).isoformat(timespec="seconds"), "ok", "schedule", "{}", "done", 20, 20,
         json.dumps({"postings": 1342, "internships": 31, "new": 4, "updated": 1, "unchanged": 15, "skipped": 11,
                     "not_internship": 1311, "closed": 1, "fetch_errors": 0, "requests": 52,
                     "skip_reasons": {"not_remote": 7, "region_restricted": 3, "restricted_phrase": 1},
                     "companies": []})),
    )
    conn.execute("INSERT INTO run_user_counts(run_id, user_id, counts) VALUES (1, 1, ?)",
                 (json.dumps({"new": 4, "updated": 1, "unchanged": 15, "skipped": 11, "closed": 1,
                              "skip_reasons": {"not_remote": 7, "region_restricted": 3, "restricted_phrase": 1}}),))
    events = [
        ("info", "start", None, "Run started (schedule): 20 companies, 6 parallel workers, 1 request/sec per host"),
        ("info", "fetched", "Northwind Studio (sample)", "Northwind Studio (sample): 42 postings, 2 internship(s)"),
        ("success", "added", "Northwind Studio (sample)", "Added “Creative Developer Intern” (score 88): night-shift compatible"),
        ("skip", "not_remote", "Northwind Studio (sample)", "Skipped “Data Intern”: Not remote (On-site: “Berlin”)"),
        ("success", "added", "Aurora XR (sample)", "Added “3D Web Intern” (score 76): AI screening likely, night-shift compatible"),
        ("skip", "region_restricted", "Orbital Data (sample)", "Skipped “ML Intern”: Region restricted (location “Remote - US” (US))"),
        ("success", "added", "QuickHire Global (sample)", "Added “Frontend Intern (Urgent!)” (score 63): red flags: Asks for a fee"),
        ("info", "closed", "Tidewater Co (sample)", "No longer listed: “Web Developer Intern”"),
        ("error", "fetch_error", "Broken Board (sample)", "Broken Board (sample): HTTP 404 (board token not found)"),
        ("info", "finish", None, "Done in 41s: 1342 postings scanned, 31 internships, 4 added, 1 updated, 15 already tracked, 11 skipped, 1 closed, 0 fetch errors"),
    ]
    for i, (level, kind, company, msg) in enumerate(events):
        conn.execute("INSERT INTO run_events(run_id, at, level, kind, company, message) VALUES (1,?,?,?,?,?)",
                     ((run_start + timedelta(seconds=i * 4)).isoformat(timespec="seconds"), level, kind, company, msg))

    for i, (role, company, status, score, remote, region, hours, paid, ai, red, night, missing, ago, fu) in enumerate(ROWS, 1):
        url = f"https://example.com/sample-jobs/{i}"
        job = Job("demo", company, str(i), role, url, location=f"Remote ({region})",
                  description=DESCRIPTION.format(region=region, hours=hours, paid=paid))
        matched = ["React", "Tailwind", "Framer Motion"] + (["React Three Fiber", "GLSL"] if score > 80 else [])
        project = notes.pick_project(profile, job, matched)
        applied = (today - timedelta(days=ago)).isoformat() if ago is not None else None
        followup = (today + timedelta(days=fu)).isoformat() if fu is not None else None
        found = (today - timedelta(days=(ago or 0) + rnd.randint(1, 4))).isoformat()
        warnings = []
        if paid == "No":
            warnings.append("Unpaid: review manually")
        elif paid == "Unknown":
            warnings.append("Pay not stated")
        if region == "Unspecified":
            warnings.append("Eligible countries not stated")
        ai_reasons = ["Mentions “HackerRank”"] if ai else []
        cur = conn.execute(
            """INSERT INTO jobs(user_id, url_key, url, apply_url, source, ats_id, company, role, location, remote_type, region,
                   hours_label, paid, score, matched_skills, missing_skills, score_detail, ai_screening, ai_reasons,
                   red_flags, warnings, night_shift, description, machine_hash, note, answers, status, applied_date,
                   followup_date, contact, user_notes, is_demo, date_found, last_seen, is_open, updated_at,
                   status_changed_at)
               VALUES (1,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?,?,?)""",
            (url, url, url + "/apply", "demo", str(i), company, role, job.location, remote, region, hours, paid, score,
             json.dumps(matched), json.dumps(missing),
             json.dumps({"skill_fit": round(score / 100, 2), "title_relevance": 1.0, "formula": "60% skill fit + 40% title relevance"}),
             ai, json.dumps(ai_reasons), json.dumps(red), json.dumps(warnings), night, job.description,
             sha1(url), notes.build_note(profile, job, matched, project, bool(night)),
             notes.build_answers(profile, job, project, region), status, applied, followup,
             "Referred by a friend (sample)" if i == 7 else None,
             "Sample note: recruiter replied within 2 days." if status == "Interview Scheduled" else None,
             found, datetime.now().isoformat(timespec="seconds"), 0 if status == "No Response" and i == 10 else 1,
             datetime.now().isoformat(timespec="seconds"), datetime.now().isoformat(timespec="seconds")),
        )
        conn.execute("INSERT INTO status_history(job_id, from_status, to_status, at) VALUES (?,?,?,?)",
                     (cur.lastrowid, None, "To Review", found + "T09:00:00"))
        if status != "To Review":
            conn.execute("INSERT INTO status_history(job_id, from_status, to_status, at) VALUES (?,?,?,?)",
                         (cur.lastrowid, "To Review", status, (applied or found) + "T21:30:00"))

    # Extra applied history so the weekly chart has a shape.
    for w in range(1, 8):
        for n in range(rnd.randint(3, 13)):
            d = today - timedelta(weeks=w, days=rnd.randint(0, 6))
            url = f"https://example.com/sample-history/{w}-{n}"
            conn.execute(
                "INSERT INTO jobs(user_id, url_key, url, company, role, status, applied_date, is_demo, date_found, score, "
                "remote_type, region, hours_label, paid) VALUES (1,?,?,?,?,?,?,1,?,?,?,?,?,?)",
                (url, url, f"History Co {w}{n} (sample)", "Frontend Intern", rnd.choice(["No Response", "Rejected", "No Response"]),
                 d.isoformat(), (d - timedelta(days=2)).isoformat(), rnd.randint(45, 85), "Remote", "Worldwide",
                 "Hours unknown", "Unknown"),
            )

    skipped = [
        ("Data Intern", "Northwind Studio (sample)", "not_remote", "On-site: “Berlin”"),
        ("ML Intern", "Orbital Data (sample)", "region_restricted", "location “Remote - US” (US)"),
        ("Design Intern", "Mosaic Health (sample)", "restricted_phrase",
         "“authorized to work in the United States”: ...applicants must be authorized to work in the United States..."),
        ("Backend Intern", "Delta Grid (sample)", "low_score", "score 14 is below your minimum of 20"),
    ]
    for i, (role, company, reason, detail) in enumerate(skipped):
        url = f"https://example.com/sample-skipped/{i}"
        conn.execute(
            "INSERT INTO seen(url_key, url, company, role, source, verdict, reason, detail, first_seen, last_seen, run_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,1)",
            (url, url, company, role, "demo", "rejected", reason, detail, run_start.isoformat(timespec="seconds"),
             run_start.isoformat(timespec="seconds")),
        )
        conn.execute("INSERT INTO user_seen(user_id, url_key, verdict, reason, detail, first_seen, last_seen, run_id) "
                     "VALUES (1,?,?,?,?,?,?,1)", (url, "rejected", reason, detail, run_start.isoformat(timespec="seconds"),
                                                  run_start.isoformat(timespec="seconds")))
    conn.commit()
    conn.close()
