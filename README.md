# 📡 Sales Radar

A self-updating daily account-news brief for enterprise sales. Every morning at
**07:30 SGT** it pulls the last 24h of news for your accounts, has Claude tag each
item by buying-cycle stage, and publishes a phone-friendly page you just open.

**How it flows:**

```
Google News RSS (free)  →  filter + dedupe (Python)  →  Claude (stage tags,
"what it means", openers)  →  static webpage on GitHub Pages (free)
        ▲                                                      ▲
        └── GitHub Actions runs this daily at 07:30 SGT ───────┘
```

**Cost:** hosting, scheduler, feeds — all free. The Claude step runs on your
existing Claude subscription's monthly Agent SDK credit (Path A below), so
normally **$0 on top of what you already pay**. A pay-as-you-go API key is the
fallback (Path B, ~US$1–3/month at this volume).

---

## Setup (once, ~15 minutes)

### 1 · Create the repo

1. Sign in / sign up at github.com.
2. Top-right **+** → **New repository** → name it `sales-radar` → visibility
   **Public** (required for free GitHub Pages — see Privacy below) → **Create**.
3. **Add file → Upload files** → drag in **everything inside** this folder
   (including the `.github` folder) → **Commit changes**.
   - If the `.github` folder didn't survive the drag-and-drop: **Add file →
     Create new file**, type `.github/workflows/daily.yml` as the filename
     (the slashes create the folders), paste the contents of that file, commit.

### 2 · Connect the brain — pick ONE path

**Path A (recommended — $0 extra, uses your Claude subscription):**

1. On your computer, install Node.js from nodejs.org (LTS version, default options).
2. Open Terminal (Mac) or PowerShell (Windows) and run:
   ```
   npm install -g @anthropic-ai/claude-code
   claude
   ```
   Log in with your Claude account when prompted, then quit (`/exit`).
3. Run:
   ```
   claude setup-token
   ```
   Approve in the browser, then **copy the long token it prints**.
4. In your Claude account, make sure you've **claimed your monthly Agent SDK
   credit** (one-time opt-in — see support.claude.com, "Use the Claude Agent SDK
   with your Claude plan").
5. In the GitHub repo: **Settings → Secrets and variables → Actions → New
   repository secret** → Name: `CLAUDE_CODE_OAUTH_TOKEN` → paste token → save.

**Path B (fallback — separate pay-as-you-go billing):**

1. Create an account at the Claude developer Console, add ~US$5 of credits,
   and create an API key.
2. Add it as a repo secret named `ANTHROPIC_API_KEY` (same menu as above).

> ⚠️ Use one path, not both. If `ANTHROPIC_API_KEY` is set it **overrides** the
> subscription token and bills the API account instead.
> 🔐 Secrets live server-side in GitHub — the key/token never appears in the
> page code. Never paste either into the HTML or commit them to the repo.

### 3 · Turn on the webpage

Repo **Settings → Pages** → under *Build and deployment*: Source = **Deploy from
a branch**, Branch = **main**, Folder = **/docs** → **Save**.
Your page URL appears at the top: `https://<your-username>.github.io/sales-radar/`
— bookmark it on your phone.

### 4 · First run

**Actions** tab → **Daily Sales Radar** → **Run workflow** → set
`window_hours` to `168` (a one-off 7-day catch-up) → **Run**. Takes ~2 minutes.
Refresh your page. From tomorrow it runs itself at 07:30 SGT with a 24h window.

---

## Daily use

- **Morning:** open the bookmark. Today's brief is already there.
- **Midday refresh:** Actions tab → Run workflow (leave 24h) — or tap
  **re-run ↗** in the page header, which takes you to the same button.
- **Past days:** archive links in the page footer (last 14 days kept).

## Editing your accounts

Everything lives in **`accounts.yaml`** — edit it right in the GitHub web UI
(open file → pencil icon → commit):

- **Promote / demote:** change `tier: B` to `tier: A` or back. Done.
- **Add an account:** copy any block, change `name`, `query`, `tier`, `sector`.
- **Sharpen a noisy feed:** tighten its `query` (quotes for exact phrases,
  `OR` for aliases, add `Singapore` to geo-scope global companies).
- **Your pitch / personas:** the `seller_context` block at the top — this is
  what the "what it means for you" inference is anchored to. Keep it current.

Changing the run time: edit the `cron` line in `.github/workflows/daily.yml`.
It's in **UTC** — Singapore is UTC+8, so `30 23 * * *` = 07:30 SGT,
`0 0 * * *` = 08:00 SGT, `0 22 * * *` = 06:00 SGT.

Changing the model: the workflow uses `claude-sonnet-4-6` by default (best
inference quality; ~US$3–4/month of your included credit at this volume). To
stretch further, add a repo **Variable** or edit `MODEL` in `fetch_brief.py`
to `claude-haiku-4-5`.

## Hiring signals (👔 section)

Alongside news, the radar scans **MyCareersFuture** daily for every account
(Singapore's Fair Consideration rules push most corporate roles onto MCF, so
coverage is strong). It surfaces three things:

- **📈 Headcount triggers** — 3+ new roles in one function (IT & Tech,
  Digital & Transformation, Finance, Ops & Shared Services) within a rolling
  14 days. Finance/Ops surges = they're hiring people for work your software
  does; IT/Transformation surges = a program is being staffed.
- **🎯 Key-role hires** — a single posting for a buyer-seat or builder role
  (CIO, Head of AI, RPA lead, etc.) surfaces immediately.
- **🔎 Stack intel** — postings naming competitor tech (Pega, Appian, UiPath,
  OpenText...) reveal the incumbent.

Tune everything in the `jobs:` block of `accounts.yaml`: thresholds, window,
function keywords, key roles, competitor list. Postings often appear under
legal entity names — if an account's jobs aren't matching, add its registered
name to the `aliases:` map (15 are pre-filled).

State lives in `data/seen_jobs.json`, committed daily — that's the memory that
makes "new since yesterday" work. Delete it to reset. The first run seeds from
MCF's own posting dates, so day one already shows each account's live 14-day
hiring picture. If the (unofficial but long-stable) MCF API ever changes shape,
the fix is one block at the top of `jobs_radar.py`; the news brief keeps
running regardless — a jobs failure never kills the morning page.

## Privacy

Free GitHub Pages means the brief URL is public — unindexed (the page carries
`noindex`) and unguessable in practice, but public. Your options, in order of
effort:

1. **Accept it** — no company-confidential info should be in the brief anyway;
   it's all public news plus your read on it.
2. **Rename the repo** to something unguessable (`radar-x7k2m`) — the URL
   follows the repo name.
3. **Put a free login in front** — Cloudflare Access (Zero Trust free tier)
   in front of the Pages URL, ~15 min setup, email-based login.

## Troubleshooting

- **Page never updates** → Actions tab, open the latest run, read the red step.
  Nine times out of ten it's a missing/expired secret.
- **"No credentials found"** → the secret name must be exactly
  `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY`.
- **Runs stop working mid-month (Path A)** → your monthly Agent SDK credit is
  exhausted (unlikely at this volume). It fails safe: requests stop until the
  credit refreshes with your billing cycle — no surprise charges unless you've
  explicitly enabled usage credits. Switch `MODEL` to Haiku or wait.
- **A feed is all noise** → tighten its `query` in `accounts.yaml`.
- **Brief feels stale** → check the status bar: `window 24h` + `generated`
  time tell you exactly what run you're looking at.
- **Scheduled run drifts a few minutes** → normal; GitHub cron isn't
  to-the-second. It also pauses on repos with no activity for 60 days — any
  commit (even editing accounts.yaml) wakes it up.
