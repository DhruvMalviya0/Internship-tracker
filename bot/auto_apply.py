"""Automated job application submission with Gemini AI answering and verification guards.

Guards:
1. If the job description or apply page requires submitting details in a separate Google Doc / Form:
   HALTS submission and sets status to "User Review Pending".
2. If human verification (Cloudflare Turnstile, reCAPTCHA, hCaptcha, bot challenge, or mandatory login) is detected:
   HALTS submission and sets status to "User Review Pending".
3. Answers custom application questions automatically using Gemini AI based on the candidate's profile.
4. Updates the database and job list automatically upon applying or setting to User Review Pending.
"""
from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from typing import Any
from urllib.parse import urlparse

import requests

from . import db, gemini, verification
from .http_client import HttpClient
from .store import Conn

log = logging.getLogger(__name__)


def _extract_token_and_id(url: str) -> tuple[str, str, str]:
    """Extracts (ats_type, board_token, job_id) from known ATS job URLs."""
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.strip("/")
    parts = path.split("/")

    # Greenhouse: boards.greenhouse.io/{token}/jobs/{id} or job-boards.greenhouse.io/{token}/jobs/{id}
    if "greenhouse.io" in host:
        token = parts[0] if parts else ""
        job_id = parts[2] if len(parts) >= 3 and parts[1] == "jobs" else (parts[1] if len(parts) >= 2 else "")
        return "greenhouse", token, job_id

    # Lever: jobs.lever.co/{token}/{id}
    if "lever.co" in host:
        token = parts[0] if parts else ""
        job_id = parts[1] if len(parts) >= 2 else ""
        return "lever", token, job_id

    # Ashby: jobs.ashbyhq.com/{token}/{id}
    if "ashbyhq.com" in host:
        token = parts[0] if parts else ""
        job_id = parts[1] if len(parts) >= 2 else ""
        return "ashby", token, job_id

    # Workable: apply.workable.com/{token}/j/{id}
    if "workable.com" in host:
        token = parts[0] if parts else ""
        job_id = parts[2] if len(parts) >= 3 and parts[1] == "j" else (parts[1] if len(parts) >= 2 else "")
        return "workable", token, job_id

    return "", "", ""


def _build_candidate_resume_text(profile: dict) -> str:
    """Builds a formatted text resume from the profile for ATS forms that take text/pasted resume."""
    contact = profile.get("contact", {})
    loc = profile.get("location", {})
    name = profile.get("name", "Dhruv Malviya")

    lines = [
        f"{name}",
        f"Email: {contact.get('email', '')} | Phone: {contact.get('phone', '')}",
        f"Location: {loc.get('city', 'Pune')}, {loc.get('country', 'India')}",
        f"Portfolio: {contact.get('portfolio', '')} | GitHub: {contact.get('github', '')} | LinkedIn: {contact.get('linkedin', '')}",
        "",
        "SUMMARY:",
        "Developer and designer building high-craft web applications, 3D interactive experiences and tools.",
        "Available for remote internships worldwide.",
        "",
        "EDUCATION:",
    ]
    for e in profile.get("education", []):
        lines.append(f"{e.get('degree', '')} - {e.get('school', '')} ({e.get('period', '')})")

    lines.append("")
    lines.append("PROJECTS:")
    for p in profile.get("projects", []):
        if p.get("name"):
            url = f" ({p.get('url')})" if p.get("url") else ""
            lines.append(f"• {p['name']}{url}: {p.get('summary', '')}")

    lines.append("")
    lines.append("SKILLS:")
    skills = [s.get("name") for s in profile.get("skills", []) if s.get("name")]
    lines.append(", ".join(skills))

    return "\n".join(lines)


