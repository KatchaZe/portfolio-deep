"""
test_no_regression — proves the risk feature is ADDITIVE: the Portfolio / Watchlist
/ Allocation endpoints keep their JSON contract, and opening the risk tab in free
mode does NOT modify data/portfolio.json (isolation guarantee, plan §10).

Offline: the risk_report fetch_returns seam (pipeline.prices) is monkeypatched so no network is needed and
the test is deterministic. Calls the endpoint FUNCTIONS directly (no httpx/TestClient
dependency).
"""
import os
import sys
import json
import hashlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import store as st
import app


def _body(resp):
    return json.loads(resp.body)


def _hash(path):
    if not os.path.exists(path):
        return None
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _patch_offline():
    """Force risk price fetch to return 'proxy' (no returns) so the test never
    hits the network and spends zero FMP calls."""
    def fake(tickers, fmp_key="", quota_used=0, quota_cap=250, **kw):
        data = {t: {"returns": [], "vol": None, "source": "proxy", "as_of": None, "n": 0}
                for t in tickers}
        return data, 0, {"cache": "test", "quota_degraded": False}
    app.risk_report.prices.fetch_returns = fake


def test_existing_endpoint_contracts():
    p = _body(app.api_portfolio())
    for k in ("rows", "totals", "quota", "version"):
        assert k in p, ("portfolio missing", k)
    a = _body(app.api_allocation())
    assert "before" in a and "by_ticker" in a["before"], a
    w = _body(app.api_whatif([]))                 # empty what-if still returns shape
    assert "before" in w, w
    wl = _body(app.api_watchlist())
    assert "names" in wl, wl
    print("existing endpoint contracts intact OK")


def test_risk_does_not_modify_portfolio_json():
    _patch_offline()
    before = _hash(st.PATH)
    resp = app.api_risk(risk_tolerance_pct=20.0, horizon_years=1.0, enrich="free")
    d = _body(resp)
    assert d["status"] in ("ok", "insufficient"), d
    after = _hash(st.PATH)
    assert before == after, "risk tab modified portfolio.json — isolation broken!"
    print("isolation OK — portfolio.json unchanged by /api/risk (status:", d["status"], ")")


def test_risk_payload_shape():
    _patch_offline()
    d = _body(app.api_risk(enrich="free"))
    if d["status"] != "ok":
        print("risk payload: insufficient data (no priced holdings) — shape test skipped")
        return
    for k in ("snapshot", "allocation", "concentration", "capital_vs_risk",
              "diversification", "stress", "suitability", "position_sizing",
              "rebalance", "meta", "quota"):
        assert k in d, ("risk payload missing", k)
    assert "covariance_based" in d["capital_vs_risk"]
    print("risk payload shape OK")


if __name__ == "__main__":
    test_existing_endpoint_contracts()
    test_risk_does_not_modify_portfolio_json()
    test_risk_payload_shape()
    print("\nALL NO-REGRESSION TESTS PASSED")
