# Deploying the multi-user tracker (free)

What you get: a website anyone can sign in to with **Google** or an **emailed one-time link**. Each person has
their own profile, skills, filters, tracked internships, drafts and history. The bot runs **every day at
9:00 IST** on GitHub Actions, fetches every source once, and matches it against each account separately.

| Piece | Service | Cost |
|---|---|---|
| Website | Render (free web service) | $0 (sleeps after 15 min idle; first visit takes ~30–60 s to wake) |
| Database | Neon Postgres (free) | $0 (0.5 GB; this app uses a few MB per user) |
| Daily bot run | GitHub Actions | $0 (private repo: 2,000 min/month free; a run takes ~2–4 min) |
| Sign-in emails | Gmail SMTP with an app password | $0 (Gmail allows ~500/day) |

Your local setup keeps working exactly as before (no sign-in, SQLite, your Windows scheduled task). The two
don't share data unless you copy it with step 7.

---

## 1. Put the code on GitHub (private repo)

The project is already a git repository with everything committed. Your personal `profile.json`, the
database, logs and backups are in `.gitignore` and are **not** in the repo.

1. On github.com → **New repository** → name it `internship-tracker` → **Private** → don't add a README.
2. In a terminal in `D:\work\Internship bot`:

```bash
git remote add origin https://github.com/YOUR-USERNAME/internship-tracker.git
```

```bash
git push -u origin main
```

## 2. Create the database (Neon)

1. Sign up at https://neon.tech → **Create project** → region **AWS Asia Pacific (Mumbai)**.
2. **Connection string** → copy it (looks like `postgresql://user:pass@ep-....aws.neon.tech/neondb?sslmode=require`).
   Pooled or direct both work. This is your `DATABASE_URL`; keep it secret.

The tables are created automatically on first start.

## 3. Google sign-in

1. https://console.cloud.google.com → create a project (e.g. "Internship Tracker").
2. **APIs & Services → OAuth consent screen** → External → app name, your email as support + developer
   contact → scopes: just the defaults (`openid`, `email`, `profile`) → save. Then **Publish app** so anyone
   can sign in (no Google review is needed for these basic scopes). While it's in "Testing", only test users you
   list can sign in.