class AutoApplyRunner:
    def __init__(self, http: HttpClient | None = None):
        self.http = http or HttpClient("InternshipAssistant/1.0", min_delay=1.0, timeout=25, retries=2)

    def check_application_eligibility(self, job: dict, profile: dict) -> tuple[bool, str, str, list[dict]]:
        """Pre-checks if a job can be auto-applied.

        Returns (eligible, kind, reason, questions).
        If not eligible, kind will be 'google_docs', 'captcha', or 'login'.
        """
        desc = job.get("description", "")
        url = job.get("apply_url") or job.get("url", "")
        source = job.get("source", "")
        platform = job.get("platform", "")

        # 1. Check description and URLs for Google Docs / Forms
        g_check = verification.check_google_docs(desc, url)
        if g_check.detected:
            return False, g_check.kind, g_check.reason, []

        # 2. Check source and platform for mandatory login. Manual imports keep
        # the source as "manual", so the platform must also be checked.
        for verification_source in (source, platform):
            if not verification_source:
                continue
            h_check = verification.check_human_verification(source=verification_source)
            if h_check.detected:
                return False, h_check.kind, h_check.reason, []

        # 3. Check apply page
        ats_type, token, job_id = _extract_token_and_id(url)
        if not ats_type and source in ("greenhouse", "lever", "ashby", "workable"):
            ats_type = source

        # If it's a known ATS, we can fetch its questions/form specification
        questions: list[dict] = []
        if ats_type == "greenhouse" and token and job_id:
            api_url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{job_id}?questions=true"
            res = self.http.get_json(api_url)
            if res.status == 401:
                return False, "login", "Greenhouse requires login to read the application questions", []
            if res.ok and isinstance(res.data, dict):
                raw_qs = res.data.get("questions") or []
                for q in raw_qs:
                    label = (q.get("label") or q.get("name") or "").strip()
                    # Skip standard personal fields that are submitted in payload automatically
                    if label.lower() in ("first name", "last name", "email", "phone", "resume/cv", "cover letter"):
                        continue
                    fields = q.get("fields") or []
                    f0 = fields[0] if fields else {}
                    qid = f0.get("name") or q.get("name") or label
                    vals = f0.get("values") or q.get("values") or []
                    opts = [v.get("label") or str(v.get("value")) for v in vals if v]
                    val_map = {str(v.get("label")).strip().lower(): v.get("value") for v in vals if v.get("label") and v.get("value") is not None}
                    questions.append({
                        "id": str(qid),
                        "label": label,
                        "type": f0.get("type") or q.get("type", "input_text"),
                        "required": bool(q.get("required")),
                        "options": opts,
                        "values_map": val_map,
                    })
                # Check questions for Google Docs requirement
                g_q_check = verification.check_google_docs(fields=questions)
                if g_q_check.detected:
                    return False, g_q_check.kind, g_q_check.reason, []

        # 4. Fetch the application HTML page to inspect for CAPTCHAs or Google Docs
        if url:
            try:
                page_res = self.http.get_text(url)
                if page_res.ok and page_res.data:
                    html_text = page_res.data.decode("utf-8", errors="replace") if isinstance(page_res.data, bytes) else str(page_res.data)
                    # Check for Google Docs in HTML
                    g_html_check = verification.check_google_docs(html_text)
                    if g_html_check.detected:
                        return False, g_html_check.kind, g_html_check.reason, []

                    # Check for human verification in HTML
                    cap_check = verification.check_human_verification(
                        html_content=html_text,
                        url=url,
                        source=source,
                        status_code=page_res.status,
                    )
                    if cap_check.detected:
                        return False, cap_check.kind, cap_check.reason, []
                elif page_res.status == 401:
                    return False, "login", "The application site requires login before the form can be read", []
                elif page_res.status in (403, 429):
                    return False, "captcha", f"HTTP {page_res.status} bot challenge received on apply page", []
            except Exception as exc:
                log.debug("Apply page inspection skipped: %s", exc)

        return True, "", "Eligible for auto-apply", questions

    def submit_greenhouse(
        self,
        token: str,
        job_id: str,
        profile: dict,
        answers: dict[str, str],
        tailored_note: str,
    ) -> tuple[bool, str]:
        """Submits an application to Greenhouse."""
        post_url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{job_id}"
        contact = profile.get("contact", {})
        name_parts = (profile.get("name") or "Dhruv Malviya").split(" ", 1)
        first_name = name_parts[0]
        last_name = name_parts[1] if len(name_parts) > 1 else name_parts[0]

        payload: dict[str, Any] = {
            "first_name": first_name,
            "last_name": last_name,
            "email": contact.get("email", ""),
            "phone": contact.get("phone", ""),
            "resume_text": _build_candidate_resume_text(profile),
            "cover_letter_text": tailored_note,
        }

        # Add custom answers
        for k, v in answers.items():
            key_name = k if k.startswith("question_") else f"question_{k}"
            payload[key_name] = v

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
        }

        try:
            r = requests.post(post_url, json=payload, headers=headers, timeout=25)
            if r.status_code in (200, 201, 302):
                return True, "Application accepted by Greenhouse API"
            if r.status_code == 401:
                return False, "Greenhouse requires login to submit this application"
            if "captcha" in r.text.lower() or r.status_code == 403:
                return False, "Greenhouse challenged submission with CAPTCHA"
            return False, f"Greenhouse returned HTTP {r.status_code}: {r.text[:150]}"
        except Exception as exc:
            return False, f"Greenhouse submission request error: {exc}"

    def submit_lever(
        self,
        token: str,
        job_id: str,
        profile: dict,
        answers: dict[str, str],
        tailored_note: str,
    ) -> tuple[bool, str]:
        """Submits an application to Lever."""
        post_url = f"https://api.lever.co/v0/postings/{token}/{job_id}"
        contact = profile.get("contact", {})

        payload: dict[str, Any] = {
            "name": profile.get("name") or "Dhruv Malviya",
            "email": contact.get("email", ""),
            "phone": contact.get("phone", ""),
            "comments": tailored_note,
            "urls": {
                "LinkedIn": contact.get("linkedin", ""),
                "GitHub": contact.get("github", ""),
                "Portfolio": contact.get("portfolio", ""),
            },
        }
        for k, v in answers.items():
            key_name = k if k.startswith("custom_") else f"custom_{k}"
            payload[key_name] = v

        try:
            r = requests.post(post_url, json=payload, timeout=25)
            if r.status_code in (200, 201, 302):
                return True, "Application accepted by Lever"
            if r.status_code == 401:
                return False, "Lever requires login to submit this application"
            if "captcha" in r.text.lower() or r.status_code == 403:
                return False, "Lever challenged submission with CAPTCHA"
            return False, f"Lever returned HTTP {r.status_code}: {r.text[:150]}"
        except Exception as exc:
            return False, f"Lever submission request error: {exc}"


