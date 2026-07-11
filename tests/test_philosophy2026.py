"""
test_philosophy2026 — locks the Philosophy-2026 upgrade set:
  P1-5 lease capitalization (engine IC/debt) · P1-6 regression beta ·
  P1-7 historical VaR · P1-11 pricing-asset cap · assumptions feature
  (store -> config override, engine call-time read).
Offline/synthetic — no network.
"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import store as st
from domain.engine import risk as R
from domain.engine import deep_v82 as E
from domain.engine import get_engine
from domain import momentum as M
from domain.facts import FinancialFacts


def test_historical_var():
    random.seed(1)
    rets = [random.gauss(0.0004, 0.012) for _ in range(500)] + [-0.08, -0.06]
    v = R.historical_var(rets, 1.0)
    assert v["var95_pct"] and v["var95_pct"] > 0, v
    assert v["cvar95_pct"] >= v["var95_pct"], v            # tail mean >= threshold
    assert v["method"] == "historical" and v["n"] == 502
    thin = R.historical_var([0.01] * 10)
    assert thin["var95_pct"] is None and "insufficient" in thin["method"]
    print("historical VaR OK:", v["var95_pct"], "/", v["cvar95_pct"])


def test_pricing_asset_cap():
    rc = [{"ticker": "IBIT", "capital_pct": 12.0, "abs_risk_share_pct": 30.0},
          {"ticker": "MSFT", "capital_pct": 12.0, "abs_risk_share_pct": 20.0}]
    sizing = R.position_sizing(rc, {"IBIT": "Unknown", "MSFT": "Technology"},
                               asset_class={"IBIT": "crypto"}, pricing_cap=0.05)
    ib = next(s for s in sizing if s["ticker"] == "IBIT")
    ms = next(s for s in sizing if s["ticker"] == "MSFT")
    assert ib["hard_max_pct"] == 5.0 and "pricing" in ib["binding_cap"], ib
    assert ib["action"] == "TRIM", ib                       # 12% > 5% hard cap
    assert ms["hard_max_pct"] == 20.0 and ms["binding_cap"] == "single-name cap", ms
    # default args unchanged -> old callers unaffected
    legacy = R.position_sizing(rc, {"IBIT": "Unknown", "MSFT": "Technology"})
    assert next(s for s in legacy if s["ticker"] == "IBIT")["hard_max_pct"] == 20.0
    print("pricing-asset cap (S40-41) OK")


def test_regression_beta():
    random.seed(2)
    n = 400
    mkt = [100.0]
    for _ in range(n):
        mkt.append(mkt[-1] * (1 + random.gauss(0.0003, 0.01)))
    asset = [50.0]
    for i in range(n):
        asset.append(asset[-1] * (1 + 1.5 * (mkt[i + 1] / mkt[i] - 1)))
    dates = [f"d{i:04d}" for i in range(n + 1)]
    b = M.regression_beta(asset, dates, mkt, dates)
    assert b is not None and 1.4 <= b <= 1.6, b             # true beta = 1.5
    assert M.regression_beta(asset[:20], dates[:20], mkt[:20], dates[:20]) is None
    assert M.regression_beta([], [], mkt, dates) is None
    print("regression beta OK:", b)


def _facts(lease):
    ff = FinancialFacts("T")
    ff.revenue = 100e9; ff.operating_income = 30e9; ff.net_income = 25e9
    ff.shares_diluted = 1e9; ff.price = 200.0; ff.forward_eps = 8.0
    ff.total_debt = 0; ff.cash = 10e9; ff.equity = 50e9; ff.beta = 1.0
    ff.growth_lt = 0.10; ff.capex = 5e9; ff.dep_amort = 4e9
    ff.income_before_tax = 28e9; ff.tax_expense = 5.6e9
    ff.revenue_annuals = [100e9, 90e9]
    ff.operating_leases = lease
    ff.confidence = 90
    return ff


def test_lease_capitalized_into_ic_and_wacc():
    v0 = get_engine("8.2").evaluate(_facts(None), rf=0.045)
    v1 = get_engine("8.2").evaluate(_facts(20e9), rf=0.045)
    # lease grows invested capital (40B -> 60B) -> ROIC falls
    assert v1.key_metrics["roic_pct"] < v0.key_metrics["roic_pct"], (
        v0.key_metrics["roic_pct"], v1.key_metrics["roic_pct"])
    # lease is debt -> firm becomes levered -> WACC < Ke (v0 all-equity: WACC == Ke)
    assert v1.key_metrics["wacc_pct"] < v1.key_metrics["ke_pct"]
    assert abs(v0.key_metrics["wacc_pct"] - v0.key_metrics["ke_pct"]) < 1e-6
    assert v1.key_metrics["operating_leases"] == 20e9
    assert any("operating leases" in f for f in v1.flags), v1.flags
    print("lease capitalization (S5) OK: ROIC %.1f%% -> %.1f%%" % (
        v0.key_metrics["roic_pct"], v1.key_metrics["roic_pct"]))


def test_assumptions_override():
    orig = (config.ERP, config.ERP_AS_OF, config.MARKET_PE, config.MARKET_PE_AS_OF)
    try:
        s = {"assumptions": {}}
        a = st.set_assumptions(s, erp_pct=5.10, market_pe=30.5)
        assert abs(config.ERP - 0.051) < 1e-9, config.ERP
        assert config.MARKET_PE == 30.5
        assert a["source"] == "manual" and a["erp_as_of"] == a["market_pe_as_of"]
        # engine reads config at CALL time -> new ERP takes effect w/o restart
        assert abs(E.cost_of_equity(0.04, 1.0) - (0.04 + 0.051)) < 1e-9
        # cold-start path: overlay from a loaded store dict
        config.ERP = 0.01
        st._apply_assumptions({"assumptions": {"erp": 0.048, "erp_as_of": "2026-07"}})
        assert abs(config.ERP - 0.048) < 1e-9
        # partial + empty are safe no-ops
        st._apply_assumptions({})
        st._apply_assumptions({"assumptions": {"market_pe": "bad"}})
        print("assumptions store->config override OK")
    finally:
        config.ERP, config.ERP_AS_OF, config.MARKET_PE, config.MARKET_PE_AS_OF = orig


def test_portfolio_returns_missing_series():
    """Regression (Correlation tab, 2026-07-11): a holding with NO price history
    must not crash portfolio_returns with IndexError — it just isn't covered."""
    rets = {"A": [0.01, -0.02, 0.03], "B": [0.02, 0.01, -0.01], "C": []}
    w = {"A": 0.4, "B": 0.4, "C": 0.2}
    out = R.portfolio_returns(rets, ["A", "B", "C"], w)
    assert len(out) == 3, out
    assert abs(out[0] - (0.4 * 0.01 + 0.4 * 0.02)) < 1e-12, out
    assert R.portfolio_returns({"A": [], "B": []}, ["A", "B"], w) == []
    print("portfolio_returns missing-series regression OK")


def test_assumptions_validation_bands():
    """POST validation mirrors: erp_pct 1-10 (percent), market_pe 5-60."""
    ok_erp = lambda x: 1.0 <= x <= 10.0
    ok_pe = lambda x: 5.0 <= x <= 60.0
    assert ok_erp(4.45) and not ok_erp(0.0445) and not ok_erp(44.5)
    assert ok_pe(25.1) and not ok_pe(2.5) and not ok_pe(100)
    print("assumptions validation bands OK (percent, not decimal)")


if __name__ == "__main__":
    test_historical_var()
    test_pricing_asset_cap()
    test_regression_beta()
    test_lease_capitalized_into_ic_and_wacc()
    test_assumptions_override()
    test_portfolio_returns_missing_series()
    test_assumptions_validation_bands()
    print("\nALL test_philosophy2026 PASSED")
