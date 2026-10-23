"""Apply login-gated tracker jobs through an already logged-in browser.

Examples:
    python browser_apply.py --list
    python browser_apply.py --cdp-url http://127.0.0.1:9222 --job-id 123 --submit
    python browser_apply.py --cdp-url http://127.0.0.1:9222 --pending --limit 5 --submit --yes
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from bot import config, db, store, workspace
from bot.browser_apply import BrowserSession, login_required_jobs, run_browser_apply


def _logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )


def _pick_user(conn, email: str) -> dict:
    users = workspace.run_users(conn)
    if email:
        wanted = email.strip().lower()
        for user in users:
            if user["email"].lower() == wanted:
                return user
        raise ValueError(f"No tracker account matches --email {email!r}.")
    if len(users) != 1:
        raise ValueError("Cloud mode has multiple accounts; pass --email to choose one.")
    return users[0]


def _select_jobs(conn, user_id: int, ids: list[int], pending: bool, limit: int) -> list[dict]:
    if ids:
        jobs = []
        for job_id in ids:
            job = db.get_job(conn, user_id, job_id)
            if not job:
                raise ValueError(f"Job #{job_id} was not found for this account.")
            jobs.append(job)
        return jobs
    if pending:
        return login_required_jobs(conn, user_id, limit)
    raise ValueError("Choose at least one --job-id, or use --pending to process login-held jobs.")


def _print_jobs(jobs: list[dict]) -> None:
    if not jobs:
        print("No login-held jobs matched.")
        return
    for job in jobs:
        print(f"#{job['id']}  {job['company']} — {job['role']}  [{job['status']}]")
        for warning in job.get("warnings") or []:
            if "login" in warning.lower():
                print(f"      {warning}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--email", default="", help="cloud account email when more than one tracker account exists")
    ap.add_argument("--job-id", action="append", type=int, default=[], help="job id to apply; repeatable")
    ap.add_argument("--pending", action="store_true", help="process jobs held with 'Login required to apply'")
    ap.add_argument("--limit", type=int, default=5, help="maximum pending jobs to process (default: 5)")
    ap.add_argument("--list", action="store_true", help="list login-held jobs without opening a browser")
    ap.add_argument("--cdp-url", default="", help="Chromium CDP endpoint, e.g. http://127.0.0.1:9222")
    ap.add_argument("--user-data-dir", default="", help="launch Chrome with this persistent profile instead of CDP")
    ap.add_argument("--channel", default="chrome", help="browser channel for --user-data-dir (default: chrome)")
    ap.add_argument("--resume", default="", help="path to the resume file for file-upload controls")
    ap.add_argument("--submit", action="store_true", help="click the final Apply/Submit button; without this, only fill and preview")
    ap.add_argument("--yes", action="store_true", help="skip the final confirmation prompt for --submit")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    _logging(args.verbose)

    if args.limit < 1:
        ap.error("--limit must be at least 1")
    if args.resume and not Path(args.resume).is_file():
        ap.error(f"Resume file does not exist: {args.resume}")
    if args.submit and not args.yes and not args.list:
        answer = input("This will submit applications in your logged-in browser. Type APPLY to continue: ").strip()
        if answer != "APPLY":
            print("Cancelled.")
            return 0

    target = args.db or store.database_url() or str(config.DB_PATH)
    conn = db.connect(target)
    try:
        user = _pick_user(conn, args.email)
        profile = workspace.effective_profile(conn, user)
        jobs = _select_jobs(conn, user["id"], args.job_id, args.pending or args.list, args.limit)
        if args.list:
            _print_jobs(jobs)
            return 0

        cdp_url = args.cdp_url or os.environ.get("BROWSER_CDP_URL", "")
        user_data_dir = args.user_data_dir or os.environ.get("BROWSER_USER_DATA_DIR", "")
        if not cdp_url and not user_data_dir:
            cdp_url = str(profile.get("browser_apply", {}).get("cdp_url") or "")
            user_data_dir = str(profile.get("browser_apply", {}).get("user_data_dir") or "")
        if not cdp_url and not user_data_dir:
            cdp_url = "http://127.0.0.1:9222"

        run_id = db.start_run(conn, "browser_apply", {"job_ids": [j["id"] for j in jobs], "submit": args.submit})
        db.update_run(conn, run_id, phase="browser_apply", companies_total=len(jobs), companies_done=0)
        db.add_event(conn, run_id, "info", "browser_start",
                     f"Browser apply started for {len(jobs)} job(s); submit={'on' if args.submit else 'preview'}",
                     user_id=user["id"])
        conn.commit()
        try:
            with BrowserSession(cdp_url=cdp_url, user_data_dir=user_data_dir, channel=args.channel) as session:
                results = run_browser_apply(
                    conn, user["id"], profile, jobs, session,
                    submit=args.submit,
                    resume_path=args.resume or profile.get("browser_apply", {}).get("resume_path") or None,
                )
            for item in results:
                result = item["result"]
                level = {"submitted": "success", "preview": "info", "blocked": "warn", "error": "error"}.get(result["status"], "warn")
                db.add_event(conn, run_id, level, f"browser_{result['status']}",
                             f"{item['company']}: {result['message']}", item["company"], item["job_id"],
                             result.get("url") or None, user["id"])
            counts = {
                "applied": sum(1 for x in results if x["result"]["status"] == "submitted"),
                "preview": sum(1 for x in results if x["result"]["status"] == "preview"),
                "blocked": sum(1 for x in results if x["result"]["status"] == "blocked"),
                "errors": sum(1 for x in results if x["result"]["status"] == "error"),
            }
            db.add_event(conn, run_id, "info", "finish", f"Browser apply finished: {counts}", user_id=user["id"])
            db.update_run(conn, run_id, status="ok" if not counts["errors"] else "partial",
                          finished_at=db.now_iso(), phase="done", companies_done=len(results), counts=counts)
            conn.commit()
        except BaseException as exc:
            conn.rollback()
            db.update_run(conn, run_id, status="error", finished_at=db.now_iso(), phase="failed", error=str(exc))
            conn.commit()
            raise
        for item in results:
            result = item["result"]
            print(f"[{result['status'].upper()}] #{item['job_id']} {item['company']} — {item['role']}: {result['message']}")
        return 0 if all(item["result"]["status"] in ("submitted", "preview") for item in results) else 1
    except db.RunInProgress as exc:
        logging.error("%s. Wait for the other run to finish first.", exc)
        return 3
    except (OSError, RuntimeError, ValueError) as exc:
        logging.error("Browser apply failed: %s", exc)
        return 2
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
