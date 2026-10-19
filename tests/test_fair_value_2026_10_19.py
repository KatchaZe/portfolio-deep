"""test_fair_value_2026_10_19 — the fair-value review of 2026-10-19.

  1. the DCF's consensus growth runs year by year (y1, y2, then years 3-5 only with
     ten analysts) and fades to stable growth; a thin year-5 row no longer sets it
  2. no FMP path -> Yahoo's revenue estimates; none at all -> organic history that
     fades, a confidence penalty and a Price cap of 3.0
  3. one-time charges (impairment always, restructuring / acquired IPR&D unless they
     recur) are added back to EBIT before anything values it
  4. a DCF far below the price stays the anchor (tests.test_damodaran_round2 #12)
  5. the terminal ROIC is floored at the stable WACC, not today's
  6. a thin margin swaps in the regression beta only for a small firm

Run: python -m tests.test_fair_value_2026_10_19
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from domain.facts import FinancialFacts             # noqa: E402
from domain.engine import deep_v82 as E, fcff_dcf   # noqa: E402
from domain import one_time                          # noqa: E402
from pipeline import validate                        # noqa: E402
from sources import fmp, yahoo                      # noqa: E402
from tests._harness import check, run_suite         # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RF = 0.051


def _facts(ticker="NEWCO", **kw):
    f = FinancialFacts(ticker)
    for k, v in kw.items():
        setattr(f, k, v)
    return f


def _operating(**kw):
    base = dict(price=100.0, shares_diluted=1e9, market_cap=100e9, revenue=50e9,
                operating_income=10e9, net_income=7.5e9, total_debt=10e9, cash=5e9,
                equity=40e9, beta=1.1, growth_lt=0.10, eps_gaap=7.5, forward_eps=8.2,
                revenue_annuals=[50e9, 45e9, 41e9, 37e9],
                operating_income_annuals=[10e9, 9e9, 8e9, 7e9],
                income_before_tax=9.5e9, tax_expense=2.0e9, capex=3e9, dep_amort=2e9,
                interest_expense=0.5e9)
    base.update(kw)
    return _facts(**base)


def test_consensus_path_drops_thin_out_years():
    print("1 consensus growth path: y1-y2 need 3 analysts; years 3-5 as reported with 10+,")
    print("  held at or below the year before with fewer (user decision 2026-10-19)")
    msft = _facts(rev_growth_path=[[0.178, 39], [0.196, 39], [0.2165, 27], [0.1747, 13], [0.3007, 13]])
    check(E.consensus_growth_path(msft) == [0.178, 0.196, 0.2165, 0.1747, 0.3],
          "13-analyst out-years are taken as reported, even rising (30.1% capped at 30%)",
          str(E.consensus_growth_path(msft)))
    thin = _facts(rev_growth_path=[[0.10, 20], [0.10, 20], [0.20, 6], [0.05, 4], [0.30, 2]])
    check(E.consensus_growth_path(thin) == [0.10, 0.10, 0.10, 0.05, 0.05],
          "a thin out-year may slow growth but never re-accelerate it",
          str(E.consensus_growth_path(thin)))
    two = _facts(rev_growth_path=[[0.40, 2], [0.30, 2]])
    check(E.consensus_growth_path(two) == [], "two analysts in year 1 are an opinion, not a path")
    legacy = _facts(fwd_growth_y1=0.10, n_analysts=20, fwd_growth_near=0.08, n_analysts_y2=18)
    check(E.consensus_growth_path(legacy) == [0.10, 0.08],
          "facts stored before the path field still read their y1 / y2")
    capped = _facts(rev_growth_path=[[0.90, 41], [0.70, 41]])
    check(E.consensus_growth_path(capped) == [E.GROWTH_CAP, E.GROWTH_CAP], "each year capped at 30%")


def test_dcf_fades_after_the_path():
    print("1b the DCF runs the path, then fades linearly to stable growth by year 10")
    kw = dict(revenue=100.0, margin_now=0.2, margin_target=0.2, g_high=0.2, g_stable=0.04,
              sales_to_capital=2.0, tax_now=0.2, tax_marginal=0.25, wacc=0.09,
              wacc_stable=0.08, roic_stable=0.12, cash=0, debt=0, shares=1)
    v = fcff_dcf.value(growth_path=[0.20, 0.16], **kw)
    gs = [r["growth"] for r in v["path"]]
    check(gs[:2] == [0.20, 0.16] and abs(gs[-1] - 0.04) < 1e-9,
          "years 1-2 from consensus, year 10 at stable growth", str(gs))
    steps = [round(a - b, 6) for a, b in zip(gs[1:], gs[2:])]
    check(len(set(steps)) == 1, "a straight line in between", str(steps))
    old = fcff_dcf.value(**kw)
    check([r["growth"] for r in old["path"]][:5] == [0.2] * 5,
          "callers without a path keep the five flat years (skill parity)")
    m = E.DeepV82Engine().evaluate(_operating(rev_growth_path=[[0.178, 39], [0.196, 39], [0.301, 2]]), rf=RF)
    check(m.dcf["inputs"]["growth_path"] == [0.178, 0.196, 0.196],
          "the engine hands the DCF the same path", str(m.dcf["inputs"].get("growth_path")))


def test_yahoo_path_skips_a_reported_year():
    print("2 Yahoo revenue estimates: years already reported are not forecasts")
    with open(os.path.join(ROOT, "tests", "fixtures", "MSFT", "yahoo_quotesummary.json"),
              encoding="utf-8") as fh:
        qs = json.load(fh)
    early = yahoo.parse_revenue_path(qs, "2026-03-31")
    late = yahoo.parse_revenue_path(qs, "2026-06-30")
    check(len(early) == 2 and len(late) == 1, "FY ending 2026-06-30 drops out once reported",
          f"{early} / {late}")
    check(late and abs(late[0][0] - (384073373830 / 329485991560 - 1)) < 0.01 and late[0][1] >= 3,
          "+1y growth and its analyst count", str(late))


def test_fmp_path_with_foreign_anchor():
    print("2b FMP path: a foreign filer anchors on FMP's own last-reported row")
    rows = [{"date": d, "revenueAvg": r, "numberAnalystsEstimatedRevenue": n}
            for d, r, n in (("2025-12-31", 100.0, 20), ("2026-12-31", 120.0, 20),
                            ("2027-12-31", 132.0, 15), ("2028-12-31", 140.0, 2))]
    p = fmp.parse_estimate_path(rows, "2026-06-30", latest_revenue=None)
    check([g for g, _ in p.get("rev_growth_path", [])] == [0.2, 0.1, 0.0606],
          "growth from FMP's own FY2025 row, same currency", str(p.get("rev_growth_path")))
    check([n for _, n in p["rev_growth_path"]] == [20, 15, 2], "each year keeps its analyst count")


def test_no_consensus_is_organic_faded_and_capped():
    print("2c no consensus: organic history, fading, -8 confidence, Price <= 3.0")
    celh = _facts(revenue_annuals_dated=[("2025-12-31", 2.5e9), ("2024-12-31", 1.36e9),
                                         ("2023-12-31", 1.32e9), ("2022-12-31", 0.65e9)],
                  acquisitions_annuals_dated=[("2025-12-31", 1.3e9), ("2023-12-31", 0.01e9)])
    g, note = E.organic_growth(celh)
    check(g is not None and abs(g - (1.36 / 1.32 - 1)) < 1e-6 and "2025-12-31" in (note or ""),
          "CELH's acquisition year (+85%) is left out of its organic growth", f"{g} {note}")
    cheap = _operating(price=40.0, market_cap=40e9)
    v = E.DeepV82Engine().evaluate(cheap, rf=RF)
    check(v.P is not None and v.P <= 3.0 and any("Price capped" in x for x in v.flags),
          "a cheap row without consensus is not rated cheap", f"P={v.P}")
    v2 = E.DeepV82Engine().evaluate(_operating(price=40.0, market_cap=40e9,
                                               rev_growth_path=[[0.10, 20], [0.09, 20]]), rf=RF)
    check(v2.P is not None and v2.P > 3.0, "with consensus the same row may rate above 3", f"P={v2.P}")
    check(v.dcf["inputs"]["growth_path"] and len(v.dcf["inputs"]["growth_path"]) == 1,
          "history is year 1 only, then the fade", str(v.dcf["inputs"]["growth_path"]))
    a, b = _operating(), _operating(rev_growth_path=[[0.1, 10]])
    validate._rescore(a)
    validate._rescore(b)
    check(a.confidence == b.confidence - validate.NO_PATH_PENALTY,
          "confidence drops by the no-path penalty", f"{a.confidence} vs {b.confidence}")


def test_one_time_charges_added_back():
    print("3 one-time charges come out of EBIT; recurring ones stay")
    rev_d = [("2025-12-31", 62e9), ("2024-12-31", 63e9), ("2023-12-31", 59e9), ("2022-12-31", 91e9)]
    pfe = _facts(revenue=63.7e9, operating_income=7.07e9, impairment_ttm=4.94e9,
                 restructuring_ttm=1.09e9, revenue_annuals_dated=rev_d,
                 restructuring_annuals_dated=[("2025-12-31", 1.09e9), ("2024-12-31", 1.99e9),
                                              ("2023-12-31", 1.97e9), ("2022-12-31", 1.0e9)])
    add = one_time.normalize_operating_income(pfe)
    check(abs(add - 4.94e9) < 1 and pfe.operating_income_gaap == 7.07e9,
          "impairment added back; restructuring filed 4 years running stays in EBIT",
          f"add {add/1e9:.2f}B")
    once = _facts(revenue=63.7e9, operating_income=7.07e9, restructuring_ttm=1.09e9,
                  revenue_annuals_dated=rev_d,
                  restructuring_annuals_dated=[("2025-12-31", 1.09e9)])
    check(abs(one_time.normalize_operating_income(once) - 1.09e9) < 1,
          "a restructuring filed once is added back")
    wild = _facts(revenue=10e9, operating_income=1e9, impairment_ttm=9e9)
    check(abs(one_time.normalize_operating_income(wild) - 2.5e9) < 1,
          "a mis-tagged concept cannot add more than 25% of revenue")
    clean = _facts(revenue=10e9, operating_income=1e9)
    check(one_time.normalize_operating_income(clean) == 0.0 and clean.operating_income == 1e9,
          "nothing filed, nothing changes")


def test_terminal_roic_floor_is_stable_wacc():
    print("5 terminal ROIC is floored at the stable WACC")
    hi_beta = _operating(beta=2.0, operating_income=2.5e9)
    v = E.DeepV82Engine().evaluate(hi_beta, rf=RF)
    i = v.dcf["inputs"]
    check(abs(i["roic_stable"] - i["wacc_stable"]) < 0.002 and i["wacc"] > i["wacc_stable"] + 0.01,
          "a low-return, high-beta firm earns no excess return in the terminal",
          f"roic {i['roic_stable']} wacc_stable {i['wacc_stable']} wacc {i['wacc']}")


def test_thin_margin_regression_beta_only_when_small():
    print("6 a thin margin swaps in the regression beta only for a small firm")
    kw = dict(operating_income=2.0e9, beta=1.9, beta_unlevered_industry=1.3,
              beta_industry="Auto & Truck")
    big = E.DeepV82Engine().evaluate(_operating(market_cap=1400e9, price=1400.0, **kw), rf=RF)
    small = E.DeepV82Engine().evaluate(_operating(market_cap=20e9, price=20.0, **kw), rf=RF)
    check(big.key_metrics["beta"] < 1.5, "a $1.4T firm keeps its bottom-up beta",
          str(big.key_metrics["beta"]))
    check(abs(small.key_metrics["beta"] - 1.9) < 1e-6, "a $20B firm takes the higher regression beta",
          str(small.key_metrics["beta"]))


def test_same_class_followups():
    print("7 the same classes, found by the follow-up audit")
    # (a) PEG/FVP earnings carry the EBIT add-back after tax
    v_b = E.DeepV82Engine().evaluate(_operating(operating_income=10e9, net_income=5e9), rf=RF)
    v_n = E.DeepV82Engine().evaluate(_operating(operating_income=10e9, net_income=5e9,
                                                operating_income_gaap=6e9), rf=RF)
    check((v_n.fv_fvp or 0) > (v_b.fv_fvp or 0),
          "an impairment taken out of EBIT is taken out of EPS too", f"{v_b.fv_fvp} -> {v_n.fv_fvp}")
    # (b) the Demand fade reads covered years only
    notes = []
    thin = _facts(rev_growth_path=[[0.25, 41], [0.15, 41], [0.30, 2]], fwd_growth_near=0.15,
                  fwd_growth_far=0.30)
    path = E.consensus_growth_path(thin)
    ratio, _ = E.fade_ratio(path[0], path[-1])
    check(abs(ratio - 0.15 / 0.25) < 1e-9, "fade read on the path, where the thin 30% is held to 15%",
          str(ratio))
    # (c) reverse DCF and DCF share one margin ceiling
    tm, label, _ = E.terminal_margin("X", 43.0, 100.0, industry_margin=0.25)
    check(abs(tm - 0.43) < 1e-9, "a 43% margin in a 25% industry is not cut to 40% in the reverse DCF",
          label)
    tm2, _, _ = E.terminal_margin("X", 65.0, 100.0, industry_margin=0.25)
    check(abs(tm2 - 0.50) < 1e-9, "above 2x industry it is held there, as in the DCF", str(tm2))


def test_yahoo_eps_in_listing_currency():
    print("8 Yahoo yearly EPS estimates are put in the listing's currency first")
    with open(os.path.join(ROOT, "tests", "fixtures", "NVO", "yahoo_quotesummary.json"),
              encoding="utf-8") as fh:
        nvo = yahoo.parse_consensus(json.load(fh))
    check(nvo["forward_eps"] and 2.5 < nvo["forward_eps"] < 4.5,
          "NVO NTM EPS in USD per ADR, not DKK 22.8", str(nvo["forward_eps"]))
    with open(os.path.join(ROOT, "tests", "fixtures", "MSFT", "yahoo_quotesummary.json"),
              encoding="utf-8") as fh:
        qs = json.load(fh)
    ms = yahoo.parse_consensus(qs)
    trend = {t["period"]: t["earningsEstimate"]["avg"]["raw"]
             for t in qs["quoteSummary"]["result"][0]["earningsTrend"]["trend"]
             if t["period"] in ("0y", "+1y")}
    check(min(trend.values()) <= ms["forward_eps"] <= max(trend.values()),
          "a USD filer's blend is untouched", str(ms["forward_eps"]))


def test_acquisition_year_from_ytd():
    print("2d an M&A year the 10-K leaves untagged is read from its largest 10-Q YTD")
    from sources import sec_edgar

    def e(start, end, val, form="10-K", filed="2026-02-20"):
        return {"start": start, "end": end, "val": val, "form": form, "filed": filed}
    cf = {"facts": {"us-gaap": {
        "Revenues": {"units": {"USD": [e("2023-01-01", "2023-12-31", 1.32e9),
                                       e("2024-01-01", "2024-12-31", 1.36e9),
                                       e("2025-01-01", "2025-12-31", 2.5e9)]}},
        "PaymentsToAcquireBusinessesNetOfCashAcquired": {"units": {"USD": [
            e("2024-01-01", "2024-12-31", 75e6),
            e("2025-01-01", "2025-06-30", 1256e6, "10-Q", "2025-08-05"),
            e("2025-01-01", "2025-09-30", 1279e6, "10-Q", "2025-11-05")]}}}}}
    d = sec_edgar.extract(cf)
    acq = dict(d.get("acquisitions_annuals_dated") or [])
    check(acq.get("2025-12-31") == 1279e6 and acq.get("2024-12-31") == 75e6,
          "FY2025 from the 9M YTD, FY2024 from the 10-K", str(acq))


def test_demand_charges_the_growth_year_deal():
    print("2e the Demand M&A penalty is charged on the year the growth was measured")
    celh = _facts(acquisitions_net=0.08e9, revenue=3.0e9,
                  revenue_annuals_dated=[("2025-12-31", 2.5e9), ("2024-12-31", 1.36e9)],
                  acquisitions_annuals_dated=[("2025-12-31", 1.28e9)])
    acq, rev = E._acquisitions_for_growth_year(celh)
    check(acq == 1.28e9 and rev == 2.5e9, "FY2025's $1.28B against FY2025 revenue", f"{acq} / {rev}")
    plain = _facts(acquisitions_net=0.1e9, revenue=3.0e9)
    check(E._acquisitions_for_growth_year(plain) == (0.1e9, 3.0e9), "no annual series: TTM as before")


def test_decisions_2026_10_19_round2():
    print("9 cyclical margin history carries the add-back; own P/E no longer moves P")
    from domain.one_time import annual_addbacks
    rev = [("2025-12-31", 100e9), ("2024-12-31", 95e9), ("2023-12-31", 90e9), ("2022-12-31", 85e9),
           ("2021-12-31", 80e9), ("2020-12-31", 70e9)]
    oi = [("2025-12-31", 2e9), ("2024-12-31", 9e9), ("2023-12-31", 8e9), ("2022-12-31", 9e9),
          ("2021-12-31", 7e9), ("2020-12-31", 6e9)]
    f = _facts(revenue_annuals_dated=rev, operating_income_annuals_dated=oi,
               impairment_annuals_dated=[("2025-12-31", 6e9)])
    check(annual_addbacks(f) == {"2025-12-31": 6e9}, "the impairment year gets its add-back")
    m = E._margin_history(f)
    check(abs(m[0] - 0.08) < 1e-9 and len(m) == 6, "2025 margin 2% -> 8% before the median", str(m[:2]))
    lo = E.DeepV82Engine().evaluate(_operating(price=40.0, market_cap=40e9, own_pe_pctile=0.1,
                                               rev_growth_path=[[0.1, 20], [0.09, 20]]), rf=RF)
    hi = E.DeepV82Engine().evaluate(_operating(price=40.0, market_cap=40e9, own_pe_pctile=0.9,
                                               rev_growth_path=[[0.1, 20], [0.09, 20]]), rf=RF)
    check(lo.P == hi.P, "own 5y P/E percentile 10% vs 90%: same Price score", f"{lo.P} vs {hi.P}")


if __name__ == "__main__":
    run_suite(globals(), "ALL test_fair_value_2026_10_19 PASSED")
