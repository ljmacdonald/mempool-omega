# Security

## Principles
1. **No real-money trading path exists.** `Settings.live_trading` always returns `False`; there is no mainnet
   order code. `tests/test_paper.py::test_live_trading_is_impossible` enforces this.
2. **No withdrawal permissions, ever.** No code calls a withdraw or transfer endpoint. If you create exchange keys for the optional
   testnet, use *testnet* keys (fake funds) with trading permission only.
3. **No private keys.** The system reads public blockchain data only. It never signs blockchain transactions and
   never stores wallet keys or seed phrases.
4. **Secrets live in GitHub Actions secrets only.** There is no `.env` support; `.gitignore` blocks `.env*`,
   `*.key`, `*.pem`, `secrets*`. Secrets reach the code as environment variables at runtime and are never logged.
   The Telegram client logs failures without the token.
5. **No pickles.** Models are saved as LightGBM text files and JSON, so loading a model cannot execute code.
   The IsolationForest used by ASI is re-fitted at run time instead of being unpickled.
6. **Least privilege in CI.** `ci.yml` has `contents: read`. Only the paper/retrain workflows get
   `contents: write`, and only to commit `state/`.
7. **Polite, bounded networking.** Every request goes through `ingest/http.py` with per-host rate limits,
   timeouts and capped retries.

## Secrets used (all optional)
| Name | Purpose | Scope |
|------|---------|-------|
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | alerts | send messages to one chat |
| `ETHERSCAN_API_KEY` | token-transfer enrichment | read-only, free tier |
| `THEGRAPH_API_KEY` | DEX swap data | read-only, free tier |
| `ETH_RPC_URL`, `SOL_RPC_URL` | private free-tier RPC instead of public | read-only |
| `BINANCE_TESTNET_API_KEY/SECRET` | optional testnet mirror | **testnet only**, fake funds |

## Threats to the *strategy* (market manipulation)
See the ASI threat model in `DECISIONS.md` and `asi/`. Data from public sources is treated as potentially
adversarial: every feature has a trust score, and untrusted features are down-weighted before they can move a decision.

## Reporting
Open a private security advisory on the GitHub repository (Security → Advisories → Report a vulnerability).