def apply_to_job(
    conn: Conn,
    user_id: int,
    job_id: int,
    profile: dict,
    dry_run: bool = False,
    gemini_key: str | None = None,
) -> dict:
    """Applies automatically to a single job.

    Enforces all safety checks:
    - Google Docs requirement -> moves to 'User Review Pending'.
    - Human verification -> moves to 'User Review Pending'.
    - Clean eligibility -> answers questions with Gemini AI and submits.
    - Updates database and history automatically.
    """
    job = db.get_job(conn, user_id, job_id)
    if not job:
        return {"ok": False, "status": "error", "message": "Job not found."}

    if job.get("status") == "Applied":
        return {"ok": True, "status": "already_applied", "message": "Job is already marked as Applied.", "job": job}

    runner = AutoApplyRunner()
    eligible, kind, reason, questions = runner.check_application_eligibility(job, profile)

    # 1. Require separate Google Docs / Forms -> Set to User Review Pending
    if kind == "google_docs":
        updated = _set_user_review_pending(conn, user_id, job_id, f"Google Docs / Form submission required: {reason}")
        return {
            "ok": True,
            "status": "user_review_pending",
            "reason": "google_docs",
            "error_code": "external_form_required",
            "stage": "applier",
            "message": f"Requires separate Google Docs/Form submission. Moved to User Review Pending.",
            "detail": reason,
            "job": updated,
        }

    # 2. Human verification / CAPTCHA / mandatory login -> Set to User Review Pending
    if kind in ("captcha", "login") or not eligible:
        if kind == "login":
            platform_label = job.get("platform") or job.get("source") or "the application site"
            warning = f"Login required to apply: {reason}"
            message = f"Login required on {platform_label} for this application. Moved to User Review Pending."
        elif kind == "captcha":
            warning = f"Human verification required to apply: {reason}"
            message = "Human verification required for this application. Moved to User Review Pending."
        else:
            warning = f"Human verification required: {reason}"
            message = "Human verification required for this application. Moved to User Review Pending."
        updated = _set_user_review_pending(conn, user_id, job_id, warning)
        return {
            "ok": True,
            "status": "user_review_pending",
            "reason": kind,
            "error_code": "login_required" if kind == "login" else "human_verification_required",
            "stage": "applier",
            "message": message,
            "detail": reason,
            "job": updated,
        }

    # 3. Form questions: Answer using Gemini AI
    answers: dict[str, str] = {}
    key = gemini.get_api_key(profile, gemini_key)
    if questions and key:
        try:
            answers = gemini.answer_questions(questions, job, profile, api_key=key)
            # Map choice labels to their values if values_map is provided
            for q in questions:
                qid = str(q.get("id") or "")
                vmap = q.get("values_map") or {}
                if qid in answers and vmap:
                    raw_val = str(answers[qid]).strip().lower()
                    if raw_val in vmap:
                        answers[qid] = str(vmap[raw_val])
                    else:
                        for lbl, val in vmap.items():
                            if lbl in raw_val or raw_val in lbl:
                                answers[qid] = str(val)
                                break
        except Exception as exc:
            log.warning("Gemini failed to answer form questions: %s", exc)

    tailored_note = job.get("note") or ""
    if not tailored_note:
        from . import notes
        tailored_note = notes.build_note(profile, job, [], None, profile["filters"].get("night_shift_ok", False))

    # 4. Attempt submission through supported ATS
    url = job.get("apply_url") or job.get("url", "")
    ats_type, token, ats_job_id = _extract_token_and_id(url)
    success, submit_msg = False, "Unknown application platform"

    if not (ats_type in ("greenhouse", "lever") and token and ats_job_id):
        # Unknown or custom ATS form: mark User Review Pending so user reviews the form
        updated = _set_user_review_pending(conn, user_id, job_id, f"Direct submission not available for ATS '{ats_type or job.get('source')}': {url}")
        return {
            "ok": True,
            "status": "user_review_pending",
            "reason": "manual_review_needed",
            "error_code": "manual_review_needed",
            "stage": "applier",
            "message": "Form requires manual submission. Moved to User Review Pending.",
            "job": updated,
        }

    if dry_run:
        return {
            "ok": True,
            "status": "dry_run",
            "message": f"Dry-run: would apply to {job['company']} for {job['role']} via {ats_type} (no human verification or Google Docs detected).",
            "answers": answers,
            "job": job,
        }

    if ats_type == "greenhouse":
        success, submit_msg = runner.submit_greenhouse(token, ats_job_id, profile, answers, tailored_note)
    elif ats_type == "lever":
        success, submit_msg = runner.submit_lever(token, ats_job_id, profile, answers, tailored_note)

    if success:
        # Successfully applied! Automatically update the list
        today = date.today().isoformat()
        followup_days = int(profile.get("drafts", {}).get("followup_days", 7))
        followup = (date.today() + timedelta(days=followup_days)).isoformat()
        user_notes = (job.get("user_notes") or "").strip()
        bot_note = f"Applied automatically by bot on {today} (answers generated with Gemini)."
        new_notes = f"{user_notes}\n{bot_note}".strip() if user_notes else bot_note

        updated = db.update_job_user_fields(
            conn,
            user_id,
            job_id,
            {
                "status": "Applied",
                "applied_date": today,
                "followup_date": followup,
                "user_notes": new_notes,
            },
            followup_days=followup_days,
        )
        return {
            "ok": True,
            "status": "applied",
            "message": f"Successfully applied to {job['company']}! List updated automatically.",
            "job": updated,
        }
    else:
        # Submission triggered a captcha or error -> move to User Review Pending
        login_required = "login" in submit_msg.lower() or "http 401" in submit_msg.lower()
        updated = _set_user_review_pending(conn, user_id, job_id, f"Auto-apply submission halted: {submit_msg}")
        return {
            "ok": False,
            "status": "user_review_pending",
            "reason": "submission_halted",
            "error_code": "login_required" if login_required else "submission_halted",
            "stage": "applier",
            "message": f"Could not complete submission: {submit_msg}. Moved to User Review Pending.",
            "job": updated,
        }


