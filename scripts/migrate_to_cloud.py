"""Copy your local tracker into your account on the deployed site (Postgres).

    set DATABASE_URL=postgresql://...      (PowerShell: $env:DATABASE_URL = "postgresql://...")
    python scripts/migrate_to_cloud.py --email you@gmail.com --admin

Copies: your profile, tracked internships (with status, dates, notes, drafts and history), skipped-listing
history, and the bot's caches. Also seeds the shared sources (feeds + company list) if the cloud database has
none yet. Safe to re-run: jobs already in your cloud account are left alone.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import config, db, store, workspace  # noqa: E402

JOB_COLUMNS = [c for c in (
    "url_key", "url", "apply_url", "source", "platform", "ats_id", "company", "role", "location", "remote_type",
    "region", "hours_num", "hours_label", "paid", "compensation", "score", "matched_skills", "missing_skills",
    "score_detail", "ai_screening", "ai_reasons", "red_flags", "warnings", "night_shift", "description",
    "posted_at", "machine_hash", "excluded_reason", "note", "answers", "drafts_edited", "status", "applied_date",
    "followup_date", "contact", "user_notes", "is_demo", "date_found", "last_seen", "is_open", "updated_at",
    "status_changed_at",
)]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--email", required=True, help="the email you sign in with on the deployed site")
    ap.add_argument("--admin", action="store_true", help="make this account an admin (also set ADMIN_EMAILS on Render)")
    ap.add_argument("--sqlite", default=str(config.DB_PATH), help="local database (default data/state.db)")
    args = ap.parse_args()

    url = store.database_url()
    if not url.startswith(("postgres://", "postgresql://")):
        print("Set DATABASE_URL to your Neon/Postgres connection string first.")
        return 2
    src = db.connect(args.sqlite)
    dst = db.connect(url)
    user, created = db.get_or_create_user(dst, args.email, is_admin=args.admin)
    uid = user["id"]
    print(f"{'Created' if created else 'Using'} cloud account #{uid} for {user['email']}")

    file_profile = config.read_profile_raw()
    db.save_profile_data(dst, uid, {k: v for k, v in file_profile.items() if k not in workspace.GLOBAL_KEYS})
    print("Profile copied (contact details, skills, projects, filters, drafts).")

    if db.get_setting(dst, "sources") is None:
        sources = {k: file_profile[k] for k in workspace.GLOBAL_KEYS if k in file_profile}
        sources.get("feeds", {}).pop("include_junior_roles", None)
        db.set_setting(dst, "sources", sources)
        print("Shared sources (feeds + network settings) seeded from your profile.json.")
    if not db.list_company_rows(dst):
        db.replace_companies(dst, [c.to_row() | {"enabled": c.enabled} for c in config.load_companies()])
        print("Company whitelist seeded from companies.csv.")

    copied = skipped = 0
    for job in src.execute("SELECT * FROM jobs WHERE user_id=? AND is_demo=0 ORDER BY id", (db.LOCAL_USER_ID,)).fetchall():
        job = dict(job)
        if db.job_exists(dst, uid, job["url_key"]):
            skipped += 1
            continue
        cols = ["user_id", *JOB_COLUMNS]
        new_id = dst.execute(f"INSERT INTO jobs({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) RETURNING id",
                             [uid, *(job.get(c) for c in JOB_COLUMNS)]).fetchone()["id"]
        for h in src.execute("SELECT from_status, to_status, at FROM status_history WHERE job_id=? ORDER BY id", (job["id"],)):
            dst.execute("INSERT INTO status_history(job_id, from_status, to_status, at) VALUES (?,?,?,?)",
                        (new_id, h["from_status"], h["to_status"], h["at"]))
        copied += 1
    dst.commit()
    print(f"Tracked internships: {copied} copied, {skipped} already there.")

    n = 0
    for r in src.execute("SELECT * FROM seen").fetchall():
        dst.execute("INSERT INTO seen(url_key, url, company, role, source, src_updated, description, compensation, "
                    "job_json, first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(url_key) DO NOTHING",
                    (r["url_key"], r["url"], r["company"], r["role"], r["source"], r["src_updated"], r["description"],
                     r["compensation"], r["job_json"], r["first_seen"], r["last_seen"]))
        n += 1
    for r in src.execute("SELECT * FROM user_seen WHERE user_id=?", (db.LOCAL_USER_ID,)).fetchall():
        dst.execute("INSERT INTO user_seen(user_id, url_key, verdict, reason, detail, first_seen, last_seen) "
                    "VALUES (?,?,?,?,?,?,?) ON CONFLICT(user_id, url_key) DO NOTHING",
                    (uid, r["url_key"], r["verdict"], r["reason"], r["detail"], r["first_seen"], r["last_seen"]))
    for r in src.execute("SELECT * FROM domain_cache").fetchall():
        dst.execute("INSERT INTO domain_cache(domain, registered, checked_at) VALUES (?,?,?) ON CONFLICT(domain) DO NOTHING",
                    (r["domain"], r["registered"], r["checked_at"]))
    dst.commit()
    print(f"Posting cache: {n} postings (saves re-downloading Internshala / Greenhouse details).")
    print("Done. Sign in on the deployed site with that email to see your tracker.")
    src.close()
    dst.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
