# Internship Assistant

A semi-automated internship finder for LEaf (Dhruv Malviya). Every day it reads public job boards of
companies you trust, keeps the internships that are **remote, open to someone in India, and a fit for your
stack**, drafts a tailored note and prefilled answers for each, and tracks everything on a local website.

**Supports both semi-automated and intelligent auto-apply modes:**
- **Auto-apply with Gemini AI**: The bot can submit automatically to companies without login requirements or human verification, answering form questions with Gemini AI. If a site requires account login, a separate Google Doc / Form, or human verification (CAPTCHA / Cloudflare Turnstile), it safely halts and marks the application as **User Review Pending**.
- **Browser apply for login-gated sites**: A separate CLI can attach to a Chromium browser session that you have already signed into (for example Internshala), fill known application fields, and submit only after the site visibly confirms success. It never logs in for you, solves a CAPTCHA, or marks a job Applied when confirmation is missing.
- **Manual apply**: You open the application form, paste the drafts or Gemini-generated answers, and submit yourself.

**Two ways to run it** (same code):

| | Local (this PC) | Deployed (multi-user website) |
|---|---|---|
| Sign-in | none | Google or emailed one-time link |
| Users | just you | anyone you allow; each has their own profile, tracker and drafts |
| Data | `data/state.db` (SQLite), `profile.json`, `companies.csv` | Postgres (Neon); profiles per user, shared sources set by admins |
| Daily run | Windows Task Scheduler | GitHub Actions, 9:00 IST |
| Setup | below | **[DEPLOY.md](DEPLOY.md)** (free: Render + Neon + GitHub) |

```
python run.py          # fetch → filter → score → flag → draft → save
python serve.py --open # open the tracker website at http://127.0.0.1:8765
```

---

## Setup (Windows, once)

Needs Python 3.10+ (tested on 3.13).

```powershell
cd "D:\work\Internship bot"
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt   # requests, flask, authlib, psycopg, waitress, playwright
```

Then fill in the blanks in **Settings** on the website (or `profile.json`): email, portfolio/GitHub/LinkedIn
links, project URLs, expected stipend. Blank fields show up as `[fill in]` / `[add link]` in drafts.

## Daily use

| What | How |
|---|---|
| Open the tracker | double-click `start_tracker.bat`, or `.venv\Scripts\python serve.py --open` |
| Run the bot now | **Run bot now** button on the website, or `.venv\Scripts\python run.py` |
| Watch what the bot is doing | **Bot activity** page: live log, per-company results, why each internship was skipped |
| See it with sample data | `.venv\Scripts\python serve.py --demo` (separate database, fictional companies) |
| Run daily automatically | `powershell -ExecutionPolicy Bypass -File scripts\schedule_windows.ps1 -Time 09:00` |
| Stop the daily run | `powershell -ExecutionPolicy Bypass -File scripts\schedule_windows.ps1 -Remove` |
| Export for Excel/Sheets | **Export CSV** on the Applications page (same columns as the original spreadsheet spec) |
| Add one you found yourself | **+ Add internship**: paste a link (Internshala, Unstop, HN, a company page…) or fill the form |
| See every platform | **Platforms** page: what's automatic, what to browse yourself, and how many you've tracked from each |

### Browser apply for login-required sites

For jobs held with a warning such as `Login required to apply: Internshala requires candidate account login and
session verification`, use the separate browser CLI after starting a Chromium-based browser with remote debugging
and signing in there:

```powershell
# Install the optional browser engine once
.venv\Scripts\python -m playwright install chromium

# List login-held jobs without opening a browser
python browser_apply.py --list

# Fill the form but do not submit (safe preview)
python browser_apply.py --pending --limit 1 --cdp-url http://127.0.0.1:9222

# Submit after reviewing the preview
python browser_apply.py --pending --limit 1 --cdp-url http://127.0.0.1:9222 --submit
```

Pass `--resume C:\path\to\resume.pdf` when the form has a resume upload. The CLI also supports
`--job-id 123` for one exact tracker row and `--user-data-dir C:\path\to\browser-profile` when it should launch
its own persistent Chrome profile instead of attaching over CDP. Unknown required fields, login pages, CAPTCHAs,
external forms, and unconfirmed submissions remain **User Review Pending**.

