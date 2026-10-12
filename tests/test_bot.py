"""Offline tests for filters, scoring, flags, notes and the tracker's edit-preservation rules.
Run with:  python -m unittest discover tests"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from bot import config, db, filters, flags, scoring
from bot.config import Company
from bot.fetchers.base import Job, is_internship
from bot.pipeline import evaluate, machine_hash
from bot.textutil import html_to_text, normalize_url

PROFILE = config.load_profile()
COMPANY = Company("Acme", "Test", "greenhouse", "acme", website="https://acme.com")


def job(**kw) -> Job:
    base = dict(source="greenhouse", company="Acme", ats_id="1", title="Frontend Intern",
                url="https://boards.greenhouse.io/acme/jobs/1", location="Remote - Worldwide",
                description="Build UIs with React, Tailwind and Framer Motion. Paid stipend of $1,500 per month.")
    base.update(kw)
    return Job(**base)


def no_domain(_):
    return None


class InternshipDetection(unittest.TestCase):
    def test_titles(self):
        for t in ["Frontend Intern", "Software Engineering Internship", "Design Co-op", "Working Student - UI"]:
            self.assertTrue(is_internship(t), t)
        for t in ["Internal Tools Engineer", "International Sales Lead", "Senior Frontend Engineer"]:
            self.assertFalse(is_internship(t), t)

    def test_employment_type(self):
        self.assertTrue(is_internship("Product Designer", "Intern"))


class RemoteAndRegion(unittest.TestCase):
    def test_workplace_field_wins(self):
        self.assertEqual(filters.workplace_type(job(workplace="onsite", location="Remote")), "On-site")
        self.assertEqual(filters.workplace_type(job(workplace="remote", location="Berlin")), "Remote")

    def test_location_text(self):
        self.assertEqual(filters.workplace_type(job(location="Hybrid - San Francisco")), "Hybrid")
        self.assertEqual(filters.workplace_type(job(location="Bengaluru", description="Office role.")), "On-site")

    def test_allowed_regions(self):
        self.assertEqual(filters.region(job(location="Remote - India"), PROFILE).label, "India")
        self.assertEqual(filters.region(job(location="Remote (APAC)"), PROFILE).label, "APAC")
        self.assertEqual(filters.region(job(location="Remote"), PROFILE).label, "Unspecified")

    def test_restricted_regions(self):
        for loc in ["Remote - US", "Remote (US + Canada Only)", "Remote, EMEA", "United States - Remote", "Austin, TX"]:
            self.assertTrue(filters.region(job(location=loc), PROFILE).reject_detail, loc)
        self.assertTrue(filters.region(job(location="Remote", countries=["US"]), PROFILE).reject_detail)

    def test_reject_phrases(self):
        j = job(description="Candidates must be authorized to work in the United States.")
        self.assertIn("authorized to work in the united states", filters.find_reject_phrase(j, PROFILE).lower())
        self.assertEqual(filters.find_reject_phrase(job(), PROFILE), "")


class HoursAndPay(unittest.TestCase):
    def test_hours(self):
        self.assertEqual(filters.hours(job(description="20-30 hours per week"), PROFILE)[1], "In range (20-30h)")
        self.assertTrue(filters.hours(job(description="about 10 hrs/week"), PROFILE)[1].startswith("Under target"))
        self.assertTrue(filters.hours(job(description="", employment_type="Full-time"), PROFILE)[1].startswith("Slightly over"))
        self.assertEqual(filters.hours(job(description="nothing here"), PROFILE)[1], "Hours unknown")

    def test_paid(self):
        self.assertEqual(filters.paid(job()), "Yes")
        self.assertEqual(filters.paid(job(description="This is an unpaid internship.")), "No")
        self.assertEqual(filters.paid(job(description="Great team.")), "Unknown")


class ScoringAndFlags(unittest.TestCase):
    def test_frontend_scores_higher_than_backend(self):
        fe, matched, missing, _ = scoring.score(job(title="Frontend Intern", description="React, Tailwind, GLSL shaders, Figma"), PROFILE)
        be, *_ = scoring.score(job(title="Backend Intern", description="Python, Go, SQL, Docker, Kubernetes"), PROFILE)
        self.assertGreater(fe, 75)
        self.assertLess(be, 30)
        self.assertIn("React", matched)
        self.assertIn("Figma", missing)

    def test_ai_screening(self):
        reasons = flags.ai_screening(job(description="You'll complete a HackerRank test and a HireVue interview."), COMPANY, PROFILE)
        self.assertEqual(len(reasons), 2)
        marked = Company("Acme", "", "greenhouse", "acme", website="https://acme.com", has_ai_screening="Yes")
        self.assertTrue(flags.ai_screening(job(), marked, PROFILE))

    def test_red_flags(self):
        j = job(description="Pay a registration fee of Rs 500. Contact us on WhatsApp or mail hr.jobs@gmail.com")
        red = flags.red_flags(j, COMPANY, "Yes", 30, PROFILE)
        self.assertEqual(len(red), 4)
        self.assertEqual(flags.red_flags(job(), COMPANY, "Yes", 4000, PROFILE), [])

    def test_registrable_domain(self):
        self.assertEqual(flags.registrable_domain("https://www.careers.example.co.in/jobs"), "example.co.in")
        self.assertEqual(flags.registrable_domain("figma.com"), "figma.com")


class Evaluate(unittest.TestCase):
    def test_kept_job_has_drafts(self):
        ev = evaluate(job(), COMPANY, PROFILE, no_domain)
        self.assertTrue(ev.kept)
        self.assertEqual(ev.initial_status, "To Review")
        self.assertIn("Acme", ev.note)
        self.assertIn("Weekly availability", ev.answers)

    def test_red_flag_job_is_skipped_status(self):
        ev = evaluate(job(description="React role. Pay a training fee first."), COMPANY, PROFILE, no_domain)
        self.assertEqual(ev.initial_status, "Skipped (red flag)")

    def test_rejections(self):
        self.assertEqual(evaluate(job(location="New York, NY"), COMPANY, PROFILE, no_domain).reason, "not_remote")
        self.assertEqual(evaluate(job(location="Remote - US"), COMPANY, PROFILE, no_domain).reason, "region_restricted")
        self.assertEqual(evaluate(job(title="Finance Intern", description="Excel"), COMPANY, PROFILE, no_domain).reason, "low_score")


class Feeds(unittest.TestCase):
    def feed_job(self, **kw):
        base = dict(source="remotive", company="Unknown Startup", title="Frontend Intern", workplace="remote",
                    url="https://remotive.com/remote-jobs/software-dev/frontend-intern-1", via="Remotive",
                    location_is_eligibility=True, location="Worldwide")
        base.update(kw)
        return job(**base)

    def test_feed_regions(self):
        ok = {"Worldwide": "Worldwide", "Anywhere in the World": "Anywhere", "Northern America, LATAM, Europe, APAC": "APAC",
              "USA, UK, India, Australia": "India"}
        for loc, label in ok.items():
            self.assertEqual(filters.region(self.feed_job(location=loc), PROFILE).label, label, loc)
        for loc in ["USA, Canada, USA timezones", "Helsinki", "North America Only", "France, Japan, Turkey"]:
            self.assertTrue(filters.region(self.feed_job(location=loc), PROFILE).reject_detail, loc)

    def test_unvetted_company_is_a_warning_not_a_red_flag(self):
        from bot.pipeline import resolve_company
        j = self.feed_job()
        company = resolve_company(j, {})
        ev = evaluate(j, company, PROFILE, no_domain)
        self.assertTrue(ev.kept)
        self.assertEqual(ev.record["red_flags"], [])
        self.assertTrue(any("whitelist" in w for w in ev.record["warnings"]))
        self.assertEqual(ev.initial_status, "To Review")

    def test_feed_job_from_whitelisted_company_uses_its_record(self):
        from bot.pipeline import resolve_company
        marked = Company("Unknown Startup", "", "greenhouse", "us", website="https://us.com", has_ai_screening="Yes")
        company = resolve_company(self.feed_job(), {"unknown startup": marked})
        self.assertIs(company, marked)
        ev = evaluate(self.feed_job(), company, PROFILE, no_domain)
        self.assertTrue(ev.record["ai_screening"])
        self.assertFalse(any("whitelist" in w for w in ev.record["warnings"]))

    def test_junior_roles_only_when_enabled(self):
        import copy
        from bot.fetchers.base import is_entry_level
        self.assertTrue(is_entry_level("Junior Frontend Developer"))
        self.assertFalse(is_entry_level("Senior Frontend Developer"))
        j = self.feed_job(title="Junior React Developer")
        self.assertEqual(evaluate(j, Company("x", "", "remotive", ""), PROFILE, no_domain).reason, "not_internship")
        p = copy.deepcopy(PROFILE)
        p["filters"]["include_junior_roles"] = True
        ev = evaluate(j, Company("x", "", "remotive", ""), p, no_domain)
        self.assertTrue(ev.kept)
        self.assertIn("Junior / entry-level role, not an internship", ev.record["warnings"])

    def test_wwr_rss_parsing(self):
        from bot.fetchers.weworkremotely import WeWorkRemotelyFetcher
        from bot.http_client import FetchResult
        rss = b"""<?xml version="1.0"?><rss><channel><item>
          <title>Acme Labs: Design Engineering Intern</title><region>Anywhere in the World</region><country></country>
          <skills>React, Figma</skills><category>Design</category><type>Internship</type>
          <description>&lt;p&gt;&lt;strong&gt;URL:&lt;/strong&gt; &lt;a href="https://acmelabs.dev"&gt;acmelabs.dev&lt;/a&gt;&lt;/p&gt;</description>
          <pubDate>Fri, 02 Oct 2026 17:39:45 +0000</pubDate>
          <link>https://weworkremotely.com/remote-jobs/acme-labs-design-engineering-intern</link></item>
          <item><title>Other: Senior Engineer</title><link>https://weworkremotely.com/remote-jobs/x</link></item>
        </channel></rss>"""

        class FakeHttp:
            def get_text(self, url, params=None):
                return FetchResult(200, rss)

        feed = Company("We Work Remotely", "Job feed", "weworkremotely", "remote-design-jobs")
        out = WeWorkRemotelyFetcher().fetch(feed, FakeHttp(), None, lambda m: None)
        self.assertEqual(out.postings, 2)
        self.assertEqual(len(out.jobs), 1)
        j = out.jobs[0]
        self.assertEqual((j.company, j.title, j.location, j.company_url), ("Acme Labs", "Design Engineering Intern",
                                                                           "Anywhere in the World", "https://acmelabs.dev"))
        self.assertIn("Skills: React, Figma", j.description)


class HackerNewsAndManual(unittest.TestCase):
    def test_hn_comment_parsing(self):
        from bot.fetchers.hackernews import parse_comment
        c = {"id": 1, "created_at": "2026-10-02T10:00:00Z",
             "text": 'Acme | Frontend Engineer, Design Interns | REMOTE (Worldwide) | $2k/mo stipend | '
                     '<a href="https://acme.dev/jobs">https://acme.dev/jobs</a>'
                     '<p>We build with React and Three.js. Internship is 25 hours per week.</p>'}
        j = parse_comment(c)
        self.assertEqual(j.company, "Acme")
        self.assertIn("Design Interns", j.title)
        self.assertNotIn("http", j.title)
        self.assertEqual(j.workplace, "remote")
        self.assertEqual(j.employment_type, "Internship")
        self.assertEqual(j.company_url, "https://acme.dev/jobs")
        self.assertEqual(j.url, "https://news.ycombinator.com/item?id=1")
        ev = evaluate(j, Company("Acme", "", "hackernews", ""), PROFILE, no_domain)
        self.assertTrue(ev.kept, ev.reason + ev.detail)
        self.assertEqual(ev.record["region"], "Worldwide")

    def test_hn_header_without_paragraph(self):
        from bot.fetchers.hackernews import parse_comment
        j = parse_comment({"id": 2, "text": "Quant Co | NYC, Hybrid\nWe are a trading firm with interns."})
        self.assertEqual(j.location, "NYC, Hybrid")
        self.assertEqual(j.workplace, "hybrid")

    def test_jsonld_import(self):
        from bot.jsonld import find_jobposting as _find_jobposting, fields_from_jobposting
        page = """<html><script type="application/ld+json">{"@context":"https://schema.org","@type":"JobPosting",
          "title":"Front End Development - Internship (WFH)","employmentType":["INTERN","PART_TIME"],
          "hiringOrganization":{"@type":"Organization","name":"Pixel Co","sameAs":"https://pixel.co"},
          "jobLocationType":"TELECOMMUTE","applicantLocationRequirements":{"@type":"Country","name":"IN"},
          "baseSalary":{"@type":"MonetaryAmount","currency":"INR","value":{"minValue":5000,"maxValue":10000,"unitText":"MONTH"}},
          "description":"&lt;p&gt;Build UIs in React and Tailwind.&lt;/p&gt;","datePosted":"2026-10-07"}</script></html>"""
        f = fields_from_jobposting(_find_jobposting(page), "https://internshala.com/internship/detail/x")
        self.assertEqual(f["role"], "Front End Development - Internship (WFH)")
        self.assertEqual(f["company"], "Pixel Co")
        self.assertEqual(f["location"], "Remote (India)")
        self.assertEqual(f["workplace"], "remote")
        self.assertEqual(f["compensation"], "INR 5,000-10,000 / month")
        self.assertIn("React", f["description"])

    def test_manual_add_keeps_but_explains_filtered_roles(self):
        import tempfile
        from bot.importer import job_from_fields
        from bot.pipeline import add_manual
        with tempfile.TemporaryDirectory() as tmp:
            conn = db.connect(Path(tmp) / "m.db")
            uid = db.ensure_local_user(conn)["id"]
            onsite = job_from_fields({"role": "Web Intern", "company": "Local Studio", "url": "https://example.com/a",
                                      "workplace": "onsite", "location": "Pune", "description": "React work",
                                      "platform": "Internshala"})
            job_id, created, note = add_manual(conn, uid, onsite, PROFILE, [], None, "Applied", "2026-10-10")
            row = db.get_job(conn, uid, job_id)
            self.assertTrue(created)
            self.assertIn("would have skipped", note)
            self.assertEqual(row["status"], "Applied")
            self.assertEqual(row["platform"], "Internshala")
            self.assertEqual(row["followup_date"], "2026-10-17")
            again = add_manual(conn, uid, onsite, PROFILE, [], None)
            self.assertEqual(again[:2], (job_id, False))
            conn.close()

    def test_onsite_ok_locations(self):
        import copy
        p = copy.deepcopy(PROFILE)
        j = job(location="Pune, Maharashtra", workplace="hybrid", description="React role, hybrid, 2 days in office")
        self.assertEqual(evaluate(j, COMPANY, p, no_domain).reason, "not_remote")
        p["filters"]["onsite_ok_locations"] = ["Pune"]
        ev = evaluate(j, COMPANY, p, no_domain)
        self.assertTrue(ev.kept, ev.reason + ev.detail)
        self.assertTrue(any("On-site OK" in w for w in ev.record["warnings"]))


class NewSources(unittest.TestCase):
    class FakeHttp:
        def __init__(self, text=None, data=None):
            self.text, self.data = text, data

        def get_text(self, url, params=None, accept=""):
            from bot.http_client import FetchResult
            return FetchResult(200, self.text.encode("utf-8"))

        def get_json(self, url, params=None):
            from bot.http_client import FetchResult
            return FetchResult(200, self.data)

    def feed(self, ats, **options):
        return Company(ats, "Job feed", ats, "x", options=options)

    def test_internshala_detail(self):
        from bot.fetchers.internshala import _job_from_detail
        page = """<script type="application/ld+json">{"@type":"JobPosting","title":"Front End Development - Internship (WFH)",
          "employmentType":"INTERN","jobLocationType":"TELECOMMUTE","applicantLocationRequirements":{"@type":"Country","name":"IN"},
          "hiringOrganization":{"@type":"Organization","name":"Pixel Co"},"description":"React and Tailwind work"}</script>
          <div>Stipend</div><span> ₹ 8,000 /month</span>"""
        j = _job_from_detail("https://internshala.com/internship/detail/x1", page)
        self.assertEqual((j.company, j.location, j.workplace), ("Pixel Co", "Remote (India)", "remote"))
        self.assertEqual(j.compensation, "₹ 8,000 /month")
        ev = evaluate(j, Company("Pixel Co", "", "internshala", ""), PROFILE, no_domain)
        self.assertTrue(ev.kept)
        self.assertEqual(ev.record["paid"], "Yes")
        unpaid = _job_from_detail("https://internshala.com/internship/detail/x2", page.replace("₹ 8,000 /month", "Unpaid"))
        self.assertEqual(filters.paid(unpaid), "No")

    def test_unstop_mapping(self):
        from bot.fetchers.unstop import UnstopFetcher
        data = {"data": {"data": [{"id": 1, "title": "Frontend Developer Internship", "public_url": "internships/fe-1",
                "region": "online", "organisation": {"name": "Acme"}, "details": "<p>React UI work</p>",
                "jobDetail": {"type": "wfh", "paid_unpaid": "paid", "min_salary": 5000, "max_salary": 10000,
                              "pay_in": "monthly", "timing": "part_time"},
                "required_skills": [{"skill_name": "React.js"}], "locations": []}]}}
        out = UnstopFetcher().fetch(self.feed("unstop", search_terms=["react"]), self.FakeHttp(data=data), None, lambda m: None)
        j = out.jobs[0]
        self.assertEqual((j.url, j.location, j.compensation), ("https://unstop.com/internships/fe-1", "Remote (India)",
                                                               "INR 5,000-10,000 / monthly"))

    def test_markdown_tables(self):
        from bot.fetchers.github_lists import SpeedyApplyFetcher
        md = """| Company | Position | Location | Posting | Age |
