"""Where settings live, per mode.

local  (no DATABASE_URL): one user; profile in profile.json, companies in companies.csv. Same as before accounts.
cloud  (DATABASE_URL set, or APP_MODE=cloud): many users. Each user's profile is a row in `profiles`; the shared
       sources (feeds + network settings, company whitelist) are app-wide and only admins edit them.

The bot fetches sources once per run and evaluates them against every user's own profile."""
from __future__ import annotations

import copy
import json
import os
import shutil

from . import config, db, store
from .config import Company
from .store import Conn

GLOBAL_KEYS = ("feeds", "network")
EXAMPLE_PROFILE_PATH = config.ROOT / "profile.example.json"


def mode() -> str:
    return os.environ.get("APP_MODE", "").strip().lower() or ("cloud" if store.database_url() else "local")


def is_cloud() -> bool:
    return mode() == "cloud"


# ---------------------------------------------------------------- local file helpers

def _file_profile() -> dict:
    if not config.PROFILE_PATH.exists() and EXAMPLE_PROFILE_PATH.exists():
        shutil.copy(EXAMPLE_PROFILE_PATH, config.PROFILE_PATH)
    return config.read_profile_raw()


def _example_profile() -> dict:
    with open(EXAMPLE_PROFILE_PATH, encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------- per-user profile

def new_user_profile(email: str, name: str = "") -> dict:
    """Starting profile for a new account: sensible filters, empty skills/projects to fill in."""
    p = {k: v for k, v in _example_profile().items() if k not in GLOBAL_KEYS}
    first = (name or email.split("@")[0]).split()[0] if (name or email) else ""
    p.update(name=name or "", preferred_name=first)
    p["contact"] = {**{k: "" for k in p.get("contact", {})}, "email": email}
    p["location"] = {**p.get("location", {}), "city": ""}
    p["skills"], p["projects"] = [], []
    p.setdefault("drafts", {})["signature"] = name or first
    return p


def user_profile_raw(conn: Conn, user: dict) -> dict:
    if not is_cloud():
        return {k: v for k, v in _file_profile().items() if k not in GLOBAL_KEYS}
    data = db.get_profile_data(conn, user["id"])
    if data is None:
        data = new_user_profile(user["email"], user.get("name") or "")
        db.save_profile_data(conn, user["id"], data)
    return data


def save_user_profile(conn: Conn, user: dict, data: dict) -> dict:
    data = {k: v for k, v in data.items() if k not in GLOBAL_KEYS}
    errors = config.validate_profile(data, require_skills=not is_cloud())
    if errors:
        raise ValueError(" ".join(errors))
    if is_cloud():
        db.save_profile_data(conn, user["id"], data)
    else:
        file = _file_profile()
        config.save_profile({**data, **{k: file[k] for k in GLOBAL_KEYS if k in file}})
    return data


def profile_complete(profile: dict) -> bool:
    return bool(profile.get("skills"))


# ---------------------------------------------------------------- shared sources

def global_settings(conn: Conn) -> dict:
    if not is_cloud():
        file = _file_profile()
        return {k: file[k] for k in GLOBAL_KEYS if k in file}
    data = db.get_setting(conn, "sources")
    if data is None:
        example = _example_profile()
        data = {k: example[k] for k in GLOBAL_KEYS if k in example}
        db.set_setting(conn, "sources", data)
    return data


def save_global_settings(conn: Conn, data: dict) -> dict:
    data = {k: v for k, v in data.items() if k in GLOBAL_KEYS}
    if not isinstance(data.get("feeds", {}), dict) or not isinstance(data.get("network", {}), dict):
        raise ValueError("feeds and network must be objects.")
    if is_cloud():
        db.set_setting(conn, "sources", data)
    else:
        file = _file_profile()
        file.update(data)
        config.save_profile(file)
    return data


def companies(conn: Conn) -> list[Company]:
    if not is_cloud():
        return config.load_companies()
    rows = db.list_company_rows(conn)
    if not rows and config.COMPANIES_PATH.exists():
        seed = config.load_companies()
        db.replace_companies(conn, [c.to_row() | {"enabled": c.enabled} for c in seed])
        rows = db.list_company_rows(conn)
    return [config.company_from_row({**r, "enabled": "yes" if r.get("enabled") else "no"}) for r in rows]


def save_companies(conn: Conn, items: list[Company]) -> None:
    errors = config.validate_companies(items)
    if errors:
        raise ValueError(" ".join(errors))
    if is_cloud():
        db.replace_companies(conn, [c.to_row() | {"enabled": c.enabled} for c in items])
    else:
        config.save_companies(items)


# ---------------------------------------------------------------- what the bot uses

def effective_profile(conn: Conn, user: dict) -> dict:
    """DEFAULTS <- shared sources <- the user's own profile (the user can't override sources)."""
    merged = config.deep_merge(config.DEFAULTS, user_profile_raw(conn, user))
    for k, v in global_settings(conn).items():
        merged[k] = config.deep_merge(config.DEFAULTS.get(k, {}), v)
    return merged


def fetch_profile(conn: Conn) -> dict:
    """Profile used for fetching (network + feeds); junior roles are always fetched so each user can opt in."""
    p = config.deep_merge(config.DEFAULTS, global_settings(conn))
    p["feeds"]["include_junior_roles"] = True
    return p


def run_users(conn: Conn) -> list[dict]:
    if not is_cloud():
        return [db.ensure_local_user(conn)]
    return db.list_users(conn)


def ensure_local_setup(conn: Conn) -> dict:
    return db.ensure_local_user(conn)


def copy_profile(profile: dict) -> dict:
    return copy.deepcopy(profile)