def _set_user_review_pending(conn: Conn, user_id: int, job_id: int, warning_msg: str) -> dict:
    """Moves a job to 'User Review Pending', appends the warning, and commits."""
    job = db.get_job(conn, user_id, job_id)
    if not job:
        raise KeyError(job_id)

    warnings = job.get("warnings") or []
    if warning_msg not in warnings:
        warnings.append(warning_msg)
        conn.execute("UPDATE jobs SET warnings=? WHERE id=? AND user_id=?", (json.dumps(warnings), job_id, user_id))

    # Update status to User Review Pending
    updated = db.update_job_user_fields(
        conn,
        user_id,
        job_id,
        {"status": "User Review Pending"},
    )
    return updated


def auto_apply_batch(
    conn: Conn,
    user_id: int,
    profile: dict,
    max_count: int = 5,
    dry_run: bool = False,
    gemini_key: str | None = None,
) -> list[dict]:
    """Applies automatically to up to max_count eligible jobs for this user."""
    # Eligible candidates are "Ready to Apply" or high-scoring "To Review" without red flags
    limit = max(100, max_count * 10)
    jobs = conn.execute(
        "SELECT id, status, company, role, score, red_flags FROM jobs "
        "WHERE user_id=? AND status IN ('Ready to Apply', 'To Review') AND is_open=1 "
        "ORDER BY (CASE WHEN status='Ready to Apply' THEN 1 ELSE 2 END), "
        "(CASE WHEN source IN ('greenhouse', 'lever') OR url LIKE '%greenhouse.io%' OR url LIKE '%lever.co%' OR apply_url LIKE '%greenhouse.io%' OR apply_url LIKE '%lever.co%' THEN 1 ELSE 2 END), "
        "score DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()

    results = []
    applied_count = 0

    for r in jobs:
        if applied_count >= max_count:
            break
        red = json.loads(r["red_flags"] or "[]")
        if red:
            continue

        res = apply_to_job(conn, user_id, r["id"], profile, dry_run=dry_run, gemini_key=gemini_key)
        results.append({
            "job_id": r["id"],
            "company": r["company"],
            "role": r["role"],
            "result": res,
        })
        if res.get("status") in ("applied", "dry_run"):
            applied_count += 1

    return results