|---|---|---|---|---|
| <a href="https://acme.dev"><strong>Acme</strong></a> | Software Engineer Intern | Remote | <a href="https://jobs.acme.dev/1"><img/></a> | 3d |
| <a href="https://us.co"><strong>US Co</strong></a> | SWE Intern | Austin, TX | <a href="https://us.co/2"><img/></a> | 1d |"""
        out = SpeedyApplyFetcher().fetch(self.feed("speedyapply", files=["o/r/main/INTERN_INTL.md"]), self.FakeHttp(text=md), None, lambda m: None)
        self.assertEqual(out.postings, 2)
        self.assertEqual(len(out.jobs), 1)
        j = out.jobs[0]
        self.assertEqual((j.company, j.url, j.company_url, j.workplace), ("Acme", "https://jobs.acme.dev/1", "https://acme.dev", "remote"))

    def test_simplify_prefilter(self):
        from bot.fetchers.github_lists import SimplifyFetcher
        data = [{"active": True, "is_visible": True, "company_name": "A", "title": "SWE Intern", "url": "https://a/1", "locations": ["Remote in USA"]},
                {"active": True, "is_visible": True, "company_name": "B", "title": "SWE Intern", "url": "https://b/1", "locations": ["Seattle, WA"]},
                {"active": False, "company_name": "C", "title": "SWE Intern", "url": "https://c/1", "locations": ["Remote"]}]
        out = SimplifyFetcher().fetch(self.feed("simplify", repos=["SimplifyJobs/Summer2027-Internships"]), self.FakeHttp(data=data), None, lambda m: None)
        self.assertEqual((out.postings, len(out.jobs)), (2, 1))
        ev = evaluate(out.jobs[0], Company("A", "", "simplify", ""), PROFILE, no_domain)
        self.assertEqual(ev.reason, "region_restricted")

    def test_ycombinator_page(self):
        import html as h, json
        from bot.fetchers.ycombinator import YCombinatorFetcher
        props = {"props": {"jobPostings": [
            {"id": 1, "title": "Software Engineering Intern", "url": "/companies/x/jobs/1", "location": "Bengaluru, KA, IN / Remote (IN)",
             "type": "Internship", "companyName": "X", "companyBatchName": "W26", "salaryRange": "₹25K - ₹75K INR / monthly"},
            {"id": 2, "title": "Staff Engineer", "url": "/companies/y/jobs/2", "location": "Remote", "type": "Full-time", "companyName": "Y"}]}}
        page = f'<div data-page="{h.escape(json.dumps(props))}"></div>'
        out = YCombinatorFetcher().fetch(self.feed("ycombinator", pages=["software-engineer/remote"]), self.FakeHttp(text=page), None, lambda m: None)
        self.assertEqual(len(out.jobs), 1)
        j = out.jobs[0]
        self.assertEqual((j.url, j.workplace), ("https://www.ycombinator.com/companies/x/jobs/1", "remote"))
        ev = evaluate(j, Company("X", "", "ycombinator", ""), PROFILE, no_domain)
        self.assertTrue(ev.kept, ev.reason + ev.detail)
        self.assertEqual(ev.record["region"], "India")

    def test_getro_board(self):
        import json
        from bot.fetchers.getro import GetroFetcher
        nd = {"props": {"pageProps": {"initialState": {"jobs": {"found": [
            {"id": 1, "title": "Frontend Intern", "workMode": "remote", "locations": ["India"], "seniority": "internship",
             "url": "https://jobs.lever.co/acme/1", "organization": {"name": "Acme", "domain": "acme.dev"}, "createdAt": 1791545743},
            {"id": 2, "title": "Firmware Intern", "workMode": "on_site", "locations": ["Austin, TX, USA"], "seniority": "internship",
             "url": "https://x/2", "organization": {"name": "Z"}}]}}}}}
        page = f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(nd)}</script>'
        out = GetroFetcher().fetch(self.feed("vcboards", boards=[{"name": "Techstars", "url": "https://jobs.techstars.com"}]),
                                   self.FakeHttp(text=page), None, lambda m: None)
        self.assertEqual(len(out.jobs), 1)
        self.assertEqual((out.jobs[0].via, out.jobs[0].company_url), ("Techstars portfolio", "https://acme.dev"))

    def test_off_topic_titles_score_low(self):
        for t in ["Law Intern", "Social Media Marketing Intern", "Growth Intern"]:
            self.assertLess(scoring.score(job(title=t, description=""), PROFILE)[0], 20, t)
        self.assertGreater(scoring.score(job(title="Front End Development - Internship", description=""), PROFILE)[0], 50)


class TrackerPreservesEdits(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.db")
        self.uid = db.ensure_local_user(self.conn)["id"]

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_rerun_keeps_status_and_edited_drafts(self):
        ev = evaluate(job(), COMPANY, PROFILE, no_domain)
        key = normalize_url(job().url)
        state, job_id = db.upsert_job(self.conn, self.uid, 1, key, ev.record, machine_hash(ev.record), ev.initial_status, ev.note, ev.answers)
        self.assertEqual(state, "new")
        db.update_job_user_fields(self.conn, self.uid, job_id, {"status": "Applied", "note": "my own note", "user_notes": "hi"})

        changed = evaluate(job(description=job().description + " Also Next.js."), COMPANY, PROFILE, no_domain)
        state, _ = db.upsert_job(self.conn, self.uid, 2, key, changed.record, machine_hash(changed.record),
                                 changed.initial_status, changed.note, changed.answers)
        self.assertEqual(state, "updated")
        row = db.get_job(self.conn, self.uid, job_id)
        self.assertEqual(row["status"], "Applied")
        self.assertEqual(row["note"], "my own note")
        self.assertEqual(row["user_notes"], "hi")
        self.assertTrue(row["applied_date"])
        self.assertTrue(row["followup_date"])
        self.assertIn("Next.js", row["matched_skills"])

    def test_unchanged_on_same_content(self):
        ev = evaluate(job(), COMPANY, PROFILE, no_domain)
        key = normalize_url(job().url)
        db.upsert_job(self.conn, self.uid, 1, key, ev.record, machine_hash(ev.record), "To Review", ev.note, ev.answers)
        state, _ = db.upsert_job(self.conn, self.uid, 2, key, ev.record, machine_hash(ev.record), "To Review", ev.note, ev.answers)
        self.assertEqual(state, "unchanged")

    def test_run_lock(self):
        db.start_run(self.conn, "cli", {})
        with self.assertRaises(db.RunInProgress):
            db.start_run(self.conn, "cli", {})


class MultiUser(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "mu.db")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_users_only_see_their_own_jobs(self):
        a, created = db.get_or_create_user(self.conn, "A@Example.com", "A")
        self.assertTrue(created)
        self.assertEqual(a["email"], "a@example.com")
        b, _ = db.get_or_create_user(self.conn, "b@example.com", "B")
        ev = evaluate(job(), COMPANY, PROFILE, no_domain)
        key = normalize_url(job().url)
        _, ja = db.upsert_job(self.conn, a["id"], None, key, ev.record, machine_hash(ev.record), "To Review", ev.note, ev.answers)
        _, jb = db.upsert_job(self.conn, b["id"], None, key, ev.record, machine_hash(ev.record), "To Review", ev.note, ev.answers)
        self.assertNotEqual(ja, jb)
        db.update_job_user_fields(self.conn, a["id"], ja, {"status": "Applied"})
        self.assertEqual(db.get_job(self.conn, b["id"], jb)["status"], "To Review")
        self.assertIsNone(db.get_job(self.conn, b["id"], ja))
        with self.assertRaises(KeyError):
            db.update_job_user_fields(self.conn, b["id"], ja, {"status": "Offer"})
        self.assertEqual([j["id"] for j in db.list_jobs(self.conn, a["id"])], [ja])
        db.delete_user(self.conn, a["id"])
        self.assertIsNone(db.get_user(self.conn, a["id"]))
        self.assertEqual(len(db.list_jobs(self.conn, b["id"])), 1)

    def test_admin_flag_follows_sign_in(self):
        u, _ = db.get_or_create_user(self.conn, "x@example.com", is_admin=True)
        self.assertTrue(u["is_admin"])
        u, created = db.get_or_create_user(self.conn, "x@example.com", is_admin=False)
        self.assertFalse(created)
        self.assertFalse(u["is_admin"])

    def test_login_tokens_are_single_use(self):
        token = db.create_login_token(self.conn, "Me@Example.com", "1.2.3.4")
        self.assertEqual(db.recent_login_requests(self.conn, email="me@example.com"), 1)
        self.assertEqual(db.consume_login_token(self.conn, token), "me@example.com")
        self.assertIsNone(db.consume_login_token(self.conn, token))
        self.assertIsNone(db.consume_login_token(self.conn, "not-a-token"))

    def test_run_counts_and_events_are_per_user(self):
        a, _ = db.get_or_create_user(self.conn, "a@example.com")
        b, _ = db.get_or_create_user(self.conn, "b@example.com")
        run_id = db.start_run(self.conn, "cli", {})
        db.add_event(self.conn, run_id, "info", "fetched", "shared")
        db.add_event(self.conn, run_id, "success", "added", "for a", user_id=a["id"])
        db.save_user_counts(self.conn, run_id, a["id"], {"new": 3})
        self.conn.commit()
        self.assertEqual([e["message"] for e in db.run_events(self.conn, run_id, user_id=b["id"])], ["shared"])
        self.assertEqual(len(db.run_events(self.conn, run_id, user_id=a["id"])), 2)
        self.assertEqual(db.get_run(self.conn, run_id, a["id"])["counts"].get("new"), 3)
        self.assertIsNone(db.get_run(self.conn, run_id, b["id"])["counts"].get("new"))

    def test_new_user_profile_has_no_personal_data(self):
        from bot import workspace
        p = workspace.new_user_profile("new@example.com", "New Person")
        self.assertEqual(p["contact"]["email"], "new@example.com")
        self.assertEqual(p["skills"], [])
        self.assertNotIn("feeds", p)
        self.assertEqual(config.validate_profile(p, require_skills=False), [])

    def test_pg_sql_translation(self):
        from bot.store import _translate
        self.assertEqual(_translate("SELECT * FROM t WHERE a=? AND b LIKE '%x?%'", False),
                         "SELECT * FROM t WHERE a=%s AND b LIKE '%%x?%%'")
        self.assertEqual(_translate("INSERT INTO p(k) VALUES (:key) ON CONFLICT(k) DO UPDATE SET k=excluded.k", True),
                         "INSERT INTO p(k) VALUES (%(key)s) ON CONFLICT(k) DO UPDATE SET k=excluded.k")


class TextUtils(unittest.TestCase):
    def test_normalize_url(self):
        self.assertEqual(normalize_url("https://www.Example.com/jobs/1/?utm_source=x&gh_jid=5#apply"),
                         "https://example.com/jobs/1?gh_jid=5")

    def test_html_to_text(self):
        self.assertEqual(html_to_text("&lt;p&gt;Hi&lt;/p&gt;&lt;ul&gt;&lt;li&gt;A&lt;/li&gt;&lt;/ul&gt;", unescape_first=True), "Hi\n\n- A")


if __name__ == "__main__":
    unittest.main()
