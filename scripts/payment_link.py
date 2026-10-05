"""Create or switch off Stripe payment links for the project's products (run by .github/workflows/payment-link.yml).

The Stripe key comes only from the STRIPE_RESTRICTED_KEY GitHub Actions secret and must be a *restricted* key
(rk_test_... or rk_live_...) that can only write Products, Prices and Payment Links. A full secret key (sk_...)
is refused: if it ever leaked it could refund payments, move money and read customers.

Every link made is recorded in state/payments/links.json (no secrets, only public link details), so the website
and later sessions can find it.

  python -m scripts.payment_link create --name "Omega Pro" --price 19 --interval month
  python -m scripts.payment_link deactivate --link plink_123
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

import requests

from core.config import state_path

API = "https://api.stripe.com/v1"
LINKS = "payments/links.json"
INTERVALS = ("one_time", "month", "year")
MAX_PRICE_USD = Decimal("10000")


class PaymentError(Exception):
    pass


def check_key(key: str | None) -> str:
    key = (key or "").strip()
    if not key:
        raise PaymentError("STRIPE_RESTRICTED_KEY is not set. Add it under Settings -> Secrets and variables -> "
                           "Actions (see PAYMENTS.md).")
    if key.startswith("sk_"):
        raise PaymentError("This is a full Stripe secret key (sk_...). For safety only a restricted key (rk_...) "
                           "with Products, Prices and Payment Links write access is accepted. See PAYMENTS.md.")
    if not re.fullmatch(r"rk_(test|live)_[A-Za-z0-9]+", key):
        raise PaymentError("STRIPE_RESTRICTED_KEY doesn't look like a Stripe restricted key (rk_test_... or rk_live_...).")
    return key


def cents(price: str) -> int:
    try:
        p = Decimal(str(price).replace("$", "").replace(",", "").strip())
    except InvalidOperation as e:
        raise PaymentError(f"Price {price!r} is not a number.") from e
    if p < Decimal("0.50") or p > MAX_PRICE_USD:
        raise PaymentError(f"Price must be between $0.50 (Stripe's minimum) and ${MAX_PRICE_USD:,.0f}.")
    return int((p * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _post(key: str, path: str, data: dict, idem: str) -> dict:
    r = requests.post(f"{API}/{path}", data=data, timeout=30,
                      headers={"Authorization": f"Bearer {key}", "Idempotency-Key": idem})
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code >= 400:
        msg = (body.get("error") or {}).get("message") or f"HTTP {r.status_code}"
        raise PaymentError(f"Stripe refused {path.split('/')[0]}: {msg}")
    return body


def _load() -> list[dict]:
    p = state_path(LINKS)
    return json.loads(p.read_text()) if p.exists() else []


def _save(rows: list[dict]) -> None:
    p = state_path(LINKS)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rows, indent=2) + "\n")


def create(key: str, name: str, price: str, interval: str = "one_time", description: str = "",
           redirect: str = "") -> dict:
    name = name.strip()
    if not name or len(name) > 120:
        raise PaymentError("Give the product a name (up to 120 characters).")
    if interval not in INTERVALS:
        raise PaymentError(f"Interval must be one of {', '.join(INTERVALS)}.")
    if redirect and not redirect.startswith("https://"):
        raise PaymentError("The after-payment page must be an https:// address.")
    amount = cents(price)
    run = uuid.uuid4().hex          # one idempotency family per request: a retried call never creates duplicates
    prod = _post(key, "products", {"name": name, **({"description": description.strip()} if description.strip() else {})},
                 f"{run}-product")
    pdata = {"product": prod["id"], "unit_amount": amount, "currency": "usd"}
    if interval != "one_time":
        pdata["recurring[interval]"] = interval
    pr = _post(key, "prices", pdata, f"{run}-price")
    ldata = {"line_items[0][price]": pr["id"], "line_items[0][quantity]": 1}
    if redirect:
        ldata.update({"after_completion[type]": "redirect", "after_completion[redirect][url]": redirect})
    link = _post(key, "payment_links", ldata, f"{run}-link")
    row = {"name": name, "price_usd": amount / 100, "interval": interval, "url": link["url"], "link_id": link["id"],
           "product_id": prod["id"], "price_id": pr["id"], "live": bool(link.get("livemode")), "active": True,
           "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    rows = _load()
    rows.append(row)
    _save(rows)
    return row


def deactivate(key: str, link_id: str) -> dict:
    if not re.fullmatch(r"plink_[A-Za-z0-9]+", link_id or ""):
        raise PaymentError("Give the payment link id (plink_...), from state/payments/links.json.")
    link = _post(key, f"payment_links/{link_id}", {"active": "false"}, f"{uuid.uuid4().hex}-off")
    rows = _load()
    for r in rows:
        if r.get("link_id") == link_id:
            r["active"] = False
            r["deactivated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _save(rows)
    return {"link_id": link_id, "active": bool(link.get("active"))}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("--name", required=True)
    c.add_argument("--price", required=True, help="US dollars, e.g. 19 or 19.99")
    c.add_argument("--interval", default="one_time", choices=INTERVALS)
    c.add_argument("--description", default="")
    c.add_argument("--redirect", default="", help="https:// page to show after payment (optional)")
    d = sub.add_parser("deactivate")
    d.add_argument("--link", required=True)
    a = ap.parse_args(argv)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    try:
        key = check_key(os.environ.get("STRIPE_RESTRICTED_KEY"))
        if a.cmd == "create":
            row = create(key, a.name, a.price, a.interval, a.description, a.redirect)
            every = "" if row["interval"] == "one_time" else f" per {row['interval']}"
            msg = (f"Payment link created ({'LIVE' if row['live'] else 'TEST mode'}): **{row['name']}**, "
                   f"${row['price_usd']:,.2f}{every}\n\n{row['url']}\n\nLink id: `{row['link_id']}`")
        else:
            row = deactivate(key, a.link)
            msg = f"Payment link `{row['link_id']}` is now switched off (it no longer accepts payments)."
    except PaymentError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        if summary:
            with open(summary, "a") as f:
                f.write(f"### Payment link not created\n\n{e}\n")
        return 1
    print(msg)
    if summary:
        with open(summary, "a") as f:
            f.write(msg + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
