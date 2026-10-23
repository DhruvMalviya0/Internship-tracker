"""Tests for auto-apply, Google Docs detection, human verification detection, and Gemini answering."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from bot import auto_apply, config, db, gemini, verification
from bot.fetchers.base import Job


class GoogleDocsDetectionTests(unittest.TestCase):
    def test_detects_google_doc_url(self):
        desc = "To apply, please submit your details in this doc: https://docs.google.com/document/d/12345/edit"
        res = verification.check_google_docs(desc)
        self.assertTrue(res.detected)
        self.assertEqual(res.kind, "google_docs")
        self.assertIn("docs.google.com", res.detail)

    def test_detects_forms_gle_url(self):
        desc = "Candidates must fill out the form at https://forms.gle/abcXYZ123 to be considered."
        res = verification.check_google_docs(desc)
        self.assertTrue(res.detected)
        self.assertEqual(res.kind, "google_docs")

    def test_detects_google_doc_phrase_without_url(self):
        desc = "Please submit your details in a separate google doc along with your github profile."
        res = verification.check_google_docs(desc)
        self.assertTrue(res.detected)
        self.assertEqual(res.kind, "google_docs")

    def test_detects_google_form_phrase(self):
        desc = "Complete the google form mentioned in the task guidelines."
        res = verification.check_google_docs(desc)
        self.assertTrue(res.detected)
        self.assertEqual(res.kind, "google_docs")

    def test_clean_description_passes(self):
        desc = "We are looking for a React intern to build clean dashboards. Apply via this job board."
        res = verification.check_google_docs(desc)
        self.assertFalse(res.detected)

    def test_detects_google_doc_in_question_fields(self):
        fields = [
            {"label": "Full Name", "name": "name"},
            {"label": "Please share your Google Doc link with the assignment", "name": "assignment_doc"},
        ]
        res = verification.check_google_docs("Regular description", fields=fields)
        self.assertTrue(res.detected)
        self.assertEqual(res.kind, "google_docs")


class HumanVerificationDetectionTests(unittest.TestCase):
    def test_detects_cloudflare_turnstile(self):
        html = '<div><script src="https://challenges.cloudflare.com/turnstile/v0/api.js"></script><div class="cf-turnstile"></div></div>'
        res = verification.check_human_verification(html)
        self.assertTrue(res.detected)
        self.assertEqual(res.kind, "captcha")

    def test_detects_recaptcha(self):
        html = '<div class="g-recaptcha" data-sitekey="6Ld..."></div>'
        res = verification.check_human_verification(html)
        self.assertTrue(res.detected)
        self.assertEqual(res.kind, "captcha")

    def test_detects_hcaptcha(self):
        html = '<script src="https://js.hcaptcha.com/1/api.js"></script>'
        res = verification.check_human_verification(html)
        self.assertTrue(res.detected)
        self.assertEqual(res.kind, "captcha")

    def test_detects_challenge_phrases(self):
        html = '<h1>Attention Required! | Cloudflare</h1><p>Please complete the security check to access.</p>'
        res = verification.check_human_verification(html)
        self.assertTrue(res.detected)
        self.assertEqual(res.kind, "captcha")

    def test_detects_login_required_platforms(self):
        res = verification.check_human_verification(source="internshala")
        self.assertTrue(res.detected)
        self.assertEqual(res.kind, "login")

        res_unstop = verification.check_human_verification(source="unstop")
        self.assertTrue(res_unstop.detected)
        self.assertEqual(res_unstop.kind, "login")

    def test_clean_greenhouse_passes(self):
        html = '<form action="/jobs/1" method="post"><input type="text" name="first_name"/></form>'
        res = verification.check_human_verification(html, source="greenhouse")
        self.assertFalse(res.detected)


class GeminiHelperTests(unittest.TestCase):
    def test_get_api_key_resolution(self):
        profile = {"gemini": {"api_key": "profile_key"}}
        self.assertEqual(gemini.get_api_key(profile), "profile_key")
        self.assertEqual(gemini.get_api_key(profile, override="override_key"), "override_key")

    def test_candidate_summary_generation(self):
        profile = {
            "name": "Dhruv Malviya",
            "preferred_name": "Leaf",
            "contact": {"email": "dhruv@example.com", "github": "https://github.com/DhruvMalviya0"},
            "skills": [{"name": "React", "weight": 3}, {"name": "Python", "weight": 3}],
            "projects": [{"name": "Portfolio", "url": "https://example.com", "summary": "3D site", "tags": ["react", "three.js"]}],
        }
        summary = gemini._candidate_summary(profile)
        self.assertIn("Dhruv Malviya", summary)
        self.assertIn("React", summary)
        self.assertIn("Portfolio", summary)


class AutoApplyIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        self.user = db.ensure_local_user(self.conn)
        self.profile = config.load_profile(config.ROOT / "profile.example.json")
        self.profile["gemini"] = {"api_key": "fake_test_key"}

    def tearDown(self):
        self.conn.close()

    def _insert_job(self, **kwargs) -> int:
        record = {
            "url": "https://job-boards.greenhouse.io/acme/jobs/123",
            "apply_url": "https://job-boards.greenhouse.io/acme/jobs/123",
            "source": "greenhouse",
            "platform": "",
            "ats_id": "123",
            "company": "Acme Inc",
            "role": "Frontend Intern",
            "location": "Remote",
            "remote_type": "Remote",
            "region": "Worldwide",
            "hours_num": 25,
            "hours_label": "In range (20-30h)",
            "paid": "Yes",
            "compensation": "$1,500/mo",
            "score": 85,
            "matched_skills": ["React"],
            "missing_skills": [],
            "score_detail": {},
            "ai_screening": 0,
            "ai_reasons": [],
            "red_flags": [],
            "warnings": [],
            "night_shift": 0,
            "description": "Build high craft React web applications with us.",
            "posted_at": "2026-10-01",
        }
        record.update(kwargs)
        status = kwargs.get("status", "To Review")
        _, job_id = db.upsert_job(self.conn, self.user["id"], None, record["url"], record, "hash123", status, "Note", "Answers")
        return job_id

    def test_auto_apply_skips_and_moves_to_user_review_pending_on_google_docs(self):
        job_id = self._insert_job(description="Submit your details in a separate google doc: https://docs.google.com/forms/d/e/123")
        res = auto_apply.apply_to_job(self.conn, self.user["id"], job_id, self.profile)
        self.assertEqual(res["status"], "user_review_pending")
        self.assertEqual(res["reason"], "google_docs")

        # Verify database was updated automatically
        updated = db.get_job(self.conn, self.user["id"], job_id)
        self.assertEqual(updated["status"], "User Review Pending")
        self.assertTrue(any("Google Docs" in w for w in updated["warnings"]))

    def test_auto_apply_skips_and_moves_to_user_review_pending_on_login_required(self):
        job_id = self._insert_job(source="internshala", url="https://internshala.com/internship/detail/123")
        res = auto_apply.apply_to_job(self.conn, self.user["id"], job_id, self.profile)
        self.assertEqual(res["status"], "user_review_pending")
        self.assertEqual(res["reason"], "login")
        self.assertEqual(res["error_code"], "login_required")
        self.assertEqual(res["stage"], "applier")
        self.assertIn("Login required", res["message"])
        self.assertIn("Login required", res["message"])

        updated = db.get_job(self.conn, self.user["id"], job_id)
        self.assertEqual(updated["status"], "User Review Pending")
        self.assertTrue(any("Login required" in w for w in updated["warnings"]))

    def test_manual_platform_login_requirement_is_detected(self):
        job_id = self._insert_job(
            source="manual",
            platform="Internshala",
            url="https://internshala.com/internship/detail/456",
        )
        res = auto_apply.apply_to_job(self.conn, self.user["id"], job_id, self.profile)
        self.assertEqual(res["reason"], "login")
        self.assertIn("Login required", res["message"])

    @patch.object(auto_apply.AutoApplyRunner, "check_application_eligibility")
    def test_captcha_message_is_distinct_from_login(self, mock_check):
        mock_check.return_value = (False, "captcha", "Cloudflare challenge", [])
        job_id = self._insert_job()
        res = auto_apply.apply_to_job(self.conn, self.user["id"], job_id, self.profile)
        self.assertEqual(res["reason"], "captcha")
        self.assertIn("Human verification required", res["message"])
        self.assertNotIn("Login required", res["message"])

    @patch.object(auto_apply.AutoApplyRunner, "submit_greenhouse")
    @patch.object(auto_apply.AutoApplyRunner, "check_application_eligibility")
    def test_auto_apply_successful_submission_updates_list_to_applied(self, mock_check, mock_submit):
        mock_check.return_value = (True, "", "Eligible", [])
        mock_submit.return_value = (True, "Application accepted")

        job_id = self._insert_job(status="Ready to Apply")
        res = auto_apply.apply_to_job(self.conn, self.user["id"], job_id, self.profile)

        self.assertEqual(res["status"], "applied")
        self.assertTrue(res["ok"])

        # Check job in DB
        updated = db.get_job(self.conn, self.user["id"], job_id)
        self.assertEqual(updated["status"], "Applied")
        self.assertIsNotNone(updated["applied_date"])
        self.assertIsNotNone(updated["followup_date"])
        self.assertIn("Applied automatically", updated["user_notes"])

        # Check history
        history = [h["to_status"] for h in updated["history"]]
        self.assertIn("Applied", history)


class AutoApplyApiTests(unittest.TestCase):
    def setUp(self):
        from web.app import create_app
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(db_target=str(Path(self.tmp.name) / "api.db"))
        self.client = self.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def test_gemini_test_api(self):
        res = self.client.post("/api/gemini/test", headers={"X-Tracker": "1"}, json={"api_key": ""})
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.json["ok"])
        self.assertIn("empty", res.json["message"].lower())

    @patch("bot.auto_apply.apply_to_job")
    def test_job_auto_apply_api(self, mock_apply):
        mock_apply.return_value = {"ok": True, "status": "applied", "message": "Applied successfully!"}
        res = self.client.post("/api/jobs/1/auto-apply", headers={"X-Tracker": "1"}, json={})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json["ok"])
        self.assertEqual(res.json["status"], "applied")

    @patch("bot.auto_apply.auto_apply_batch")
    def test_batch_auto_apply_api(self, mock_batch):
        mock_batch.return_value = [{"job_id": 1, "company": "Acme", "result": {"status": "applied"}}]
        res = self.client.post("/api/auto-apply/batch", headers={"X-Tracker": "1"}, json={"max_count": 3})
        self.assertEqual(res.status_code, 200)
        self.assertIsInstance(res.json["run_id"], int)
        self.assertEqual(len(res.json["results"]), 1)

    @patch("bot.auto_apply.auto_apply_batch")
    def test_batch_login_failure_is_visible_in_bot_activity(self, mock_batch):
        mock_batch.return_value = [{
            "job_id": 7,
            "company": "Internshala",
            "role": "Frontend Intern",
            "result": {
                "status": "user_review_pending",
                "error_code": "login_required",
                "message": "Login required for this application. Moved to User Review Pending.",
            },
        }]
        res = self.client.post("/api/auto-apply/batch", headers={"X-Tracker": "1"}, json={"max_count": 1})
        self.assertEqual(res.status_code, 200)
        run_id = res.json["run_id"]
        events = self.client.get(f"/api/runs/{run_id}/events").json["events"]
        self.assertTrue(any(e["kind"] == "applier_login_required" for e in events))


if __name__ == "__main__":
    unittest.main()
