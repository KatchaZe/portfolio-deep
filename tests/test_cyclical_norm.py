"""test_cyclical_norm: a cyclical at its peak is valued on its cycle, not its peak (E83-E86).

MU, watchlist run 2026-11-25: STANDARD (Semiconductor is not a cyclical industry), so
the FCFF DCF held a 70.7% margin on $133.2B of peak revenue grown at a capped 30%
trough-to-peak CAGR, the Bear floor kept the FY2023 -37% year out, and every other
method inherited the peak: FV $2,540 against a $707.7 price. Fixtures are MU, NVDA,
TMDX, HIMS and RKLB after normalize.build + pipeline.finish from SEC companyfacts
(cached 2026-11-21, offline Damodaran snapshots), priced at the store's close."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from domain import company_type as CT                      # noqa: E402
from domain.engine import fcff_dcf                         # noqa: E402
from domain.engine.deep_v82 import DeepV82Engine           # noqa: E402
from domain.facts import FinancialFacts                    # noqa: E402
from tests._harness import check, run_suite                # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "cyclical")
FIELDS = set(FinancialFacts.__dataclass_fields__)

# Fiscal-year operating margins (one-time add-backs applied) and revenue ($B), newest
# first, from each filer's 10-K facts in the fixtures. MU FY2018-22 also match Micron's
# fourth-quarter releases (investors.micron.com: FY18 30,391/14,994; FY19 23,406/7,376;
# FY20 21,435/3,003; FY21 27,705/6,283; FY22 30,758/9,702 $M revenue / GAAP OI).
MU_M = [.746, .261, .052, -.363, .315, .227, .140, .315, .493, .289]
MU_R = [("2026-09-03", 133.188), ("2025-08-28", 37.378), ("2024-08-29", 25.111),
        ("2023-08-31", 15.540), ("2022-09-01", 30.758), ("2021-09-02", 27.705),
        ("2020-09-03", 21.435), ("2019-08-29", 23.406), ("2018-08-30", 30.391),
        ("2017-08-31", 20.322)]
NVDA_M = [.604, .624, .541, .157, .373, .272, .261, .330, .280, .149]
NVDA_R = [("2026-01-25", 215.938), ("2025-01-26", 130.497), ("2024-01-28", 60.922),
          ("2023-01-29", 26.974), ("2022-01-30", 26.914), ("2021-01-31", 16.675),
          ("2020-01-26", 10.918), ("2018-01-28", 9.714), ("2017-01-29", 6.910),
          ("2016-01-31", 5.010)]
TMDX_M = [.179, .085, -.119, -.336, -1.303, -1.029, -1.254, -1.555]
TMDX_R = [("2025-12-31", .605), ("2024-12-31", .442), ("2023-12-31", .242),
          ("2022-12-31", .093), ("2021-12-31", .030), ("2020-12-31", .026),
          ("2019-12-28", .024), ("2018-12-29", .013)]
HIMS_M = [.045, .042, -.033, -.129, -.423, -.102, -.901]
HIMS_R = [("2025-12-31", 2.348), ("2024-12-31", 1.477), ("2023-12-31", .872),
          ("2022-12-31", .527), ("2021-12-31", .272), ("2020-12-31", .149),
          ("2019-12-31", .083)]
RKLB_M = [-.380, -.435, -.727, -.641, -1.640, -1.563]
RKLB_R = [("2025-12-31", .602), ("2024-12-31", .436), ("2023-12-31", .245),
          ("2022-12-31", .211), ("2021-12-31", .062), ("2020-12-31", .035)]


def _facts(t, **over):
    d = json.load(open(os.path.join(FIX, t + ".json")))
    d.update(over)
    ff = FinancialFacts(**{k: v for k, v in d.items() if k in FIELDS})
    ff.flags = []
    return ff


def _value(t, **over):
    return DeepV82Engine().evaluate(_facts(t, **over), rf=0.045)


def test_E83_firm_level_cyclical_detection():
    ctype, why, cyc = CT.classify("Semiconductor", 99.34, 133.188, MU_M, MU_R)
    check(ctype is CT.CompanyType.CYCLICAL, "MU (Semiconductor) is CYCLICAL from its own years", str(ctype))
    check(cyc is not None and cyc.basis == "firm", "MU: basis = firm, not the industry list")
    check("รายได้ลด 49%" in why and "2023" in why and "74.6%" in why and "27.5%" in why,
          "MU: the Thai reason names the drop, its year, today's and the normal margin", why)
    for name, m, r in (("NVDA", NVDA_M, NVDA_R), ("TMDX", TMDX_M, TMDX_R),
                       ("HIMS", HIMS_M, HIMS_R), ("RKLB", RKLB_M, RKLB_R)):
        oi = m[0] * r[0][1]
        got = CT.classify("Semiconductor" if name == "NVDA" else None, oi, r[0][1], m, r)[0]
        check(got is not CT.CompanyType.CYCLICAL, f"{name} is not caught (growth, not a cycle)", str(got))
    # each condition is needed: drop the FY2023 fall, the loss, or the peak
    no_drop = [(d, max(v, 30.0)) for d, v in MU_R]
    check(CT.classify("Semiconductor", 99.34, 133.188, MU_M, no_drop)[0] is CT.CompanyType.STANDARD,
          "(a) without a >=15% revenue fall: STANDARD")
    calm = [.30, .26, .20, .12, .315, .227, .140, .315, .493, .289]
    check(CT.classify("Semiconductor", .30 * 133.188, 133.188, calm, MU_R)[0] is CT.CompanyType.STANDARD,
          "(b) no loss and a range under 30 points: STANDARD")
    check(CT.classify("Semiconductor", .30 * 133.188, 133.188, [.30] + MU_M[1:], MU_R)[0]
          is CT.CompanyType.STANDARD, "(c) a margin under 1.5x normal: STANDARD")
    check((CT.CYCLE_LOOKBACK_YEARS, CT.CYCLE_REVENUE_DROP, CT.CYCLE_MARGIN_RANGE, CT.CYCLE_PEAK_MARGIN_X)
          == (5, 0.15, 0.30, 1.5), "thresholds are the approved constants (Ref quotes them)")


def test_E84_normal_margin_window():
    cyc = CT.cycle_profile("Semiconductor", MU_M, MU_R, .746)
    check(abs(cyc.normal_margin - .275) < 1e-9 and cyc.margin_years == 10 and not cyc.short_window,
          "MU normal margin = 10-year median 27.5%", str(cyc))
    six = CT.cycle_profile("Steel", [.2, .1, -.05, .08, .15, .12], None, .2)
    check(six is not None and six.short_window and six.margin_years == 6,
          "6 years: normalized with the short-window flag")
    check(CT.cycle_profile("Steel", [.2, .1, -.05, .08], None, .2) is None, "4 years: not normalized")


def test_E83_MU_engine_cyclical_and_normalized():
    v = _value("MU")
    check(v.company_type == "cyclical", "MU valued as CYCLICAL", v.company_type)
    inp, path = v.dcf["inputs"], v.dcf["path"]
    check(abs(inp["margin_target"] - .2753) < 0.002, "DCF margin target = normal 27.5% (+ lease)",
          str(inp["margin_target"]))
    check(v.anchor_value < 707.7, "MU FV below the $707.7 price (was $2,769)", str(v.anchor_value))
    check(all(r["reinvestment"] >= 0 for r in path), "no capital released on the way down")
    km = v.key_metrics
    check(km["growth_pct"] <= 4.5 + 1e-9, "PEG growth = through-cycle rate, not the 30% cap", str(km["growth_pct"]))
    check(v.fv_peg < 1415 and v.fv_fvp < 1415,
          "PEG / FVP on normalized EPS (were $2,928 / $3,033)", f"{v.fv_peg} {v.fv_fvp}")
    check(km["cycle"] and km["cycle"]["at_peak"], "key_metrics.cycle carries the classification", str(km["cycle"]))


def test_E92_MU_normal_margin_on_current_revenue():
    """E92 (verifier 2026-11-28, Major 1): Damodaran's scaled method is the normal margin
    on CURRENT revenue. The log-linear trend through FY17-26 took 2.4% -> 11.4% of its
    slope from the peak year it was testing, and put FV anywhere in $145-$411 on the
    window choice. Reference: ~/work/v_cyc/tools/hand.py alternative (i') = $470.86."""
    v = _value("MU")
    inp, path = v.dcf["inputs"], v.dcf["path"]
    g = inp["growth_path"]
    check(g == [0.045], "growth = the stable 4.5% (through-cycle 23% M&A-free CAGR is above it)", str(g))
    check(abs(path[0]["revenue"] - 133.188e9 * 1.045) < 1e6,
          "year 1 grows from TODAY's $133.2B, no convergence to a trend line", str(path[0]["revenue"]))
    check(abs(v.anchor_value - 470.86) / 470.86 < 0.01, "FV within 1% of hand.py (i') $470.86",
          str(v.anchor_value))
    c = v.key_metrics["cycle"]
    for k in ("trend_revenue", "peak_ratio", "trend_growth_pct", "revenue_converges"):
        check(k not in c, f"the primary path carries no {k}", str(c))
    check(not hasattr(CT, "PEAK_REVENUE_RATIO"), "the revenue peak test is retired")
    check("trend" not in v.dcf["sources"]["growth"], "growth source names no trend", v.dcf["sources"]["growth"])


def test_E92_trend_and_average_ebit_are_bear_cross_checks():
    v = _value("MU")
    checks = {c["name"]: c for c in v.scenarios.get("cycle_checks") or []}
    check(set(checks) == {"trend_revenue", "average_ebit"}, "two Bear cross-checks", str(checks))
    check(abs(checks["trend_revenue"]["value"] - 283.56) / 283.56 < 0.01,
          "trend-revenue check = hand.py engine method $283.56", str(checks["trend_revenue"]))
    check(abs(checks["average_ebit"]["value"] - 262.15) / 262.15 < 0.01,
          "average-EBIT check = hand.py (iii) $262.15", str(checks["average_ebit"]))
    check(all(c["value"] < v.anchor_value for c in checks.values()), "both sit below the primary FV")
    check(all(c.get("label") for c in checks.values()), "each carries a Thai label for the UI")
    check(v.range_low <= v.anchor_value <= v.range_high, "cross-checks never move the primary FV")


def test_E93_consensus_only_for_years_1_2():
    """E93 (Major 2): with consensus MU went $284 -> $705 BUY. A cyclical's analyst path
    is used for years 1-2 only, its margin held there, then margin and growth return to
    the cycle's normal."""
    hyp = dict(rev_growth_path=[[0.70, 25], [0.05, 20]], fwd_growth_y1=0.70, n_analysts=25,
               fwd_growth_near=0.05, n_analysts_y2=20)
    v = _value("MU", **hyp)
    inp, path = v.dcf["inputs"], v.dcf["path"]
    g = inp["growth_path"]
    check(g[:2] == [0.70, 0.05] and all(abs(x - 0.045) < 1e-12 for x in g[2:]) and len(g) == 5,
          "years 1-2 consensus, years 3-5 through-cycle", str(g))
    m_now = inp["margin_now"]
    check(abs(path[0]["margin"] - round(m_now, 4)) < 1e-4 and abs(path[1]["margin"] - round(m_now, 4)) < 1e-4,
          "margin held at today's for the consensus years", str([r["margin"] for r in path[:3]]))
    check(abs(path[4]["margin"] - round(inp["margin_target"], 4)) < 1e-4,
          "then back to the normal margin by year 5", str([r["margin"] for r in path[:6]]))
    check(any("consensus ถึงปีที่ 2 แล้วกลับสู่ค่าปกติของรอบ" in fl for fl in v.flags), "Thai flag shown")
    check(not v.key_metrics["no_consensus"], "consensus basis: no badge")
    v3 = _value("MU", rev_growth_path=[[0.70, 25], [0.05, 20], [0.40, 15], [0.30, 12]])
    check(v3.dcf["inputs"]["growth_path"][:3] == [0.70, 0.05, 0.045],
          "consensus year 3+ is not used", str(v3.dcf["inputs"]["growth_path"]))
    none = _value("MU")
    check(not any("consensus ถึงปีที่ 2" in fl for fl in none.flags), "no flag without consensus")
    check(none.dcf["inputs"]["growth_path"] == [0.045], "without consensus: through-cycle only")
    check(abs(v3.anchor_value - _value("MU", rev_growth_path=[[0.70, 25], [0.05, 20]]).anchor_value) < 0.01,
          "the FV ignores consensus past year 2", str(v3.anchor_value))


# Digital Turbine (APPS) fiscal-year revenue ($M) from the scout cache part (CIK 317788).
# It bought AdColony and Fyber in FY2022: the acquisition spend below is set above 10% of
# FY2021 revenue to stand for those deals (the scout part carries no acquisition series).
APPS_R = [("2026-03-31", 565.251), ("2025-03-31", 490.506), ("2024-03-31", 544.482),
          ("2023-03-31", 665.920), ("2022-03-31", 747.596), ("2021-03-31", 313.579),
          ("2020-03-31", 138.715), ("2019-03-31", 103.569), ("2018-03-31", 74.751)]


def test_E94_through_cycle_growth_leaves_acquisition_years_out():
    """E94 (Major 3): the no-consensus cyclical path returned the trend slope before
    organic_growth, so M&A years set growth (APPS FY21 $314M -> FY22 $748M)."""
    import math
    from domain.engine import deep_v82 as E
    ff = FinancialFacts(ticker="APPS")
    ff.revenue_annuals_dated = [[d, r * 1e6] for d, r in APPS_R]
    ff.acquisitions_annuals_dated = [["2022-03-31", 0.5 * 313.579e6]]
    g, note = E.through_cycle_growth(ff)
    clean = [r0 / r1 for (d0, r0), (_, r1) in zip(APPS_R, APPS_R[1:]) if d0 != "2022-03-31"]
    want = math.prod(clean) ** (1 / len(clean)) - 1
    check(abs(g - want) < 1e-9, "geometric mean of the clean years only", f"{g} vs {want}")
    check(note and "2022-03-31" in note, "the note names the acquisition year", str(note))
    ff.acquisitions_annuals_dated = []
    g_all, _ = E.through_cycle_growth(ff)
    check(g_all > g, "with FY2022 counted the rate is higher", f"{g_all} vs {g}")
    # the same rule the standard path uses: organic_growth's year classifier
    check(E.organic_growth.__code__.co_names.count("_organic_years") == 1
          and E.through_cycle_growth.__code__.co_names.count("_organic_years") == 1,
          "both read the one shared _organic_years")


def test_E95_mos_below_minus_50_caps_at_hold():
    from domain.engine import deep_v82 as E
    check(E.MOS_HOLD_CAP == -0.50, "the cap is the named -50% constant")
    check(E.recommendation(4.2, mos=-0.51) == "HOLD", "BUY composite, MoS -51% -> HOLD")
    check(E.recommendation(3.5, mos=-0.60) == "HOLD", "Accumulate composite, MoS -60% -> HOLD")
    check(E.recommendation(4.2, mos=-0.49) == "BUY", "MoS -49% keeps BUY")
    check(E.recommendation(1.5, mos=-0.9) == "SELL / AVOID", "never lifts a SELL")
    check(E.recommendation(4.2) == "BUY", "no MoS: composite bands only")
    v = _value("MU", price=1500.0)
    mos = (v.anchor_value - 1500.0) / v.anchor_value
    check(mos < -0.5 and v.recommendation == "HOLD", "MU at $1,500: HOLD", f"{mos} {v.recommendation}")
    check(any("MoS ต่ำกว่า −50% จำกัดคำแนะนำไม่เกิน HOLD" in fl for fl in v.flags), "Thai reason flag")


def test_E96_bear_reaches_the_2pct_floor():
    m = [.30, -.20, -.15, -.10, .05, .06, .07, .08, .25, .40]
    cyc = CT.cycle_profile("Steel", m, None, .30)
    check(cyc is not None and CT._quantile(m, CT.BEAR_PERCENTILE) < CT.BEAR_CYCLE_FLOOR,
          "the window's lower quartile is under 2%", str(cyc))
    check(cyc.low_margin == CT.BEAR_CYCLE_FLOOR, "Bear margin floored at 2%", str(cyc.low_margin))
    from domain.engine import scenarios
    kw = dict(revenue=100.0, margin_now=.30, margin_target=cyc.normal_margin, g_high=.03, g_stable=.03,
              sales_to_capital=1.3, tax_now=.2, tax_marginal=.25, wacc=.10, wacc_stable=.085,
              roic_stable=.15, cash=0, debt=0, shares=1, growth_path=[.03], margin_years=3)
    _, pack = scenarios.operating(kw, 100.0, None, .30, cycle=cyc)
    bear = next(c for c in pack["cases"] if c["name"] == "Bear")
    check(abs(bear["margin_pct"] - 2.0) < 1e-9, "the engine's Bear runs at the 2% floor", str(bear))


def test_E97_uncertainty_at_a_peak_spans_the_cycle():
    """A cyclical at a peak margin is not 'Low' uncertainty: its Monte Carlo margin spans
    the cycle's whole range (min to max of the window), not the quartiles."""
    v = _value("MU")
    st = v.scenarios["story_mc"]["story"]["margin_pct"]
    check(st[0] <= 2.0 + 0.7 and st[2] >= 74.0, "MC margin from the floored trough to the peak", str(st))
    check(v.uncertainty["rating"] != "Low", "MU uncertainty is not Low at a 2.9x peak margin",
          str(v.uncertainty))


def test_E85_MU_bear_includes_trough_years():
    v = _value("MU")
    bear = next(x for x in v.scenarios["cases"] if x["name"] == "Bear")
    check(bear["margin_pct"] < 20, "Bear margin is the cycle's lower quartile (~16%), not half the "
          "model margin (35.3%)", str(bear))
    bull = next(x for x in v.scenarios["cases"] if x["name"] == "Bull")
    check(bull["margin_pct"] < 40, "Bull is the upper quartile, not the 74.6% peak", str(bull))
    check("ควอไทล์ล่าง" in v.scenarios["basis"], "the Bear basis says so", v.scenarios["basis"])


def test_E86_no_consensus_badge_and_cycle_verdict():
    v = _value("MU")
    check(v.key_metrics["no_consensus"] is True, "key_metrics.no_consensus set (no analyst path)")
    check(any(fl.startswith("ไม่มี consensus รองรับ") for fl in v.flags), "flag ไม่มี consensus รองรับ")
    check(v.reverse_dcf["verdict"] == "ตลาดตั้งราคาว่ารอบกำลังจะลง",
          "reverse DCF reads the cycle turn, not Plausible", str(v.reverse_dcf.get("verdict")))
    check(v.reverse_dcf.get("verdict_growth") == "Plausible", "the growth label is kept beside it")
    pi = v.priced_in
    check(pi["margin_at_revenue_now_pct"] and pi["margin_now_pct"] / pi["margin_at_revenue_now_pct"] > 1.5,
          "on today's revenue the price pays for a margin under today's / 1.5", str(pi))
    # NVDA's FMP path as stored 2026-11-21 (36 / 42 / 40 / 20 / 19 analysts)
    nv = _value("NVDA", rev_growth_path=[[0.9559, 36], [0.7169, 42], [0.2884, 40],
                                         [0.1738, 20], [0.1239, 19]])
    check(not nv.key_metrics.get("no_consensus"), "NVDA (consensus path) has no badge")
    check(not any(fl.startswith("ไม่มี consensus") for fl in nv.flags), "and no flag")


def test_standard_names_stay_standard():
    for t in ("NVDA", "TMDX", "HIMS", "RKLB"):
        v = _value(t)
        check(v.company_type != "cyclical", f"{t} is not valued as cyclical", v.company_type)
        check(v.key_metrics.get("cycle") is None, f"{t}: no cycle block")


def test_release_capital_floor():
    kw = dict(revenue=100.0, margin_now=.3, margin_target=.3, g_high=-.2, g_stable=.02,
              sales_to_capital=1.0, tax_now=.2, tax_marginal=.25, wacc=.1, wacc_stable=.08,
              roic_stable=.15, cash=0, debt=0, shares=1, growth_path=[-.2, -.2, -.2])
    a, b = fcff_dcf.value(**kw), fcff_dcf.value(**dict(kw, release_capital=False))
    check(a["path"][0]["reinvestment"] < 0 and b["path"][0]["reinvestment"] == 0,
          "release_capital=False floors a falling year's reinvestment at 0")
    check(b["per_share"] < a["per_share"], "so the fall is not paid out as cash")


def test_relative_target_normalized():
    import datetime
    from pipeline import peers
    ff = _facts("MU")
    tp, _nd, _why = peers._target(ff, datetime.date(2026, 11, 24))
    tn, note = peers._normalized_target(ff, tp)
    check(abs(tn["margin"] - .275) < 1e-3 and tn["revenue"] == tp["revenue"],
          "MU relative point at normal margin on current revenue", str((tn["margin"], tn["revenue"])))
    check(tn["ev_sales"] == tp["ev_sales"], "the observed EV/Sales is kept for display")
    nv = _facts("NVDA")
    tp2, _, _ = peers._target(nv, datetime.date(2026, 11, 24))
    check(peers._normalized_target(nv, tp2) == (tp2, None), "NVDA point unchanged")


def test_ref_quotes_the_constants():
    from docs.ref import content_b
    e = next(x for x in content_b.VAL.entries if x.id == "cycle")
    for txt in (f"≥{CT.CYCLE_REVENUE_DROP*100:.0f}%", f"เกิน {CT.CYCLE_MARGIN_RANGE*100:.0f} จุด",
                f"{CT.CYCLE_PEAK_MARGIN_X}×", f"≥{CT.NORMAL_MIN_YEARS} ปี",
                f"{CT.CYCLE_LOOKBACK_YEARS} ปีงบล่าสุด", f"สูงสุด {CT.NORMAL_YEARS} ปี"):
        check(txt in e.rule or txt in e.calc, f"Ref cycle entry quotes {txt}")


# The 614-filer scan (2026-11-26) caught these under the first cut of rule (a)-(c); each
# is a different reason the rule had to be tightened (E87). Margins (one-time add-backs
# applied) and revenue ($B), newest first, from the cached companyfacts / scout parts.
APP_M = [.758, .593, .419, -.017, .054, -.043]
APP_R = [("2025-12-31", 5.481), ("2024-12-31", 3.224), ("2023-12-31", 1.842),
         ("2022-12-31", 2.817), ("2021-12-31", 2.793), ("2020-12-31", 1.451)]
VSEC_M = [.081, .075, .092, .080, -.006, .072, .080, .078, .071, .074]
VSEC_R = [("2025-12-31", 1.112), ("2024-12-31", .786), ("2023-12-31", .544),
          ("2022-12-31", .669), ("2021-12-31", .481), ("2020-12-31", .662),
          ("2019-12-31", .753), ("2018-12-31", .697), ("2017-12-31", .760), ("2016-12-31", .692)]
BKD_M = [.027, .018, .020, -.005, -.070, .003, .001, -.023, .029]
BKD_R = [("2025-12-31", 3.194), ("2024-12-31", 3.125), ("2023-12-31", 3.016),
         ("2022-12-31", 2.825), ("2021-12-31", 2.758), ("2020-12-31", 3.540),
         ("2019-12-31", 4.057), ("2018-12-31", 4.531), ("2017-12-31", 4.747)]


def test_E87_scan_false_positives_stay_out():
    for name, m, r, now, why in (
            ("APP", APP_M, APP_R, .778, "a fall with the margin RISING is a divestiture, not a cycle"),
            ("VSEC", VSEC_M, VSEC_R, .119, "a -0.6% breakeven year is not a loss year"),
            ("BKD", BKD_M, BKD_R, .075, "a ~0% normal margin is a turnaround's loss history")):
        got = CT.classify(None, now * r[0][1], r[0][1], m, r)[0]
        check(got is not CT.CompanyType.CYCLICAL, f"{name} is not CYCLICAL: {why}", str(got))
    check((CT.CYCLE_LOSS_MARGIN, CT.CYCLE_MIN_NORMAL_MARGIN) == (-0.02, 0.05),
          "tuning constants are the documented ones")
    # MU still passes: its FY2023 fall took the margin from 31.5% to -36.3%
    check(CT.classify("Semiconductor", 99.34, 133.188, MU_M, MU_R)[0] is CT.CompanyType.CYCLICAL,
          "MU still CYCLICAL after tuning")


if __name__ == "__main__":
    run_suite(globals(), "ALL test_cyclical_norm PASSED")