3. **Credentials → Create credentials → OAuth client ID** → type **Web application** →
   **Authorized redirect URIs**: `https://YOUR-APP.onrender.com/auth/google/callback`
   (you'll know the exact URL after step 5; you can come back and add it).
4. Copy the **Client ID** and **Client secret**.

## 4. Email sign-in links (optional but recommended)

Using Gmail:
1. Your Google Account → **Security** → turn on **2-Step Verification**.
2. **Security → App passwords** → create one named "Internship Tracker" → copy the 16-character password.
3. You'll use: `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`, `SMTP_USER=you@gmail.com`,
   `SMTP_PASSWORD=<app password>`, `EMAIL_FROM=Internship Tracker <you@gmail.com>`.

Skip this step and the sign-in page shows only the Google button.

## 5. Deploy the website (Render)

1. https://render.com → sign up with GitHub → **New + → Blueprint** → pick your `internship-tracker` repo.
   Render reads `render.yaml` and creates the web service (free plan). Choose region **Singapore**.
2. Fill in the environment variables it asks for:

| Variable | Value |
|---|---|
| `DATABASE_URL` | from step 2 |
| `PUBLIC_URL` | `https://YOUR-APP.onrender.com` (shown at the top of the service page) |
| `ADMIN_EMAILS` | your email (admins edit the shared sources: companies + feeds, and can press Run bot now) |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | from step 3 |
| `SMTP_*`, `EMAIL_FROM` | from step 4 (or leave empty) |
| `ALLOWED_EMAILS` | optional: comma-separated list if only certain people may sign up; empty = anyone |

`SECRET_KEY` is generated for you. Click **Apply**. When it's live, open the URL: you should see the sign-in page.
If you set `PUBLIC_URL` after the first deploy, Render redeploys automatically.

## 6. The daily bot run (GitHub Actions)

1. GitHub repo → **Settings → Secrets and variables → Actions → New repository secret** →
   name `DATABASE_URL`, value from step 2.
2. **Actions** tab → enable workflows if asked → **Daily internship run → Run workflow** to test it now.
   It then runs every day at 9:00 IST (GitHub sometimes starts scheduled jobs a few minutes late).
3. Watch it on the website's **Bot activity** page; the run log is also attached to each workflow run.

### Pause notices for inactive accounts

The daily run skips accounts nobody has opened for 14 days and emails them once ("your daily search is
paused"); signing in switches it back on. For the email to go out from GitHub Actions, add these repository
secrets too (same values as on Render): `PUBLIC_URL`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`,
`EMAIL_FROM`. Without them the account is still paused, just without the email. To change the 14 days, add a
repository **variable** (not secret) `INACTIVE_DAYS`.

### "Run bot now"

Every signed-in user has **Run bot now**. It first matches them against everything fetched in the last 7 days
(instant, no fetching), then starts a fresh check of every source on the Render server. Regular users can start
a fresh check at most every 2 hours (`RUN_COOLDOWN_MINUTES` on Render); admins any time. Saving a profile also
matches against recent postings straight away, so new users see results without waiting for 9:00.

### Optional: run the "Run bot now" checks on GitHub instead of Render

1. GitHub → your avatar → **Settings → Developer settings → Fine-grained tokens → Generate** → only your
   `internship-tracker` repo → **Repository permissions → Actions: Read and write** → generate, copy.
2. On Render add `GITHUB_REPO=YOUR-USERNAME/internship-tracker` and `GITHUB_TOKEN=<that token>`.
   Fresh checks started from the website then run on GitHub Actions instead of the Render server.

## 7. Bring your existing tracker online

This copies your profile, all tracked internships (statuses, dates, notes, drafts, history) and the bot's
caches into your online account, and seeds the shared company list and feed settings. From
`D:\work\Internship bot` in PowerShell:

```bash
$env:DATABASE_URL = "postgresql://...your Neon string..."
```

```bash
.venv\Scripts\python scripts\migrate_to_cloud.py --email YOUR-EMAIL@gmail.com --admin
```

Then sign in on the website with that same email (Google or link). Safe to run again later; jobs already
online are left alone.

**Two daily runs?** If you now use the website, remove the local scheduled task so the bot doesn't run twice:

```bash
powershell -ExecutionPolicy Bypass -File scripts\schedule_windows.ps1 -Remove
```

---

## How accounts work

- **Everyone** gets their own profile (Settings), tracker, dashboard, skipped list and drafts. Nobody can see
  anyone else's data. A new account starts with sensible filters and no skills; the bot matches internships for
  an account once it has at least one skill.
- **Admins** (`ADMIN_EMAILS`) also edit the shared sources everyone's matches come from: the company
  whitelist (Companies page) and the job feeds (Settings → Shared sources), and can start extra runs.
- **Inactive accounts** (no visit for 14 days) are paused and emailed once; their data is kept and the search
  resumes when they sign in.
- **Delete my account** (bottom of Settings) permanently removes that person's data.
- Sessions last 30 days. Sign-in links work once and expire after 15 minutes; requests are rate-limited.

## Things to know

- **Cold starts:** the free Render service sleeps when nobody has used it for 15 minutes.
- **Some sites may treat GitHub's servers differently** from your home connection. After the first cloud run,
  check Bot activity; if a source shows errors there but works locally, switch it off in Settings → Shared
  sources, or keep using your local run for it.
- **Privacy:** you are storing other people's emails and profiles in your Neon database. Only share the site
  with people you're happy to be responsible for, or set `ALLOWED_EMAILS`.
- **Updating:** push to GitHub → Render redeploys automatically; the next Actions run uses the new code.
- **Testing against Postgres locally** (optional): see `tests/test_postgres.py`.
