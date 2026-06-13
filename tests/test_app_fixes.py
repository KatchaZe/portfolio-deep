"""
test_app_fixes — hardening round 2 (the freeze/crash fixes):
  * what-if allocation never crashes on bad amounts (old: unhandled 500)
  * quota guard skips tickers ONLY when an FMP key exists (key-less = 0 calls)
  * fetch/commit split: commit_fundamentals + commit_daily merge into the store
  * engine verdict never prints "$None" / "None%" when data is missing
  * auth_ok: constant-time token check; disabled when no APP_TOKEN
  * fetch_treasury_10y returns (rate, live_bool) and falls back safely
Offline/synthetic — no network.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import store
from pipeline import refresh
from domain.facts import FinancialFacts
from domain.engine import get_engine
from domain.engine.contract import Valuation
from sources import yahoo


def _fresh_store():
    import json
    return json.loads(json.dumps(store._DEFAULT))


def test_whatif_bad_amount_safe():
    s = _fresh_store()
    store.set_holding(s, "NVDA", 10, 100.0)
    bad = [{"ticker": "NVDA", "amount": "xx"},        # old code: ValueError -> 500
           {"ticker": "MSFT", "amount": 500},
           {"ticker": "", "amount": 100},
           None if False else {"ticker": "AAPL", "amount": None}]
    res, calls = refresh.allocation(s, bad, fmp_key="")
    added = {a["ticker"] for a in res.get("added", [])}
    assert added == {"MSFT"}, added
    assert "NVDA" in res.get("skipped", []), res.get("skipped")
    assert calls == 0
    print("what-if bad amount safe OK:", res.get("added"), "skipped", res.get("skipped"))


def test_quota_guard_keyless(monkeypatched=None):
    # monkeypatch the network bits
    orig_rf, orig_an = yahoo.fetch_treasury_10y, refresh.analyze
    refresh.yahoo.fetch_treasury_10y = lambda **k: (0.045, True)

    def fake_analyze(t, rf, fmp_key="", rf_live=True):
        ff = FinancialFacts(t)
        v = Valuation(version="7.3")
        return ff, v, (1 if fmp_key else 0)
    refresh.analyze = fake_analyze
    try:
        # no key, quota already at cap -> must NOT skip (zero FMP calls happen)
        fetched, errors, calls, rf_pct, rf_live = refresh.fetch_fundamentals(
            ["NVDA", "MSFT"], fmp_key="", quota_used=250, quota_cap=250)
        assert sorted(fetched) == ["MSFT", "NVDA"] and calls == 0 and not errors, (errors, calls)
        # with a key at cap -> every ticker skipped with (quota)
        fetched2, errors2, calls2, _, _ = refresh.fetch_fundamentals(
            ["NVDA", "MSFT"], fmp_key="k", quota_used=250, quota_cap=250)
        assert not fetched2 and calls2 == 0 and all("(quota)" in e for e in errors2), errors2
        assert rf_pct == 4.5 and rf_live is True
    finally:
        refresh.yahoo.fetch_treasury_10y = orig_rf
        refresh.analyze = orig_an
    print("quota guard keyless OK")


def test_commit_split_merges_store():
    s = _fresh_store()
    ff = FinancialFacts("NVDA", company="NVIDIA")
    ff.revenue_quarters = {}
    val = Valuation(version="7.3", composite=4.2)
    refresh.commit_fundamentals(s, {"NVDA": (ff, val)}, fmp_calls=3)
    assert s["facts"]["NVDA"]["company"] == "NVIDIA"
    assert s["results"]["NVDA"]["composite"] == 4.2
    assert "NVDA" in s["updated"]
    assert store.fmp_used_today(s) == 3
    n = refresh.commit_daily(s, {"NVDA": {"price": 100.0, "momentum_signal": "Bullish"}})
    assert n == ["NVDA"] and s["momentum"]["NVDA"]["price"] == 100.0
    print("commit split merges store OK")


def test_verdict_never_prints_none():
    eng = get_engine("7.3")
    # totally empty facts -> the old code printed "$None" / "None%"
    v = eng.evaluate(FinancialFacts("XXXX"), rf=0.045)
    assert "None" not in (v.verdict or ""), v.verdict
    # facts rich enough to trigger RevDCF but with no point fair value
    ff = FinancialFacts("YYYY")
    ff.price, ff.shares_diluted, ff.revenue = 50.0, 1e9, 5e9
    ff.revenue_annuals = [5e9, 4e9, 3e9, 2.5e9]
    v2 = eng.evaluate(ff, rf=0.045)
    assert "None" not in (v2.verdict or ""), v2.verdict
    # generic-margin flag should appear for a ticker outside TERMINAL_MARGIN
    assert any("terminal margin default" in f for f in v2.flags), v2.flags
    print("verdict no-None OK:", v2.verdict[:70], "…")


def test_auth_ok():
    from app import auth_ok
    assert auth_ok(None, "") is True            # auth disabled
    assert auth_ok("anything", "") is True
    assert auth_ok("secret", "secret") is True
    assert auth_ok("wrong", "secret") is False
    assert auth_ok(None, "secret") is False
    assert auth_ok("", "secret") is False
    print("auth_ok OK")


def test_treasury_fallback_shape():
    class _Boom:
        def get(self, *a, **k):
            raise RuntimeError("no network")
    rf, live = yahoo.fetch_treasury_10y(requests_mod=_Boom())
    assert rf == 0.043 and live is False
    print("treasury fallback shape OK:", rf, live)


if __name__ == "__main__":
    test_whatif_bad_amount_safe()
    test_quota_guard_keyless()
    test_commit_split_merges_store()
    test_verdict_never_prints_none()
    test_auth_ok()
    test_treasury_fallback_shape()
    print("\nALL APP-FIX TESTS PASSED ✅")
