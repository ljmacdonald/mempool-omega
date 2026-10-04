# Decisions & assumptions log

The brief said "don't ask, decide and log it". Each entry gives the decision and the reasoning behind it.

## Repository & process
| # | Decision | Why |
|---|----------|-----|
| D1 | Mempool Omega lives in its own **public** repository, `mempool-omega`, with `main` as the default branch. It was first developed inside a private repository and moved here with a fresh history. | Public repositories get unlimited free GitHub Actions minutes, so the full hourly plan (including the live WebSocket order-book capture) runs at $0. Scheduled workflows run from `main`. |
| D2 | The monorepo lives at the repository root (this repo *is* `mempool-omega`). | Avoids a nested folder; the package name in `pyproject.toml` is `mempool-omega`. |
| D3 | Added a small `core/` package (config, logging, schema, synthetic data) and `scripts/` (demo, notifications) beyond the listed folders. | Shared plumbing needs a home that every module can import without cycles. |
| D4 | GitHub Actions workflows live in `.github/workflows/` (the only place GitHub reads them). `infra/` holds the Dockerfile and the state-commit script. | Platform constraint. |
| D5 | `ARCHITECTURE.md` and `EXITS.md` are in `docs/`; the other docs are at the root. | Matches the repo structure in §11 and the `docs/EXITS.md` path in §9. |

## Data
| # | Decision | Why |
|---|----------|-----|
| D6 | Binance spot data comes from `data-api.binance.vision` (its official public market-data mirror), with `api.binance.com` as fallback. | `api.binance.com` and `fapi.binance.com` return **HTTP 451** from US cloud IPs, including GitHub-hosted runners. |
| D7 | Perpetual derivatives (mark/perp price, funding, OI, liquidations) come from **OKX** public endpoints. Binance futures adapters exist but are geo-blocked from CI. Bybit returns 403 from cloud IPs; an adapter exists for local use. | Free, keyless and reachable from GitHub runners. |
| D8 | Bar size **5 minutes**; ~600 bars of context per hourly pass; 30-day rolling training window. | The intraday horizon fits the GitHub Actions free tier and public rate limits. |
| D9 | **Live-only columns** (order book, public mempool, current gas, stablecoin logs) are filled only for the latest bar; they are NaN in history. | No free historical L2/mempool archive exists. Training on NaN for these columns means the model never relies on data it would not have had historically. LightGBM routes NaN natively. As the paper ledger grows, a future version can train on the recorded live values. |
| D10 | WebSocket capture is **time-boxed** (default 30 s per symbol per hour), falling back to a short REST order-book polling window. | GitHub Actions is batch compute; a 24/7 socket daemon would need paid hosting. |
| D11 | DEX intent is measured relative to ETH. Router calls paying ETH for tokens count as ETH sell pressure, and token→ETH calls as buy pressure. Each call is weighted by priority fee above base fee and log trade size. The same reading is used as a risk-appetite proxy for BTC. | Simple, explainable, and needs only public RPC. Private order flow (Flashbots etc.) is invisible, so this feature gets a low cost-to-fake prior. |
| D12 | Stablecoin "exchange netflow" uses a small, editable list of publicly labelled exchange hot wallets (`asi/known_wallets.yaml`). | Labels are best-effort; users can extend them. Internal exchange↔exchange transfers are filtered out. |
| D13 | `OMEGA_DATA_MODE=auto` falls back to a **synthetic market** if the primary exchange is unreachable, and tags every artefact with `data_mode`. | Never stop. The dashboard and reports display the mode, so synthetic results can't be mistaken for real ones. |
| D14 | Synthetic data contains **small planted alpha**, and is anchored to a fixed epoch (2026-01-01) so the same timestamp always has the same values across hourly runs. | Lets the pipeline, tests and red team be exercised offline. Performance on synthetic data says nothing about real markets. |
| D15 | Macro and unlock calendar is a hand-maintained YAML (`features/calendar_events.yaml`). | There is no reliable free API. Dates are marked "verify". |

## Modelling
| # | Decision | Why |
|---|----------|-----|
| D16 | One model **per symbol**. | Simple and robust; pooled cross-asset training is a future extension. |
| D17 | Labels are **triple-barrier** (1σ symmetric for the primary model). Meta-labels use the *live* exit plan (1.5σ target, 1σ stop, 12-bar time stop) and are **net of round-trip cost**. | Keeps the model's objective consistent with how trades are actually exited and paid for. |
| D18 | Meta-labels are trained on **purged K-fold out-of-sample** primary predictions; evaluation uses **purged walk-forward** with an embargo equal to the label horizon. | Prevents label-overlap leakage (López de Prado). |
| D19 | Expected edge = `(meta_p·W − (1−meta_p)·L)·σ_h`, with W and L the calibrated mean win and loss in σ units. Trade only if `edge > cost + manipulation_premium(trust)`. | Implements the brief's trade rule in units the cost model understands (bps). |
| D20 | Hawkes kernel is fitted nightly by Poisson MLE on per-bar liquidation counts (log-compressed), with a stationarity penalty. | Cheap, robust, interpretable. |
| D21 | Kalman filter tracks the cross-venue fair value (Binance vs Coinbase). Its standardised innovation is a feature (`xv_kalman_z`), and the module also gives dynamic hedge betas. | "Dynamic hedge: Kalman filter for cross-venue beta." Initial noise is estimated from the first 100 points only, after the no-look-ahead test caught a leak. |
| D22 | Models are stored as **LightGBM text + JSON** (no pickle). ONNX export is not wired in. | Security (no arbitrary code on load) and readable diffs. ONNX can be added later with `onnxmltools` if inference speed ever matters. |
| D23 | No online gradient updates; "online learning" = **nightly retrain on a rolling window**. | Matches the brief and keeps runs deterministic. |

