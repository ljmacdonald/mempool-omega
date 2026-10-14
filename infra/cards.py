"""Share cards (DECISIONS D102): the picture shown when someone shares the Results page or a $100 challenge.

Writes 1200x630 PNG images to state/cards/ from state/summary.json and the challenges' value files:
  results.png          every section's status (ahead / even / behind / too early), losers included
  challenge.png        the $100 DEX challenge: value now and its line since the start
  challenge-small.png  the $100 Small-coin challenge, the same way
Run from the summary job at most every CARD_EVERY_H hours (`--due` only answers whether it is time), so the
repository isn't filled with images. Needs Pillow, which that job installs only when cards are due.
"""
from __future__ import annotations

import csv
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = Path(os.environ.get("OMEGA_STATE_DIR") or ROOT / "state")
OUT = STATE / "cards"
CARD_EVERY_H = 12
W, H = 1200, 630
BG, FG, MUTED, LINE = (11, 13, 18), (236, 238, 245), (150, 156, 175), (40, 44, 58)
ACCENT, ACCENT2 = (59, 78, 232), (122, 78, 240)
TONE = {"ahead": ((22, 163, 74), "Ahead"), "even": ((100, 116, 139), "Even"), "behind": ((220, 38, 38), "Behind"),
        "early": ((202, 138, 4), "Too early")}
UP, DOWN = (34, 197, 94), (248, 113, 113)
FOOT = "Paper trading only · every result after costs, against random picks · not financial advice"
FONTS = "/usr/share/fonts/truetype/dejavu/"


def due(now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    try:
        t = datetime.fromisoformat(json.loads((OUT / "cards.json").read_text())["generated_at"])
    except (OSError, ValueError, KeyError):
        return True
    return now - t >= timedelta(hours=CARD_EVERY_H)


def font(size: int, bold: bool = False):
    from PIL import ImageFont
    try:
        return ImageFont.truetype(FONTS + ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"), size)
    except OSError:
        return ImageFont.load_default(size)


def canvas():
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    for x in range(W):                                   # the brand stripe along the top
        f = x / (W - 1)
        d.line([(x, 0), (x, 9)], fill=tuple(round(a + (b - a) * f) for a, b in zip(ACCENT, ACCENT2)))
    d.text((60, 44), "Ω  MEMPOOL OMEGA", font=font(26, True), fill=FG)
    d.line([(60, H - 74), (W - 60, H - 74)], fill=LINE, width=2)
    d.text((60, H - 56), FOOT, font=font(20), fill=MUTED)
    return im, d


def pct(x, unit="%") -> str:
    if x is None:
        return "–"
    return f"{'+' if x >= 0 else '−'}{abs(x):.2f} R" if unit == "R" else f"{'+' if x >= 0 else '−'}{abs(x) * 100:.2f}%"


def results_card(summary: dict, when: str):
    im, d = canvas()
    rest = [s for s in summary.get("sections", []) if not s["key"].startswith("challenge")]
    count = {k: sum(s["status"] == k for s in rest) for k in TONE}
    d.text((60, 92), "Live paper record, every section", font=font(46, True), fill=FG)
    d.text((60, 152), f"{count['ahead']} ahead · {count['even']} even · {count['behind']} behind · {count['early']} too early"
                      f"  ·  {when}", font=font(24), fill=MUTED)
    order = {"ahead": 0, "even": 1, "behind": 2, "early": 3}
    rest.sort(key=lambda s: (order.get(s["status"], 9), -(s.get("n") or 0)))
    col_w, row_h, top = 540, 50, 205
    f_name, f_pill, f_num = font(24, True), font(18, True), font(20)
    for i, s in enumerate(rest[:12]):
        x, y = 60 + (i // 6) * (col_w + 20), top + (i % 6) * row_h
        colour, label = TONE.get(s["status"], (MUTED, s["status"]))
        d.rounded_rectangle([x, y + 6, x + 118, y + 38], radius=16, fill=colour)
        tw = d.textlength(label, font=f_pill)
        d.text((x + 59 - tw / 2, y + 11), label, font=f_pill, fill=(255, 255, 255))
        right = f"{(s.get('n') or 0):,} · {pct(s.get('avg'), s.get('unit', '%'))}" if s.get("n") else "no trades yet"
        rw = d.textlength(right, font=f_num)
        fn = f_name if d.textlength(s["name"], font=f_name) < col_w - 134 - rw - 16 else font(20, True)
        d.text((x + 134, y + 8 if fn is f_name else y + 11), s["name"], font=fn, fill=FG)
        d.text((x + col_w - rw, y + 11), right, font=f_num, fill=MUTED)
    return im


def read_equity(rel: str) -> list[float]:
    try:
        with open(STATE / rel, newline="") as f:
            return [float(r["equity"]) for r in csv.DictReader(f) if r.get("equity")]
    except (OSError, ValueError):
        return []


def challenge_card(s: dict, curve: list[float], when: str):
    im, d = canvas()
    eq = s.get("equity")
    d.text((60, 92), s["name"], font=font(46, True), fill=FG)
    d.text((60, 152), f"A paper account started at $100, trading by fixed rules after every real cost  ·  {when}",
           font=font(22), fill=MUTED)
    colour = FG if eq is None else UP if eq >= 100 else DOWN
    d.text((60, 205), "–" if eq is None else f"${eq:,.2f}", font=font(110, True), fill=colour)
    n, op = s.get("n") or 0, s.get("open") or 0
    lines = [f"{'+' if (eq or 100) >= 100 else '−'}{abs((eq or 100) - 100):.2f}% since the start" if eq is not None else "",
             f"{n} finished trade{'' if n == 1 else 's'}", f"{op} open" if op else "in cash right now",
             "paused by its loss limit" if s.get("paused") else ""]
    d.text((60, 345), "  ·  ".join(x for x in lines if x), font=font(26), fill=FG)
    if len(curve) >= 2:                                   # the account's value since the start
        x0, x1, y0, y1 = 60, W - 60, 400, H - 100
        lo, hi = min(min(curve), 100), max(max(curve), 100)
        span = (hi - lo) or 1
        ys = lambda v: y1 - (v - lo) / span * (y1 - y0)
        d.line([(x0, ys(100)), (x1, ys(100))], fill=LINE, width=2)
        d.text((x1 - 60, ys(100) - 26), "$100", font=font(18), fill=MUTED)
        pts = [(x0 + i / (len(curve) - 1) * (x1 - x0), ys(v)) for i, v in enumerate(curve)]
        d.line(pts, fill=ACCENT2 if curve[-1] >= 100 else DOWN, width=5, joint="curve")
    return im


def make(now: datetime | None = None) -> list[str]:
    now = now or datetime.now(timezone.utc)
    summary = json.loads((STATE / "summary.json").read_text())
    when = now.strftime("%-d %b %Y")
    OUT.mkdir(parents=True, exist_ok=True)
    made = []
    results_card(summary, when).save(OUT / "results.png", optimize=True)
    made.append("results.png")
    files = {"challenge": "dex/challenge/equity.csv", "challenge-small": "suggestions/challenge_small/equity.csv"}
    for s in summary.get("sections", []):
        if s["key"] in files:
            challenge_card(s, read_equity(files[s["key"]]), when).save(OUT / f"{s['key']}.png", optimize=True)
            made.append(f"{s['key']}.png")
    (OUT / "cards.json").write_text(json.dumps({"generated_at": now.isoformat(), "cards": made}, indent=1))
    return made


if __name__ == "__main__":
    if "--due" in sys.argv:
        raise SystemExit(0 if due() else 1)
    print("cards:", ", ".join(make()))
