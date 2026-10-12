"""Inactive-account pausing and instant matching from recently fetched postings."""
from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from bot import activity, config, db, workspace
from bot.fetchers.base import Job
from bot.pipeline import match_from_cache
from bot.textutil import normalize_url

PROFILE = config.load_profile()


def days_ago(n: int) -> str:
    return (datetime.now() - timedelta(days=n)).isoformat(timespec="seconds")


class Inactivity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "a.db")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def user(self, email, last_active_days):
        u, _ = db.get_or_create_user(self.conn, email)
        self.conn.execute("UPDATE users SET last_active=?, last_login=?, created_at=? WHERE id=?",
                          (days_ago(last_active_days), days_ago(last_active_days), days_ago(60), u["id"]))
        self.conn.commit()
        return db.get_user(self.conn, u["id"])

    def test_inactive_accounts_are_paused_and_emailed_once(self):
        active = self.user("active@example.com", 2)
        idle = self.user("idle@example.com", 20)
        sent = []
        with mock.patch.dict(os.environ, {"EMAIL_DEV_LOG": "1", "INACTIVE_DAYS": "14", "PUBLIC_URL": "https://x.test"}), \
                mock.patch("bot.mailer.send_pause_notice", side_effect=lambda *a, **k: sent.append(a[0])):
            users, notes = activity.apply_inactivity(self.conn, db.list_users(self.conn))
            self.assertEqual([u["email"] for u in users], ["active@example.com"])
            self.assertEqual(sent, ["idle@example.com"])
            self.assertIn("Paused the daily search for 1 account", notes[0])
            # Next run: still paused, not emailed again.
            users, notes = activity.apply_inactivity(self.conn, db.list_users(self.conn))
            self.assertEqual(sent, ["idle@example.com"])
            self.assertIn("1 paused account skipped", notes[0])
        self.assertTrue(db.get_user(self.conn, idle["id"])["pause_notified_at"])
        db.resume_user(self.conn, idle["id"])
        back = db.get_user(self.conn, idle["id"])
        self.assertIsNone(back["paused_at"])
        users, _ = activity.apply_inactivity(self.conn, db.list_users(self.conn))
        self.assertEqual(len(users), 2)
        self.assertTrue(active)

    def test_pause_without_email_setup_still_pauses(self):
        self.user("idle@example.com", 30)
        env = {k: "" for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "EMAIL_FROM", "EMAIL_DEV_LOG")}
        with mock.patch.dict(os.environ, env):
            users, notes = activity.apply_inactivity(self.conn, db.list_users(self.conn))
        self.assertEqual(users, [])
        self.assertIn("email not set up", notes[0])

    def test_touch_is_throttled(self):
        u = self.user("x@example.com", 3)
        db.touch_user(self.conn, u)
        fresh = db.get_user(self.conn, u["id"])
        self.assertGreater(fresh["last_active"], days_ago(1))
        db.touch_user(self.conn, fresh)  # within the hour: no write needed
        self.assertEqual(db.get_user(self.conn, u["id"])["last_active"], fresh["last_active"])


class MatchFromCache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "m.db")
        self.a, _ = db.get_or_create_user(self.conn, "a@example.com")
        self.b, _ = db.get_or_create_user(self.conn, "b@example.com")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def posting(self, url, source="unstop", title="Frontend Developer Internship", company="Pixel Studio",
                description="Build React and Tailwind interfaces with Framer Motion. Stipend INR 10,000 per month."):
        j = Job(source, company, "1", title, url, location="Remote (India)", workplace="remote",
                description=description,
                employment_type="Internship", via="Unstop")
        db.upsert_posting(self.conn, None, normalize_url(url), j.to_dict())
        self.conn.commit()

    def test_new_user_gets_matches_without_fetching(self):
        self.posting("https://unstop.com/a")
        self.posting("https://unstop.com/b", title="Accounts Payable Intern", company="Ledger Co",
                     description="Reconcile invoices and vendor payments in Tally and Excel. Stipend INR 8,000 per month.")
        res = match_from_cache(self.conn, self.a["id"], PROFILE, [])
        self.assertEqual((res["checked"], res.get("new"), res["skipped"]), (2, 1, 1))
        again = match_from_cache(self.conn, self.a["id"], PROFILE, [])
        self.assertEqual(again["checked"], 0)  # already judged

    def test_other_users_manual_adds_are_never_shared(self):
        self.posting("https://example.com/private-referral", source="manual")
        res = match_from_cache(self.conn, self.b["id"], PROFILE, [])
        self.assertEqual(res["checked"], 0)
        self.assertEqual(db.list_jobs(self.conn, self.b["id"]), [])

    def test_incomplete_profile_matches_nothing(self):
        self.posting("https://unstop.com/c")
        p = workspace.new_user_profile("c@example.com")
        res = match_from_cache(self.conn, self.a["id"], config.deep_merge(config.DEFAULTS, p), [])
        self.assertTrue(res.get("profile_incomplete"))


if __name__ == "__main__":
    unittest.main()
