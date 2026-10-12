"""One run: fetch every enabled company in parallel (polite per-host limits), evaluate each internship,
store results, and log every decision as a run event the website shows live."""
from __future__ import annotations

import json
import logging
import queue
import time
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import datetime

from . import db, filters, flags, notes, programs, scoring
from .config import Company
from .fetchers import FETCHERS, FetchOutcome, Job, is_entry_level
from .http_client import HttpClient, RobotsDisallowed
from .store import Conn
from .textutil import normalize_url, sha1

log = logging.getLogger(__name__)
_LOG_LEVELS = {"debug": logging.DEBUG, "warn": logging.WARNING, "error": logging.ERROR}


@dataclass
class Evaluation:
    kept: bool
    reason: str = ""
    detail: str = ""
    record: dict = field(default_factory=dict)
    initial_status: str = "To Review"
    note: str = ""
    answers: str = ""


def evaluate(job: Job, company: Company, profile: dict, domain_age, force: bool = False,
             context: dict | None = None) -> Evaluation:
    """Applies filters, scoring, flags and drafts. force=True skips the reject rules (used by 'Track anyway').
    context: run-wide facts, e.g. {"template_others": [companies posting the same text]}."""
    junior_ok = bool(job.via) and profile["filters"].get("include_junior_roles",
                                                         profile.get("feeds", {}).get("include_junior_roles", False))
    entry = is_entry_level(job.title) or is_entry_level(job.employment_type)
    if not force and not (filters.is_internship(job.title, job.employment_type) or (junior_ok and entry)):
        return Evaluation(False, "not_internship", job.title)

    remote_type = filters.workplace_type(job)
    onsite_ok = filters.onsite_allowed(job, remote_type, profile)
    if (not force and profile["filters"].get("remote_only", True) and remote_type not in filters.REMOTE_TYPES_OK
            and not onsite_ok):
        return Evaluation(False, "not_remote", f"{remote_type}: “{job.location or 'no location given'}”")

    phrase = filters.find_reject_phrase(job, profile, allow_onsite=onsite_ok)
    if phrase and not force:
        return Evaluation(False, "restricted_phrase", phrase)

    reg = filters.region(job, profile)
    if reg.reject_detail and not force:
        return Evaluation(False, "region_restricted", reg.reject_detail)

    hours_num, hours_label = filters.hours(job, profile)
    paid = filters.paid(job)
    score, matched, missing, breakdown = scoring.score(job, profile)
    if profile.get("llm_scoring", {}).get("enabled"):
        llm = scoring.llm_rescore(job, profile)
        if llm is not None:
            breakdown["llm_score"] = llm
    min_score = profile["filters"].get("min_score", 0)
    if score < min_score and not force:
        return Evaluation(False, "low_score", f"score {score} is below your minimum of {min_score}")

    ai = flags.ai_screening(job, company, profile)
    night = flags.night_shift(job, profile)
    whitelisted = not company.is_feed and bool(company.board_token)
    red = flags.red_flags(job, company, paid, domain_age(company.website or job.company_url), profile, whitelisted)
    warnings = []
    if flags.is_blocked(job.company, profile):
        red.insert(0, f"You marked {job.company} as not a real employer")
    if not whitelisted:  # vetted companies on the whitelist are trusted by name
        name_red, name_warn = flags.company_name_flags(job.company)
        red += name_red
        warnings += name_warn
    copies = (context or {}).get("template_others") or []
    if copies:
        shown = ", ".join(copies[:3]) + (f" and {len(copies) - 3} more" if len(copies) > 3 else "")
        red.append(f"The same posting text is used by {len(copies)} other compan{'y' if len(copies) == 1 else 'ies'} "
                   f"({shown}): a mass-posted template, common with pay-to-train outfits")

    if phrase:
        warnings.append(f"Tracked manually despite: {phrase}")
    if reg.reject_detail:
        warnings.append(f"Tracked manually despite region: {reg.reject_detail}")
    if paid == "No":
        warnings.append("Unpaid: review manually")
    elif paid == "Unknown" and profile["filters"].get("require_paid", True):
        warnings.append("Pay not stated")
    if reg.label == "Unspecified":
        warnings.append("Eligible countries not stated")
    if remote_type == "Remote (from description)":
        warnings.append("Remote only per description text; location field says otherwise")
    if onsite_ok:
        warnings.append(f"{remote_type} in “{job.location}”: allowed by your On-site OK locations setting")
    if job.via and not whitelisted:
        warnings.append(f"Found via {job.via}. {job.company} isn't on your company whitelist, so check the company "
                        "(website, reviews, LinkedIn) before applying.")
    if job.via and job.source != "manual" and not filters.is_internship(job.title, job.employment_type):
        warnings.append("Junior / entry-level role, not an internship")

    project = notes.pick_project(profile, job, matched)
    record = {
        "url": job.url, "apply_url": job.apply_url or job.url, "source": job.source, "platform": job.via,
        "ats_id": job.ats_id,
        "company": job.company, "role": job.title, "location": job.location, "remote_type": remote_type,
        "region": reg.label, "hours_num": hours_num, "hours_label": hours_label, "paid": paid,
        "compensation": job.compensation, "score": score, "matched_skills": matched, "missing_skills": missing,
        "score_detail": breakdown, "ai_screening": 1 if ai else 0, "ai_reasons": ai, "red_flags": red,
        "warnings": warnings, "night_shift": 1 if night else 0, "description": job.description[:40000],
        "posted_at": job.posted_at,
    }
    return Evaluation(
        kept=True,
        record=record,
        initial_status="Skipped (red flag)" if red else "To Review",
        note=notes.build_note(profile, job, matched, project, night or profile["filters"].get("night_shift_ok", False)),
        answers=notes.build_answers(profile, job, project, reg.label),
    )


