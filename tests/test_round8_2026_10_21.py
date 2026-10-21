"""test_round8_2026_10_21 — fair-value audit of 2026-10-21 (round 8).

Each test fails on the code before its fix (main 451db98).

Run: python -m tests.test_round8_2026_10_21
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from domain.facts import FinancialFacts             # noqa: E402
from domain.engine import deep_v82 as E             # noqa: E402
from sources import sec_edgar                       # noqa: E402
from tests._harness import check, run_suite         # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
RF = 0.0513


def _facts(ticker="NEWCO", **kw):
    f = FinancialFacts(ticker)
    for k, v in kw.items():
        setattr(f, k, v)
    return f


def test_revenue_top_line_beats_contract_subset():
    print("1 revenue: the income-statement total beats ASC 606 contract revenue (MELI)")
    alts = [("Revenues", 35.18e9)]
    v, c, rest = sec_edgar._prefer_top_line(None, 24.46e9, sec_edgar.REV[0], alts)
    check(c == "Revenues" and v == 35.18e9, "MELI: Revenues $35.18B replaces contract $24.46B", f"{c} {v}")
    check((sec_edgar.REV[0], 24.46e9) in rest, "the contract figure stays listed as the conflict")
    v, c, _ = sec_edgar._prefer_top_line(None, 0.61e9, sec_edgar.REV[0],
                                         [("RevenuesNetOfInterestExpense", 4.31e9)])
    check(c == "RevenuesNetOfInterestExpense", "a bank's net revenue is a top line (SOFI)")
    v, c, _ = sec_edgar._prefer_top_line(None, 50e9, sec_edgar.REV[0], [("Revenues", 45e9)])
    check(c == sec_edgar.REV[0], "a SMALLER Revenues figure does not replace contract revenue")


def test_outyear_reacceleration_held():
    print("2 a consensus out-year that jumps >10pp above the year before is held (TSLA, MSFT)")
    tsla = _facts(rev_growth_path=[[0.1294, 32], [0.1307, 32], [0.1866, 32], [0.5424, 30], [0.0545, 16]])
    held = []
    p = E.consensus_growth_path(tsla, held)
    check(p[3] == 0.1866, "TSLA FY2029 +54% held at the year before (18.7%)", str(p))
    check(p[4] == 0.0545 and len(held) == 1, "the slower year 5 is kept and the hold is reported")
    msft = _facts(rev_growth_path=[[0.178, 39], [0.1963, 39], [0.2165, 27], [0.1747, 13], [0.3007, 13]])
    p = E.consensus_growth_path(msft)
    check(p[4] == 0.1747 and p[2] == 0.2165, "MSFT year 5 +30% held at 17.5%; +2pp in year 3 is kept", str(p))
    near = _facts(rev_growth_path=[[0.10, 20], [0.35, 20]])
    check(E.consensus_growth_path(near)[1] == 0.35, "years 1-2 are not jump-tested")


def test_consensus_rebased_to_ttm_clock():
    print("3 consensus growth is re-cut to the TTM clock (LLY: TTM already 1.22x FY2025)")
    lly = _facts(revenue=79.67e9, fiscal_year="2026-06-30",
                 revenue_annuals_dated=[["2025-12-31", 65.18e9], ["2024-12-31", 45.04e9]],
                 rev_growth_path=[[0.3584, 29], [0.1528, 27]])
    p = E.consensus_growth_path(lly)
    fy26, fy27 = 65.18e9 * 1.3584, 65.18e9 * 1.3584 * 1.1528
    want = (fy26 ** 0.5 * fy27 ** 0.5) / 79.67e9 - 1
    check(abs(p[0] - want) < 0.003, f"year 1 = NTM level / TTM - 1 = {want:.1%} (not 35.8%)", str(p))
    check(p[1] == 0.1528, "year 2 keeps FY27/FY26 growth (same geometric interpolation)", str(p))
    fy_end = _facts(revenue=331.84e9, fiscal_year="2026-06-30",
                    revenue_annuals_dated=[["2026-06-30", 331.84e9]], rev_growth_path=[[0.178, 39]])
    check(E.consensus_growth_path(fy_end) == [0.178], "TTM == last fiscal year (MSFT): unchanged")


def test_interim_quarters_not_used_is_a_warning():
    print("4 a base period 6+ months old warns that interim quarters are missing (ASML, TSM)")
    import datetime as dt
    from pipeline import dataquality as Q
    codes = lambda fy: [x["code"] for x in Q.assess(_facts(fiscal_year=fy), dt.date(2026, 10, 21))]
    check("interim_not_used" in codes("2025-12-31"), "FY2025 base in Oct 2026 -> warning")
    check("interim_not_used" not in codes("2026-06-30"), "a June 10-Q in October is current")
    check("interim_not_used" not in codes("2024-06-30"), "older still is the existing stale rule")


def test_tax_and_flags_for_loss_makers():
    print("5 a loss year's tax benefit is not a future tax rate; the 21% flag matches the rate used")
    from domain.facts import DEFAULT_TAX_RATE, effective_tax_rate
    check(effective_tax_rate(-260e6, -35.6e6) is None, "RKLB: benefit/loss 13.7% is not a rate")
    rk = _facts(income_before_tax=-260e6, tax_expense=-35.6e6)
    check(rk.tax_rate == DEFAULT_TAX_RATE and rk.tax_rate_defaulted, "loss year -> default rate, flagged")
    pfe = _facts(income_before_tax=4.209e9, tax_expense=-0.5e9,
                 operating_income=17.611e9, operating_income_gaap=6.908e9)
    check(not pfe.tax_rate_defaulted and pfe.tax_rate != DEFAULT_TAX_RATE,
          "PFE: the add-back makes the rate sourced, so no 'defaults to 21%' flag",
          f"{pfe.tax_rate:.3f}")


def test_pfe_cash_reads_other_short_term_investments():
    print("6 cash + securities reads OtherShortTermInvestments (PFE $8.3B -> $11.7B)")
    check("OtherShortTermInvestments" in sec_edgar.STI_TAGS
          and sec_edgar.STI_TAGS.index("OtherShortTermInvestments")
          < sec_edgar.STI_TAGS.index("AvailableForSaleSecuritiesDebtSecuritiesCurrent"),
          "the balance-sheet total ranks ahead of its AFS-debt component")


def test_young_dcf_fades_wacc_and_holds_s2c():
    print("7 young DCF: WACC fades to stable, terminal at stable WACC + marginal tax, S2C held to industry")
    from domain.engine import young_dcf as Y
    d = dict(current_revenue=2.58e9, g_high=0.37, g_stable=0.0513, horizon=10,
             current_margin=-0.06, target_margin=0.147, sales_to_capital=2.5, tax=0.131,
             wacc=0.136, roic_stable=0.15, net_debt=0.7e9, shares=230e6)
    flat = Y.going_concern(d)["going_concern_per_share"]
    faded = Y.going_concern(dict(d, wacc_stable=0.0936))["going_concern_per_share"]
    check(faded > 2 * flat, f"HIMS-like: fading 13.6% -> 9.4% lifts going concern ${flat:.1f} -> ${faded:.1f}")
    taxed = Y.going_concern(dict(d, wacc_stable=0.0936, tax_terminal=0.25))["going_concern_per_share"]
    check(taxed < faded, "the terminal year pays the marginal 25%, not a loss year's 13%")
    s2c, label = Y._held_to_industry(0.59, "revenue / invested capital 0.59", 2.74)
    check(abs(s2c - 1.37) < 1e-9 and "held to 1.37" in label, "RKLB 0.59 held to 0.5x industry 2.74", label)
    check(Y.going_concern(d) == Y.going_concern(dict(d, wacc_stable=None)), "no stable WACC given: unchanged")


def _elv(**kw):
    base = dict(price=430.0, shares_diluted=219e6, market_cap=93e9, revenue=201e9,
                operating_income=5.6e9, revenue_annuals=[199e9, 177e9, 171e9, 156e9, 138e9],
                revenue_annuals_dated=[["2025-12-31", 199e9], ["2024-12-31", 177e9],
                                       ["2023-12-31", 171e9], ["2022-12-31", 156e9],
                                       ["2021-12-31", 138e9]],
                operating_income_annuals_dated=[["2025-12-31", 7.2e9], ["2024-12-31", 8.6e9],
                                                ["2023-12-31", 8.9e9], ["2022-12-31", 8.1e9],
                                                ["2021-12-31", 7.6e9]],
                cash=10e9, total_debt=31e9, equity=42e9, income_before_tax=5.9e9,
                tax_expense=1.0e9, capex=1.3e9, dep_amort=1.4e9, interest_expense=1.4e9,
                rev_growth_path=[[0.01, 12], [0.02, 11]], fiscal_year="2025-12-31")
    base.update(kw)
    return _facts("ELVX", **base)


def test_trough_margin_reverts_to_five_year_median():
    print("8 DCF target margin: a trough TTM margin reverts to the 5-year median (ELV)")
    v = E.DeepV82Engine().evaluate(_elv(), rf=RF)
    tgt = v.dcf["inputs"]["margin_target"]
    check(tgt > 0.04, f"target {tgt:.1%} is the 5.2% median, not today's 2.8%", str(v.dcf.get("sources")))
    check(any("reverts to the 5-year median" in x for x in v.flags), "the reversion is flagged")
    hi = E.DeepV82Engine().evaluate(_elv(operating_income=12e9), rf=RF)
    check(abs(hi.dcf["inputs"]["margin_target"] - hi.dcf["inputs"]["margin_now"]) < 1e-9,
          "a margin ABOVE its median keeps its own (no forced reversion down)")


def test_consensus_as_reported_fade_starts_at_cap():
    print("9 consensus years as reported; the extrapolated fade starts from at most 30% (AVGO)")
    from domain.engine import fcff_dcf
    kw = dict(revenue=89e9, margin_now=0.5, margin_target=0.5, g_high=0.66, g_stable=0.05,
              sales_to_capital=0.64, tax_now=0.06, tax_marginal=0.25, wacc=0.12,
              wacc_stable=0.105, roic_stable=0.19, cash=24e9, debt=62e9, shares=4.9e9,
              growth_path=[0.66, 0.64])
    a = fcff_dcf.value(**kw, fade_start_cap=0.30)
    g = [r["growth"] for r in a["path"]]
    check(g[:2] == [0.66, 0.64], "years 1-2 are the consensus 66% / 64%", str(g[:3]))
    check(abs(g[2] - (0.30 + (0.05 - 0.30) / 8)) < 1e-4, "year 3 fades from 30%, not from 64%", str(g[2]))
    b = fcff_dcf.value(**kw)
    check(b["path"][2]["growth"] > 0.55, "without the cap argument the old fade is unchanged (skill parity)")


if __name__ == "__main__":
    run_suite(globals(), "ALL test_round8_2026_10_21 PASSED")
