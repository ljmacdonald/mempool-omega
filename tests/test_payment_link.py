"""Payment links: only restricted keys, sane prices, correct Stripe calls, links recorded without secrets."""
import json

import pytest

from scripts import payment_link as P


class _Resp:
    def __init__(self, status, body):
        self.status_code, self._body, self.headers = status, body, {"content-type": "application/json"}

    def json(self):
        return self._body


@pytest.fixture
def stripe(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(P, "state_path", lambda p: tmp_path / p)

    def post(url, data, timeout, headers):
        calls.append((url.rsplit("/v1/", 1)[1], dict(data), headers))
        path = url.rsplit("/v1/", 1)[1]
        if path == "products":
            return _Resp(200, {"id": "prod_1"})
        if path == "prices":
            return _Resp(200, {"id": "price_1"})
        if path == "payment_links":
            return _Resp(200, {"id": "plink_1", "url": "https://buy.stripe.com/test_abc", "livemode": False})
        if path.startswith("payment_links/"):
            return _Resp(200, {"id": "plink_1", "active": False})
        return _Resp(404, {"error": {"message": "no"}})
    monkeypatch.setattr(P.requests, "post", post)
    return calls, tmp_path


def test_only_restricted_keys():
    with pytest.raises(P.PaymentError, match="full Stripe secret key"):
        P.check_key("sk_live_abc123")
    with pytest.raises(P.PaymentError, match="not set"):
        P.check_key("")
    with pytest.raises(P.PaymentError):
        P.check_key("pk_live_abc")
    assert P.check_key(" rk_test_abc123 ") == "rk_test_abc123"


def test_prices():
    assert P.cents("19") == 1900 and P.cents("$19.99") == 1999 and P.cents("0.505") == 51
    for bad in ("0.10", "abc", "20000"):
        with pytest.raises(P.PaymentError):
            P.cents(bad)


def test_create_monthly_link(stripe):
    calls, tmp = stripe
    row = P.create("rk_test_x", "Omega Pro", "19.00", "month", "All pages", "https://example.com/thanks")
    assert [c[0] for c in calls] == ["products", "prices", "payment_links"]
    assert calls[1][1] == {"product": "prod_1", "unit_amount": 1900, "currency": "usd", "recurring[interval]": "month"}
    assert calls[2][1]["after_completion[redirect][url]"] == "https://example.com/thanks"
    assert all(c[2]["Authorization"] == "Bearer rk_test_x" for c in calls)
    assert len({c[2]["Idempotency-Key"] for c in calls}) == 3
    saved = json.loads((tmp / "payments/links.json").read_text())
    assert saved[0]["url"] == row["url"] == "https://buy.stripe.com/test_abc" and saved[0]["live"] is False
    assert "rk_test_x" not in json.dumps(saved)


def test_one_time_has_no_recurring_and_bad_inputs_refused(stripe):
    calls, _ = stripe
    P.create("rk_test_x", "Report", "5", "one_time")
    assert "recurring[interval]" not in calls[1][1]
    with pytest.raises(P.PaymentError):
        P.create("rk_test_x", "X", "5", "week")
    with pytest.raises(P.PaymentError):
        P.create("rk_test_x", "X", "5", redirect="http://insecure.example")


def test_deactivate(stripe):
    _, tmp = stripe
    P.create("rk_test_x", "Omega Pro", "19", "month")
    out = P.deactivate("rk_test_x", "plink_1")
    assert out == {"link_id": "plink_1", "active": False}
    assert json.loads((tmp / "payments/links.json").read_text())[0]["active"] is False
    with pytest.raises(P.PaymentError):
        P.deactivate("rk_test_x", "not-a-link")


def test_stripe_error_is_reported(monkeypatch):
    monkeypatch.setattr(P.requests, "post", lambda *a, **k: _Resp(403, {"error": {"message": "The provided key does not have the required permissions"}}))
    with pytest.raises(P.PaymentError, match="required permissions"):
        P.create("rk_test_x", "Omega Pro", "19")


def test_cli_refuses_secret_key(monkeypatch, capsys):
    monkeypatch.setenv("STRIPE_RESTRICTED_KEY", "sk_test_abc")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert P.main(["create", "--name", "X", "--price", "5"]) == 1
    assert "restricted key" in capsys.readouterr().err
