"""Pay-to-train / fake-employer protections: company-name flags, copy-pasted postings, blocklist, re-check."""
from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from bot import config, db, flags
from bot.config import Company
from bot.fetchers.base import Job
from bot.pipeline import evaluate, machine_hash, rescore_user
from bot.textutil import normalize_url

PROFILE = config.load_profile(config.ROOT / "profile.example.json")
TEMPLATE = "\n".join([
    "Responsibilities of the intern:",
    "Answer customer calls professionally and resolve their questions with patience and care.",
    "Maintain detailed records of every customer interaction in the internal CRM system.",
    "Adjust your tone, pace and approach to match the needs and personality of each customer.",
    "Escalate complex issues to the senior support team with a clear written summary of the problem.",
    "Build React components for the support dashboard and keep the styling consistent with Tailwind.",
])
BOILERPLATE = "\n".join([
    "Only those candidates can apply who are available for the work from home internship.",
    "Certificate, letter of recommendation and flexible work hours are included as perks here.",
])


def job(company="Acme Labs", description=TEMPLATE, url=None, title="Frontend Intern"):
    return Job("unstop", company, "1", title, url or f"https://unstop.com/internships/{company.replace(' ', '-')}",
               location="Remote (India)", workplace="remote", description=description, via="Unstop")


def no_domain(_):
    return None


class CompanyNames(unittest.TestCase):
    def test_training_provider_names_are_red_flags(self):
        for name in ("Internship Mela", "The Intern Group", "Placement Hub", "XYZ Training Pvt Ltd"):
            red, _ = flags.company_name_flags(name)
            self.assertTrue(red, name)

    def test_education_names_are_warnings(self):
        for name in ("Learntricks Edutech", "INFINEXA LEARNING SOLUTIONS", "Code Academy"):
            red, warn = flags.company_name_flags(name)
            self.assertFalse(red, name)
            self.assertTrue(warn, name)

    def test_ordinary_names_pass(self):
        for name in ("Figma", "Internshala Partner Co", "Sociokix LLP"):
            red, warn = flags.company_name_flags(name)
            self.assertEqual((red, warn), ([], []), name)

    def test_whitelisted_companies_are_trusted_by_name(self):
        vetted = Company("Training Wheels", "", "greenhouse", "tw", website="https://tw.dev")
        ev = evaluate(job("Training Wheels"), vetted, PROFILE, no_domain)
        self.assertFalse(any("training / placement" in r for r in ev.record["red_flags"]))


class CopiedPostings(unittest.TestCase):
    def test_copies_with_small_edits_are_found(self):
        idx = flags.TemplateIndex()
        idx.add("Acme Labs", TEMPLATE)
        edited = TEMPLATE.replace("React components", "web components")
        idx.add("Other Name Co", edited)
        self.assertEqual(idx.others("Other Name Co", edited), ["acme labs"])
        self.assertEqual(idx.others("Acme Labs", TEMPLATE), ["other name co"])

    def test_same_company_is_not_a_copy(self):
        idx = flags.TemplateIndex()
        idx.add("Acme Labs", TEMPLATE)
        self.assertEqual(idx.others("Acme Labs", TEMPLATE), [])

    def test_job_site_boilerplate_is_ignored(self):
        idx = flags.TemplateIndex()
        for i in range(flags.TemplateIndex.BOILERPLATE_COMPANIES + 2):
            idx.add(f"Company {i}", BOILERPLATE + f"\nUnique responsibility number {i} written only for this company and nowhere else.")
        self.assertEqual(idx.others("Company 0", BOILERPLATE + "\nUnique responsibility number 0 written only for this company and nowhere else."), [])

    def test_copy_becomes_red_flag(self):
        ev = evaluate(job(), Company("Acme Labs", "", "unstop", ""), PROFILE, no_domain,
                      context={"template_others": ["other name co"]})
        self.assertEqual(ev.initial_status, "Skipped (red flag)")
        self.assertTrue(any("same posting text" in r for r in ev.record["red_flags"]))


class BlockAndRecheck(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "s.db")
        self.uid = db.ensure_local_user(self.conn)["id"]

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def track(self, j, status_change=None):
        key = normalize_url(j.url)
        db.upsert_posting(self.conn, None, key, j.to_dict())
        ev = evaluate(j, Company(j.company, "", "unstop", ""), PROFILE, no_domain)
        _, job_id = db.upsert_job(self.conn, self.uid, None, key, ev.record, machine_hash(ev.record), ev.initial_status,
                                  ev.note, ev.answers)
        self.conn.commit()
        if status_change:
            db.update_job_user_fields(self.conn, self.uid, job_id, {"status": status_change})
        return job_id

    def test_blocking_skips_untouched_listings_only(self):
        fresh = self.track(job("Shady Co", url="https://unstop.com/a"))
        applied = self.track(job("Shady Co", url="https://unstop.com/b"), status_change="Applied")
        moved = db.skip_company_jobs(self.conn, self.uid, "shady co", "You marked Shady Co as not a real employer")
        self.assertEqual(moved, 1)
        self.assertEqual(db.get_job(self.conn, self.uid, fresh)["status"], "Skipped (red flag)")
        self.assertEqual(db.get_job(self.conn, self.uid, applied)["status"], "Applied")
        self.assertIn("You marked Shady Co as not a real employer", db.get_job(self.conn, self.uid, applied)["red_flags"])

    def test_blocklist_flags_future_postings(self):
        p = copy.deepcopy(PROFILE)
        p["red_flags"]["blocked_companies"] = ["shady co"]
        ev = evaluate(job("Shady Co"), Company("Shady Co", "", "unstop", ""), p, no_domain)
        self.assertEqual(ev.initial_status, "Skipped (red flag)")

    def test_recheck_moves_untouched_jobs_and_keeps_user_statuses(self):
        untouched = self.track(job("Internship Hub", url="https://unstop.com/c", description=TEMPLATE))
        ready = self.track(job("Placement Pros", url="https://unstop.com/d", description=TEMPLATE + "\nOne more line about the role here."),
                           status_change="Ready to Apply")
        # Both started as To Review only if no red flag existed; force a clean start for the test:
        self.conn.execute("UPDATE jobs SET red_flags='[]', machine_hash='old' WHERE user_id=?", (self.uid,))
        self.conn.execute("UPDATE jobs SET status='To Review' WHERE id=?", (untouched,))
        self.conn.execute("DELETE FROM status_history WHERE job_id=? AND to_status<>'To Review'", (untouched,))
        self.conn.commit()
        res = rescore_user(self.conn, self.uid, PROFILE, [])
        self.assertEqual(res["checked"], 2)
        self.assertEqual(db.get_job(self.conn, self.uid, untouched)["status"], "Skipped (red flag)")
        self.assertEqual(db.get_job(self.conn, self.uid, ready)["status"], "Ready to Apply")
        self.assertTrue(db.get_job(self.conn, self.uid, ready)["red_flags"])


if __name__ == "__main__":
    unittest.main()