`run.py` options: `--company Figma` (repeatable; also `--company Remotive`), `--source lever` (any ATS, or
`remotive` / `weworkremotely`), `--auto-apply` (automatically submit to eligible boards without human verification using Gemini),
`--dry-run` (works on a throwaway copy of the database), `-v` (show every request).

### The workflow for one internship

1. It appears on **Applications** as *To Review* (or *Skipped (red flag)* if something looks off).
2. Click it. The drawer shows the match score, skills you have and are missing, flags, the tailored note and
   prefilled answers (editable, autosaved, with Copy buttons).
3. Set *Ready to Apply* when you've decided. Click **Open application form ↗**, paste, submit on their site.
4. Click **I applied** (or set status *Applied*). Applied date = today, follow-up date = +7 days.
5. When the follow-up date arrives the row's follow-up cell turns orange and the Dashboard counts it.

## What the website shows

- **Applications**: every tracked internship, color-coded by status (the same 10 statuses and colors as the
  spreadsheet spec), with search, status chips, score slider, flag filter and sort. Rows from boards that
  removed the posting show *Closed*.
- **+ Add internship**: paste a job link and the tracker reads that one page (robots.txt respected) and fills
  the form from its structured job data. This works well for Internshala, and Greenhouse/Lever/Ashby links go
  through their APIs. Sites that need a login or block automated requests (Wellfound, Handshake, LinkedIn)
  fall back to the form. Manually added roles get the same score, flags, drafts and follow-ups. If your
  filters would have skipped one, it's still added, with a note saying why.
- **Platforms**: every platform from your list (`platforms.json`, editable) with a search link, how it's
  covered (*Automatic*, *Paste link*, *Fill form*, *Program*), and how many jobs you've tracked from it.
- **Dashboard**: totals (found, applied, interviews, offers, rejections, follow-ups due), this week's
  applications against your 10–15 goal, applications per week for 12 weeks, counts by status, and the skills
  postings ask for that you don't list.
- **Bot activity**: each run with live progress, counts, skip reasons, per-company results and a full event
  log. The **Access issues** section separates scraper login failures from applications the applier paused
  because the destination site requires login. **Skipped listings** shows every internship a filter rejected and
  the exact text that triggered it; **Track anyway** pulls one into your tracker if the filter was wrong.
- **Companies**: the job feeds' status, then your whitelist (`companies.csv`), editable, with a **Test**
  button to check a board token and the last fetch result per company. Set *AI screening = Yes* to flag every
  role from that company (including its roles found via feeds).
- **Settings**: `profile.json` as a form (or raw JSON). Changes apply on the next run.

## How it decides

**Sources** (all automatic; switch each on/off and set its keywords in **Settings → Job feeds**):

| Source | How it's read | Notes |
|---|---|---|
| Company boards (`companies.csv`) | Greenhouse, Lever, Ashby, Workable public APIs | Your whitelist |
| Internshala | Work-from-home listing pages per category; each internship's detail page once (JobPosting data + stipend) | Up to 60 new detail pages per run; the rest next run |
| Unstop | Public search API, one search per keyword | |
| Y Combinator jobs | Data embedded in ycombinator.com/jobs/role/... pages | Same YC startups as Work at a Startup, no login |
| VC portfolio boards | Getro-hosted boards (Techstars, Antler; add more) | Top 20 matches per board and query |
| Peerlist | Public jobs API | |
| Himalayas | Public search API (worldwide + India) | |
| Remote OK | Public API | Credited + linked back, per its terms |
| Remotive | Public API | Credited + linked back; at most every 6 h, per its terms |
| We Work Remotely | Category RSS feeds | |
| Hacker News "Who is hiring?" | HN Search API (hn.algolia.com) | Posts mentioning interns |
| SimplifyJobs (GitHub) | The repo's `.github/scripts/listings.json` | Only remote / India-eligible listings are evaluated |
| speedyapply (GitHub) | `INTERN_INTL.md` table | International internships incl. India |
| GSoC, LFX Mentorship | GSoC timeline API; CNCF mentoring repo | Program dates shown on the Platforms page |

