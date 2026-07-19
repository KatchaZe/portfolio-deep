"""test_risk_report — the /api/risk payload builder extracted from app.py
(pipeline/risk_report.py). The network seam (fetch_returns) is injected with a
fake provider, so the FULL payload shaping — including the realized/mixed/proxy
covariance decision behind the '0.60 everywhere' bug — is tested offline."""
import os
import sys
import random

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import risk_report


def _series(n, seed):
    random.seed(seed)
    return [random.gauss(0.0005, 0.02) for _ in range(n)]


def _store(tickers):
    s = {"holdings": {}, "facts": {}, "momentum": {}, "updated": {}}
    for i, t in enumerate(tickers):
        s["holdings"][t] = {"shares": 10 + i}
        s["facts"][t] = {"price": 100.0 + i,
                         "sector": "Technology" if i % 2 == 0 else "Healthcare",
                         "currency": "USD", "beta": 1.0 + 0.1 * i}
    return s


def _fake_provider(nmap):
    def fetch_returns(tickers, fmp_key="", quota_used=0, quota_cap=250, **kw):
        data = {}
        for t in tickers:
            n = nmap.get(t, 120)
            rets = _series(n, seed=sum(map(ord, t))) if n else []
            data[t] = {"returns": rets, "vol": None,
                       "source": "stooq" if n else "proxy",
                       "as_of": "2026-07-18" if n else None, "n": len(rets)}
        return data, 0, {"cache": "miss", "key": "test", "quota_degraded": False}
    return fetch_returns


def test_realized_mode():
    tks = ["AAA", "BBB", "CCC"]
    payload, calls = risk_report.build(_store(tks), "", 0, 250, 20.0, 1.0, False,
                                       fetch_returns=_fake_provider({}))
    assert payload["status"] == "ok"
    assert calls == 0
    co = payload["correlation"]
    assert co["meta"]["cov_mode"] == "realized"
    assert co["meta"]["proxy_tickers"] == []
    m = co["matrix"]
    off = [m[i][j] for i in range(3) for j in range(3) if i != j]
    assert any(abs(v - 0.6) > 0.05 for v in off), off   # real corr, never all 0.6
    print("realized mode OK")


def test_mixed_mode_regression_060():
    """THE regression: one thin ticker must NOT drag every pair to 0.60."""
    tks = ["AAA", "BBB", "THIN"]
    payload, _ = risk_report.build(_store(tks), "", 0, 250, 20.0, 1.0, False,
                                   fetch_returns=_fake_provider({"THIN": 0}))
    co = payload["correlation"]
    assert co["meta"]["cov_mode"] == "mixed"
    assert co["meta"]["proxy_tickers"] == ["THIN"]
    order, m = co["order"], co["matrix"]
    ia, ib, it = order.index("AAA"), order.index("BBB"), order.index("THIN")
    assert abs(m[ia][ib] - 0.6) > 0.05, "realized pair must keep its real corr"
    assert abs(m[ia][it] - 0.6) < 1e-6, "thin pair uses the assumed 0.6"
    print("mixed-mode 0.60 regression OK")


def test_insufficient():
    payload, calls = risk_report.build({"holdings": {}, "facts": {}, "momentum": {}},
                                       "", 0, 250, 20.0, 1.0, False,
                                       fetch_returns=_fake_provider({}))
    assert payload["status"] == "insufficient" and calls == 0
    print("insufficient OK")


if __name__ == "__main__":
    test_realized_mode()
    test_mixed_mode_regression_060()
    test_insufficient()
    print("ALL test_risk_report PASSED")
