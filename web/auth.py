"""Sign-in: Google (OpenID Connect) and one-time email links. Only used in cloud mode.

Env:
  GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET  Google sign-in (Google Cloud console → OAuth client, type "Web").
  PUBLIC_URL                               e.g. https://your-app.onrender.com (used for callback + email links).
  ADMIN_EMAILS                             comma-separated; these accounts manage shared sources and can run the bot.
  ALLOWED_EMAILS / ALLOWED_DOMAINS         optional allow-lists; leave both empty to let anyone sign up.
"""
from __future__ import annotations

import logging
import os
import re

from authlib.integrations.flask_client import OAuth, OAuthError
from flask import Blueprint, current_app, jsonify, redirect, request, session

from bot import db, mailer, workspace
from bot.mailer import _env as env

log = logging.getLogger(__name__)
bp = Blueprint("auth", __name__)
oauth = OAuth()
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[A-Za-z]{2,}$")


def _list_env(name: str) -> set[str]:
    return {x.strip().lower() for x in env(name).split(",") if x.strip() and x.strip() not in mailer.PLACEHOLDERS}


def google_enabled() -> bool:
    return bool(env("GOOGLE_CLIENT_ID") and env("GOOGLE_CLIENT_SECRET"))


def is_allowed(email: str) -> bool:
    emails, domains = _list_env("ALLOWED_EMAILS"), _list_env("ALLOWED_DOMAINS")
    if not emails and not domains:
        return True
    email = email.lower()
    return email in emails or email.rsplit("@", 1)[-1] in domains


def is_admin_email(email: str) -> bool:
    return email.lower() in _list_env("ADMIN_EMAILS")


def public_url() -> str:
    return (env("PUBLIC_URL") or request.host_url).rstrip("/")


def init_auth(app) -> None:
    oauth.init_app(app)
    if google_enabled():
        oauth.register(
            "google",
            client_id=env("GOOGLE_CLIENT_ID"),
            client_secret=env("GOOGLE_CLIENT_SECRET"),
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email profile", "prompt": "select_account"},
        )
    app.register_blueprint(bp)


def sign_in(email: str, name: str = "", picture: str = "") -> dict:
    conn = current_app.get_conn()
    user, created = db.get_or_create_user(conn, email, name, picture, is_admin=is_admin_email(email))
    if created:
        workspace.user_profile_raw(conn, user)  # creates their starting profile
        log.info("New account: user #%s", user["id"])
    session.clear()
    session["uid"] = user["id"]
    session.permanent = True
    return user


@bp.get("/auth/config")
def auth_config():
    return jsonify({"google": google_enabled(), "email": mailer.configured(),
                    "app_name": os.environ.get("APP_NAME", "Internship Tracker"),
                    "restricted": bool(_list_env("ALLOWED_EMAILS") or _list_env("ALLOWED_DOMAINS"))})


@bp.get("/auth/google")
def google_start():
    if not google_enabled():
        return redirect("/login?error=google_off")
    return oauth.google.authorize_redirect(f"{public_url()}/auth/google/callback")


@bp.get("/auth/google/callback")
def google_callback():
    try:
        token = oauth.google.authorize_access_token()
    except OAuthError as exc:
        log.warning("Google sign-in failed: %s", exc)
        return redirect("/login?error=google")
    info = token.get("userinfo") or {}
    email = (info.get("email") or "").lower()
    if not email or not info.get("email_verified"):
        return redirect("/login?error=unverified")
    if not is_allowed(email):
        return redirect("/login?error=not_allowed")
    sign_in(email, info.get("name") or "", info.get("picture") or "")
    return redirect("/")


@bp.post("/auth/email")
def email_start():
    if not mailer.configured():
        return jsonify({"error": "Email sign-in isn't set up on this server."}), 400
    email = str((request.get_json(silent=True) or {}).get("email", "")).strip().lower()
    if not EMAIL_RE.match(email):
        return jsonify({"error": "Enter a valid email address."}), 400
    conn = current_app.get_conn()
    ip = request.remote_addr or ""
    if db.recent_login_requests(conn, email=email) >= 5 or db.recent_login_requests(conn, ip=ip) >= 20:
        return jsonify({"error": "Too many sign-in links requested. Try again in an hour."}), 429
    if is_allowed(email):
        token = db.create_login_token(conn, email, ip)
        try:
            mailer.send_login_link(email, f"{public_url()}/login#token={token}",
                                   os.environ.get("APP_NAME", "Internship Tracker"))
        except Exception:  # SMTP problems shouldn't leak details to the page
            log.exception("Could not send sign-in email")
            return jsonify({"error": "Couldn't send the email right now. Try again, or sign in with Google."}), 502
    # Same answer either way, so the allow-list can't be probed.
    return jsonify({"sent": True})


@bp.post("/auth/verify")
def email_verify():
    token = str((request.get_json(silent=True) or {}).get("token", ""))
    email = db.consume_login_token(current_app.get_conn(), token)
    if not email:
        return jsonify({"error": "That sign-in link has expired or was already used. Request a new one."}), 400
    if not is_allowed(email):
        return jsonify({"error": "This email isn't allowed to sign in here."}), 403
    sign_in(email)
    return jsonify({"ok": True})


@bp.post("/auth/logout")
def logout():
    session.clear()
    return jsonify({"ok": True})
