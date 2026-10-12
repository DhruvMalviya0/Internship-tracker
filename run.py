"""Fetch, filter, score and track internships. Never submits anything.

    python run.py                    # full run
    python run.py --company Figma    # one company (repeatable)
    python run.py --source lever     # one ATS or feed (remotive, weworkremotely)
    python run.py --dry-run          # run against a throwaway copy of the database
"""
from __future__ import annotations

import argparse
import logging
import shutil
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

from bot import config, db, store, workspace
from bot.filters import REASON_LABELS
from bot.pipeline import Run, RunUser


def setup_logging(verbose: bool) -> Path:
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = config.LOG_DIR / f"run-{date.today().isoformat()}.log"
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S")
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(fmt)
    file_handler.setLevel(logging.DEBUG)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(file_handler)
    root.addHandler(console)
    for noisy in ("urllib3", "requests"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    # Keep 30 days of logs.
    cutoff = time.time() - 30 * 86400
    for old in config.LOG_DIR.glob("run-*.log"):
        if old.stat().st_mtime < cutoff:
            old.unlink(missing_ok=True)
    return log_path


def print_summary(result: dict, log_path: Path) -> None:
    c = result
    print("\n" + "=" * 64)
    print(f"Run #{c['run_id']} finished ({c['status']}) in {c['seconds']:.0f}s, {c.get('requests', 0)} HTTP requests")
    print("-" * 64)
    print(f"  Postings scanned      {c.get('postings', 0):>6}")
    print(f"  Internships found     {c.get('internships', 0):>6}")
    per_user = c.get("per_user", {})
    for email, u in per_user.items():
        if len(per_user) > 1:
            print(f"  -- {email}")
        print(f"  Added (new)           {u.get('new', 0):>6}")
        print(f"  Updated               {u.get('updated', 0):>6}")
        print(f"  Already tracked       {u.get('unchanged', 0):>6}")
        print(f"  Skipped               {u.get('skipped', 0):>6}")
        for reason, n in sorted(u.get("skip_reasons", {}).items(), key=lambda kv: -kv[1]):
            print(f"    - {REASON_LABELS.get(reason, reason):<20}{n:>4}")
        print(f"  Closed (delisted)     {u.get('closed', 0):>6}")
    print(f"  Fetch errors          {c.get('fetch_errors', 0):>6}")
    for comp in c.get("companies", []):
        if not comp["ok"]:
            print(f"    - {comp['company']}: {comp['message']}")
    print("-" * 64)
    print(f"Log: {log_path}")
    print("=" * 64)


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--company", action="append",
                    help="only this company or feed (name, case-insensitive, e.g. Remotive); repeatable")
    ap.add_argument("--source", choices=(*config.ATS_TYPES, *config.FEED_TYPES), help="only this ATS or feed")
    ap.add_argument("--dry-run", action="store_true", help="local only: use a temporary copy of the database")
    ap.add_argument("--db", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--trigger", default="cli", choices=("cli", "web", "schedule"), help=argparse.SUPPRESS)
    ap.add_argument("-v", "--verbose", action="store_true", help="show per-request progress")
    args = ap.parse_args(argv)

    log_path = setup_logging(args.verbose)
    target = args.db or store.database_url() or str(config.DB_PATH)
    tmpdir = None
    if args.dry_run:
        if str(target).startswith("postgres"):
            logging.error("--dry-run only works with the local SQLite database")
            return 2
        tmpdir = tempfile.mkdtemp(prefix="internship-dry-")
        copy_path = Path(tmpdir) / "state.db"
        if Path(target).exists():
            src = db.connect(target)
            src.backup_to(copy_path)
            src.close()
        target = str(copy_path)
        logging.info("Dry run: changes go to a temporary copy and are discarded")

    conn = db.connect(target)
    try:
        try:
            whitelist = workspace.companies(conn)
            fetch_profile = workspace.fetch_profile(conn)
            users = [RunUser(u["id"], u["email"], workspace.effective_profile(conn, u))
                     for u in workspace.run_users(conn)]
        except (OSError, ValueError) as exc:
            logging.error("Config problem: %s", exc)
            return 2
        sources = whitelist + config.feed_sources(fetch_profile)
        if args.company:
            wanted = {c.lower() for c in args.company}
            sources = [c for c in sources if c.name.lower() in wanted]
            missing = wanted - {c.name.lower() for c in sources}
            if missing:
                logging.error("Not in your companies or enabled feeds: %s", ", ".join(sorted(missing)))
                return 2
            for c in sources:
                c.enabled = True
        if args.source:
            sources = [c for c in sources if c.ats == args.source]

        run = Run(conn, fetch_profile, sources, args.trigger,
                  {"company": args.company, "source": args.source, "dry_run": args.dry_run},
                  whitelist=whitelist, users=users)
        result = run.execute()
    except db.RunInProgress as exc:
        logging.error("%s. Wait for it to finish (watch it on the Bot activity page).", exc)
        return 3
    except KeyboardInterrupt:
        logging.warning("Interrupted")
        return 130
    finally:
        conn.close()
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)
    print_summary(result, log_path)
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