def resolve_company(job: Job, whitelist: dict[str, Company], board: Company | None = None) -> Company:
    """The company record to judge a job by: the board it came from, the whitelisted company with the same
    name (for feed jobs), or a stand-in with no board token for companies you haven't vetted."""
    if board is not None and not board.is_feed:
        return board
    known = whitelist.get(job.company.strip().lower())
    if known:
        return known
    return Company(job.company, f"via {job.via}" if job.via else "", job.source, "", website=job.company_url)


def machine_hash(record: dict) -> str:
    return sha1(json.dumps(record, sort_keys=True, ensure_ascii=False, default=str))


@dataclass
class RunUser:
    """One account the run evaluates postings for."""
    id: int
    email: str
    profile: dict
    counts: Counter = field(default_factory=Counter)
    reasons: Counter = field(default_factory=Counter)


class Run:
    def __init__(self, conn: Conn, fetch_profile: dict, sources: list[Company], trigger: str,
                 run_args: dict | None = None, whitelist: list[Company] | None = None,
                 users: list[RunUser] | None = None):
        """sources: company boards and feeds to fetch. whitelist: every vetted company (recognizes them in feed
        results). users: whose trackers to fill; each is judged by their own profile."""
        self.conn, self.trigger = conn, trigger
        self.companies = sources
        self.profile = fetch_profile
        self.whitelist = {c.name.strip().lower(): c for c in (whitelist if whitelist is not None else sources)
                          if not c.is_feed}
        self.users = users or []
        net = fetch_profile["network"]
        self.workers = max(1, int(net.get("max_workers", 6)))
        self.http = HttpClient(net["user_agent"], net.get("min_delay_seconds", 1.0), net.get("timeout_seconds", 30),
                               net.get("retries", 3), pool=self.workers + 2)
        self.domains = flags.DomainChecker(conn, self.http)
        self.templates = flags.TemplateIndex.from_conn(conn)
        self.counts: Counter = Counter()
        self.company_results: list[dict] = []
        self.progress_q: queue.Queue = queue.Queue()
        self.run_args = run_args or {}
        self.run_id: int | None = None
        self._last_beat = 0.0

    # ------------------------------------------------------------ events

    def event(self, level: str, kind: str, message: str, company: str | None = None, job_id=None, url=None,
              user: RunUser | None = None):
        db.add_event(self.conn, self.run_id, level, kind, message, company, job_id, url, user.id if user else None)
        who = f"<{user.email}> " if user and len(self.users) > 1 else ""
        log.log(_LOG_LEVELS.get(level, logging.INFO), "%s%s%s", who, f"[{company}] " if company else "", message)

    def _beat(self, force: bool = False, **fields):
        if force or fields or time.monotonic() - self._last_beat > 3:
            db.update_run(self.conn, self.run_id, counts=self._counts_payload(), **fields)
            for u in self.users:
                db.save_user_counts(self.conn, self.run_id, u.id, self._user_payload(u))
            self.conn.commit()
            self._last_beat = time.monotonic()

    def _counts_payload(self) -> dict:
        totals: Counter = Counter()
        for u in self.users:
            totals.update(u.counts)
        return {**self.counts, **totals, "companies": self.company_results, "requests": self.http.request_count,
                "users": len(self.users)}

    @staticmethod
    def _user_payload(u: RunUser) -> dict:
        return {**u.counts, "skip_reasons": dict(u.reasons)}

    def _drain_progress(self):
        while True:
            try:
                company, message = self.progress_q.get_nowait()
            except queue.Empty:
                return
            self.event("debug", "progress", message, company)

    # ------------------------------------------------------------ main

    def execute(self) -> dict:
        self.run_id = db.start_run(self.conn, self.trigger, self.run_args)
        started = time.monotonic()
        enabled = [c for c in self.companies if c.enabled]
        try:
            self._beat(force=True, phase="fetching", companies_total=len(enabled))
            boards = sum(1 for c in enabled if not c.is_feed)
            self.event("info", "start", f"Run started ({self.trigger}): {boards} company boards + "
                                        f"{len(enabled) - boards} job feeds for {len(self.users)} "
                                        f"account{'s' if len(self.users) != 1 else ''}, {self.workers} parallel "
                                        "workers, 1 request/sec per host")
            for u in self.users:
                filled = db.fill_followups(self.conn, u.id, u.profile["drafts"].get("followup_days", 7))
                if filled:
                    self.event("info", "followup", f"Set follow-up dates on {filled} applied job(s)", user=u)
                if not workspace_profile_complete(u.profile):
                    self.event("warn", "profile", "Your profile has no skills yet, so nothing was matched for you. "
                                                  "Add skills in Settings.", user=u)
            self.active_users = [u for u in self.users if workspace_profile_complete(u.profile)]
            cache = db.detail_cache(self.conn)

            done = 0
            to_fetch = []
            for c in enabled:
                wait_note = self._feed_cooldown(c)
                if wait_note:
                    self.company_results.append({"company": c.name, "ats": c.ats, "ok": True, "postings": 0,
                                                 "internships": 0, "kept": 0, "seconds": 0, "message": wait_note})
                    self.event("info", "cooldown", f"{c.name}: {wait_note}", c.name)
                    done += 1
                else:
                    to_fetch.append(c)
            with ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix="fetch") as pool:
                pending = {pool.submit(self._fetch, c, cache): c for c in to_fetch}
                while pending:
                    finished, _ = wait(pending, timeout=0.5, return_when=FIRST_COMPLETED)
                    self._drain_progress()
                    for fut in finished:
                        pending.pop(fut)
                        self._process(fut.result())
                        done += 1
                        self._beat(force=True, companies_done=done)
                    self._beat()
            self._drain_progress()

            if programs.due(self.conn):
                for p in programs.check_all(self.conn, self.http):
                    if p.get("error"):
                        self.event("warn", "program", f"{p['name']}: couldn't check ({p['error']})")
                    else:
                        nxt = f" Next: {p['next_label']} on {p['next_date']}." if p.get("next_date") else ""
                        self.event("info", "program", f"{p['name']}: {p['status']}.{nxt}")

            secs = time.monotonic() - started
            status = "partial" if self.counts["fetch_errors"] else "ok"
            self.event("info", "finish", self.summary_line(secs))
            for u in self.users:
                db.save_user_counts(self.conn, self.run_id, u.id, self._user_payload(u))
            db.update_run(self.conn, self.run_id, status=status, finished_at=db.now_iso(), phase="done",
                          counts=self._counts_payload())
            db.prune_old_runs(self.conn)
            self.conn.commit()
            return {"run_id": self.run_id, "status": status, "seconds": secs, **self._counts_payload(),
                    "per_user": {u.email: self._user_payload(u) for u in self.users}}
        except BaseException as exc:
            self.conn.rollback()
            msg = "Run interrupted" if isinstance(exc, KeyboardInterrupt) else f"{type(exc).__name__}: {exc}"
            self.event("error", "crash", msg)
            db.update_run(self.conn, self.run_id, status="error", finished_at=db.now_iso(), phase="failed",
                          error=msg, counts=self._counts_payload())
            self.conn.commit()
            raise

    def _feed_cooldown(self, c: Company) -> str:
        """Non-empty (the reason) when a feed was fetched too recently to ask again, per its terms."""
        hours = float(c.options.get("min_hours_between_fetches", 0) or 0) if c.is_feed else 0
        if hours <= 0:
            return ""
        row = self.conn.execute("SELECT last_fetch_at FROM company_state WHERE name=? AND ok=1", (c.name,)).fetchone()
        if not row or not row["last_fetch_at"]:
            return ""
        age_h = (datetime.now() - datetime.fromisoformat(row["last_fetch_at"])).total_seconds() / 3600
        if age_h >= hours:
            return ""
        terms = " per Remotive’s API terms" if c.ats == "remotive" else ""
        return (f"not fetched: last fetch was {age_h:.1f} h ago and the minimum gap is {hours:g} h"
                f" (Settings → Job feeds){terms}")

    def summary_line(self, secs: float) -> str:
        c = self._counts_payload()
        who = f" across {len(self.users)} accounts" if len(self.users) > 1 else ""
        return (f"Done in {secs:.0f}s: {c.get('postings', 0)} postings scanned, {c.get('internships', 0)} internships"
                f"{who}: {c.get('new', 0)} added, {c.get('updated', 0)} updated, {c.get('unchanged', 0)} already "
                f"tracked, {c.get('skipped', 0)} skipped, {c.get('closed', 0)} closed, "
                f"{c.get('fetch_errors', 0)} fetch errors")

    def _fetch(self, company: Company, cache: dict) -> FetchOutcome:
        """Runs in a worker thread: network only, no DB access."""
        start = time.monotonic()
        fetcher = FETCHERS[company.ats]

        def progress(msg: str):
            self.progress_q.put((company.name, msg))

        progress(f"Fetching {company.name} feed" if company.is_feed
                 else f"Fetching {company.ats} board “{company.board_token}”")
        try:
            out = fetcher.fetch(company, self.http, cache, progress)
        except RobotsDisallowed as exc:
            out = FetchOutcome(company, error=f"Skipped: {exc}")
        except Exception as exc:  # one broken board must not stop the run
            log.exception("Fetcher crashed for %s", company.name)
            out = FetchOutcome(company, error=f"{type(exc).__name__}: {exc}")
        out.seconds = time.monotonic() - start
        return out

    def _process(self, out: FetchOutcome) -> None:
        c = out.company
        result = {"company": c.name, "ats": c.ats, "ok": out.ok, "postings": out.postings,
                  "internships": len(out.jobs), "kept": 0, "seconds": round(out.seconds, 1),
                  "message": out.error or out.warning}
        if not out.ok:
            self.counts["fetch_errors"] += 1
            self.event("error", "fetch_error", f"{c.name}: {out.error}", c.name)
            self._save_company_state(out, result)
            return

        self.counts["postings"] += out.postings
        self.counts["internships"] += len(out.jobs)
        self.counts["not_internship"] += out.postings - len(out.jobs)
        extra = f", {out.details_fetched} detail pages read, {out.details_cached} reused from cache" \
            if out.details_fetched or out.details_cached else ""
        self.event("warn" if out.warning else "info", "fetched",
                   f"{c.name}: {out.postings} postings, {len(out.jobs)} internship(s){extra}"
                   + (f". {out.warning}" if out.warning else ""), c.name)

        open_keys: set[str] = set()
        kept_any: set[str] = set()
        for job in out.jobs:  # index first so copies within this batch are seen too
            self.templates.add(job.company, job.description)
        for job in out.jobs:
            key = normalize_url(job.url)
            if not key:
                continue
            open_keys.add(key)
            db.upsert_posting(self.conn, self.run_id, key, job.to_dict())
            company = resolve_company(job, self.whitelist, c)
            context = {"template_others": self.templates.others(job.company, job.description)}
            for u in self.active_users:
                if self._evaluate_for(u, job, key, company, context):
                    kept_any.add(key)
        result["kept"] = len(kept_any)

        if out.complete:
            for u in self.active_users:
                for closed in db.close_missing(self.conn, u.id, c.ats, open_keys, company=None if c.is_feed else c.name):
                    u.counts["closed"] += 1
                    self.event("info", "closed", f"No longer listed: “{closed['role']}”", closed["company"],
                               closed["id"], user=u)
        self._save_company_state(out, result)

    def _evaluate_for(self, u: RunUser, job: Job, key: str, company: Company, context: dict | None = None) -> bool:
        ev = evaluate(job, company, u.profile, self.domains.age_days, context=context)
        if not ev.kept:
            self._record_skip(u, job, key, ev.reason, ev.detail)
            return False
        dup = db.find_duplicate(self.conn, u.id, key, job.company, job.title)
        if dup and not db.job_exists(self.conn, u.id, key):
            self._record_skip(u, job, key, "duplicate", f"same company and title as tracked job #{dup}")
            return False
        db.upsert_user_seen(self.conn, u.id, self.run_id, key, "kept")
        state, job_id = db.upsert_job(self.conn, u.id, self.run_id, key, ev.record, machine_hash(ev.record),
                                      ev.initial_status, ev.note, ev.answers)
        u.counts[state] += 1
        if state == "new":
            tags = []
            if ev.record["red_flags"]:
                tags.append(f"red flags: {'; '.join(ev.record['red_flags'])}")
            if ev.record["ai_screening"]:
                tags.append("AI screening likely")
            if ev.record["night_shift"]:
                tags.append("night-shift compatible")
            self.event("success", "added", f"Added “{job.title}” (score {ev.record['score']})"
                       + (f": {', '.join(tags)}" if tags else ""), job.company, job_id, job.url, user=u)
        elif state == "updated":
            self.event("info", "updated", f"Posting changed, refreshed “{job.title}”", job.company, job_id,
                       job.url, user=u)
        return True

    def _record_skip(self, u: RunUser, job: Job, key: str, reason: str, detail: str) -> None:
        if reason == "not_internship":
            return  # junior roles are fetched for users who opted in; for everyone else they aren't "skipped internships"
        u.counts["skipped"] += 1
        u.reasons[reason] += 1
        verdict = "duplicate" if reason == "duplicate" else "rejected"
        prev = db.upsert_user_seen(self.conn, u.id, self.run_id, key, verdict, reason, detail)
        label = filters.REASON_LABELS.get(reason, reason)
        if db.mark_excluded(self.conn, u.id, key, f"{label}: {detail}"):
            self.event("warn", "excluded", f"Tracked job “{job.title}” no longer passes filters ({label})",
                       job.company, url=job.url, user=u)
        elif prev != f"{verdict}:{reason}":
            self.event("skip", reason, f"Skipped “{job.title}”: {label} ({detail})", job.company,
                       url=job.url, user=u)

    def _save_company_state(self, out: FetchOutcome, result: dict) -> None:
        self.company_results.append(result)
        self.conn.execute(
            """INSERT INTO company_state(name, ats, last_run_id, last_fetch_at, ok, http_status, postings, internships,
                                         kept, seconds, message)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(name) DO UPDATE SET ats=excluded.ats, last_run_id=excluded.last_run_id,
                   last_fetch_at=excluded.last_fetch_at, ok=excluded.ok, http_status=excluded.http_status,
                   postings=excluded.postings, internships=excluded.internships, kept=excluded.kept,
                   seconds=excluded.seconds, message=excluded.message""",
            (out.company.name, out.company.ats, self.run_id, db.now_iso(), 1 if out.ok else 0, out.http_status,
             out.postings, result["internships"], result["kept"], result["seconds"], result["message"]),
        )
        self.conn.commit()