## ASI (adversarial signal integrity)
| # | Decision | Why |
|---|----------|-----|
| D24 | Trust weights: cost-to-fake 0.25, cross-source 0.25, persistence 0.15, anomaly 0.15, graph 0.10, time 0.10. | Judgement call: what is expensive to fake and independently confirmed should dominate. Weights are in `asi/trust.py::TrustWeights`. |
| D25 | Cost-to-fake priors: executed trades and liquidations are high (they cost real money); resting order-book depth and public-mempool intent are low (cheap to place and cancel); volume is lowish (wash-tradable). | Economic reasoning about manipulation cost. |
| D26 | **Trust-weighted inference**: each feature's SHAP contribution is multiplied by `clip((trust−0.25)/0.35, 0, 1)` before the sigmoid. Signal trust = trust of the features *pushing in the trade's direction*, weighted by how hard they push. | Gating alone was too weak in red-team tests. Neutralising untrusted inputs stops the model following bait. With high trust the output equals the raw model's. |
| D27 | Trust is **not** a meta-model input. | Keeps ASI an independent, auditable layer, and makes the "no ASI" baseline a fair comparison. |
| D28 | Integrity vetoes (hard multipliers): spoof (low depth persistence or high cancel rate), wash (volume without price impact × Benford), lone-venue lead-lag spikes, and mempool spam without gas urgency. | Direct manipulation evidence should override a weighted average. |
| D29 | Red-team results are reported honestly, including when ASI costs return. | On synthetic data ASI usually cuts bait trades substantially (see `state/reports/redteam_latest.json`) but also filters some good trades. That is the expected trade-off. |

## Execution & risk
| # | Decision | Why |
|---|----------|-----|
| D30 | The simulated instrument is a **USDT perpetual** (long and short), priced off the primary spot close, with funding accrued from OKX funding rates. | Allows shorts; funding is a real cost. |
| D31 | Fill model: maker-first limit at the decision close; it fills on the next bar only if price trades through *and* a queue-position coin flip (60 %) succeeds. Otherwise it is replaced as a taker at the next open plus √-impact slippage. 10 % of orders go straight to taker; size jitter is up to −15 %; venue is random. Profit targets fill as maker; all other exits as taker. | Conservative, adversarially unpredictable, and identical in backtest and paper. |
| D32 | If one bar touches both stop and target, the stop is assumed first. A gap through the stop fills at the open. | Pessimistic by design. |
| D33 | Kill switch: 3 % daily drawdown and 5 consecutive losses halt until the next UTC day. Data failure and systemic trust collapse halt until conditions clear. | Simple and explainable. |
| D34 | Correlation cap: at most 2 same-direction positions (BTC and ETH are ~0.8 correlated); gross leverage ≤ 1.5×. | Avoids doubling the same bet. |
| D35 | Hourly pass replays every bar since the last pass for exits, but **entries only on the latest bar**. | Honest: no retroactive trades. Accurate: stops and targets honour the real path. |

| D40 | **Nightly self-improvement** (`scanner/improve.py`): 4 model settings compete on the newest 25 % of data (purged by the horizon); a challenger must beat `base` by ≥ 0.05 % top-5 excess per idea; check penalties are re-learned from the track record, blended with the prior by n/(n+50) and bounded to 0.02–0.30; a speed whose last 50 ideas trail random picks is on probation (−0.10 R). | Learns only from out-of-sample evidence; resists chasing noise; every change is logged in `state/reports/improvements.json`. |
| D41 | **All-in cost per venue** in the website: CEX = live order book walked for the user's amount + taker fee both ways + a typical stablecoin withdrawal fee; DEX = pool fee + constant-product impact + live gas (public RPC) ×2 + a per-chain MEV/sandwich allowance (shown with and without protection). Venues whose price is more than 20 % off the reference are treated as a different token. | Users see the money they could actually take out, and where. Entry-level fee rates are a conservative default. |

## Storage & infra
| # | Decision | Why |
|---|----------|-----|
| D36 | The ledger and models are committed to the repo (month-partitioned Parquet + CSV). Supabase and Upstash are **documented extension points, not dependencies**. | Zero accounts needed; GitHub is the single source of truth, as the brief requires. |
| D37 | The state-writing workflows share one concurrency group; commits are tagged `[skip ci]`; logs are truncated to the last 5000 lines. | No races; the repo doesn't bloat; CI minutes aren't wasted. |
| D39 | Hourly paper pass runs the full plan: a **30 s live WebSocket order-book capture** per symbol (`OMEGA_WS_CAPTURE=30`). | The repository is intended to be public (unlimited free Actions minutes). On a private repo, set `OMEGA_WS_CAPTURE=0` to stay within 2,000 free minutes/month. |
| D38 | GitHub cron runs at minute 7 (paper) and 02:17 UTC (retrain). | Avoids the top-of-hour scheduler congestion. Note: GitHub may delay or skip scheduled runs under load. |
