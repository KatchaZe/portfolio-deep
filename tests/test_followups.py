"""
test_followups — locks the 3 audit follow-ups:
  1. ERP centralised in config + staleness helper.
  2. terminal margin data-anchored to current SEC op margin (clamped), fallback flagged.
  3. validate + engine read the SAME ERP from config.
Pure/synthetic; no network.
"""
import os
import sys
import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from domain.engine import deep_v82 as E
from pipeline import validate as V


def approx(a, b, tol=1e-9):
    return abs(a - b) <= tol


def test_terminal_margin_data_anchored():
    # profitable -> derived from current op margin, clamped to [5%, 40%]
    tm, label, anchored = E.terminal_margin("MSFT", 40e9, 100e9)      # 40% margin
    assert approx(tm, 0.40) and anchored and "from current op margin" in label, (tm, label)
    tm, _, anchored = E.terminal_margin("NVDA", 65e9, 100e9)          # 65% -> capped 40%
    assert approx(tm, 0.40) and anchored, tm
    tm, _, anchored = E.terminal_margin("X", 2e9, 100e9)              # 2% -> floored 5%
    assert approx(tm, 0.05) and anchored, tm
    # pre-profit (negative margin) in the table -> table value, NOT anchored, flagged
    tm, label, anchored = E.terminal_margin("TSLA", -1e9, 100e9)
    assert approx(tm, 0.15) and not anchored and "table" in label, (tm, label)
    # pre-profit, unknown ticker -> generic 25%, flagged
    tm, label, anchored = E.terminal_margin("ZZZZ", None, None)
    assert approx(tm, 0.25) and not anchored and "generic" in label, (tm, label)
    print("terminal margin data-anchored + fallback flags OK")


def test_erp_centralised_and_staleness():
    # one source of truth: engine + validate both read config.ERP
    assert E.ERP == config.ERP, (E.ERP, config.ERP)
    assert V.ERP == config.ERP, (V.ERP, config.ERP)
    # staleness math: 5 months past as-of
    old = E.erp_months_old(as_of="2026-01", today=datetime.date(2026, 6, 1))
    assert old == 5, old
    assert E.erp_months_old(as_of="bad") == 0
    print("ERP centralised + staleness helper OK")


def test_erp_in_key_metrics_and_flag():
    from domain.facts import FinancialFacts
    ff = FinancialFacts("T")
    ff.revenue = 100e9; ff.operating_income = 30e9; ff.net_income = 25e9
    ff.shares_diluted = 1e9; ff.price = 200.0; ff.forward_eps = 8.0
    ff.total_debt = 20e9; ff.cash = 10e9; ff.equity = 50e9
    ff.beta = 1.1; ff.growth_lt = 0.12; ff.capex = 5e9; ff.dep_amort = 4e9
    ff.income_before_tax = 28e9; ff.tax_expense = 5.6e9
    ff.revenue_annuals = [100e9, 88e9, 78e9]
    v = E.DeepV82Engine().evaluate(ff, rf=0.045)
    km = v.key_metrics
    assert km["erp_pct"] == round(config.ERP * 100, 2)
    assert km["erp_as_of"] == config.ERP_AS_OF
    assert km["terminal_margin_anchored"] is True          # 30% op margin -> anchored
    assert approx(km["terminal_margin_pct"], 30.0)
    print("ERP + terminal margin surfaced in key_metrics OK")


if __name__ == "__main__":
    test_terminal_margin_data_anchored()
    test_erp_centralised_and_staleness()
    test_erp_in_key_metrics_and_flag()
    print("\nALL FOLLOW-UP TESTS PASSED")
