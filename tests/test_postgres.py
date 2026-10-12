"""Runs the database tests against a real Postgres (what the deployed site uses).

Skipped unless TEST_DATABASE_URL is set, e.g. with a throwaway Docker container:
    docker run -d --name tracker-pg-test -e POSTGRES_PASSWORD=testpw -e POSTGRES_DB=tracker -p 127.0.0.1:54329:5432 postgres:16-alpine
    TEST_DATABASE_URL=postgresql://postgres:testpw@127.0.0.1:54329/tracker python -m unittest tests.test_postgres
WARNING: every test drops all tables in that database first.
"""
from __future__ import annotations

import os
import unittest

from bot import db, store

try:
    from tests.test_bot import MultiUser, TrackerPreservesEdits
except ImportError:  # run via `unittest discover tests`
    from test_bot import MultiUser, TrackerPreservesEdits

URL = os.environ.get("TEST_DATABASE_URL", "")


def fresh_pg():
    raw = store.connect_raw(URL)
    tables = [r["tablename"] for r in raw.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")]
    if tables:
        raw.execute(f"DROP TABLE IF EXISTS {', '.join(tables)} CASCADE")
    raw.commit()
    raw.close()
    return db.connect(URL)


@unittest.skipUnless(URL, "TEST_DATABASE_URL not set")
class PgMultiUser(MultiUser):
    def setUp(self):
        self.conn = fresh_pg()

    def tearDown(self):
        self.conn.close()


@unittest.skipUnless(URL, "TEST_DATABASE_URL not set")
class PgTrackerPreservesEdits(TrackerPreservesEdits):
    def setUp(self):
        self.conn = fresh_pg()
        self.uid = db.get_or_create_user(self.conn, "pg@example.com")[0]["id"]

    def tearDown(self):
        self.conn.close()


@unittest.skipUnless(URL, "TEST_DATABASE_URL not set")
class PgSchemaAndQueries(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_pg()

    def tearDown(self):
        self.conn.close()

    def test_stats_runs_prune_and_settings(self):
        u, _ = db.get_or_create_user(self.conn, "s@example.com")
        self.assertEqual(db.stats(self.conn, u["id"])["total"], 0)
        for _ in range(3):
            rid = db.start_run(self.conn, "cli", {})
            db.update_run(self.conn, rid, status="ok", counts={"postings": 1})
            self.conn.commit()
        db.prune_old_runs(self.conn, keep=2)
        self.conn.commit()
        self.assertEqual(len(db.list_runs(self.conn, u["id"])), 2)
        db.set_setting(self.conn, "sources", {"feeds": {"x": 1}})
        self.assertEqual(db.get_setting(self.conn, "sources"), {"feeds": {"x": 1}})
        db.save_profile_data(self.conn, u["id"], {"skills": []})
        self.assertEqual(db.get_profile_data(self.conn, u["id"]), {"skills": []})
        self.assertTrue(db.jobs_version(self.conn, u["id"]).endswith("#0"))

    def test_programs_named_params(self):
        from bot import programs
        r = {"key": "gsoc", "name": "GSoC", "status": "x", "detail": "", "next_label": "", "next_date": "", "url": "",
             "error": "", "checked_at": db.now_iso()}
        self.conn.execute(
            "INSERT INTO programs(key, name, status, detail, next_label, next_date, url, checked_at, error) "
            "VALUES (:key, :name, :status, :detail, :next_label, :next_date, :url, :checked_at, :error) "
            "ON CONFLICT(key) DO UPDATE SET status=excluded.status", r)
        self.conn.commit()
        self.assertEqual(programs.all_status(self.conn)["gsoc"]["status"], "x")


if __name__ == "__main__":
    unittest.main()