def workspace_profile_complete(profile: dict) -> bool:
    return bool(profile.get("skills"))


def track_anyway(conn: Conn, user_id: int, url_key: str, profile: dict, companies: list[Company]) -> int:
    """Moves a skipped listing into the user's tracker (status To Review) when they think the filter was wrong."""
    data = db.posting_json(conn, url_key)
    if not data:
        raise KeyError(url_key)
    job = Job.from_dict(data)
    company = resolve_company(job, {c.name.strip().lower(): c for c in companies})
    checker = flags.DomainChecker(conn, None)
    ev = evaluate(job, company, profile, checker.age_days, force=True)
    _, job_id = db.upsert_job(conn, user_id, None, url_key, ev.record, machine_hash(ev.record), "To Review",
                              ev.note, ev.answers)
    db.upsert_user_seen(conn, user_id, None, url_key, "kept", "manual")
    conn.commit()
    return job_id


def regenerate_drafts(conn: Conn, user_id: int, job_id: int, profile: dict, companies: list[Company]) -> dict:
    key = db.get_job_url_key(conn, user_id, job_id)
    if key is None:
        raise KeyError(job_id)
    data = db.posting_json(conn, key)
    if not data:
        raise LookupError("The original posting isn't stored for this job (demo rows can't be regenerated).")
    job = Job.from_dict(data)
    company = resolve_company(job, {c.name.strip().lower(): c for c in companies})
    ev = evaluate(job, company, profile, flags.DomainChecker(conn, None).age_days, force=True)
    db.set_drafts(conn, user_id, job_id, ev.note, ev.answers)
    return db.get_job(conn, user_id, job_id)


