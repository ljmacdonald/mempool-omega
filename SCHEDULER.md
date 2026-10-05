# Reliable timing with cron-job.org (free, about 10 minutes)

GitHub's built-in timer is "best effort". In early October 2026 it skipped most of this project's scheduled runs:
Forex ran once in 28 slots, and the hourly jobs ran every 3–6 hours. The track records grew slowly, and the pages
that depend on the scheduled jobs went stale.

The fix: an outside timer (cron-job.org, free) starts one small job, **Tick**, every 15 minutes. Tick
(`.github/workflows/tick.yml`, `infra/tick.py`) looks at when each job last ran and starts whichever are due:
- the paper trader hourly;
- DEX hourly;
- Forex and US stocks every 15 minutes while their markets are open;
- ICT every 15 minutes;
- each nightly retrain or backtest once a night.

The jobs keep their own GitHub timers as a backup. Tick never starts a job that is already running.

## What you need to create (only you can, from your accounts)

### 1. A GitHub token that can only start this repository's jobs
1. github.com → your profile picture → **Settings** → **Developer settings** (bottom of the left menu) →
   **Personal access tokens** → **Fine-grained tokens** → **Generate new token**.
2. **Token name:** `cron-job.org tick`. **Expiration:** custom, one year from today (GitHub's maximum).
3. **Repository access:** **Only select repositories** → `ljmacdonald/mempool-omega`.
4. **Permissions** → **Repository permissions** → **Actions:** **Read and write**. Leave everything else as it is
   ("Metadata: read-only" is added automatically).
5. **Generate token**, then copy it (it starts with `github_pat_`). GitHub shows it only once.

What this token can do: start, re-run or cancel this repository's jobs and delete their logs. It cannot change code,
the website or settings, cannot see other repositories, and nothing here can touch money.

### 2. The cron-job.org job
1. Sign up free at https://cron-job.org and confirm your email.
2. **Create cronjob**:
   - **Title:** `Mempool Omega tick`
   - **URL:** `https://api.github.com/repos/ljmacdonald/mempool-omega/actions/workflows/tick.yml/dispatches`
   - **Execution schedule:** every 15 minutes.
3. Open the **Advanced** tab:
   - **Request method:** `POST`
   - **Headers** (add three):
     - `Authorization` = `Bearer github_pat_...` (your token)
     - `Accept` = `application/vnd.github+json`
     - `X-GitHub-Api-Version` = `2022-11-28`
   - **Request body:** `{"ref":"main"}`
4. **Save**, then **Test run**. A good answer is **204 No Content**.

### 3. Check it works
GitHub → the repository → **Actions** → **Tick (starts whatever jobs are due)**. A run should appear every 15 minutes.
Each run's log ends with a line like `started: fx.yml scan, ict.yml scan`.

## Keeping it safe
- **Renew yearly.** Set a reminder for the token's expiry date. If it lapses, Tick simply stops being started and the
  jobs fall back to GitHub's slower timer. Nothing breaks.
- **If the token leaks**, delete it under the same Fine-grained tokens page and make a new one. It stops working at
  once. The most anyone could do with it is start or cancel jobs.
- This is the one secret kept outside GitHub, a deliberate exception to the "secrets only in GitHub" rule
  (DECISIONS D69).
