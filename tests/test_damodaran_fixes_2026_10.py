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
    from sources import fmp
    f = _ff(fwd_growth_y1=0.20, fwd_growth_near=0.10, fwd_growth_far=0.33, n_analysts=30, n_analysts_y2=25)
    N.resolve_growth(f)
    check(abs(f.growth_lt - 0.15) < 1e-9 and "consensus" in f.provenance["growth_lt"],
          "consensus = mean of next-FY and the year after; the thin out-year is not used", repr(f.growth_lt))
    f = _ff(fwd_growth_y1=0.20, fwd_growth_near=0.10, n_analysts=2, n_analysts_y2=2)
    N.resolve_growth(f)
    check(f.growth_lt != 0.15, "two analysts are not a consensus")
    f = _ff()                                   # NVO: forward 3.40 vs core 3.41
    N.resolve_growth(f)
    check(f.growth_lt == 0.0 and any("capped" in x for x in f.flags),
          "forward EPS below core trailing EPS caps growth at 0%, flagged (NVO 19.5% case)", repr(f.growth_lt))
    lo, hi = _ff(forward_eps=3.48 * 4.4e9 / 4.4e9), _ff(forward_eps=3.50)
    N.resolve_growth(lo); N.resolve_growth(hi)
    check(abs(hi.growth_lt - lo.growth_lt) < 0.01, "a 2-cent EPS revision moves growth by < 1pt, not 0% <-> 19.5%",
          "%r vs %r" % (lo.growth_lt, hi.growth_lt))
    f = _ff(forward_eps=15.0)                   # adjusted >> GAAP never raises growth
    N.resolve_growth(f)
    check(f.growth_lt == 0.195 and any("historical revenue CAGR" in x for x in f.flags),
          "with no forward growth, history is used and labelled historical")
    f = _ff(fwd_growth_y1=0.2, n_analysts=30)
    f.set("growth_lt", 0.11, "yahoo")
    N.resolve_growth(f)
    check(f.growth_lt == 0.11, "an analyst 5y growth already set is kept")
    rows = [{"date": d, "revenueAvg": r, "numberAnalystsEstimatedRevenue": n}
            for d, r, n in (("2027-06-30", 360, 30), ("2028-06-30", 396, 25), ("2029-06-30", 436, 12),
                            ("2030-06-30", 580, 2))]
    p = fmp.parse_estimate_path(rows, "2026-06-30", latest_revenue=300)
    check(p.get("fwd_growth_y1") == 0.2 and p.get("n_analysts_y2") == 25,
          "the path now measures next-FY growth against the last actual year", repr(p))


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


def test_n2_bottom_up_beta():
    from domain.engine import deep_v82 as E
    from domain.facts import FinancialFacts
    from sources import damodaran as D
    check(abs(E.relevered_beta(0.92, 20e9, 200e9) - 0.92 * (1 + 0.75 * 0.10)) < 1e-12,
          "beta_L = beta_U x (1 + (1 - 25%) x D/E)")
    bu, ind = D.industry_beta_for("NVO", "Healthcare")
    check(bu == 0.92 and ind == "Drugs (Pharmaceutical)", "NVO maps to pharma, unlevered (cash-corrected) 0.92")
    check(D.parse_beta_html("<table><tr><td>Industry Name</td><td>Beta</td>"
                            "<td>Unlevered beta corrected for cash</td></tr>"
                            "<tr><td>Semiconductor</td><td>1.52</td><td>1.50</td></tr></table>")
          == {"semiconductor": 1.50}, "the live page is read by header text")
    f = FinancialFacts("NVO", beta=0.349, beta_unlevered_industry=0.92, beta_industry="Drugs (Pharmaceutical)",
                       market_cap=196.6e9, total_debt=20.3e9, operating_income=19.8e9)
    b = E._bottom_up_beta(f, [])
    check(abs(b - 0.92 * (1 + 0.75 * 20.3 / 196.6)) < 1e-9 and any("bottom-up" in x for x in f.flags),
          "a profitable firm runs on the re-levered industry beta, flagged", repr(b))
    young = FinancialFacts("RKLB", beta=2.1, beta_unlevered_industry=0.87, market_cap=40e9,
                           total_debt=0.4e9, operating_income=-0.2e9)
    check(E._bottom_up_beta(young, []) == 2.1, "a pre-profit firm keeps the higher regression beta")
    tmdx = FinancialFacts("TMDX", beta=1.88, beta_unlevered_industry=0.86, market_cap=3e9, total_debt=0.5e9,
                          operating_income=0.08e9, revenue=0.6e9,
                          revenue_annuals=[0.605e9, 0.44e9, 0.24e9, 0.094e9])
    check(E._bottom_up_beta(tmdx, []) == 1.88, "a small firm growing revenue >30%/yr keeps the higher beta")
    axon = FinancialFacts("AXON", beta=1.40, beta_unlevered_industry=0.87, market_cap=45e9, total_debt=2e9,
                          operating_income=0.02e9, revenue=2.5e9)
    check(E._bottom_up_beta(axon, []) == 1.40, "an operating margin under 5% keeps the higher beta")
    check(E._bottom_up_beta(FinancialFacts("X", beta=1.3), []) == 1.3,
          "no industry mapping -> regression beta, unchanged")


def test_n9_implied_erp_is_read_and_newest_wins():
    import json, tempfile, os
    import config
    import store
    from sources import damodaran as D
    html = ("<li><b>Implied ERP on October 1, 2026</b> = 4.20% (Trailing 12 month, with adjusted "
            "payout); 3.70% (cash yield)<br>(with the US treasury rate of 5.29% used as the riskfree rate")
    v = D.parse_implied_erp(html)
    check(v == {"erp": 0.042, "as_of": "2026-10", "date": "2026-10-01", "rf": 0.0529},
          "the home-page sentence parses to ERP 4.20% as of 2026-10", repr(v))
    check(D.parse_implied_erp("<p>Implied ERP on October 1, 2026 = 42.0%</p>") is None,
          "an implausible ERP is refused, not used")
    keep = (config.CACHE_DIR, config.ERP, config.ERP_AS_OF, getattr(config, "ERP_SOURCE", None))
    with tempfile.TemporaryDirectory() as d:
        config.CACHE_DIR = d
        with open(os.path.join(d, D.ERP_CACHE_FILE), "w") as fh:
            json.dump({"erp": 0.042, "as_of": "2026-10"}, fh)
        store._apply_assumptions({"assumptions": {"erp": 0.0445, "erp_as_of": "2026-07"}})
        check(config.ERP == 0.042 and config.ERP_SOURCE == "damodaran-live",
              "the page's October ERP beats a July manual value")
        store._apply_assumptions({"assumptions": {"erp": 0.05, "erp_as_of": "2026-11"}})
        check(config.ERP == 0.05 and config.ERP_SOURCE == "manual",
              "a manual value saved after the page's month wins")
    config.CACHE_DIR, config.ERP, config.ERP_AS_OF, config.ERP_SOURCE = keep
    from domain.engine import deep_v82 as E
    check(E.erp_months_old("garbage") > config.ERP_STALE_MONTHS, "an unreadable as-of is stale, not fresh")


if __name__ == "__main__":
    run_suite(globals(), "ALL test_damodaran_fixes_2026_10 PASSED")
