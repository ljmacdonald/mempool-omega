# RUNBOOK: operating Mempool Omega without writing code

**Just want to use it?** Open **https://ljmacdonald.github.io/mempool-omega/**. That's all you need. Everything
below is optional background for people who want to change settings.

Everything happens in your browser on github.com. No computer setup is needed.

> Reminder: this is **paper trading** (simulated money). Nothing here can spend real funds.

---

## A. First-time setup (about 10 minutes)

### 1. The code is already on `main`
This repository's default branch `main` holds everything, so the hourly and nightly schedules run automatically.

### 2. Allow the workflows to save results
1. **Settings → Actions → General**.
2. Under **Workflow permissions** choose **Read and write permissions** → **Save**.
   (The hourly job commits its ledger back into `state/`.)

### 3. (Optional) Telegram alerts
Follow section 1 of `MISSING_SECRETS.md`. Skip it if you're happy reading the dashboard.

### 4. Kick off the first runs manually
1. Click the **Actions** tab. If asked, click **I understand my workflows, go ahead and enable them**.
2. In the left list click **Nightly retrain + backtest + red team** → **Run workflow** → **Run workflow**.
   It takes about 10–20 minutes. It trains the models on the last 30 days of public data and writes reports.
3. Then click **Paper trader (hourly)** → **Run workflow**. After that it runs on its own every hour at :07.

### 5. Put the live dashboard online (free, about 3 minutes, needed for live refresh)
1. Go to https://share.streamlit.io and sign in with GitHub.
2. **Create app → Deploy a public app from GitHub**.
3. Repository: `ljmacdonald/mempool-omega`. Branch: `main`. Main file path: `dashboard/app.py`. Click **Deploy**.
4. Bookmark the web address it gives you (something like `https://….streamlit.app`). That's your live screen.
5. In its left panel choose your **trading speed** and how often to **refresh** (every 1, 2, 5, 15, 30 or 60
   minutes). It pulls live prices directly; the GitHub hourly job only keeps the track record and alerts.
   A free Streamlit app goes to sleep after a few days without visitors. Opening the link wakes it up in about
   30 seconds.

---

## B. Day-to-day

| You want to… | Do this |
|--------------|---------|
| See how it's doing | Open the Streamlit dashboard, or browse `state/status.json` and `state/ledger/*.csv` on GitHub. |
| See every trade | `state/ledger/trades_YYYY-MM.csv` (open it on GitHub; it renders as a table). |
| See why a trade was / wasn't taken | `state/ledger/signals_YYYY-MM.csv` → columns `final_reason`, `edge_bps`, `cost_bps`, `premium_bps`, `trust`. |
| Know why a trade closed | `exit_reason` column; see `docs/EXITS.md`. |
| Check the latest backtest | `state/reports/backtest_latest.json` and the dashboard's **Backtest** tab. |
| Check the red-team | `state/reports/redteam_latest.json` and the dashboard's **Red team** tab. |

## C. Controls

| Action | How |
|--------|-----|
| **Pause everything** | Actions → *Paper trader (hourly)* → **⋯ → Disable workflow** (same for the retrain job). |
| Resume | Same place → **Enable workflow**. |
| Change symbols | Settings → Secrets and variables → Actions → **Variables** → `OMEGA_SYMBOLS` = `BTCUSDT,ETHUSDT,SOLUSDT`. The next nightly retrain trains them; to start sooner, run the retrain manually. |
| Make the kill switch stricter | Variable `OMEGA_DAILY_DD_KILL` = `0.02` (2 %). |
| Force offline/synthetic mode | Variable `OMEGA_DATA_MODE` = `synthetic` (useful if public APIs are down). |
| Reset the paper account | Delete `state/paper_state.json` in the GitHub web editor (open file → 🗑 → commit). Next run starts at $100,000 with no positions. The ledger history is kept. |
| Turn on the testnet mirror | `MISSING_SECRETS.md` §5. |

## C2. GitHub Actions minutes
The full plan (hourly paper pass with a 30 s live WebSocket order-book capture, nightly retrain, CI) uses about
3,000 Actions minutes a month. That is free and unlimited on a **public** repository. On a private repository the
free allowance is 2,000 minutes/month; with GitHub's default $0 spending limit the workflows simply pause when it
runs out (you are never charged). To fit a private repo, set the variable `OMEGA_WS_CAPTURE` = `0`.

## C3. Payment links (Stripe)
Products are sold through Stripe payment links made by `.github/workflows/payment-link.yml`
(`scripts/payment_link.py`), using only the `STRIPE_RESTRICTED_KEY` secret. Setup and use: [PAYMENTS.md](PAYMENTS.md).
For Claude: trigger the workflow with `workflow_dispatch` inputs `action=create`, `name`, `price_usd`,
`interval` (`one_time`/`month`/`year`), optional `description` and `redirect`. Read the link from the run summary or
`state/payments/links.json` on `main`. To switch one off, use `action=deactivate` and `link_id`. Never ask for the key in
a chat.

## C4. The timer (cron-job.org → Tick)
cron-job.org starts `tick.yml` at minutes 1, 16, 31 and 46 (UTC); Tick starts whatever is due (`infra/tick.py`).
For Claude: check or change the timer with `workflow_dispatch` on `cronjob.yml` (`action` = status / schedule / pause /
resume, `minutes` = e.g. `1,16,31,46`), which uses the `CRONJOB_API_KEY` secret. Never ask for the key in a chat.

## D. Troubleshooting

| Symptom | Likely cause → fix |
|---------|--------------------|
| Red ❌ on *Paper trader* run | Open the run → the failed step → read the last lines. Public APIs are sometimes down; the next hourly run usually recovers. In `auto` mode the system falls back to synthetic data rather than failing. |
| "push failed" in *Commit state* | You skipped **A.2** (read & write permissions). |
| Scheduled runs not happening | Workflows must be on the **default branch** (A.1). GitHub also pauses schedules in repos with no activity for 60 days; the hourly commits normally keep it active. Scheduled runs can be delayed by up to ~15–30 min at busy times. |
| Kill switch says `data_failure` | The primary price feed was stale or unreachable. It re-arms automatically when data is healthy. |
| Kill switch says `daily_drawdown` / `consecutive_losses` | Working as designed. It re-arms at the next UTC midnight. |
| Dashboard shows `data_mode: synthetic` | Public endpoints were unreachable from the runner, so the system used the synthetic fallback. Check the run log's `source ... unavailable` lines. |
| No trades for days | Normal. The system only trades when `edge > cost + manipulation premium`. Real markets rarely offer that. Look at `final_reason` in the signals ledger to see what blocked it. |

## E. For tinkerers (optional, needs Python 3.10+)
```bash
make setup        # install
make demo         # offline end-to-end demo on synthetic data
make test         # unit tests
make paper        # one live paper pass on public data
make dashboard    # local dashboard at http://localhost:8501
```
Or open the repo in **GitHub Codespaces** (Code → Codespaces → Create), then run the same commands in its terminal.
