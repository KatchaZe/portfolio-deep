"""test_damodaran_round3 — the audit findings left open after rounds 1-2 (2026-10-18).

One section per finding id from the DEEP Valuation Audit (F7 ... F30). Each check is
written against the defect it closes: it fails on the old behaviour.

Run: python -m tests.test_damodaran_round3
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from domain.engine import deep_v82 as E        # noqa: E402
from domain.facts import FinancialFacts        # noqa: E402
from tests._harness import check, run_suite    # noqa: E402

RF = 0.045
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _inst(end, val):
    return {"end": end, "val": val, "filed": end, "form": "10-Q"}


def _flow(start, end, val):
    return {"start": start, "end": end, "val": val, "filed": end, "form": "10-K"}


def _cf(extra):
    rev = [_flow("2025-01-01", "2025-12-31", 100e9), _flow("2026-01-01", "2026-06-30", 60e9),
           _flow("2025-01-01", "2025-06-30", 50e9)]
    ns = {"RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": rev}}}
    ns.update(extra)
    return {"facts": {"us-gaap": ns}}


def _u(*rows, unit="USD"):
    return {"units": {unit: list(rows)}}


def _facts(**kw):
    base = dict(price=100.0, shares_diluted=1e9, market_cap=100e9, revenue=50e9,
                operating_income=10e9, net_income=7.5e9, total_debt=10e9, cash=5e9,
                equity=40e9, beta=1.1, growth_lt=0.10, eps_gaap=7.5, forward_eps=8.2,
                revenue_annuals=[50e9, 45e9, 41e9, 37e9],
                operating_income_annuals=[10e9, 9e9, 8e9, 7e9],
                income_before_tax=9.5e9, tax_expense=2.0e9, capex=3e9, dep_amort=2e9,
                interest_expense=0.5e9)
    base.update(kw)
    f = FinancialFacts(kw.pop("ticker", "ZZNEW"))
    for k, v in base.items():
        setattr(f, k, v)
    return f


# --------------------------------------------------------------------------- #
#  data layer                                                                  #
# --------------------------------------------------------------------------- #
def test_f9_debt_families():
    print("F9 debt: convertibles, capital-lease family, finance leases")
    from sources import sec_edgar as S
    d = S.extract(_cf({"ConvertibleDebtNoncurrent": _u(_inst("2026-06-30", 1.37e9))}))
    check(d["total_debt"] == 1.37e9, "convertible notes are debt (HIMS read 0)", str(d["total_debt"]))
    d = S.extract(_cf({"LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities":
                       _u(_inst("2026-06-30", 43.56e9)),
                       "FinanceLeaseLiability": _u(_inst("2026-06-30", 1e9))}))
    check(d["total_debt"] == 43.56e9, "the ...CapitalLeaseObligations total already has leases (KO)",
          str(d["total_debt"]))
    d = S.extract(_cf({"LongTermDebt": _u(_inst("2026-06-30", 40.29e9)),
                       "FinanceLeaseLiability": _u(_inst("2026-06-30", 66.59e9))}))
    check(abs(d["total_debt"] - 106.88e9) < 1, "finance leases are added on the debt's date (MSFT)",
          str(d["total_debt"]))
    d = S.extract(_cf({"LongTermDebt": _u(_inst("2026-06-30", 40e9)),
                       "FinanceLeaseLiability": _u(_inst("2025-12-31", 9e9))}))
    check(d["total_debt"] == 40e9, "a finance lease from another date is never added", str(d["total_debt"]))
    d = S.extract({"facts": {"ifrs-full": {
        "Revenue": _u(_flow("2025-01-01", "2025-12-31", 300e9), unit="DKK"),
        "LeaseLiabilities": _u(_inst("2025-12-31", 8.57e9), unit="DKK")}}})
    check(d["operating_leases"] == 8.57e9, "IFRS 16 lease liabilities are leases (NVO read none)",
          str(d["operating_leases"]))


def test_f10_prior_year_is_one_year_earlier():
    print("F10 prior year = the same balance-sheet date a year earlier")
    from sources import sec_edgar as S
    eq = _u(_inst("2026-06-30", 400e9), _inst("2025-12-31", 380e9), _inst("2025-06-30", 340e9),
            _inst("2014-12-31", 20e9))
    d = S.extract(_cf({"StockholdersEquity": eq}))
    check(d["equity_prior"] == 340e9, "2025-06-30, not 2025-12-31 (6 months) or 2014",
          str(d["equity_prior"]))
    d = S.extract(_cf({"StockholdersEquity": _u(_inst("2026-06-30", 400e9), _inst("2025-12-31", 380e9))}))
    check(d["equity_prior"] is None, "no balance a year earlier: missing, not a nearer one",
          str(d["equity_prior"]))


def test_f20_split_restates_history():
    print("F20 split: share and EPS history put on today's basis")
    from sources import sec_edgar as S
    sh = _u(*[_flow(f"{y-1}-02-01", f"{y}-01-31", v) for y, v in
              ((2026, 24.5e9), (2025, 24.8e9), (2024, 24.9e9), (2023, 25.0e9), (2022, 2.5e9),
               (2021, 2.48e9))], unit="shares")
    eps = _u(*[_flow(f"{y-1}-02-01", f"{y}-01-31", v) for y, v in
               ((2026, 4.9), (2025, 2.9), (2024, 1.2), (2023, 0.17), (2022, 3.85), (2021, 1.73))],
             unit="USD/shares")
    ratio = _u({"start": "2024-06-01", "end": "2024-06-30", "val": 10, "filed": "2024-08-01",
                "form": "10-Q"}, unit="pure")
    d = S.extract(_cf({"WeightedAverageNumberOfDilutedSharesOutstanding": sh,
                       "EarningsPerShareDiluted": eps,
                       "StockholdersEquityNoteStockSplitConversionRatio1": ratio}))
    e = dict(d["eps_annuals_dated"])
    check(abs(e["2022-01-31"] - 0.385) < 1e-9 and e["2023-01-31"] == 0.17,
          "pre-split EPS divided by the filed 10x factor, post-split untouched", str(e))
    check(abs(d["shares_diluted_annuals"][4] - 25e9) < 1, "pre-split share count x10",
          str(d["shares_diluted_annuals"][:6]))
    d = S.extract(_cf({"WeightedAverageNumberOfDilutedSharesOutstanding": sh,
                       "EarningsPerShareDiluted": eps}))
    check(dict(d["eps_annuals_dated"])["2022-01-31"] == 3.85,
          "no filed split ratio: no adjustment (share growth is not a split)")
    ff = FinancialFacts("ZZSPLIT")
    ff.eps_gaap, ff.net_income, ff.shares_diluted = 12.87, 52e9, 24.5e9
    import sources.sec_edgar as SE
    # the TTM-straddle guard lives in populate(); replay its rule on the facts
    implied = ff.net_income / ff.shares_diluted
    check(ff.eps_gaap / implied > 1.8 and "disagrees with net income / shares" in
          open(SE.__file__, encoding="utf-8").read(), "a split-straddling TTM EPS is caught")


def test_f11_fallback_converts_only_its_own_figures():
    print("F11 stale-SEC fallback: convert the fallback, clear the old period")
    from pipeline import dataquality as dq
    ff = FinancialFacts("TSM", fiscal_year="2024-12-31", revenue=88e9, capex=30e9, cfo=60e9,
                        receivables=10e9, interest_expense=1e9)
    alt = FinancialFacts("TSM", fiscal_year="2025-12-31")
    alt.set("revenue", 3800e9, "fmp/annual")
    alt.set("currency", "TWD", "fmp")
    alt.set("fiscal_year", "2025-12-31", "fmp")
    applied, notes = dq.apply_stale_fallback(ff, alt, "USD", 1.0, lambda c: 1 / 32.0)
    check(applied and abs(ff.revenue - 3800e9 / 32) < 1, "the fallback's TWD revenue is converted",
          str(ff.revenue))
    check(ff.capex is None and ff.cfo is None and ff.receivables is None,
          "SEC figures from the old year are cleared, never multiplied by the TWD rate",
          f"capex {ff.capex} cfo {ff.cfo}")


def test_f21_forward_eps_on_one_clock():
    print("F21 every forward-EPS source is put on next-twelve-months")
    from pipeline.consensus import ntm_eps
    rows = [("2026-12-31", 8.0), ("2027-12-31", 10.0)]
    check(ntm_eps(rows, dt.date(2026, 12, 31)) == 10.0, "year end: NTM = next fiscal year")
    check(abs(ntm_eps(rows, dt.date(2026, 7, 2)) - 9.0) < 0.02, "mid-year: half and half")
    check(ntm_eps([("2024-12-31", 3.0)] + rows, dt.date(2026, 7, 2)) == ntm_eps(rows, dt.date(2026, 7, 2)),
          "a year already reported never enters")
    from sources import fmp
    est = [{"date": "2026-12-31", "epsAvg": 8.0}, {"date": "2027-12-31", "epsAvg": 10.0}]
    check(fmp.parse_ntm_eps(est, dt.date(2026, 12, 31)) == 10.0, "FMP on the same clock")


# --------------------------------------------------------------------------- #
#  engine                                                                      #
# --------------------------------------------------------------------------- #
def test_f8_core_net_income():
    print("F8 non-operating income is stripped before EPS and earnings quality")
    f = _facts(net_income=60e9, income_before_tax=75e9, tax_expense=15e9,
               operating_income=40e9, interest_expense=1e9, cfo=45e9, total_assets=400e9)
    core, nonop = E.core_net_income(f)
    check(abs(nonop - 36e9) < 1 and abs(core - (60e9 - 36e9 * 0.8)) < 1,
          "a $36B gain is removed after tax", f"{nonop} {core}")
    v = E.DeepV82Engine().evaluate(f, rf=RF)
    check(v.eq_verdict == "CLEAN", "the gain no longer reads as an accrual problem", str(v.eq_verdict))
    check(any("non-operating income" in x for x in v.flags), "and the card says so")
    plain = _facts()
    check(E.core_net_income(plain)[1] is None, "immaterial non-operating income is left alone")


def test_f17_untested_is_not_clean():
    print("F17 no applicable earnings-quality test -> untested, not CLEAN")
    verdict, notes, _ = E.earnings_quality(-1e9, -0.5e9, None, None, 2e9)
    check(verdict is None and any("untested" in n for n in notes), "loss-maker, no assets: untested",
          str(verdict))
    check(E.earnings_quality(1e9, 1.2e9, 10e9, None, 5e9)[0] == "CLEAN", "a tested clean firm stays CLEAN")


def test_f18_fvp_counts_dividends():
    print("F18 Future Value Projection includes the dividends paid on the way")
    no = E.future_value_projection(5.0, 0.10, 0.09, 20.0)
    yes = E.future_value_projection(5.0, 0.10, 0.09, 20.0, payout=0.5)
    pv_div = sum(5.0 * 1.1 ** t * 0.5 / 1.09 ** t for t in range(1, 6))
    check(abs(yes - no - pv_div) < 1e-9, "adds PV of years 1-5 dividends", f"{no} -> {yes}")


def test_f29_reverse_dcf_roic_fades():
    print("F29 reverse DCF reinvests at a ROIC that fades from the firm's own")
    common = dict(price=50.0, shares=1e9, revenue=20e9, rev_1y=18e9, total_debt=0, cash=2e9,
                  wacc_val=0.09, g=0.04, tax=0.21, margin=0.30, roic_term=0.15, wacc_term=0.085)
    flat = E.reverse_dcf(**common)
    fade = E.reverse_dcf(**common, roic_now=0.34)
    check(fade["implied_cagr_pct"] is not None and flat["implied_cagr_pct"] is not None
          and fade["implied_cagr_pct"] <= flat["implied_cagr_pct"],
          "a 34%-ROIC firm needs no more growth than if charged at 15%",
          f"{flat['implied_cagr_pct']} vs {fade['implied_cagr_pct']}")
    check(E.reverse_dcf(**common, roic_now=0.10)["implied_cagr_pct"] == flat["implied_cagr_pct"],
          "a firm below the terminal ROIC is not flattered")


def test_f13_f16_cost_of_debt_and_eva():
    print("F13 Kd after tax on the card · F16 EVA on one basis")
    f = _facts(rnd_annuals=[3e9, 2.8e9, 2.5e9, 2.2e9, 2e9, 1.8e9])
    v = E.DeepV82Engine().evaluate(f, rf=RF)
    km = v.key_metrics
    check(km.get("kd_after_pct") is not None and km["kd_after_pct"] < km["kd_pct"],
          "after-tax Kd published beside the pre-tax", f"{km.get('kd_pct')} / {km.get('kd_after_pct')}")
    rd = E.rd_capitalize(f.rnd_annuals, f.operating_income, 10e9 + 40e9 - 5e9, f.tax_rate)
    want = (rd[2] - km["wacc_pct"] / 100) * rd[1]
    check(abs(v.eva - want) / abs(want) < 0.01, "EVA = (R&D-adj ROIC - WACC) x R&D-adj capital",
          f"{v.eva} vs {want}")
    drawer = open(os.path.join(ROOT, "static", "js", "card_drawer.js"), encoding="utf-8").read()
    check("kd_after_pct" in drawer, "the drawer's 'Kd a-t' reads the after-tax figure")


def test_f7_young_dcf_no_double_sbc():
    print("F7 young-company DCF does not grow the share count on top of GAAP margins")
    from domain.engine import young_dcf as Y
    d = {"current_revenue": 1e9, "g_high": 0.4, "g_stable": 0.04, "horizon": 10,
         "current_margin": -0.2, "target_margin": 0.2, "sales_to_capital": 2.0, "tax": 0.21,
         "wacc": 0.11, "roic_stable": 0.15, "net_debt": 0, "shares": 1e8,
         "annual_dilution": 0.0, "p_survival": 0.8, "distress_value_per_share": 0}
    a = Y.going_concern(d)["going_concern_per_share"]
    f = _facts(operating_income=-0.2e9, net_income=-0.25e9, eps_gaap=-0.25, revenue=1e9,
               revenue_annuals=[1e9, 0.7e9, 0.5e9, 0.3e9], sbc=0.1e9, market_cap=5e9,
               price=50.0, shares_diluted=1e8, forward_eps=-0.1,
               operating_income_annuals=[-0.2e9, -0.3e9, -0.35e9, -0.4e9])
    v = E.DeepV82Engine().evaluate(f, rf=RF)
    inp = ((v.young_dcf or {}).get("inputs") or {})
    check(inp.get("dilution_charged") is False, "dilution disclosed, not charged", str(inp.get("dilution_charged")))
    check(a > 0, "going concern still values the firm", str(a))


# --------------------------------------------------------------------------- #
#  display                                                                     #
# --------------------------------------------------------------------------- #
def test_f14_one_margin_of_safety():
    print("F14 one margin of safety, (FV - P) / FV, in advice, card and Risk Desk")
    from domain import advice
    row = {"upside_pct": 17.0, "composite": 3.5, "key_metrics": {"spread_pct": 5}}
    a = advice.build(row)
    check("MoS +15%" in a, "upside +17% is a 15% margin of safety", a[:90])
    row2 = {"upside_pct": 15.0, "composite": 3.5, "key_metrics": {"spread_pct": 5}}
    check("ใกล้ Fair Value" in advice.build(row2),
          "upside +15% (MoS 13%) is 'near fair value', as the P-score reads it")
    from pipeline import rows
    import inspect
    check('"mos_pct"' in inspect.getsource(rows.valuation_row), "rows carry mos_pct")
    js = open(os.path.join(ROOT, "static", "js", "risk.js"), encoding="utf-8").read()
    check("mosOf(r)" in js, "Risk Desk portfolio MoS uses the card's definition")


def test_f22_risk_desk_keyed_on_holdings():
    print("F22 Risk Desk cache keyed on the holdings, not only the inputs")
    js = open(os.path.join(ROOT, "static", "js", "risk.js"), encoding="utf-8").read()
    check("const key=`${tol}|${hz}|${en}|${hold}`" in js, "holdings are part of the key")


def test_f28_overlay_carries_rf_live():
    print("F28 the market overlay says when Rf is the fallback")
    src = open(os.path.join(ROOT, "pipeline", "daily.py"), encoding="utf-8").read()
    check('mkt["valuation"]["rf_live"]' in src, "rf_live stored with the overlay")
    js = open(os.path.join(ROOT, "static", "js", "strip.js"), encoding="utf-8").read()
    check("v.rf_live===false" in js, "and the strip shows it")


def test_f30_verify_display_reads_the_js():
    print("F30 verify_display checks the dashboard's JavaScript")
    import importlib
    vd = importlib.import_module("tools.verify_display")
    check(hasattr(vd, "check_frontend"), "check_frontend exists")
    vd.FAILS.clear()
    vd.check_frontend()
    check(not vd.FAILS, "and passes on the current JS", str(vd.FAILS))


if __name__ == "__main__":
    run_suite(globals(), "test_damodaran_round3 OK - remaining audit findings closed")
