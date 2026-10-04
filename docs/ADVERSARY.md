# Adversary model: a manipulator who knows we're watching

This project is public, including every check and threshold. So we assume the most capable opponent: someone
who has read all of it, can run the same model, and shapes their activity to exploit whoever follows the
scanner. This page explains how such a person would try to turn the scanner against its users, and what the
system does about each move.

It is written for defence. It describes manipulation at the level needed to recognise it, not as instructions.

## The core idea

A manipulator's goal is to make followers buy before they sell, or sell before they buy. A watched market gives
them two extra levers:

1. **They know what the watcher rewards**, so they can manufacture it.
2. **They know what the watcher fears**, so they can fake that too, to scare people out of positions they
   want cheap.

Our counter-strategy has four parts:

- **Make the rewarded signals expensive to fake.** Use signals that cost real money and real risk to fake, not
  ones that are cheap to show and cancel.
- **Remove fixed targets.** Exact thresholds are secret and keep changing.
- **Watch our own footprint.** If our published ideas reliably reverse after we publish them, someone is using
  us, so learn which patterns are bait and penalise them.
- **Fail safe.** The scanner only suggests buys. When in doubt it lowers a score or skips; it never pushes you
  into a trade.

## Moves and counter-moves

| # | What a sophisticated manipulator would try | Why it would work against a naive scanner | What this system does |
|---|---|---|---|
| 1 | **Calibrate to the published thresholds.** Keep each fake signal just under a known limit. | Fixed public limits are a target. | **Secret, rotating thresholds.** Every limit is jittered to 85–115% of its base value from a private seed (per device in the website, per run on the server, optionally a GitHub secret) that changes every hour. Nobody can know today's exact lines. |
| 2 | **Hold fake order-book walls past the check window**, then pull them. | A check that re-reads the book after a fixed delay is beaten by waiting that long. | **Three snapshots at random gaps** (2–5 s, then another 4–10 s). A wall must survive all of them. The longer fake orders stay up, the more likely they get filled, which turns the bluff into a real, risky position. |
| 3 | **Wash trade with random sizes and timing** to dodge "identical back-and-forth" detection. | Pattern detectors look for a fingerprint, and randomness removes it. | **Price-impact check.** Real buying and selling moves prices, in proportion to the square root of the money traded. Lots of volume that barely moves the price is fake however it is dressed up. On live data it flagged 1 of 60 coins. |
| 4 | **Pump on several exchanges at once** so a cross-exchange check confirms the move. | Confirmation becomes a stamp of approval. | Cross-exchange confirmation is kept, but a move confirmed everywhere still has to pass the price-impact, whale and engineered-setup checks. Pumping several venues at once costs much more. |
| 5 | **Split one whale into many small accounts and trades.** | "Average trade size" checks stop firing. | The price-impact and engineered-setup checks don't depend on trade size. |
| 6 | **Build the exact look the scanner likes**: a burst of volume and aggressive buying, with a perfect checklist. | A model trained on these features ranks it highly. | **"Too good to be true" check.** A case that rests on cheap-to-fake signals (volume bursts, buy spikes) with no lasting price trend behind it is penalised. |
| 7 | **Front-run the published list.** Run the public model, buy what it's about to suggest, sell to followers. | The ranking is deterministic and published on a timetable. | (a) The website computes ideas **in each visitor's browser at a time they choose**, with their own secret thresholds, so there's no single list to front-run. (b) The hourly server list goes out after a **random 0–10 minute delay**. (c) The **bait monitor** (see below) penalises patterns that reverse after we suggest them. |
| 8 | **Bait with a pre-planned dump.** Pump a coin until it tops the list, then sell into the buyers. | The scanner keeps recommending the pumped coin. | The **bait monitor** looks at our own track record every night. Ideas that had already run up, or had a volume burst, are compared with similar ideas without those patterns. If they did worse after being suggested, the pattern gets a growing penalty, which the server and website apply automatically (`state/web/adaptive.json`). |
| 9 | **Hunt stops.** Push the price briefly through obvious levels to trigger everyone's safety exits, buy cheap, let it recover. | Stops at round numbers or just under the recent low are where everyone's stops sit. | **Exits are moved off crowded levels.** A safety exit sitting on a round number, or just above the recent low, is moved a little below it (never more than 25% of the planned risk). Take-profits are set just below round numbers, where sell orders pile up. |
| 10 | **Reverse psychology.** Plant red flags (fake walls, wash prints) on a coin you hold, so holders panic-sell to you. | A monitor that reacts to every warning can be steered. | The **"Consider leaving early"** alert needs the score to stay low on **two separate refreshes**, so one planted reading can't trigger it. Warnings never move your safety exit; only real price reaching it does. A coin flagged on the buy side is simply not suggested, which costs you a missed opportunity, not a loss. |
| 11 | **Exploit the system's own consistency**, for example by spoofing at the moment of every hourly check. | Predictable checking times. | Website checks run when you refresh. Server checks have a random delay and random snapshot gaps. |
| 12 | **Sandwich / front-run the follower's own swap** on a DEX. | A visible swap in the public mempool can be bought ahead of and sold into. | The "Where to buy" panel prices the **MEV cost per chain**, prefers the cheapest venue after it, and tells you how to protect yourself (private RPC, tight slippage limit). Exchanges (CEX) have no public mempool. |

DEX tokens have their own, stricter adversary model: see [DEX.md](DEX.md).

## What this can't stop (honest limits)

- **Real money moving real prices.** If someone pays for genuine buying on several exchanges with deep order
  books, every check correctly sees real buying. Then they sell. No public-data system can read intentions.
  The safety exit and time limit are your protection.
- **Private information.** Insiders, listings, unlocks and news aren't visible in market data.
- **Very fast moves** within a single candle, faster than any refresh.
- **Small sample sizes.** The bait monitor needs dozens of settled ideas before it can act (currently it needs
  60). Until then it does nothing.
- **Public code.** The secret seed hides today's exact thresholds, but the *kinds* of checks are public. The
  defence relies on making faking **costly**, not on secrecy.

## Where it lives

| Defence | Server (Python) | Website (JavaScript) |
|---|---|---|
| Secret rotating thresholds, multi-snapshot walls, price impact, engineered setup | `scanner/integrity.py` | `site/engine.js` (same logic, parity-tested) |
| Exits moved off crowded levels, bait-pattern penalty | `scanner/defence.py` | `site/engine.js` |
| Bait monitor (nightly) | `scanner/defence.py::update_adaptive`, run by `retrain.yml` | reads `state/web/adaptive.json` |
| Random publication delay | `.github/workflows/paper.yml` | n/a (each visitor's own timing) |
| Two-reading rule for "leave early" | n/a | `site/app.js` |
| Tests of each evasion | `tests/test_adversary.py`, `tests/test_web_engine.py` | |

Optional: add a GitHub secret `OMEGA_SECRET_SEED` (any number) to fix the server's private seed. Without it,
a fresh random seed is used on every run.
