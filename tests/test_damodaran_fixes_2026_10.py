"""Regression checks for the 2026-10 Damodaran audit fixes (one section per item)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._harness import check, run_suite   # noqa: E402


def _fact(start, end, val, filed=None):
    return {"start": start, "end": end, "val": val, "filed": filed or end, "form": "10-Q"}


def _companyfacts(extra):
    rev = [_fact("2025-01-01", "2025-12-31", 100e9), _fact("2026-01-01", "2026-06-30", 60e9),
           _fact("2025-01-01", "2025-06-30", 50e9)]
    ns = {"RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": rev}}}
    ns.update(extra)
    return {"facts": {"us-gaap": ns}}


def test_f1_dead_tag_never_wins():
    from sources import sec_edgar as S
    dead = [_fact("2020-01-01", "2020-12-31", 0.455e9)]
    live = [_fact("2025-01-01", "2025-12-31", 6.0e9), _fact("2026-01-01", "2026-06-30", 3.5e9),
            _fact("2025-01-01", "2025-06-30", 2.9e9)]
    d = S.extract(_companyfacts({
        "PaymentsToAcquirePropertyPlantAndEquipment": {"units": {"USD": dead}},
        "PaymentsToAcquireProductiveAssets": {"units": {"USD": live}}}))
    check(abs(d["capex"] - 6.6e9) < 1, "capex comes from the tag still being filed (6.6B), "
          "not the first ladder tag frozen in 2020", "got %r" % d["capex"])
    d = S.extract(_companyfacts({"PaymentsToAcquirePropertyPlantAndEquipment": {"units": {"USD": dead}}}))
    check(d["capex"] is None, "a tag dead for years reads as missing, not as today's value",
          "got %r" % d["capex"])
    d = S.extract(_companyfacts({"InterestExpenseNonoperating": {"units": {"USD": live}}}))
    check(d["interest_expense"] is not None, "InterestExpenseNonoperating is on the interest ladder")


def _inst(end, val):
    return {"end": end, "val": val, "filed": end, "form": "10-Q"}


def test_f4_cash_includes_marketable_securities_on_one_date():
    from sources import sec_edgar as S
    cash = {"units": {"USD": [_inst("2026-06-30", 55.9e9), _inst("2025-12-31", 30.7e9)]}}
    secs = {"units": {"USD": [_inst("2026-06-30", 186.6e9), _inst("2025-12-31", 64.4e9)]}}
    d = S.extract(_companyfacts({"CashAndCashEquivalentsAtCarryingValue": cash,
                                 "MarketableSecuritiesCurrent": secs}))
    check(abs(d["cash"] - 242.5e9) < 1, "cash = cash & equivalents + marketable securities (GOOGL 242.5B)",
          "got %r" % d["cash"])
    check(abs(d["cash_prior"] - 95.1e9) < 1, "the prior year adds securities on its own date too",
          "got %r" % d["cash_prior"])
    stale = {"units": {"USD": [_inst("2025-10-26", 49.1e9)]}}
    d = S.extract(_companyfacts({"CashAndCashEquivalentsAtCarryingValue": cash,
                                 "MarketableSecuritiesCurrent": stale}))
    check(abs(d["cash"] - 55.9e9) < 1, "a securities leg from another date is never added (NVDA)",
          "got %r" % d["cash"])
    comb = {"units": {"USD": [_inst("2026-06-30", 76.8e9)]}}
    d = S.extract(_companyfacts({"CashAndCashEquivalentsAtCarryingValue": cash,
                                 "CashCashEquivalentsAndShortTermInvestments": comb,
                                 "ShortTermInvestments": secs}))
    check(abs(d["cash"] - 76.8e9) < 1, "a filed combined total wins over summing legs (MSFT)",
          "got %r" % d["cash"])


def _ff(**kw):
    from domain.facts import FinancialFacts
    f = FinancialFacts("T")
    base = dict(revenue=50e9, net_income=15e9, operating_income=20e9, shares_diluted=4.4e9,
                income_before_tax=20e9, tax_expense=4e9, forward_eps=3.4)
    for k, v in {**base, **kw}.items():
        f.set(k, v, "test")
    f.set("growth_lt", kw.get("growth_lt", 0.195), "sec-cagr(min 3y/5y)")
    return f


def test_f2_growth_is_forward_looking():
    from pipeline import normalize as N
    f = _ff(fwd_growth_near=0.20, fwd_growth_far=0.10, n_analysts=30)
    N.resolve_growth(f)
    check(abs(f.growth_lt - 0.15) < 1e-9 and "consensus" in f.provenance["growth_lt"],
          "a covered consensus revenue path replaces historical CAGR", repr(f.growth_lt))
    f = _ff(fwd_growth_near=0.20, fwd_growth_far=0.10, n_analysts=2)
    N.resolve_growth(f)
    check(f.growth_lt != 0.15, "two analysts are not a consensus")
    f = _ff()                                   # NVO: forward 3.40 < core 3.41
    N.resolve_growth(f)
    check(f.growth_lt == 0.0 and any("below core trailing EPS" in x for x in f.flags),
          "forward EPS below core trailing EPS -> 0% growth, flagged (NVO 19.5% case)", repr(f.growth_lt))
    f = _ff(forward_eps=15.0)                   # adjusted >> GAAP never raises growth
    N.resolve_growth(f)
    check(f.growth_lt == 0.195 and any("historical revenue CAGR" in x for x in f.flags),
          "with no forward growth, history is used and labelled historical")
    f = _ff(fwd_growth_near=0.2, n_analysts=30)
    f.set("growth_lt", 0.11, "yahoo")
    N.resolve_growth(f)
    check(f.growth_lt == 0.11, "an analyst 5y growth already set is kept")


def test_f3_fundamental_peg_prices_today_not_next_year():
    from domain.engine import deep_v82 as E
    g, n, gs, ke, rh, rs, e0 = 0.20, 5, 0.04, 0.10, 0.30, 0.15, 1.0
    # an independent dividend discount model: P0 = PV(dividends 1..n) + PV(terminal price)
    ph, ps = 1 - g / rh, 1 - gs / rs
    eps = [e0 * (1 + g) ** t for t in range(1, n + 1)]
    pv = sum(ph * e / (1 + ke) ** t for t, e in enumerate(eps, 1))
    pv += ps * eps[-1] * (1 + gs) / (ke - gs) / (1 + ke) ** n
    fv, d = E.fundamental_peg_price(g, gs, ke, rh, rs, forward_eps=e0 * (1 + g), years=n)
    check(abs(fv - pv) < 1e-6, "Fundamental PEG on forward EPS equals the DDM price today",
          "fv %.4f vs DDM %.4f" % (fv, pv))
    check(abs(d["fair_pe"] * e0 * (1 + g) - fv) < 0.01,
          "the fair P/E shown is the multiple actually applied to forward EPS")


def test_f5_advice_never_buys_what_it_just_called_expensive():
    from domain import advice
    msft = dict(upside_pct=-34, rev_implied_cagr=19.1, rev_verdict="Plausible",
                key_metrics={"spread_pct": 16.3}, eq_verdict="REVIEW",
                momentum_v2={"mom_label": "Strong"}, composite=3.2)
    a = advice.build(msft)
    check("ซื้อ" not in a.split("Action:")[1] and "สะสม" not in a.split("Action:")[1],
          "an anchor 34% below price is not a buy, whatever the RevDCF verdict", a)
    hims = dict(upside_pct=25, key_metrics={"spread_pct": None}, eq_verdict="CLEAN",
                momentum_v2={"mom_label": "Strong"}, composite=2.8)
    a = advice.build(hims)
    check("DCA" not in a and "ซื้อ" not in a.split("Action:")[1],
          "an unmeasured ROIC-WACC spread does not pass the quality test", a)
    pre = dict(upside_pct=None, rev_implied_cagr=20, rev_verdict="Plausible", rev_actual_1y=30,
               key_metrics={"spread_pct": 5}, eq_verdict="CLEAN", momentum_v2={"mom_label": "Strong"})
    check("ซื้อ" in advice.build(pre).split("Action:")[1],
          "with no anchor, a plausible RevDCF still decides value (pre-profit path)")


def test_n1_spread_table_is_january_2026():
    from domain.engine import deep_v82 as E
    check(E._spread_from_coverage(9.0) == 0.0040, "coverage >= 8.5 -> Aaa 0.40% (Jan 2026)")
    check(E._spread_from_coverage(3.5) == 0.0089, "coverage 3.0-4.25 -> A3 0.89%")
    check(E._spread_from_coverage(2.7) == 0.0111, "coverage 2.5-3.0 -> Baa2 1.11%")
    check(E._spread_from_coverage(0.1) == 0.19, "coverage < 0.2 -> D 19%")


if __name__ == "__main__":
    run_suite(globals(), "ALL test_damodaran_fixes_2026_10 PASSED")
