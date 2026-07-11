"""
test_engine_v82 — locks the v8.2 finance fixes so they can't silently regress.
Offline/synthetic — no network.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from domain.engine import get_engine, available_versions
from domain.engine import deep_v82 as E
from domain.facts import FinancialFacts


def approx(a, b, tol=1e-6):
    return abs(a - b) <= tol


def test_cost_of_equity_and_erp():
    assert approx(E.ERP, 0.0445), E.ERP          # Damodaran US ERP, 2026-07 refresh
    assert approx(E.cost_of_equity(0.045, 1.0), 0.045 + 0.0445)
    print("cost of equity + ERP 4.45% OK")


def test_true_wacc_weights_debt():
    w, ke, kd, note = E.wacc_true(0.045, 1.10, equity_mktcap=200e9, total_debt=50e9,
                                  cash=0, tax=0.21, interest_expense=1e9, operating_income=30e9)
    assert ke > w, (ke, w)
    assert kd is not None and kd > 0.045, kd
    w2, ke2, kd2, note2 = E.wacc_true(0.045, 1.10, 200e9, 0, 0, 0.21, None, None)
    assert approx(w2, ke2) and "WACC=Ke" in note2, note2
    print("true WACC weights debt + collapses to Ke OK")


def test_ev_bridge_reverse_dcf():
    out = E.reverse_dcf(price=150, shares=3.0e9, revenue=8e9, rev_1y=6e9,
                        total_debt=5e9, cash=20e9, wacc_val=0.10, g=0.043,
                        tax=0.21, margin=0.20)
    assert out["triggered"]
    assert approx(out["enterprise_value"], 150 * 3.0e9 + 5e9 - 20e9, tol=1.0), out["enterprise_value"]
    print("EV-bridge reverse DCF OK (EV = mktcap + debt - cash)")


def test_rd_capitalization():
    rd = E.rd_capitalize([5e9, 4e9, 3e9, 2e9, 1e9, 1e9], reported_oi=8e9, reported_ic=30e9, tax=0.21)
    assert rd is not None
    adj_oi, adj_ic, adj_roic = rd
    assert adj_ic > 30e9 and adj_oi != 8e9 and adj_roic > 0
    assert E.rd_capitalize([], 8e9, 30e9, 0.21) is None
    print("R&D capitalization OK")


def test_two_stage_pe_clamp():
    price, detail = E.fundamental_peg_price(g_high=0.12, g_stable=0.04, ke=0.07,
                                            roic_high=0.30, roic_stable=0.15, forward_eps=10.0)
    assert price is not None
    assert E.PE_FLOOR <= detail["fair_pe"] <= E.PE_CEIL, detail
    assert E.fundamental_peg_price(0.12, 0.04, 0.09, 0.3, 0.15, 0)[0] is None
    print("fundamental 2-stage PEG + clamp OK")


def test_earnings_quality():
    assert E.earnings_quality(1.0e9, 1.2e9, 40e9, 0.05e9, 18e9)[0] == "CLEAN"
    v, flags, cc = E.earnings_quality(1.0e9, 0.5e9, 40e9, 3e9, 18e9)
    assert v in ("REVIEW", "LOW") and flags, (v, flags)
    assert E.earnings_quality(1.0e9, None, None, None, None)[0] is None
    print("earnings quality OK")


def _facts():
    ff = FinancialFacts("TEST")
    ff.revenue = 100e9; ff.operating_income = 30e9; ff.net_income = 25e9
    ff.shares_diluted = 1e9; ff.price = 200.0; ff.forward_eps = 8.0
    ff.total_debt = 20e9; ff.cash = 10e9; ff.equity = 50e9
    ff.beta = 1.1; ff.growth_lt = 0.12
    ff.capex = 5e9; ff.dep_amort = 4e9
    ff.income_before_tax = 28e9; ff.tax_expense = 5.6e9
    ff.revenue_annuals = [100e9, 88e9, 78e9, 70e9]
    ff.cfo = 27e9; ff.total_assets = 120e9; ff.interest_expense = 1e9; ff.sbc = 3e9
    ff.rnd_annuals = [6e9, 5e9, 4e9, 3e9, 2e9, 2e9]
    ff.operating_income_annuals = [30e9, 26e9, 22e9, 19e9]
    ff.equity_prior = 45e9; ff.total_debt_prior = 22e9; ff.cash_prior = 9e9
    ff.acquisitions_net = 2e9; ff.deferred_revenue = 12e9; ff.deferred_revenue_prior = 9e9
    ff.fwd_growth_near = 0.14; ff.fwd_growth_far = 0.10; ff.n_analysts = 20
    ff.peer_median_growth = 0.10
    ff.own_pe_pctile = 0.20
    ff.confidence = 90
    return ff


def test_engine_end_to_end():
    v = get_engine("8.2").evaluate(_facts(), rf=0.045)
    assert v.version == "8.2"
    for s in (v.D, v.E_exec, v.E_econ, v.P):
        assert s is None or 0 <= s <= 5, s
    assert v.composite is not None and 0 <= v.composite <= 5
    assert v.recommendation and v.verdict and v.signal in ("BUY", "HOLD", "SELL")
    assert v.cost_of_equity and approx(v.cost_of_equity, 0.045 + 1.1 * 0.0445, tol=1e-4)
    km = v.key_metrics
    assert km["wacc_pct"] < km["ke_pct"]
    assert v.eq_verdict in ("CLEAN", "REVIEW", "LOW")
    assert v.anchor_value is not None
    assert "breakdown" in v.subscores and v.subscores["breakdown"]
    print(f"end-to-end OK: {v.recommendation} comp {v.composite} "
          f"WACC {km['wacc_pct']}% < Ke {km['ke_pct']}% anchor {v.anchor_method} ${v.anchor_value}")


def test_registry():
    assert "8.2" in available_versions()
    assert get_engine("8.2").version == "8.2"
    print("registry exposes 8.2 OK")


def test_consensus_path():
    from sources import fmp
    est = [
        {"date": "2025-12-31", "revenueAvg": 100, "numberAnalystsEstimatedRevenue": 20},
        {"date": "2026-12-31", "revenueAvg": 120, "numberAnalystsEstimatedRevenue": 18},
        {"date": "2027-12-31", "revenueAvg": 132, "numberAnalystsEstimatedRevenue": 15},
    ]
    out = fmp.parse_estimate_path(est, latest_fy="2024-12-31")
    assert approx(out["fwd_growth_near"], 0.20, 1e-3), out
    assert approx(out["fwd_growth_far"], 0.10, 1e-3), out
    assert out["n_analysts"] == 20
    assert fmp.parse_estimate_path([], None) == {}
    print("consensus path (FMP estimates) OK")


def test_peer_medians():
    from pipeline.refresh import compute_peer_medians

    def mk(sec, g):
        ff = FinancialFacts("x"); ff.sector = sec; ff.revenue_annuals = [100 * (1 + g), 100]
        return (ff, None)
    fetched = {"A": mk("Tech", 0.30), "B": mk("Tech", 0.10), "C": mk("Tech", 0.20), "D": mk("Health", 0.05)}
    med = compute_peer_medians(fetched)
    assert approx(med["A"], 0.15, 1e-6), med
    assert "D" not in med
    print("peer-median (sector cohort) OK")


def test_demand_adjustments():
    notes = []
    s1 = E._r_demand(0.30, 0.10, 0.02, 0.8, notes)
    assert approx(s1, 4.75, 1e-6), s1
    notes = []
    s2 = E._r_demand(0.16, 0.30, 0.15, 0.30, notes)
    assert approx(s2, 1.0, 1e-6), s2
    print("demand rubric adjustments (organic/peer/durability) OK")


def test_pe_percentile():
    from sources import yahoo
    eps = [["2024-12-31", 5.0], ["2023-12-31", 4.0]]
    dates = ["2023-06-30", "2023-12-31", "2024-06-30", "2024-12-31"]
    closes = [36.0, 40.0, 80.0, 100.0]
    pct = yahoo.pe_percentile_5y(eps, closes, dates, price=110.0, current_eps=5.0)
    assert approx(pct, 1.0, 1e-6), pct
    assert yahoo.pe_percentile_5y([], closes, dates, 110, 5) is None
    print("own 5y P/E percentile OK")


def test_price_adjustment():
    notes = []
    s = E._r_price(150, 100, 0.20, notes)
    assert approx(s, 5.0), s
    notes = []
    s2 = E._r_price(90, 100, 0.90, notes)
    assert approx(s2, 2.5), s2
    print("price own-multiple adjustment OK")


if __name__ == "__main__":
    test_cost_of_equity_and_erp()
    test_true_wacc_weights_debt()
    test_ev_bridge_reverse_dcf()
    test_rd_capitalization()
    test_two_stage_pe_clamp()
    test_earnings_quality()
    test_engine_end_to_end()
    test_registry()
    test_consensus_path()
    test_peer_medians()
    test_demand_adjustments()
    test_pe_percentile()
    test_price_adjustment()
    print("\nALL v8.2 ENGINE TESTS PASSED OK")
