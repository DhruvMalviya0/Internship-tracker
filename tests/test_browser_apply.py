"""Tests for the optional logged-in-browser application flow."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

from bot import config, db
from bot.browser_apply import (
    BrowserApplyResult,
    build_fill_plan,
    is_login_page,
    login_required_jobs,
    parse_answer_draft,
    run_browser_apply,
)


PROFILE = config.load_profile(config.ROOT / "profile.example.json")
PROFILE["name"] = "Test Person"
PROFILE["contact"] = {
    "email": "test@example.com",
    "phone": "+91 9999999999",
    "linkedin": "https://linkedin.com/in/test",
    "github": "https://github.com/test",
    "portfolio": "https://test.example.com",
}
PROFILE["location"] = {"city": "Pune", "country": "India"}


class BrowserApplyHelpers(unittest.TestCase):
    def test_parse_answer_draft_ignores_placeholders(self):
        answers = parse_answer_draft("Email          : test@example.com\nStart date     : [fill in]\nGitHub         : https://github.com/test")
        self.assertEqual(answers, {"email": "test@example.com", "github": "https://github.com/test"})

    def test_build_fill_plan_maps_profile_and_draft_answers(self):
        job = {"note": "Hello team", "answers": "Why us? : Because the product matches my interests."}
        fields = [
            {"index": 0, "label": "Full name", "type": "text"},
            {"index": 1, "label": "Email address", "type": "email"},
            {"index": 2, "label": "Why us?", "type": "text"},
        ]
        plan = build_fill_plan(fields, PROFILE, job)
        self.assertEqual([x["value"] for x in plan], ["Test Person", "test@example.com", "Because the product matches my interests."])

    def test_login_page_detection_requires_url_or_password_context(self):
        self.assertTrue(is_login_page("https://example.com/login", "Welcome back"))
        self.assertTrue(is_login_page("https://example.com/apply", "Please sign in", password_fields=1))
        self.assertFalse(is_login_page("https://example.com/apply", "Our login policy is described here"))


class BrowserApplyDatabaseFlow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "browser.db")
        self.user = db.ensure_local_user(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def job(self, warning: bool | str = True):
        warning_text = (
            "Login required to apply: Internshala requires candidate account login" if warning is True else
            "Human verification required (login): Internshala requires candidate account login and session verification"
            if warning == "legacy" else ""
        )
        record = {
            "url": "https://internshala.com/internship/detail/1",
            "apply_url": "https://internshala.com/internship/detail/1",
            "source": "internshala",
            "platform": "Internshala",
            "company": "Acme",
            "role": "Frontend Intern",
            "warnings": [warning_text] if warning_text else [],
            "red_flags": [],
        }
        _, job_id = db.upsert_job(
            self.conn, self.user["id"], None, record["url"], record, "hash", "User Review Pending", "Note", "Answers",
        )
        job = db.get_job(self.conn, self.user["id"], job_id)
        assert job is not None
        return job

    def test_login_held_jobs_are_selectable(self):
        job = self.job()
        self.assertEqual([j["id"] for j in login_required_jobs(self.conn, self.user["id"])], [job["id"]])

    def test_legacy_login_warning_is_selectable(self):
        job = self.job("legacy")
        self.assertEqual([j["id"] for j in login_required_jobs(self.conn, self.user["id"])], [job["id"]])

    def test_submitted_result_marks_job_applied(self):
        job = self.job()

        class FakePage:
            def close(self):
                pass

        class FakeSession:
            def new_page(self):
                return FakePage()

        class FakeRunner:
            def __init__(self, timeout_ms):
                pass

            def apply(self, page, job, profile, submit, resume_path):
                return BrowserApplyResult(True, "submitted", message="Confirmed")

        with patch("bot.browser_apply.BrowserApplier", FakeRunner):
            results = run_browser_apply(self.conn, self.user["id"], PROFILE, [job], cast(Any, FakeSession()), submit=True)

        self.assertEqual(results[0]["result"]["status"], "submitted")
        updated = db.get_job(self.conn, self.user["id"], job["id"])
        assert updated is not None
        self.assertEqual(updated["status"], "Applied")
        self.assertTrue(updated["applied_date"])

    def test_blocked_result_keeps_pending_and_adds_warning(self):
        job = self.job()

        class FakePage:
            def close(self):
                pass

        class FakeSession:
            def new_page(self):
                return FakePage()

        class FakeRunner:
            def __init__(self, timeout_ms):
                pass

            def apply(self, page, job, profile, submit, resume_path):
                return BrowserApplyResult(False, "blocked", reason="browser_login_required", message="Browser is not logged in")

        with patch("bot.browser_apply.BrowserApplier", FakeRunner):
            results = run_browser_apply(self.conn, self.user["id"], PROFILE, [job], cast(Any, FakeSession()), submit=True)

        self.assertEqual(results[0]["result"]["status"], "blocked")
        updated = db.get_job(self.conn, self.user["id"], job["id"])
        assert updated is not None
        self.assertEqual(updated["status"], "User Review Pending")
        self.assertTrue(any("Browser applier" in warning for warning in updated["warnings"]))


if __name__ == "__main__":
    unittest.main()
