# Optional secrets: what they do and how to add them

**Nothing here is required.** Mempool Omega runs fully on public endpoints with no accounts. Each secret only
switches on an extra feature. If one is missing, the system logs it and carries on.

## How to add a secret on GitHub (same steps for every secret)
1. Open your repository on github.com.
2. Click **Settings** (top bar, far right).
3. In the left sidebar click **Secrets and variables → Actions**.
4. Click the green **New repository secret** button.
5. Type the **Name** exactly as written below (capital letters, underscores), paste the **Secret** value, then click **Add secret**.

*Variables* (non-secret settings) are on the same page under the **Variables** tab → **New repository variable**.

---

## 1. Telegram alerts (recommended): `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`
Without these, alerts go to `state/logs/alerts.log` in the repo.
1. In Telegram, search for **@BotFather** and open the chat.
2. Send `/newbot`. Choose a display name, then a username ending in `bot` (e.g. `my_omega_alerts_bot`).
3. BotFather replies with a token like `123456789:AA...`. That is **`TELEGRAM_BOT_TOKEN`**.
4. Open a chat with your new bot and send it any message (e.g. "hi").
5. In a browser, open `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` (paste your token in place of `<YOUR_TOKEN>`).
6. Find `"chat":{"id":123456789`. That number (it may start with `-`) is **`TELEGRAM_CHAT_ID`**.
7. Add both as repository secrets (steps above).

## 2. Etherscan (optional): `ETHERSCAN_API_KEY`
Enriches stablecoin/exchange-wallet analysis.
1. Sign up free at https://etherscan.io/register and confirm your email.
2. Go to https://etherscan.io/myapikey → **Add** → name it "omega" → copy the key.
3. Add it as the secret `ETHERSCAN_API_KEY`.

## 3. The Graph (optional): `THEGRAPH_API_KEY`
Adds Uniswap v3 swap data.
1. Go to https://thegraph.com/studio/ and sign in (wallet or email).
2. Open **API Keys** → **Create API Key** → copy it. The free tier includes 100k queries/month.
3. Add it as the secret `THEGRAPH_API_KEY`.

## 4. Your own free RPC endpoints (optional): `ETH_RPC_URL`, `SOL_RPC_URL`
The defaults are public endpoints (`ethereum-rpc.publicnode.com`, `api.mainnet-beta.solana.com`). A free
Alchemy, Infura or Helius endpoint is more reliable:
1. Create a free account (e.g. https://www.alchemy.com/), create an app on *Ethereum Mainnet*, copy the HTTPS URL.
2. Add it as the secret `ETH_RPC_URL` (and `SOL_RPC_URL` for Solana, e.g. from https://www.helius.dev/).

## 5. Binance Spot TESTNET mirror (optional, off by default)
Mirrors paper entries as tiny orders on Binance's **testnet** (fake money, no withdrawals possible).
1. Go to https://testnet.binance.vision/ → **Log In with GitHub**.
2. Click **Generate HMAC_SHA256 Key**, give it a name, then copy the **API Key** and **Secret Key**.
3. Add them as the secrets `BINANCE_TESTNET_API_KEY` and `BINANCE_TESTNET_API_SECRET`.
4. Add a repository **variable** (Variables tab) `ENABLE_TESTNET` = `1`.

## 6. Optional storage mirrors (not used by default)
The ledger is committed to the repo (Parquet + CSV), so no database is needed. If you later want Supabase
(Postgres) or Upstash (Redis) free tiers, create the project there and add `SUPABASE_URL`/`SUPABASE_KEY` or
`UPSTASH_REDIS_REST_URL`/`UPSTASH_REDIS_REST_TOKEN`. These are documented as extension points in DECISIONS.md and
are not wired in yet.

## 7. Stripe payment links (optional): `STRIPE_RESTRICTED_KEY`
Lets the **Create or switch off a payment link** job (and Claude, on request) create Stripe payment links. It must be a
**restricted** key (`rk_test_...` or `rk_live_...`) with only Products, Prices and Payment Links set to Write; a full
secret key (`sk_...`) is refused. Step-by-step: [PAYMENTS.md](PAYMENTS.md).

## 8. Timer maintenance (optional): `CRONJOB_API_KEY`
Lets the **Timer maintenance (cron-job.org)** job (and Claude, on request) check, reschedule, pause or resume the
cron-job.org timer that starts the Tick job. Create it on cron-job.org under **Settings → API**. See
[SCHEDULER.md](SCHEDULER.md).

## Useful repository *variables* (Settings → Secrets and variables → Actions → Variables)
| Variable | Default | Meaning |
|----------|---------|---------|
| `OMEGA_DATA_MODE` | `auto` | `auto` (live, falls back to synthetic if offline), `live`, or `synthetic` |
| `OMEGA_SYMBOLS` | `BTCUSDT,ETHUSDT` | symbols to paper-trade |
| `OMEGA_TRAIN_DAYS` | `30` | rolling training window (days) |
| `OMEGA_WS_CAPTURE` | `30` | seconds of live WebSocket order-book capture per symbol each hour (0 = short REST order-book sampling only) |
| `OMEGA_DAILY_DD_KILL` | `0.03` | daily drawdown that triggers the kill switch |
| `ENABLE_TESTNET` | `0` | `1` to mirror to the Binance testnet (needs keys) |
