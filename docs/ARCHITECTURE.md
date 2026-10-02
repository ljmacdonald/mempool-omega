# Architecture

```
            ┌──────────────── free / public sources ─────────────────┐
            │ Binance (data mirror) · OKX spot+perp · Coinbase · Kraken │
            │ mempool.space · Ethereum RPC · Solana RPC · (Etherscan,  │
            │ The Graph: optional free keys)                           │
            └───────────────┬──────────────────────────────────────────┘
                            ▼
 ingest/   PoliteClient (rate limits, retries) → normalise → ONE bar frame per symbol (core/schema.py)
                            ▼
 features/ 35 causal features: OFI, depth persistence, cancel/iceberg, DEX intent, gas urgency,
           liquidation Hawkes, funding/basis/OI, stablecoin plumbing, cross-venue lead-lag + Kalman,
           momentum/vol context, time & event calendar
                            ▼
 asi/      Trust per feature per bar  ─┬─ cost-to-fake prior
                                       ├─ cross-source confirmation (≥3 independent families)
                                       ├─ persistence
                                       ├─ anomaly (robust z, IsolationForest, CUSUM, Hurst, Benford)
                                       ├─ wallet-graph cleanliness (NetworkX: wash rings, internal transfers)
                                       ├─ time context (funding prints, cascades, weekends)
                                       └─ integrity vetoes (spoof walls, wash volume, lone-venue spikes, mempool spam)
                            ▼
 omega/    LightGBM primary (direction) → TRUST-WEIGHTED INFERENCE (each feature's SHAP contribution × trust gate)
           → LightGBM meta-labeler (take / skip) → expected edge (bps)
           decision:  edge > cost_model + manipulation_premium(trust)
                            ▼
 risk/     size = min(1 % risk-to-stop, vol target, ¼-Kelly, caps) · correlation cap · gross cap
           exit plan: target / stop / time / decay / trust-collapse / kill switch
                            ▼
 paper/    TradingEngine (shared with the back-tester) · SimBroker (maker-first, cancel→taker,
           randomised size / venue / order type) · ledger (Parquet + CSV) · optional testnet mirror
                            ▼
 alerts/ Telegram      dashboard/ Streamlit      state/ committed back to git by GitHub Actions
```

## Runtime topology (all on GitHub)

| Workflow | Schedule | Does |
|----------|----------|------|
| `ci.yml` | every push / PR | ruff, pytest, offline synthetic smoke run |
| `paper.yml` | hourly (`:07`) | one paper pass, then commits `state/` |
| `retrain.yml` | nightly 02:17 UTC | retrain on a rolling window, walk-forward backtest, red-team, commits `state/` |

Both state-writing workflows share the concurrency group `omega-state-writer`, so they never race.

## Why batch passes instead of a 24/7 WebSocket daemon?
GitHub Actions is batch compute. Each hourly pass:
1. pulls the last ~600 closed 5-minute bars over REST (fast, rate-limit friendly);
2. optionally opens public WebSockets for a bounded window (default 30 s) to measure live book microstructure;
3. **replays every bar closed since the previous pass** through the engine, so stops and targets that
   fired between runs are honoured on the real high/low path;
4. considers new entries **only on the latest bar** (no retroactive trades).

## One code path for research and paper trading
`backtest/engine.py` and `paper/trader.py` both drive `paper/engine.py::TradingEngine.on_bar`. Fees, slippage,
latency (fill on the next bar), maker/taker logic, exits and the kill switch are therefore identical in the
backtest and in paper trading.

## Leakage controls
- Features are causal (unit test `test_no_lookahead` mutates the future and asserts the past is unchanged).
- Labels: triple barrier with the same exits as live trading.
- Purged K-fold + embargo for out-of-sample meta-labels; purged walk-forward for evaluation.
- Live-only columns (order book, mempool) are NaN in history, so the model can't learn from data it would never
  have had historically.

## Storage
- `state/models/*.txt|json`: LightGBM text models + JSON metadata (no pickle).
- `state/ledger/{trades,signals,events,equity}_YYYY-MM.{parquet,csv}`: the append-only ledger.
- `state/reports/*.json|csv`: training, backtest and red-team reports.
- `state/paper_state.json`: open positions, pending orders, kill-switch state.
- `state/status.json`: latest snapshot for the dashboard.
- Optional (not required): Supabase Postgres / Upstash Redis free tiers can mirror the ledger. See DECISIONS.md.
