"""The tracker website: JSON API + static files.

local: no sign-in, one user, binds to 127.0.0.1 (serve.py).
cloud: Google / email sign-in, many users, each sees only their own tracker (gunicorn "web.app:create_app()").
"""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import secrets
import subprocess
import sys
import threading
from datetime import date, timedelta
from functools import wraps
from pathlib import Path

import requests
from flask import Flask, Response, abort, g, jsonify, redirect, request, send_from_directory, session
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from bot import config, db, importer, mailer, pipeline, programs, store, workspace
from bot.fetchers import FETCHERS
from bot.filters import REASON_LABELS
from bot.http_client import HttpClient, RobotsDisallowed

from bot.mailer import _env as env

from .auth import init_auth

log = logging.getLogger(__name__)
STATIC = Path(__file__).resolve().parent / "static"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
CSP = ("default-src 'self'; img-src 'self' data: https://lh3.googleusercontent.com; style-src 'self'; "
       "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def create_app(db_target: str | None = None, demo: bool = False) -> Flask:
    cloud = workspace.is_cloud() and not demo
    app = Flask(__name__, static_folder=None)
    secret = os.environ.get("SECRET_KEY", "")
    if cloud and len(secret) < 32:
        raise RuntimeError("Set SECRET_KEY (32+ random characters) before running in cloud mode.")
    if cloud and not db_target and not store.database_url().startswith(("postgres://", "postgresql://")) \
            and os.environ.get("ALLOW_LOCAL_DB") != "1":
        # Without Postgres the accounts would live in data/state.db, which Render's disk wipes on every
        # restart: users, sign-ins and profiles silently disappear. Refuse to start instead.
        raise RuntimeError("Cloud mode needs DATABASE_URL set to a Postgres connection string (e.g. Neon); "
                           "the server's local disk is wiped on restart. Set ALLOW_LOCAL_DB=1 to test without one.")
    app.config.update(
        SECRET_KEY=secret or secrets.token_hex(32),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=cloud and os.environ.get("INSECURE_COOKIES") != "1",
        SESSION_COOKIE_NAME="tracker_session",
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
        MAX_CONTENT_LENGTH=2_000_000,
        JSON_SORT_KEYS=False,
    )
    if cloud:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    target = db_target or store.database_url() or str(config.DB_PATH)
    first = db.connect(target)
    if not cloud:
        db.ensure_local_user(first)
    first.close()
    app.extensions["tracker"] = {"target": target, "cloud": cloud, "demo": demo, "run_lock": threading.Lock()}

    def get_conn():
        if "conn" not in g:
            g.conn = db.connect(target)
        return g.conn

    app.get_conn = get_conn  # used by web.auth

    @app.teardown_appcontext
    def _close(_exc):
        conn = g.pop("conn", None)
        if conn is not None:
            conn.close()

    if cloud:
        init_auth(app)

    # ------------------------------------------------------------------ request guards

    @app.before_request
    def guard():
        if not cloud:
            host = (request.host or "").rsplit(":", 1)[0].lower()
            if host not in LOCAL_HOSTS:
                abort(403)
        if request.method not in ("GET", "HEAD", "OPTIONS") and request.path.startswith(("/api/", "/auth/")):
            # Custom header => cross-site pages can't send these requests (CORS preflight is never approved).
            if request.headers.get("X-Tracker") != "1":
                return jsonify({"error": "Missing X-Tracker header"}), 403
        g.user = None
        if not cloud:
            g.user = db.ensure_local_user(get_conn())
        elif session.get("uid"):
            g.user = db.get_user(get_conn(), session["uid"])
            if g.user is None:
                session.clear()
            elif request.path.startswith("/api/"):
                db.touch_user(get_conn(), g.user)  # "last active" drives pausing inactive accounts
        if request.path.startswith("/api/") and g.user is None:
            return jsonify({"error": "Sign in required"}), 401
        if demo and request.method in ("PUT", "DELETE") and request.path in ("/api/profile", "/api/sources",
                                                                             "/api/companies", "/api/account"):
            # Demo mode shares your real profile.json / companies.csv, so it must not change them.
            return jsonify({"error": "Demo mode: settings are read-only. Start the tracker without --demo to edit them."}), 409

    @app.after_request
    def headers(resp: Response):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["X-Frame-Options"] = "DENY"
        if resp.mimetype == "text/html":
            resp.headers["Content-Security-Policy"] = CSP
        if request.path.startswith(("/api/", "/auth/")):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.errorhandler(ApiError)
    def api_error(exc: ApiError):
        return jsonify({"error": str(exc)}), exc.status

    @app.errorhandler(ValueError)
    def value_error(exc):
        return jsonify({"error": str(exc) or "Bad request"}), 400

    @app.errorhandler(Exception)
    def crash(exc):
        if isinstance(exc, HTTPException):
            return exc  # normal HTTP errors (404, 403...)
        log.exception("Unhandled error on %s %s", request.method, request.path)
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 500

    def body() -> dict:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ApiError(400, "Body must be a JSON object")
        return data

    def me() -> dict:
        return g.user

    def is_admin() -> bool:
        return bool(g.user and g.user.get("is_admin"))

    def admin_only(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            if not is_admin():
                raise ApiError(403, "Only admins can change shared sources or start runs.")
            return fn(*a, **kw)
        return wrapper

    def profile() -> dict:
        return workspace.effective_profile(get_conn(), me())

    def http_client() -> HttpClient:
        net = workspace.fetch_profile(get_conn())["network"]
        return HttpClient(net["user_agent"], net.get("min_delay_seconds", 1), net.get("timeout_seconds", 30), 1)

    def can_run() -> bool:
        return not demo

    def run_cooldown_minutes() -> int:
        """Minimum gap between full source checks started by regular users (admins can always start one)."""
        try:
            return max(0, int(env("RUN_COOLDOWN_MINUTES") or 120))
        except ValueError:
            return 120

    # ------------------------------------------------------------------ pages

    def page(name: str):
        return send_from_directory(STATIC, name, max_age=0)

    @app.get("/")
    def index():
        if cloud and g.user is None:
            return redirect("/login")
        return page("index.html")

    @app.get("/login")
    def login_page():
        if not cloud or g.user is not None:
            return redirect("/")
        return page("login.html")

    @app.get("/healthz")
    def healthz():
        get_conn().execute("SELECT 1 AS ok").fetchone()
        return jsonify({"ok": True})

    @app.get("/<path:name>")
    def static_file(name: str):
        if name.startswith(("api/", "auth/")):
            abort(404)
        target_file = (STATIC / name).resolve()
        if STATIC not in target_file.parents or not target_file.is_file():
            abort(404)
        return send_from_directory(STATIC, name, max_age=0)

    # ------------------------------------------------------------------ account & meta

    @app.get("/api/me")
    def api_me():
        u, p = me(), profile()
        resumed = False
        if cloud and u.get("paused_at"):
            db.resume_user(get_conn(), u["id"])  # coming back switches the daily search on again
            resumed = True
        from bot.activity import inactive_days
        return jsonify({"id": u["id"], "email": u["email"], "name": u.get("name") or "", "picture": u.get("picture") or "",
                        "is_admin": is_admin(), "cloud": cloud, "demo": demo, "can_run": can_run(),
                        "profile_complete": workspace.profile_complete(p), "resumed": resumed,
                        "inactive_days": inactive_days() if cloud else None,
                        "run_schedule": os.environ.get("RUN_SCHEDULE_TEXT", "daily at 9:00 IST") if cloud else ""})

    @app.delete("/api/account")
    def delete_account():
        if not cloud:
            raise ApiError(400, "There's no account to delete in local mode.")
        db.delete_user(get_conn(), me()["id"])
        session.clear()
        return jsonify({"deleted": True})

    @app.get("/api/meta")
    def meta():
        p = profile()
        return jsonify({"demo": demo, "statuses": db.STATUSES, "reason_labels": REASON_LABELS, "goals": p.get("goals", {}),
                        "name": p.get("preferred_name") or p.get("name"),
                        "followup_days": p["drafts"].get("followup_days", 7), "today": date.today().isoformat()})

    @app.get("/api/status")
    def status():
        conn, uid = get_conn(), me()["id"]
        return jsonify({"running": db.current_run(conn, uid), "last": db.last_finished_run(conn, uid),
                        "jobs_version": db.jobs_version(conn, uid)})

    # ------------------------------------------------------------------ jobs

    @app.get("/api/jobs")
    def jobs():
        return jsonify({"jobs": db.list_jobs(get_conn(), me()["id"])})

    @app.get("/api/jobs/<int:job_id>")
    def job(job_id: int):
        j = db.get_job(get_conn(), me()["id"], job_id)
        if not j:
            raise ApiError(404, "No such job")
        return jsonify(j)

    @app.patch("/api/jobs/<int:job_id>")
    def update_job(job_id: int):
        try:
            return jsonify(db.update_job_user_fields(get_conn(), me()["id"], job_id, body(),
                                                     profile()["drafts"].get("followup_days", 7)))
        except KeyError:
            raise ApiError(404, "No such job")

    @app.post("/api/jobs/<int:job_id>/regenerate")
    def regenerate(job_id: int):
        conn = get_conn()
        try:
            return jsonify(pipeline.regenerate_drafts(conn, me()["id"], job_id, profile(), workspace.companies(conn)))
        except KeyError:
            raise ApiError(404, "No such job")
        except LookupError as exc:
            raise ApiError(409, str(exc))

    @app.post("/api/jobs/<int:job_id>/block")
    def block_company(job_id: int):
        """'Not a real employer': blocks the job's company for this user and skips their listings from it."""
        conn, uid = get_conn(), me()["id"]
        j = db.get_job(conn, uid, job_id)
        if not j:
            raise ApiError(404, "No such job")
        if not demo:  # demo rows are fictional; don't touch your real blocklist
            workspace.block_company(conn, me(), j["company"])
        moved = db.skip_company_jobs(conn, uid, j["company"], f"You marked {j['company']} as not a real employer")
        return jsonify({"job": db.get_job(conn, uid, job_id), "company": j["company"], "moved": moved})

    @app.post("/api/rescore")
    def rescore():
        conn = get_conn()
        return jsonify(pipeline.rescore_user(conn, me()["id"], profile(), workspace.companies(conn)))

    @app.post("/api/jobs/lookup")
    def lookup_job():
        return jsonify(importer.lookup(str(body().get("url", "")), http_client()))

    @app.post("/api/jobs/manual")
    def add_manual():
        data = body()
        status_ = data.get("status") or ""
        if status_ and status_ not in db.STATUSES:
            raise ApiError(400, f"Unknown status {status_!r}")
        job_ = importer.job_from_fields(data)
        conn = get_conn()
        job_id, created, note = pipeline.add_manual(conn, me()["id"], job_, profile(), workspace.companies(conn),
                                                    http_client(), status_, data.get("applied_date") or "")
        return jsonify({"job_id": job_id, "created": created, "note": note})

    @app.get("/api/jobs.csv")
    def export_csv():
        rows = db.list_jobs(get_conn(), me()["id"])
        cols = [("id", "ID"), ("date_found", "Date Found"), ("company", "Company"), ("role", "Role"),
                ("source", "Source"), ("platform", "Platform"), ("url", "Job URL"), ("score", "Match Score"),
                ("missing_skills", "Missing Skills"), ("remote_type", "Remote Type"), ("region", "Region Allowed"),
                ("hours_label", "Hours/Week"), ("paid", "Paid?"), ("compensation", "Stipend / Pay"),
                ("ai_screening", "AI Screening Flag"), ("red_flags", "Red Flags"), ("status", "Status"),
                ("applied_date", "Applied Date"), ("followup_date", "Follow-up Date"),
                ("contact", "Contact/Referral"), ("user_notes", "Notes")]
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow([label for _, label in cols])
        for j in rows:
            out = []
            for key, _ in cols:
                v = j.get(key)
                if isinstance(v, list):
                    v = "; ".join(v)
                if key == "ai_screening":
                    v = "Yes" if v else "No"
                out.append("" if v is None else v)
            w.writerow(out)
        return Response("﻿" + buf.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="internships-{date.today()}.csv"'})

    @app.get("/api/stats")
    def stats():
        return jsonify(db.stats(get_conn(), me()["id"]))

    # ------------------------------------------------------------------ runs

    @app.get("/api/runs")
    def runs():
        return jsonify({"runs": db.list_runs(get_conn(), me()["id"])})

    @app.get("/api/runs/<int:run_id>/events")
    def events(run_id: int):
        conn, uid = get_conn(), me()["id"]
        run = db.get_run(conn, run_id, uid)
        if not run:
            raise ApiError(404, "No such run")
        return jsonify({"run": run, "events": db.run_events(conn, run_id, int(request.args.get("after", 0)), uid)})

    @app.post("/api/runs")
    def start_run():
        """Run bot now: (1) instantly match this user against postings fetched in the last week, then (2) start a
        fresh check of every source in the background, unless one ran recently (regular users) or is running."""
        if demo:
            raise ApiError(409, "Demo mode: start the tracker without --demo to run the bot.")
        conn, u, p = get_conn(), me(), profile()
        matched = pipeline.match_from_cache(conn, u["id"], p, workspace.companies(conn))
        started, note = False, ""
        state = app.extensions["tracker"]
        with state["run_lock"]:
            current = db.current_run(conn)
            last = db.last_run_started(conn)
            wait = 0
            if cloud and not is_admin() and last:
                from datetime import datetime
                elapsed = (datetime.now() - datetime.fromisoformat(last)).total_seconds() / 60
                wait = max(0, int(run_cooldown_minutes() - elapsed))
            if current:
                note = f"A full check of every source is already running (run #{current['id']}); results appear as it goes."
            elif wait:
                note = (f"Every source was checked {int((run_cooldown_minutes() - wait))} min ago, so a new full "
                        f"check can start in {wait} min. Your matches from that check are already in.")
            else:
                _launch_full_run(state)
                started = True
                note = "A fresh check of every source started. Watch it on Bot activity; new matches appear as it runs."
        return jsonify({"started": started, "note": note, "matched": matched})

    def _launch_full_run(state: dict) -> None:
        if cloud and env("GITHUB_REPO") and env("GITHUB_TOKEN"):
            _dispatch_github_run()
            return
        cmd = [sys.executable, str(config.ROOT / "run.py"), "--trigger", "web"]
        if not cloud:
            cmd += ["--db", state["target"]]
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        subprocess.Popen(cmd, cwd=config.ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         stdin=subprocess.DEVNULL, creationflags=flags)

    def _dispatch_github_run() -> dict:
        repo, token = env("GITHUB_REPO"), env("GITHUB_TOKEN")
        workflow = os.environ.get("GITHUB_WORKFLOW", "daily-run.yml")
        r = requests.post(f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/dispatches",
                          headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                                   "X-GitHub-Api-Version": "2022-11-28"},
                          json={"ref": os.environ.get("GITHUB_REF", "main")}, timeout=20)
        if r.status_code != 204:
            raise ApiError(502, f"GitHub didn't start the run (HTTP {r.status_code}): {r.text[:200]}")
        return {"started": True, "note": "Started on GitHub Actions; it shows up here within a minute."}

    @app.get("/api/skipped")
    def skipped():
        return jsonify({"skipped": db.rejected_listings(get_conn(), me()["id"])})

    @app.post("/api/skipped/track")
    def track_anyway():
        conn = get_conn()
        try:
            job_id = pipeline.track_anyway(conn, me()["id"], str(body().get("url_key", "")), profile(),
                                           workspace.companies(conn))
        except KeyError:
            raise ApiError(404, "That listing isn't in the bot's history")
        return jsonify({"job_id": job_id})

    # ------------------------------------------------------------------ profile & shared sources

    @app.get("/api/profile")
    def get_profile():
        conn = get_conn()
        return jsonify({"profile": workspace.user_profile_raw(conn, me()),
                        "sources": workspace.global_settings(conn) if is_admin() else None,
                        "is_admin": is_admin()})

    @app.put("/api/profile")
    def put_profile():
        data = body().get("profile")
        if not isinstance(data, dict):
            raise ApiError(400, "profile must be an object")
        conn = get_conn()
        try:
            saved = workspace.save_user_profile(conn, me(), data)
        except ValueError as exc:
            raise ApiError(422, str(exc))
        # New or changed skills: match against recent postings right away instead of waiting for tomorrow's run.
        matched = pipeline.match_from_cache(conn, me()["id"], profile(), workspace.companies(conn)) if not demo else None
        return jsonify({"profile": saved, "saved": True, "matched": matched})

    @app.put("/api/sources")
    @admin_only
    def put_sources():
        data = body().get("sources")
        if not isinstance(data, dict):
            raise ApiError(400, "sources must be an object")
        return jsonify({"sources": workspace.save_global_settings(get_conn(), data), "saved": True})

    @app.get("/api/companies")
    def get_companies():
        conn, uid = get_conn(), me()["id"]
        items = [c.to_row() for c in workspace.companies(conn)]
        state = {r["name"]: dict(r) for r in conn.execute("SELECT * FROM company_state")}
        by_company, by_source, _ = db.company_job_counts(conn, uid)
        for c in items:
            c["state"] = state.get(c["name"])
            c["tracked"] = by_company.get(c["name"], 0)
        feeds_cfg = workspace.global_settings(conn).get("feeds", {})
        feeds = [{"ats": ats, "name": label, "enabled": bool((feeds_cfg.get(ats) or {}).get("enabled")),
                  "config": feeds_cfg.get(ats) or {}, "state": state.get(label), "tracked": by_source.get(ats, 0)}
                 for ats, label in config.FEED_TYPES.items()]
        p = profile()
        return jsonify({"companies": items, "ats_types": list(config.ATS_TYPES), "feeds": feeds,
                        "include_junior_roles": bool(p["filters"].get("include_junior_roles")),
                        "can_edit": is_admin()})

    @app.put("/api/companies")
    @admin_only
    def put_companies():
        rows = body().get("companies")
        if not isinstance(rows, list):
            raise ApiError(400, "companies must be a list")
        try:
            workspace.save_companies(get_conn(), [config.company_from_row(r) for r in rows])
        except ValueError as exc:
            raise ApiError(422, str(exc))
        return get_companies()

    @app.post("/api/companies/test")
    @admin_only
    def test_company():
        c = config.company_from_row(body())
        if c.ats not in FETCHERS or not c.board_token:
            raise ApiError(400, "Pick an ATS and enter a board token.")
        try:
            out = FETCHERS[c.ats].fetch(c, http_client(), None, lambda m: None)
        except RobotsDisallowed as exc:
            return jsonify({"ok": False, "message": str(exc)})
        if not out.ok:
            return jsonify({"ok": False, "message": out.error})
        return jsonify({"ok": True, "postings": out.postings, "internships": len(out.jobs), "message": out.warning})

    @app.get("/api/platforms")
    def platforms():
        path = config.ROOT / "platforms.json"
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"groups": []}
        conn, uid = get_conn(), me()["id"]
        _, by_source, by_platform = db.company_job_counts(conn, uid)
        state = {r["name"]: dict(r) for r in conn.execute("SELECT * FROM company_state")}
        program_status = programs.all_status(conn)
        feeds_cfg = workspace.global_settings(conn).get("feeds", {})
        boards = sum(n for s, n in by_source.items() if s in config.ATS_TYPES)
        for group in data.get("groups", []):
            for p in group.get("platforms", []):
                src = p.get("source")
                if p.get("program"):
                    p["program_status"] = program_status.get(p["program"])
                    p["tracked"] = sum(n for name, n in by_platform.items() if p["name"].split(" ")[0].lower() in name.lower())
                elif src == "boards":
                    p["tracked"] = boards
                elif src:
                    p["tracked"] = by_source.get(src, 0)
                    p["state"] = state.get(config.FEED_TYPES.get(src, ""))
                    p["enabled"] = bool((feeds_cfg.get(src) or {}).get("enabled"))
                else:
                    p["tracked"] = sum(n for name, n in by_platform.items()
                                       if name.lower().split(" (")[0] in p["name"].lower()
                                       or p["name"].lower().split(" (")[0] in name.lower())
        return jsonify(data)

    return app
