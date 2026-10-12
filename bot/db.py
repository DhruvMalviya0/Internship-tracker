"""Database: accounts, each user's tracker, and the shared bot state.

Works on SQLite (local) and Postgres (deployed) through bot.store. The bot writes machine fields; the website
writes each user's own fields. Neither overwrites the other, and every user-facing query is scoped to a user."""
from __future__ import annotations

import hashlib
import json
import secrets
from datetime import date, datetime, timedelta
from pathlib import Path

from . import store
from .store import Conn

STATUSES = [
    "To Review", "Ready to Apply", "Applied", "Follow-up Due", "AI Interview / Assessment",
    "Interview Scheduled", "Offer", "Rejected", "No Response", "Skipped (red flag)",
]
APPLIED_STATUSES = {
    "Applied", "Follow-up Due", "AI Interview / Assessment", "Interview Scheduled", "Offer", "Rejected", "No Response",
}
INTERVIEW_STATUSES = {"AI Interview / Assessment", "Interview Scheduled", "Offer"}
USER_FIELDS = ("status", "applied_date", "followup_date", "contact", "user_notes", "note", "answers")
HEARTBEAT_STALE_SECONDS = 90
LOCAL_USER_ID = 1
LOCAL_USER_EMAIL = "local@localhost"
SCHEMA_VERSION = "2"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id {PK},
    email TEXT NOT NULL UNIQUE,
    name TEXT,
    picture TEXT,
    is_admin INTEGER DEFAULT 0,
    created_at TEXT,
    last_login TEXT
);

