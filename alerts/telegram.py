"""Minimal Telegram Bot API client. If TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID are not set, alerts are
written to state/logs/alerts.log instead (so nothing is lost and nothing breaks)."""
from __future__ import annotations

import time

import requests

from core.config import get_settings, state_path
from core.log import get_logger

log = get_logger("alerts")
EMOJI = {"entry": "🟢", "exit": "🔵", "kill_switch": "🛑", "trust_drop": "⚠️", "error": "❗", "info": "ℹ️"}


def send(kind: str, text: str) -> bool:
    msg = f"{EMOJI.get(kind, '')} [Mempool Omega · PAPER] {text}"
    with open(state_path("logs", "alerts.log"), "a") as fh:
        fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {kind} {text}\n")
    s = get_settings()
    if not (s.telegram_token and s.telegram_chat_id):
        return False
    try:
        r = requests.post(f"https://api.telegram.org/bot{s.telegram_token}/sendMessage",
                          json={"chat_id": s.telegram_chat_id, "text": msg[:4000], "disable_web_page_preview": True},
                          timeout=10)
        return r.ok
    except requests.RequestException as e:
        log.warning("telegram send failed: %s", e)  # never log the token
        return False


def format_event(ev: dict) -> tuple[str, str]:
    k = ev.get("kind", "info")
    if k == "entry":
        side = "LONG" if ev.get("side", 0) > 0 else "SHORT"
        return k, (f"ENTRY {side} {ev['symbol']} @ {ev['price']:.2f} notional ${ev['notional']:,.0f} "
                   f"trust={ev.get('trust', 0):.2f} edge={ev.get('edge_bps', 0):.1f}bps "
                   f"({'maker' if ev.get('maker') else 'taker'} via {ev.get('venue')})")
    if k == "exit":
        return k, f"EXIT {ev['symbol']} reason={ev['reason']} net P&L ${ev['net_pnl']:,.2f}"
    if k == "kill_switch":
        return k, f"KILL SWITCH: {ev.get('reason')} - all positions flattened"
    return k, str(ev)