def add_manual(conn: Conn, user_id: int, job: Job, profile: dict, companies: list[Company],
               http: HttpClient | None = None, status: str = "", applied_date: str = "") -> tuple[int, bool, str]:
    """Adds an internship the user found themselves. Returns (job id, created?, note about their filters)."""
    key = normalize_url(job.url) if job.url else f"manual:{sha1(f'{user_id}|{job.company}|{job.title}|{db.now_iso()}')}"
    if db.job_exists(conn, user_id, key):
        row = conn.execute("SELECT id FROM jobs WHERE user_id=? AND url_key=?", (user_id, key)).fetchone()
        return row["id"], False, "Already in your tracker."

    company = resolve_company(job, {c.name.strip().lower(): c for c in companies})
    checker = flags.DomainChecker(conn, http)
    strict = evaluate(job, company, profile, checker.age_days)
    ev = strict if strict.kept else evaluate(job, company, profile, checker.age_days, force=True)
    note = ""
    if not strict.kept:
        label = filters.REASON_LABELS.get(strict.reason, strict.reason)
        note = f"Your filters would have skipped this ({label}: {strict.detail}). Added because you chose to."
        ev.record["warnings"].insert(0, note)

    db.upsert_posting(conn, None, key, job.to_dict())
    db.upsert_user_seen(conn, user_id, None, key, "kept", "manual")
    _, job_id = db.upsert_job(conn, user_id, None, key, ev.record, machine_hash(ev.record), ev.initial_status,
                              ev.note, ev.answers)
    conn.commit()
    # "To Review" is the form's default, so it doesn't override "Skipped (red flag)"; any other choice does.
    if status and status != "To Review" and status != ev.initial_status:
        changes = {"status": status}
        if applied_date:
            changes["applied_date"] = applied_date
        db.update_job_user_fields(conn, user_id, job_id, changes, profile["drafts"].get("followup_days", 7))
    return job_id, True, note