CREATE TABLE IF NOT EXISTS profiles (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    data TEXT NOT NULL,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS companies (
    id {PK},
    position INTEGER,
    name TEXT NOT NULL,
    category TEXT,
    ats TEXT NOT NULL,
    board_token TEXT NOT NULL,
    website TEXT,
    careers_url TEXT,
    has_ai_screening TEXT,
    enabled INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS login_tokens (
    token_hash TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    ip TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at TEXT
);

CREATE TABLE IF NOT EXISTS jobs (
    id {PK},
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    url_key TEXT NOT NULL,
    url TEXT NOT NULL,
    apply_url TEXT,
    source TEXT,
    platform TEXT,
    ats_id TEXT,
    company TEXT NOT NULL,
    role TEXT NOT NULL,
    location TEXT,
    remote_type TEXT,
    region TEXT,
    hours_num REAL,
    hours_label TEXT,
    paid TEXT,
    compensation TEXT,
    score INTEGER,
    matched_skills TEXT DEFAULT '[]',
    missing_skills TEXT DEFAULT '[]',
    score_detail TEXT DEFAULT '{}',
    ai_screening INTEGER DEFAULT 0,
    ai_reasons TEXT DEFAULT '[]',
    red_flags TEXT DEFAULT '[]',
    warnings TEXT DEFAULT '[]',
    night_shift INTEGER DEFAULT 0,
    description TEXT,
    posted_at TEXT,
    machine_hash TEXT,
    excluded_reason TEXT,
    note TEXT,
    answers TEXT,
    drafts_edited INTEGER DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'To Review',
    applied_date TEXT,
    followup_date TEXT,
    contact TEXT,
    user_notes TEXT,
    is_demo INTEGER DEFAULT 0,
    date_found TEXT NOT NULL,
    last_seen TEXT,
    is_open INTEGER DEFAULT 1,
    created_run INTEGER,
    updated_at TEXT,
    status_changed_at TEXT,
    UNIQUE (user_id, url_key)
);
CREATE INDEX IF NOT EXISTS jobs_user_company_role ON jobs(user_id, company, role);
CREATE INDEX IF NOT EXISTS jobs_user_status ON jobs(user_id, status);

CREATE TABLE IF NOT EXISTS status_history (
    id {PK},
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    from_status TEXT,
    to_status TEXT NOT NULL,
    at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS status_history_job ON status_history(job_id);

CREATE TABLE IF NOT EXISTS seen (
    url_key TEXT PRIMARY KEY,
    url TEXT,
    company TEXT,
    role TEXT,
    source TEXT,
    verdict TEXT,
    reason TEXT,
    detail TEXT,
    src_updated TEXT,
    description TEXT,
    compensation TEXT,
    job_json TEXT,
    first_seen TEXT,
    last_seen TEXT,
    run_id INTEGER
);

CREATE TABLE IF NOT EXISTS user_seen (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    url_key TEXT NOT NULL,
    verdict TEXT,
    reason TEXT,
    detail TEXT,
    first_seen TEXT,
    last_seen TEXT,
    run_id INTEGER,
    PRIMARY KEY (user_id, url_key)
);
CREATE INDEX IF NOT EXISTS user_seen_recent ON user_seen(user_id, last_seen);

CREATE TABLE IF NOT EXISTS runs (
    id {PK},
    started_at TEXT NOT NULL,
    finished_at TEXT,
    heartbeat_at TEXT,
    status TEXT NOT NULL,
    trigger TEXT,
    args TEXT,
    phase TEXT,
    companies_total INTEGER DEFAULT 0,
    companies_done INTEGER DEFAULT 0,
    counts TEXT DEFAULT '{}',
    error TEXT
);

CREATE TABLE IF NOT EXISTS run_user_counts (
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    counts TEXT NOT NULL,
    PRIMARY KEY (run_id, user_id)
);

CREATE TABLE IF NOT EXISTS run_events (
    id {PK},
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    user_id INTEGER,
    at TEXT NOT NULL,
    level TEXT NOT NULL,
    kind TEXT NOT NULL,
    company TEXT,
    message TEXT NOT NULL,
    job_id INTEGER,
    url TEXT
);
CREATE INDEX IF NOT EXISTS run_events_run ON run_events(run_id, id);

CREATE TABLE IF NOT EXISTS company_state (
    name TEXT PRIMARY KEY,
    ats TEXT,
    last_run_id INTEGER,
    last_fetch_at TEXT,
    ok INTEGER,
    http_status INTEGER,
    postings INTEGER,
    internships INTEGER,
    kept INTEGER,
    seconds REAL,
    message TEXT
);

CREATE TABLE IF NOT EXISTS domain_cache (
    domain TEXT PRIMARY KEY,
    registered TEXT,
    checked_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS programs (
    key TEXT PRIMARY KEY,
    name TEXT,
    status TEXT,
    detail TEXT,
    next_label TEXT,
    next_date TEXT,
    url TEXT,
    checked_at TEXT,
    error TEXT
)
"""

JSON_COLUMNS = ("matched_skills", "missing_skills", "score_detail", "ai_reasons", "red_flags", "warnings")


class RunInProgress(Exception):
    def __init__(self, run_id: int):
        super().__init__(f"Run #{run_id} is already in progress")
        self.run_id = run_id


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def today_iso() -> str:
    return date.today().isoformat()


# ---------------------------------------------------------------- connection & schema

def connect(target: Path | str | None = None) -> Conn:
    """target: a SQLite path, a postgres:// URL, or None for DATABASE_URL / the default local file."""
    if target is None:
        from .config import DB_PATH
        target = store.database_url() or DB_PATH
    conn = store.connect_raw(target)
    if not conn.is_pg:
        _migrate_sqlite_v1(conn)
    schema = SCHEMA.replace(" INTEGER", " BIGINT") if conn.is_pg else SCHEMA  # Postgres: match BIGINT ids
    conn.executescript(schema.replace("{PK}", store.PK[conn.dialect]))
    if conn.execute("SELECT value FROM app_settings WHERE key='schema_version'").fetchone() is None:
        conn.execute("INSERT INTO app_settings(key, value, updated_at) VALUES ('schema_version', ?, ?)",
                     (SCHEMA_VERSION, now_iso()))
        conn.commit()
    return conn


def _sqlite_columns(conn: Conn, table: str) -> list[str]:
    return [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]


def _migrate_sqlite_v1(conn: Conn) -> None:
    """Upgrades a single-user database from before accounts existed: everything becomes user 1's data."""
    cols = _sqlite_columns(conn, "jobs")
    if not cols or "user_id" in cols:
        return
    raw = conn.raw
    raw.execute("PRAGMA foreign_keys=OFF")
    raw.executescript(SCHEMA.replace("{PK}", store.PK["sqlite"]).split("CREATE TABLE IF NOT EXISTS jobs")[0])
    raw.execute("INSERT OR IGNORE INTO users(id, email, name, is_admin, created_at) VALUES (?, ?, ?, 1, ?)",
                (LOCAL_USER_ID, LOCAL_USER_EMAIL, "You", now_iso()))
    # SQLite's recommended table rebuild: create new, copy, drop old, rename new (keeps references to "jobs").
    jobs_sql = "CREATE TABLE jobs_new" + SCHEMA.split("CREATE TABLE IF NOT EXISTS jobs", 1)[1].split(");", 1)[0] + ");"
    raw.executescript(jobs_sql.replace("{PK}", store.PK["sqlite"]))
    new_cols = set(_sqlite_columns(conn, "jobs_new"))
    keep = [c for c in cols if c in new_cols]
    raw.execute(f"INSERT INTO jobs_new(user_id, {', '.join(keep)}) SELECT {LOCAL_USER_ID}, {', '.join(keep)} FROM jobs")
    raw.execute("DROP TABLE jobs")
    raw.execute("ALTER TABLE jobs_new RENAME TO jobs")
    if "user_id" not in _sqlite_columns(conn, "run_events"):
        raw.execute("ALTER TABLE run_events ADD COLUMN user_id INTEGER")
    raw.execute("""CREATE TABLE IF NOT EXISTS user_seen (user_id INTEGER NOT NULL, url_key TEXT NOT NULL, verdict TEXT,
                   reason TEXT, detail TEXT, first_seen TEXT, last_seen TEXT, run_id INTEGER, PRIMARY KEY (user_id, url_key))""")
    raw.execute(f"INSERT OR IGNORE INTO user_seen SELECT {LOCAL_USER_ID}, url_key, verdict, reason, detail, first_seen, "
                "last_seen, run_id FROM seen")
    raw.commit()
    raw.execute("PRAGMA foreign_keys=ON")


def row_to_job(row, include_description: bool = True) -> dict:
    d = dict(row)
    for col in JSON_COLUMNS:
        try:
            d[col] = json.loads(d.get(col) or ("{}" if col == "score_detail" else "[]"))
        except ValueError:
            d[col] = [] if col != "score_detail" else {}
    if not include_description:
        for k in ("description", "answers", "note"):
            d.pop(k, None)
    return d


# ---------------------------------------------------------------- users & profiles

def get_user(conn: Conn, user_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    return dict(row) if row else None


def ensure_local_user(conn: Conn) -> dict:
    if not get_user(conn, LOCAL_USER_ID):
        conn.execute("INSERT INTO users(id, email, name, is_admin, created_at) VALUES (?, ?, ?, 1, ?)",
                     (LOCAL_USER_ID, LOCAL_USER_EMAIL, "You", now_iso()))
        conn.commit()
    return get_user(conn, LOCAL_USER_ID)


def get_or_create_user(conn: Conn, email: str, name: str = "", picture: str = "", is_admin: bool = False) -> tuple[dict, bool]:
    """Returns (user, created). The admin flag follows ADMIN_EMAILS on every sign-in."""
    email = email.strip().lower()
    row = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    now = now_iso()
    if row:
        conn.execute("UPDATE users SET last_login=?, name=COALESCE(NULLIF(?, ''), name), "
                     "picture=COALESCE(NULLIF(?, ''), picture), is_admin=? WHERE id=?",
                     (now, name, picture, 1 if is_admin else 0, row["id"]))
        conn.commit()
        return get_user(conn, row["id"]), False
    new_id = conn.execute(
        "INSERT INTO users(email, name, picture, is_admin, created_at, last_login) VALUES (?,?,?,?,?,?) RETURNING id",
        (email, name, picture, 1 if is_admin else 0, now, now)).fetchone()["id"]
    conn.commit()
    return get_user(conn, new_id), True


def list_users(conn: Conn) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM users ORDER BY id")]


def delete_user(conn: Conn, user_id: int) -> None:
    for table in ("run_user_counts", "user_seen", "profiles"):
        conn.execute(f"DELETE FROM {table} WHERE user_id=?", (user_id,))
    conn.execute("DELETE FROM status_history WHERE job_id IN (SELECT id FROM jobs WHERE user_id=?)", (user_id,))
    conn.execute("DELETE FROM jobs WHERE user_id=?", (user_id,))
    conn.execute("DELETE FROM run_events WHERE user_id=?", (user_id,))
    conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    conn.commit()


def get_profile_data(conn: Conn, user_id: int) -> dict | None:
    row = conn.execute("SELECT data FROM profiles WHERE user_id=?", (user_id,)).fetchone()
    return json.loads(row["data"]) if row else None


def save_profile_data(conn: Conn, user_id: int, data: dict) -> None:
    conn.execute("INSERT INTO profiles(user_id, data, updated_at) VALUES (?,?,?) "
                 "ON CONFLICT(user_id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at",
                 (user_id, json.dumps(data, ensure_ascii=False), now_iso()))
    conn.commit()


def get_setting(conn: Conn, key: str, default=None):
    row = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def set_setting(conn: Conn, key: str, value) -> None:
    conn.execute("INSERT INTO app_settings(key, value, updated_at) VALUES (?,?,?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                 (key, json.dumps(value, ensure_ascii=False), now_iso()))
    conn.commit()


def list_company_rows(conn: Conn) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM companies ORDER BY position, id")]


def replace_companies(conn: Conn, rows: list[dict]) -> None:
    conn.execute("DELETE FROM companies")
    for i, r in enumerate(rows):
        conn.execute("INSERT INTO companies(position, name, category, ats, board_token, website, careers_url, "
                     "has_ai_screening, enabled) VALUES (?,?,?,?,?,?,?,?,?)",
                     (i, r["name"], r.get("category", ""), r["ats"], r["board_token"], r.get("website", ""),
                      r.get("careers_url", ""), r.get("has_ai_screening", "Unknown"), 1 if r.get("enabled", True) else 0))
    conn.commit()


# ---------------------------------------------------------------- email sign-in links

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_login_token(conn: Conn, email: str, ip: str, ttl_minutes: int = 15) -> str:
    token = secrets.token_urlsafe(32)
    now = datetime.now()
    conn.execute("INSERT INTO login_tokens(token_hash, email, ip, created_at, expires_at) VALUES (?,?,?,?,?)",
                 (_hash_token(token), email.strip().lower(), ip, now.isoformat(timespec="seconds"),
                  (now + timedelta(minutes=ttl_minutes)).isoformat(timespec="seconds")))
    conn.execute("DELETE FROM login_tokens WHERE expires_at < ?", ((now - timedelta(days=1)).isoformat(timespec="seconds"),))
    conn.commit()
    return token


def consume_login_token(conn: Conn, token: str) -> str | None:
    """Returns the email for a valid, unused, unexpired token and marks it used."""
    row = conn.execute("SELECT email, expires_at, used_at FROM login_tokens WHERE token_hash=?",
                       (_hash_token(token or ""),)).fetchone()
    if not row or row["used_at"] or row["expires_at"] < now_iso():
        return None
    conn.execute("UPDATE login_tokens SET used_at=? WHERE token_hash=?", (now_iso(), _hash_token(token)))
    conn.commit()
    return row["email"]


def recent_login_requests(conn: Conn, email: str = "", ip: str = "", minutes: int = 60) -> int:
    since = (datetime.now() - timedelta(minutes=minutes)).isoformat(timespec="seconds")
    if email:
        return conn.execute("SELECT COUNT(*) n FROM login_tokens WHERE email=? AND created_at>=?",
                            (email.strip().lower(), since)).fetchone()["n"]
    return conn.execute("SELECT COUNT(*) n FROM login_tokens WHERE ip=? AND created_at>=?", (ip, since)).fetchone()["n"]


# ---------------------------------------------------------------- runs & events

def mark_stale_runs(conn: Conn) -> None:
    cutoff = (datetime.now() - timedelta(seconds=HEARTBEAT_STALE_SECONDS)).isoformat(timespec="seconds")
    conn.execute(
        "UPDATE runs SET status='abandoned', finished_at=heartbeat_at, "
        "error='The run stopped without finishing (process closed or crashed).' "
        "WHERE status='running' AND heartbeat_at < ?",
        (cutoff,),
    )


def start_run(conn: Conn, trigger: str, args: dict) -> int:
    conn.commit()
    conn.lock_runs()
    try:
        mark_stale_runs(conn)
        row = conn.execute("SELECT id FROM runs WHERE status='running'").fetchone()
        if row:
            raise RunInProgress(row["id"])
        now = now_iso()
        run_id = conn.execute(
            "INSERT INTO runs(started_at, heartbeat_at, status, trigger, args, phase) VALUES (?,?,?,?,?,?) RETURNING id",
            (now, now, "running", trigger, json.dumps(args), "starting"),
        ).fetchone()["id"]
        conn.commit()
        return run_id
    except BaseException:
        conn.rollback()
        raise


def update_run(conn: Conn, run_id: int, **fields) -> None:
    fields["heartbeat_at"] = now_iso()
    if "counts" in fields and not isinstance(fields["counts"], str):
        fields["counts"] = json.dumps(fields["counts"])
    cols = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE runs SET {cols} WHERE id=?", (*fields.values(), run_id))


def save_user_counts(conn: Conn, run_id: int, user_id: int, counts: dict) -> None:
    conn.execute("INSERT INTO run_user_counts(run_id, user_id, counts) VALUES (?,?,?) "
                 "ON CONFLICT(run_id, user_id) DO UPDATE SET counts=excluded.counts",
                 (run_id, user_id, json.dumps(counts)))


def add_event(conn: Conn, run_id: int, level: str, kind: str, message: str, company: str | None = None,
              job_id: int | None = None, url: str | None = None, user_id: int | None = None) -> None:
    conn.execute(
        "INSERT INTO run_events(run_id, user_id, at, level, kind, company, message, job_id, url) VALUES (?,?,?,?,?,?,?,?,?)",
        (run_id, user_id, now_iso(), level, kind, company, message, job_id, url),
    )


def _run_dict(row, user_counts: dict | None = None) -> dict:
    d = dict(row)
    d["counts"] = {**json.loads(d.get("counts") or "{}"), **(user_counts or {})}
    d["args"] = json.loads(d.get("args") or "{}")
    return d


def _user_counts(conn: Conn, run_ids: list[int], user_id: int | None) -> dict[int, dict]:
    if user_id is None or not run_ids:
        return {}
    marks = ",".join("?" * len(run_ids))
    rows = conn.execute(f"SELECT run_id, counts FROM run_user_counts WHERE user_id=? AND run_id IN ({marks})",
                        (user_id, *run_ids))
    return {r["run_id"]: json.loads(r["counts"]) for r in rows}


def current_run(conn: Conn, user_id: int | None = None) -> dict | None:
    mark_stale_runs(conn)
    conn.commit()
    row = conn.execute("SELECT * FROM runs WHERE status='running' ORDER BY id DESC LIMIT 1").fetchone()
    return _run_dict(row, _user_counts(conn, [row["id"]], user_id).get(row["id"])) if row else None


def last_finished_run(conn: Conn, user_id: int | None = None) -> dict | None:
    row = conn.execute("SELECT * FROM runs WHERE status<>'running' ORDER BY id DESC LIMIT 1").fetchone()
    return _run_dict(row, _user_counts(conn, [row["id"]], user_id).get(row["id"])) if row else None


def list_runs(conn: Conn, user_id: int | None = None, limit: int = 50) -> list[dict]:
    mark_stale_runs(conn)
    conn.commit()
    rows = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    uc = _user_counts(conn, [r["id"] for r in rows], user_id)
    return [_run_dict(r, uc.get(r["id"])) for r in rows]


def get_run(conn: Conn, run_id: int, user_id: int | None = None) -> dict | None:
    row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    return _run_dict(row, _user_counts(conn, [run_id], user_id).get(run_id)) if row else None


def run_events(conn: Conn, run_id: int, after: int = 0, user_id: int | None = None, limit: int = 1000) -> list[dict]:
    """Shared events (fetch results) plus this user's own (what was added/skipped for them)."""
    if user_id is None:
        rows = conn.execute("SELECT * FROM run_events WHERE run_id=? AND id>? ORDER BY id LIMIT ?", (run_id, after, limit))
    else:
        rows = conn.execute("SELECT * FROM run_events WHERE run_id=? AND id>? AND (user_id IS NULL OR user_id=?) "
                            "ORDER BY id LIMIT ?", (run_id, after, user_id, limit))
    return [dict(r) for r in rows]


def prune_old_runs(conn: Conn, keep: int = 60) -> None:
    old = [r["id"] for r in conn.execute("SELECT id FROM runs ORDER BY id DESC LIMIT -1 OFFSET ?" if not conn.is_pg
                                         else "SELECT id FROM runs ORDER BY id DESC OFFSET ?", (keep,))]
    for rid in old:
        conn.execute("DELETE FROM run_events WHERE run_id=?", (rid,))
        conn.execute("DELETE FROM run_user_counts WHERE run_id=?", (rid,))
        conn.execute("DELETE FROM runs WHERE id=?", (rid,))


# ---------------------------------------------------------------- shared posting cache

def detail_cache(conn: Conn) -> dict:
    """url_key -> what earlier runs stored, so fetchers can skip re-downloading unchanged detail pages."""
    out = {}
    for r in conn.execute("SELECT url_key, src_updated, description, compensation, job_json FROM seen"):
        try:
            job = json.loads(r["job_json"]) if r["job_json"] else None
        except ValueError:
            job = None
        out[r["url_key"]] = {"src_updated": r["src_updated"], "description": r["description"],
                             "compensation": r["compensation"], "job": job}
    return out


def upsert_posting(conn: Conn, run_id: int | None, url_key: str, job: dict) -> None:
    now = now_iso()
    conn.execute(
        """INSERT INTO seen(url_key, url, company, role, source, src_updated, description, compensation, job_json,
                            first_seen, last_seen, run_id)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(url_key) DO UPDATE SET url=excluded.url, company=excluded.company, role=excluded.role,
               src_updated=excluded.src_updated, description=excluded.description,
               compensation=excluded.compensation, job_json=excluded.job_json,
               last_seen=excluded.last_seen, run_id=excluded.run_id""",
        (url_key, job["url"], job["company"], job["title"], job["source"], job.get("src_updated", ""),
         (job.get("description") or "")[:40000], job.get("compensation", ""), json.dumps(job), now, now, run_id),
    )


def posting_json(conn: Conn, url_key: str) -> dict | None:
    row = conn.execute("SELECT job_json FROM seen WHERE url_key=?", (url_key,)).fetchone()
    return json.loads(row["job_json"]) if row and row["job_json"] else None


def upsert_user_seen(conn: Conn, user_id: int, run_id: int | None, url_key: str, verdict: str,
                     reason: str = "", detail: str = "") -> str | None:
    """Records how a posting was judged for this user. Returns the previous 'verdict:reason' (None if new)."""
    prev = conn.execute("SELECT verdict, reason FROM user_seen WHERE user_id=? AND url_key=?", (user_id, url_key)).fetchone()
    now = now_iso()
    conn.execute(
        """INSERT INTO user_seen(user_id, url_key, verdict, reason, detail, first_seen, last_seen, run_id)
           VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(user_id, url_key) DO UPDATE SET verdict=excluded.verdict, reason=excluded.reason,
               detail=excluded.detail, last_seen=excluded.last_seen, run_id=excluded.run_id""",
        (user_id, url_key, verdict, reason, detail, now, now, run_id),
    )
    return None if prev is None else f"{prev['verdict']}:{prev['reason']}"


def rejected_listings(conn: Conn, user_id: int, limit: int = 500) -> list[dict]:
    rows = conn.execute(
        "SELECT us.url_key, s.url, s.company, s.role, s.source, us.reason, us.detail, us.first_seen, us.last_seen "
        "FROM user_seen us JOIN seen s ON s.url_key = us.url_key "
        "WHERE us.user_id=? AND us.verdict IN ('rejected','duplicate') "
        "AND us.url_key NOT IN (SELECT url_key FROM jobs WHERE user_id=?) "
        "ORDER BY us.last_seen DESC LIMIT ?",
        (user_id, user_id, limit),
    )
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- each user's jobs

MACHINE_FIELDS = (
    "url", "apply_url", "source", "platform", "ats_id", "company", "role", "location", "remote_type", "region",
    "hours_num", "hours_label", "paid", "compensation", "score", "matched_skills", "missing_skills", "score_detail",
    "ai_screening", "ai_reasons", "red_flags", "warnings", "night_shift", "description", "posted_at",
)


def _encode(rec: dict) -> dict:
    out = dict(rec)
    for col in JSON_COLUMNS:
        if col in out and not isinstance(out[col], str):
            out[col] = json.dumps(out[col], ensure_ascii=False)
    return out


def find_duplicate(conn: Conn, user_id: int, url_key: str, company: str, role: str) -> int | None:
    row = conn.execute(
        "SELECT id FROM jobs WHERE user_id=? AND lower(company)=lower(?) AND lower(role)=lower(?) AND url_key<>? AND is_open=1",
        (user_id, company, role, url_key),
    ).fetchone()
    return row["id"] if row else None


def job_exists(conn: Conn, user_id: int, url_key: str) -> bool:
    return conn.execute("SELECT 1 x FROM jobs WHERE user_id=? AND url_key=?", (user_id, url_key)).fetchone() is not None


def upsert_job(conn: Conn, user_id: int, run_id: int | None, url_key: str, rec: dict, machine_hash: str,
               initial_status: str, note: str, answers: str) -> tuple[str, int]:
    """Inserts or refreshes one of a user's jobs. Returns ('new'|'updated'|'unchanged', job id)."""
    rec = _encode(rec)
    now = now_iso()
    row = conn.execute("SELECT id, machine_hash, drafts_edited FROM jobs WHERE user_id=? AND url_key=?",
                       (user_id, url_key)).fetchone()
    if row is None:
        cols = ["user_id", "url_key", *MACHINE_FIELDS, "machine_hash", "note", "answers", "status", "date_found",
                "last_seen", "is_open", "created_run", "updated_at", "status_changed_at"]
        vals = [user_id, url_key, *(rec.get(c) for c in MACHINE_FIELDS), machine_hash, note, answers, initial_status,
                today_iso(), now, 1, run_id, now, now]
        job_id = conn.execute(f"INSERT INTO jobs({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) RETURNING id",
                              vals).fetchone()["id"]
        conn.execute("INSERT INTO status_history(job_id, from_status, to_status, at) VALUES (?,?,?,?)",
                     (job_id, None, initial_status, now))
        return "new", job_id

    job_id = row["id"]
    if row["machine_hash"] == machine_hash:
        conn.execute("UPDATE jobs SET last_seen=?, is_open=1, excluded_reason=NULL WHERE id=?", (now, job_id))
        return "unchanged", job_id
    sets = {c: rec.get(c) for c in MACHINE_FIELDS}
    sets.update(machine_hash=machine_hash, last_seen=now, is_open=1, excluded_reason=None, updated_at=now)
    if not row["drafts_edited"]:
        sets.update(note=note, answers=answers)
    conn.execute(f"UPDATE jobs SET {', '.join(f'{k}=?' for k in sets)} WHERE id=?", (*sets.values(), job_id))
    return "updated", job_id


def mark_excluded(conn: Conn, user_id: int, url_key: str, reason: str) -> bool:
    cur = conn.execute("UPDATE jobs SET excluded_reason=?, last_seen=?, is_open=1 WHERE user_id=? AND url_key=?",
                       (reason, now_iso(), user_id, url_key))
    return cur.rowcount > 0


def close_missing(conn: Conn, user_id: int, source: str, open_keys: set[str], company: str | None = None) -> list[dict]:
    """Marks a user's jobs that disappeared from a board or feed (fetched completely) as closed."""
    sql = "SELECT id, url_key, role, company FROM jobs WHERE user_id=? AND source=? AND is_open=1 AND is_demo=0"
    args: tuple = (user_id, source)
    if company is not None:
        sql += " AND company=?"
        args += (company,)
    closed = [dict(r) for r in conn.execute(sql, args).fetchall() if r["url_key"] not in open_keys]
    for r in closed:
        conn.execute("UPDATE jobs SET is_open=0, updated_at=? WHERE id=?", (now_iso(), r["id"]))
    return closed


def list_jobs(conn: Conn, user_id: int) -> list[dict]:
    rows = conn.execute("SELECT * FROM jobs WHERE user_id=? ORDER BY id DESC", (user_id,))
    return [row_to_job(r, include_description=False) for r in rows]


def get_job(conn: Conn, user_id: int, job_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM jobs WHERE id=? AND user_id=?", (job_id, user_id)).fetchone()
    if not row:
        return None
    job = row_to_job(row)
    job["history"] = [dict(r) for r in conn.execute(
        "SELECT from_status, to_status, at FROM status_history WHERE job_id=? ORDER BY id", (job_id,))]
    return job


def get_job_url_key(conn: Conn, user_id: int, job_id: int) -> str | None:
    row = conn.execute("SELECT url_key FROM jobs WHERE id=? AND user_id=?", (job_id, user_id)).fetchone()
    return row["url_key"] if row else None


def update_job_user_fields(conn: Conn, user_id: int, job_id: int, changes: dict, followup_days: int = 7) -> dict:
    row = conn.execute("SELECT status, applied_date, followup_date FROM jobs WHERE id=? AND user_id=?",
                       (job_id, user_id)).fetchone()
    if row is None:
        raise KeyError(job_id)
    changes = {k: v for k, v in changes.items() if k in USER_FIELDS}
    for k in ("applied_date", "followup_date"):
        if k in changes and changes[k]:
            date.fromisoformat(changes[k])  # raises ValueError on bad input
    if "status" in changes and changes["status"] not in STATUSES:
        raise ValueError(f"Unknown status {changes['status']!r}")

    now = now_iso()
    new_status = changes.get("status")
    if new_status and new_status != row["status"]:
        conn.execute("INSERT INTO status_history(job_id, from_status, to_status, at) VALUES (?,?,?,?)",
                     (job_id, row["status"], new_status, now))
        changes["status_changed_at"] = now
        if new_status == "Applied":
            applied = changes.get("applied_date") or row["applied_date"] or today_iso()
            changes.setdefault("applied_date", applied)
            if not (changes.get("followup_date") or row["followup_date"]):
                changes["followup_date"] = (date.fromisoformat(applied) + timedelta(days=followup_days)).isoformat()
    if "note" in changes or "answers" in changes:
        changes["drafts_edited"] = 1
    changes["updated_at"] = now
    conn.execute(f"UPDATE jobs SET {', '.join(f'{k}=?' for k in changes)} WHERE id=? AND user_id=?",
                 (*changes.values(), job_id, user_id))
    conn.commit()
    return get_job(conn, user_id, job_id)


def set_drafts(conn: Conn, user_id: int, job_id: int, note: str, answers: str) -> None:
    conn.execute("UPDATE jobs SET note=?, answers=?, drafts_edited=0, updated_at=? WHERE id=? AND user_id=?",
                 (note, answers, now_iso(), job_id, user_id))
    conn.commit()


def fill_followups(conn: Conn, user_id: int, followup_days: int) -> int:
    """Applied rows with an applied date but no follow-up date get applied + N days."""
    rows = conn.execute(
        "SELECT id, applied_date FROM jobs WHERE user_id=? AND status='Applied' AND applied_date IS NOT NULL "
        "AND applied_date<>'' AND (followup_date IS NULL OR followup_date='')", (user_id,)
    ).fetchall()
    for r in rows:
        try:
            due = (date.fromisoformat(r["applied_date"]) + timedelta(days=followup_days)).isoformat()
        except ValueError:
            continue
        conn.execute("UPDATE jobs SET followup_date=? WHERE id=?", (due, r["id"]))
    return len(rows)


def jobs_version(conn: Conn, user_id: int) -> str:
    row = conn.execute("SELECT COALESCE(MAX(updated_at), '') v, COUNT(*) n FROM jobs WHERE user_id=?", (user_id,)).fetchone()
    return f"{row['v']}#{row['n']}"


def company_job_counts(conn: Conn, user_id: int) -> tuple[dict, dict, dict]:
    """(by company, by source, by platform) counts of this user's tracked jobs."""
    rows = conn.execute("SELECT company, source, platform FROM jobs WHERE user_id=?", (user_id,)).fetchall()
    by_company: dict = {}
    by_source: dict = {}
    by_platform: dict = {}
    for r in rows:
        by_company[r["company"]] = by_company.get(r["company"], 0) + 1
        by_source[r["source"]] = by_source.get(r["source"], 0) + 1
        if r["platform"]:
            by_platform[r["platform"]] = by_platform.get(r["platform"], 0) + 1
    return by_company, by_source, by_platform


# ---------------------------------------------------------------- dashboard

def stats(conn: Conn, user_id: int, weeks: int = 12) -> dict:
    jobs = [dict(r) for r in conn.execute(
        "SELECT status, applied_date, followup_date, missing_skills, score FROM jobs WHERE user_id=?", (user_id,))]
    today = date.today()
    by_status = {s: 0 for s in STATUSES}
    for j in jobs:
        by_status[j["status"]] = by_status.get(j["status"], 0) + 1

    week0 = today - timedelta(days=today.weekday())
    starts = [week0 - timedelta(weeks=i) for i in range(weeks - 1, -1, -1)]
    per_week = {s.isoformat(): 0 for s in starts}
    applied_total = 0
    for j in jobs:
        if not j["applied_date"]:
            continue
        applied_total += 1
        try:
            d = date.fromisoformat(j["applied_date"])
        except ValueError:
            continue
        key = (d - timedelta(days=d.weekday())).isoformat()
        if key in per_week:
            per_week[key] += 1

    missing: dict[str, int] = {}
    for j in jobs:
        if j["status"] == "Skipped (red flag)":
            continue
        for s in json.loads(j["missing_skills"] or "[]"):
            missing[s] = missing.get(s, 0) + 1

    followups_due = sum(
        1 for j in jobs
        if j["status"] == "Applied" and j["followup_date"] and j["followup_date"] <= today.isoformat()
    )
    return {
        "total": len(jobs),
        "applied": applied_total,
        "interviews": sum(by_status[s] for s in INTERVIEW_STATUSES),
        "offers": by_status["Offer"],
        "rejections": by_status["Rejected"],
        "followups_due": followups_due,
        "to_review": by_status["To Review"],
        "ready": by_status["Ready to Apply"],
        "by_status": by_status,
        "weekly": [{"week": k, "count": v} for k, v in per_week.items()],
        "this_week": per_week[week0.isoformat()],
        "top_missing": sorted(missing.items(), key=lambda kv: (-kv[1], kv[0]))[:8],
        "today": today.isoformat(),
    }