Every source is read the same polite way: robots.txt respected, 1 request/second per site, a minimum gap
between fetches of the same feed, no logins, and nothing that gets around bot checks or CSRF protection.
That's why Wellfound, Otta, Handshake, WayUp, X, Discord/Slack, Instahyre (no working internship filter),
Cuvette (unreachable) and the Consider-hosted VC boards (Peak XV, Lightspeed, a16z) aren't automatic. The
Platforms page lists each with the reason; **+ Add internship** covers them.

A feed job from a company on your whitelist uses that company's record (website, AI-screening setting).
Otherwise it gets a "check the company" warning (not a red flag), and the domain-age check runs when a
company URL is known. Where a feed's location means "where candidates must live", anything other than
worldwide / India / APAC is skipped. **Include junior roles** (off by default) also takes junior /
entry-level roles from feeds.

**Filters**, in order. Each rejection is logged with its reason:

1. *Internship*: title or employment type says intern / internship / co-op / apprentice / trainee.
2. *Remote*: the ATS `workplaceType` / `isRemote` / `telecommuting` field when present, otherwise the location
   text, otherwise phrases like "fully remote" in the description. On-site and hybrid are skipped, unless
   the location matches **Settings → Filters → On-site OK locations** (e.g. `Pune`).
3. *Eligibility phrases*: anything in `filters.reject_phrases` (e.g. "authorized to work in the United
   States", "EU only", "relocation required") or `location_reject_terms` in the location.
4. *Region*: India / APAC / Worldwide / Anywhere pass. A location or country field naming somewhere else
   ("Remote - US", "Remote (EMEA)", "London") is skipped. Plain "Remote" passes as *Unspecified* with a
   warning to check eligibility.
5. *Score*: below `filters.min_score` (default 20) is skipped.

**Labels** (kept, not rejected): hours (*In range*, *Under target*, *Slightly over*, *Hours unknown*), paid
(*Yes* / *No* / *Unknown*; unpaid is flagged for manual review), *Night shift OK* when a posting mentions US
hours.

**Score (0–100)** = 60% skill fit + 40% title relevance. Skill fit = weight of your skills the posting
mentions ÷ (that + 1.5 per known skill it asks for that you don't have). Weights and aliases live in
Settings → Skills. `scoring.llm_rescore` is the hook for optional LLM scoring later; it does nothing yet.

**AI screening likely**: mentions of HireVue, one-way/async video interviews, AI screening, Karat,
Codility, HackerRank, CodeSignal… (Settings → AI screening terms), a Greenhouse AI-use disclaimer on the
posting, or the company marked *Yes* on the Companies page. Flagged, never rejected.

**Red flags** → status *Skipped (red flag)*: asks for a fee or deposit, WhatsApp/Telegram contact,
applications to a personal Gmail/Yahoo/etc. address, no company website on file, company domain younger
than 180 days (checked via RDAP, cached 30 days), unpaid with no interview mentioned.

## Your edits are never overwritten

SQLite (`data/state.db`) is the single source of truth; the website edits it directly. The bot only
writes machine fields (score, flags, location, description…). It never touches status, applied/follow-up
dates, contact, or notes, and once you edit a note or answers block it stops regenerating it (use
**Regenerate from profile** in the drawer to opt back in). De-duplication is by normalized job URL (tracking
parameters stripped), plus same company + same title across boards.

## Politeness and speed

- One `User-Agent` (Settings → Advanced), at most 1 request/sec per host (or the host's `Crawl-delay`),
  retries with backoff on 429/5xx honoring `Retry-After`.
- `robots.txt` is checked per host following RFC 9309 (4xx = no rules; unreachable = skip that host).
- Companies on different hosts are fetched in parallel (6 workers); same-host requests still queue.
- Greenhouse and Internshala detail pages are fetched only for internships, and only once (Greenhouse
  again when the posting's `updated_at` changes). A full run of every source takes about 1–2 minutes.

## A realistic expectation

Fully remote internships open to India are rare on company ATS boards. On the first real run, the 20
starter companies had 1,288 postings and 20 internships, and every one was on-site or hybrid (mostly SF/NYC
or Bengaluru offices). With all sources on, the first full run scanned 6,993 postings, found 308
internships and added 88, of which 48 were paid, remote and To Review (mostly Internshala and Unstop,
₹5,000–25,000/month). Unpaid volunteer listings, common on Internshala, are flagged and skipped. The fastest way to improve results is to
add more remote-first companies on the Companies page (use **Test** to check each board), and to look at
**Skipped listings** after a few runs to see whether a phrase is too strict.

## Files

```
run.py                  CLI entry point (also what the scheduler / GitHub Actions run)
serve.py                starts the tracker website locally (127.0.0.1 only, no sign-in)
DEPLOY.md               deploying the multi-user version (Render + Neon + GitHub Actions)
render.yaml             Render blueprint;  .github/workflows/daily-run.yml  the daily cloud run
profile.example.json    template profile (no personal data); profile.json (yours) is git-ignored
start_tracker.bat       double-click launcher for the website
profile.json            your profile, skills, projects, filters (edit here or in Settings)
companies.csv           whitelist: name, category, ats, board_token, website, careers_url, has_ai_screening, enabled
platforms.json          the Platforms page list (edit to add your own)
bot/
  config.py             loads/saves profile.json and companies.csv
  http_client.py        rate limiting, robots.txt, retries
  fetchers/             greenhouse.py, lever.py, ashby.py, workable.py (company boards); internshala.py,
                        unstop.py, ycombinator.py, getro.py, peerlist.py, himalayas.py, remoteok.py,
                        remotive.py, weworkremotely.py, hackernews.py, github_lists.py (feeds) → one Job shape
  programs.py           GSoC / LFX date watcher
  jsonld.py             schema.org JobPosting parsing (Internshala + link importer)
  importer.py           + Add internship: reads one pasted job page (JSON-LD JobPosting / ATS APIs / HN)
  filters.py            internship / remote / region / phrases / hours / pay
  scoring.py            0-100 score, matched + missing skills
  flags.py              AI screening, red flags, night shift, RDAP domain age
  notes.py              tailored note + prefilled answers
  pipeline.py           one run: parallel fetch, evaluate, store, log events
  browser_apply.py      optional logged-in-browser applier for login-gated application sites
  db.py                 schema and queries: accounts, each user's jobs, shared bot state
  store.py              one connection type for SQLite (local) and Postgres (DATABASE_URL)
  workspace.py          where settings live per mode: per-user profiles, admin-managed shared sources
  mailer.py             SMTP sign-in emails
  demo.py               sample data for --demo
web/app.py              Flask app: JSON API + static files, scoped to the signed-in user
web/auth.py             Google sign-in + email links (cloud mode)
web/wsgi.py             gunicorn entry point for Render
web/static/             index.html, login.html, app.css, app.js, login.js (no frameworks, no CDN)
browser_apply.py        separate CLI for the logged-in browser applier
scripts/migrate_to_cloud.py   copies your local tracker into your online account
scripts/                schedule_windows.ps1, cron.example
tests/                  python -m unittest discover tests   (test_postgres.py needs TEST_DATABASE_URL)
data/                   state.db (yours), demo.db (sample); safe to back up by copying
logs/                   run-YYYY-MM-DD.log, kept 30 days
```

## Troubleshooting

- **"Run #N is already in progress"**: a run is going (see Bot activity). A run that crashes is marked
  *abandoned* automatically after 90 seconds without a heartbeat.
- **A company shows "board token not found" or "0 postings"**: open its careers page, copy the token from
  the job-board URL, fix it on the Companies page and press **Test**.
- **Port in use**: `python serve.py --port 8766`.
- **Scheduled run didn't happen**: Task Scheduler runs it when you're logged in; with *StartWhenAvailable*
  it catches up after the PC wakes. Check `logs/` and the Bot activity page.