def rescore_user(conn: Conn, user_id: int, profile: dict, companies: list[Company],
                 templates: "flags.TemplateIndex | None" = None) -> dict:
    """Re-applies the user's current settings, blocklist and red-flag rules to everything already in their tracker,
    using the stored copy of each posting (no network). Statuses the user set are never changed; a job still in its
    original To Review status moves to Skipped (red flag) if it now has red flags."""
    templates = templates or flags.TemplateIndex.from_conn(conn)
    whitelist = {c.name.strip().lower(): c for c in companies}
    domain_age = flags.DomainChecker(conn, None).age_days
    counts = Counter()
    rows = conn.execute("SELECT id, url_key, status FROM jobs WHERE user_id=? AND is_demo=0", (user_id,)).fetchall()
    for row in rows:
        data = db.posting_json(conn, row["url_key"])
        if not data:
            counts["no_copy"] += 1
            continue
        counts["checked"] += 1
        job = Job.from_dict(data)
        company = resolve_company(job, whitelist)
        context = {"template_others": templates.others(job.company, job.description)}
        strict = evaluate(job, company, profile, domain_age, context=context)
        ev = strict if strict.kept else evaluate(job, company, profile, domain_age, force=True, context=context)
        state, _ = db.upsert_job(conn, user_id, None, row["url_key"], ev.record, machine_hash(ev.record),
                                 ev.initial_status, ev.note, ev.answers)
        counts[state] += 1
        if not strict.kept and job.source != "manual":
            db.mark_excluded(conn, user_id, row["url_key"],
                             f"{filters.REASON_LABELS.get(strict.reason, strict.reason)}: {strict.detail}")
            counts["now_filtered_out"] += 1
        after = conn.execute("SELECT status FROM jobs WHERE id=?", (row["id"],)).fetchone()["status"]
        if after == "Skipped (red flag)" and row["status"] != after:
            counts["moved_to_skipped"] += 1
    conn.commit()
    return dict(counts)
