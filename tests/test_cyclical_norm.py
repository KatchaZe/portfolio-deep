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
    check(path[0]["revenue"] < 133.188e9 and path[2]["revenue"] < 70e9,
          "revenue converges from the $133B peak toward the ~$48B trend",
          str([r["revenue"] for r in path[:3]]))
    check(all(r["reinvestment"] >= 0 for r in path), "no capital released on the way down")
    check(abs(inp["growth_path"][-1] - 0.1144) < 0.002, "after convergence: trend growth 11.4%",
          str(inp["growth_path"]))
    check(v.anchor_value < 707.7, "MU FV below the $707.7 price (was $2,769)", str(v.anchor_value))
    km = v.key_metrics
    check(km["growth_pct"] < 15, "PEG growth = through-cycle trend, not the 30% cap", str(km["growth_pct"]))
    check(v.fv_peg < 1415 and v.fv_fvp < 1415,
          "PEG / FVP on normalized EPS (were $2,928 / $3,033)", f"{v.fv_peg} {v.fv_fvp}")
    c = km["cycle"]
    check(c and c["at_peak"] and c["revenue_converges"] and abs(c["peak_ratio"] - 2.76) < 0.05,
          "key_metrics.cycle carries the classification and the normalized revenue", str(c))


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


def test_E86_consensus_overrides_trend_revenue():
    v = _value("MU", rev_growth_path=[[0.20, 25], [0.05, 22]], fwd_growth_y1=0.20,
               n_analysts=25, fwd_growth_near=0.05, n_analysts_y2=22)
    check(v.company_type == "cyclical", "still cyclical (margin still reverts)")
    check(abs(v.dcf["inputs"]["growth_path"][0] - 0.20) < 1e-9, "consensus path replaces the trend revenue",
          str(v.dcf["inputs"]["growth_path"]))
    check(any("consensus covers it" in fl for fl in v.flags), "visible note: consensus overrides the trend")
    check(not v.key_metrics["no_consensus"], "no badge with consensus")


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
    check(abs(tn["margin"] - .275) < 1e-3 and tn["revenue"] < 0.5 * tp["revenue"],
          "MU relative point at normal margin and trend revenue", str((tn["margin"], tn["revenue"])))
    check(tn["ev_sales"] == tp["ev_sales"], "the observed EV/Sales is kept for display")
    nv = _facts("NVDA")
    tp2, _, _ = peers._target(nv, datetime.date(2026, 11, 24))
    check(peers._normalized_target(nv, tp2) == (tp2, None), "NVDA point unchanged")


def test_ref_quotes_the_constants():
    from docs.ref import content_b
    e = next(x for x in content_b.VAL.entries if x.id == "cycle")
    for txt in (f"≥{CT.CYCLE_REVENUE_DROP*100:.0f}%", f"เกิน {CT.CYCLE_MARGIN_RANGE*100:.0f} จุด",
                f"{CT.CYCLE_PEAK_MARGIN_X}×", f"≥{CT.NORMAL_MIN_YEARS} ปี", f"{CT.PEAK_REVENUE_RATIO}×",
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
