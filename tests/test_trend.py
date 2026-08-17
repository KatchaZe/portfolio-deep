"""test_trend — the 5-year performance strip (T5). Offline; uses committed fixtures.

The strip exists because every DEEP subscore is a one- or two-period snapshot, so the
question "is this compounding, or was last year a good year?" had no answer anywhere in
the app. Its whole value is that the five lines are TRUE, so the checks below are about
the ways a trend chart lies:

  T5-A  a ratio built by zipping two series on INDEX (P3-1, one level down)
  T5-B  a ratio built from a half-filed year (P2-3)
  T5-C  a GAP drawn as a continuous line
  T5-D  years from 2017 presented inside a panel headed "5 ปีล่าสุด"
  T5-E  the watchlist path and the holdings path disagreeing (`2df2ce2` parity bug)
  T5-F  a CAGR computed out of zero or a loss
  T5-G  the score leg moving a row before there is enough history to justify it

Run: python -m tests.test_trend
"""
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from domain import trend                          # noqa: E402
from domain.engine import deep_v82 as E           # noqa: E402
from domain.facts import FinancialFacts           # noqa: E402
from sources import sec_edgar                     # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FAIL = []


def check(ok, label, detail=""):
    print(("  ok   " if ok else "  FAIL ") + label + (("  -> " + detail) if detail and not ok else ""))
    if not ok:
        FAIL.append(label)


def _facts(ticker):
    ff = FinancialFacts(ticker)
    with open(os.path.join(HERE, "fixtures", ticker, "sec_companyfacts.json"), encoding="utf-8") as fh:
        sec_edgar.populate(ff, json.load(fh))
    return ff


def _row(out, key):
    return next((r for r in out.get("rows", []) if r["key"] == key), None)


# --------------------------------------------------------------------------
def test_alignment_is_by_date_not_index():
    """T5-A. Give revenue 3 years and gross profit 6, overlapping on 2 — a margin may
    only be reported for the overlap, and only at the right values."""
    print("T5-A margins align on the fiscal-year date, never on list position")
    f = FinancialFacts("X",
                       revenue_annuals_dated=[["2025-12-31", 100.0], ["2024-12-31", 90.0],
                                              ["2023-12-31", 80.0]],
                       gross_profit_annuals_dated=[["2025-12-31", 60.0], ["2024-12-31", 45.0],
                                                   ["2019-12-31", 10.0], ["2018-12-31", 9.0],
                                                   ["2017-12-31", 8.0], ["2016-12-31", 7.0]])
    r = _row(trend.build(f), "gross_margin")
    check(r is not None and r["n"] == 2, "only the overlapping years appear",
          f"n={(r or {}).get('n')}")
    if r:
        got = {p["fy"]: p["v"] for p in r["points"]}
        check(got == {"2024": 50.0, "2025": 60.0}, "and each year divides its OWN revenue",
              str(got))
        # index-zipping would have paired 2025 gross with 2025 revenue but 2023 revenue
        # with a 2019 gross profit and reported 12.5%
        check(12.5 not in got.values(), "no index-zipped value leaks through", str(got))


def test_partial_year_is_dropped():
    """T5-B. FCF needs BOTH cash from operations and capex. A year with only CFO must
    not appear as if capex were zero — that inflates FCF, the flattering direction."""
    print("T5-B a year missing one leg is dropped, not half-counted")
    fcf = trend.free_cash_flow(
        [["2025-12-31", 100.0], ["2024-12-31", 90.0], ["2023-12-31", 80.0]],
        [["2025-12-31", 30.0], ["2024-12-31", 25.0]])          # 2023 capex not filed
    check(set(fcf) == {"2025-12-31", "2024-12-31"}, "only fully filed years survive", str(fcf))
    check(fcf.get("2025-12-31") == 70.0, "FCF = CFO - capex", str(fcf))


def test_gap_breaks_the_window():
    """T5-C. AVGO's real shape: the long-term debt tag is absent for three years, so
    those years leave the invested-capital series. The window must NOT then reach back
    past the hole and draw 2018→2021→2025 as one line."""
    print("T5-C a gap in the years breaks the window instead of being smoothed over")
    dates = ["2025-12-31", "2021-12-31", "2020-12-31", "2019-12-31", "2018-12-31"]
    win = trend._window(dates, years=5)
    check(win == ["2025-12-31"], "only the contiguous run from the newest year is kept", str(win))
    contiguous = ["2025-12-31", "2024-12-31", "2023-12-31", "2022-12-31", "2021-12-31"]
    check(trend._window(contiguous, 5) == sorted(contiguous), "an unbroken run is kept whole")
    # end to end on the fixture that produced the bug
    r = _row(trend.build(_facts("AVGO")), "roic")
    if r:
        yrs = [int(p["fy"]) for p in r["points"]]
        check(yrs == list(range(yrs[0], yrs[-1] + 1)), "AVGO ROIC years are consecutive", str(yrs))
        check(max(p["v"] for p in r["points"]) < 100,
              "and no 130% ROIC from a debt leg that was not filed",
              str([p["v"] for p in r["points"]]))


def test_stale_rows_are_not_shown_as_current():
    """T5-D. ORCL tags CostOfRevenue for four years ending 2018. Deriving a gross margin
    from it is arithmetically fine and completely misleading under a '5 ปีล่าสุด' heading."""
    print("T5-D a row whose newest year is stale is dropped")
    out = trend.build(_facts("ORCL"))
    gm = _row(out, "gross_margin")
    check(gm is None, "ORCL shows no gross-margin row rather than a 2018 one",
          str([p["fy"] for p in (gm or {}).get("points", [])]))
    rev = _row(out, "revenue")
    check(rev is not None and int(rev["points"][-1]["fy"]) >= 2024,
          "while the rows that ARE shown are current", str((rev or {}).get("points")))


def test_watchlist_and_holdings_paths_agree():
    """T5-E. analyze_row holds a live FinancialFacts; portfolio_view reads the stored
    dict. Same function, same output, or the two tabs disagree about the same company."""
    print("T5-E object path and stored-dict path produce identical strips")
    ff = _facts("MSFT")
    a = trend.build(ff)
    b = trend.build(json.loads(json.dumps(ff.to_dict())))     # through the store
    check(json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True),
          "byte-identical", f"{[r['key'] for r in a.get('rows', [])]} vs "
                            f"{[r['key'] for r in b.get('rows', [])]}")


def test_cagr_needs_a_positive_base():
    """T5-F. A growth rate out of a loss or a zero has no meaning; report nothing."""
    print("T5-F CAGR is None when the starting value is not positive")
    for pts, why in (([{"v": 0.0}, {"v": 10.0}], "zero start"),
                     ([{"v": -5.0}, {"v": 10.0}], "loss start"),
                     ([{"v": 10.0}, {"v": -5.0}], "loss end")):
        check(trend._cagr_pct(pts) is None, f"no CAGR from a {why}", str(trend._cagr_pct(pts)))
    # 3 points span 2 years: 100 -> 110 -> 121 is 10%/yr. (2 points span ONE year, so
    # 100 -> 121 is 21% — the off-by-one that makes a 5-point strip a 4-year CAGR.)
    check(trend._cagr_pct([{"v": 100.0}, {"v": 110.0}, {"v": 121.0}]) == 10.0,
          "3 points = 2 years of compounding", str(trend._cagr_pct([{"v": 100.0}, {"v": 110.0}, {"v": 121.0}])))
    check(trend._cagr_pct([{"v": 100.0}, {"v": 121.0}]) == 21.0,
          "2 points = 1 year", str(trend._cagr_pct([{"v": 100.0}, {"v": 121.0}])))
    # ORCL's FY2025 free cash flow is negative — the row must still render, without a CAGR
    r = _row(trend.build(_facts("ORCL")), "fcf")
    check(r is not None and r["summary"] is None,
          "ORCL FCF row exists with no CAGR (latest year negative)", str((r or {}).get("summary")))


def test_incremental_roic_needs_a_real_capital_change():
    """A near-zero change in invested capital makes ΔNOPAT/ΔIC explode — the REV-13 trap."""
    print("T5-F2 incremental ROIC is withheld when the capital base barely moved")
    rmap = {"2024-12-31": {"roic": 20.0, "nopat": 200.0, "ic": 1000.0},
            "2025-12-31": {"roic": 21.0, "nopat": 210.0, "ic": 1001.0}}
    val, why = trend.incremental_roic_pct(rmap, ["2024-12-31", "2025-12-31"])
    check(val is None and why, "returns None with a reason, not a four-digit number", str(val))
    rmap["2025-12-31"]["ic"] = 1500.0
    val2, _ = trend.incremental_roic_pct(rmap, ["2024-12-31", "2025-12-31"])
    check(val2 == 2.0, "and computes normally once capital really moved", str(val2))


def test_fcf_durability_leg():
    """T5-G. The E_exec addition: bounded, and silent until there is enough history."""
    print("T5-G FCF durability moves E_exec by at most +/-0.5 and needs 3+ years")
    def row(n, cagr, last):
        return [{"key": "fcf", "n": n, "summary": cagr,
                 "points": [{"fy": str(2020 + i), "v": last} for i in range(n)]}]
    check(E.fcf_durability(row(2, 20.0, 10.0)) == (0.0, None), "skipped with only 2 years")
    check(E.fcf_durability(None) == (0.0, None), "skipped with no strip at all")
    adj, note = E.fcf_durability(row(5, 12.0, 10.0))
    check(adj == 0.5 and note, "compounding FCF -> +0.5", f"{adj} {note}")
    adj, note = E.fcf_durability(row(5, -8.0, 10.0))
    check(adj == -0.5 and note, "shrinking FCF -> -0.5", f"{adj} {note}")
    adj, note = E.fcf_durability(row(5, 0.5, 10.0))
    check(adj == 0.0 and note, "flat FCF -> 0 but still reported", f"{adj} {note}")
    adj, note = E.fcf_durability(row(5, 30.0, -2.0))
    check(adj == -0.5, "a negative latest year overrides a flattering CAGR", f"{adj} {note}")


def test_engine_degrades_silently_without_the_new_series():
    """Stored facts written before T5 have none of the dated series. Those rows must
    score EXACTLY as before — a new feature may not silently re-rate the portfolio."""
    print("T5-H old stored facts are unaffected (no dated series -> leg skipped)")
    old = FinancialFacts(
        ticker="OLD", beta=1.0, price=50.0, revenue=10e9, operating_income=2e9,
        net_income=1.5e9, shares_diluted=1e9, market_cap=50e9, cash=1e9, equity=8e9,
        total_debt=2e9, forward_eps=1.8, growth_lt=0.1, cfo=1.8e9, total_assets=20e9,
        revenue_annuals=[10e9, 9e9], operating_income_annuals=[2e9, 1.7e9])
    check(trend.build(old) == {}, "no strip is built", str(trend.build(old)))
    v = E.DeepV82Engine().evaluate(old, rf=0.045)
    note = next((n for n in v.subscores["breakdown"] if "FCF-durability" in n), "")
    check("skipped" in note, "and the score leg says it was skipped", note)


def test_fixture_values_match_the_filings():
    """Spot-check against figures Microsoft published for FY2025."""
    print("T5-I fixture values reconcile to the 10-K")
    out = trend.build(_facts("MSFT"))
    rev = _row(out, "revenue")["points"][-1]["v"]
    gm = _row(out, "gross_margin")["points"][-1]["v"]
    om = _row(out, "op_margin")["points"][-1]["v"]
    fcf = _row(out, "fcf")["points"][-1]["v"]
    check(abs(rev - 281.724e9) < 1e8, "FY2025 revenue $281.7B", f"{rev/1e9:.1f}B")
    check(abs(om - 45.6) < 0.2, "FY2025 operating margin 45.6%", str(om))
    check(abs(gm - 68.8) < 0.2, "FY2025 gross margin 68.8%", str(gm))
    # 2026-08-17: FCF now DEDUCTS stock-based compensation. CFO adds SBC back as a
    # non-cash item by construction, so "CFO - capex" handed the cost straight back to
    # the company — and Damodaran is explicit that SBC is a real expense whose add-back
    # is "an indefensible practice". MSFT FY2025: 136.2 - 64.6 - 11.97 = 59.6B.
    # The reconciliation to the 10-K is unchanged; only the definition of the line is.
    check(abs(fcf - 59.6e9) < 3e8,
          "FY2025 free cash flow $59.6B (CFO 136.2 - capex 64.6 - SBC 12.0)",
          f"{fcf/1e9:.1f}B")
    _sbc = _row(out, "fcf").get("note") or ""
    check("SBC" in _sbc, "and the row states that SBC was deducted", _sbc)


def test_incremental_roic_needs_a_profitable_start():
    """C, found by reading the live dashboard. Incremental ROIC assumes the extra profit
    came from the extra capital. A company that BEGAN the window losing money breaks
    that: the numerator is the turnaround, not the return on anything new.

    HIMS: NOPAT -99.9M -> +91.7M on +49.5M of capital, shown as 'new capital earned
    387%'. The existing guard only tested the denominator and capital HAD grown 19%, so
    it passed. `_cagr_pct` in this same module already refuses a non-positive start;
    the rule had simply never been carried across."""
    print("T5-F3 a turnaround is not a return on new capital")
    hims = {"2021-12-31": {"nopat": -99.9e6, "ic": 262.8e6, "roic": -38.0},
            "2025-12-31": {"nopat": 91.7e6, "ic": 312.3e6, "roic": 29.4}}
    inc, why = trend.incremental_roic_pct(hims, ["2021-12-31", "2025-12-31"])
    check(inc is None, "withheld, not reported as +387.2%", str(inc))
    check(why and "ติดลบ" in why, "and the reason names the negative start", str(why))

    ok = {"2021-12-31": {"nopat": 100e6, "ic": 500e6, "roic": 20.0},
          "2025-12-31": {"nopat": 200e6, "ic": 900e6, "roic": 22.2}}
    check(trend.incremental_roic_pct(ok, ["2021-12-31", "2025-12-31"])[0] == 25.0,
          "a profitable start is unaffected: ΔNOPAT 100M / ΔIC 400M = 25%")

    # a profitable start that DECLINES must still measure — new capital destroying
    # value is the signal Damodaran cares most about (PFE), not a data problem
    dn = {"2021-12-31": {"nopat": 200e6, "ic": 500e6, "roic": 40.0},
          "2025-12-31": {"nopat": 50e6, "ic": 900e6, "roic": 5.6}}
    check(trend.incremental_roic_pct(dn, ["2021-12-31", "2025-12-31"])[0] == -37.5,
          "and a NEGATIVE incremental return is still reported")

    store = os.path.join(os.path.dirname(HERE), "data", "portfolio.json")
    if os.path.exists(store):
        with open(store, encoding="utf-8") as fh:
            f = (json.load(fh).get("facts") or {}).get("HIMS")
        if f and f.get("ic_components_dated"):
            rows = {r["key"]: r for r in (trend.build(f) or {}).get("rows", [])}
            r = rows.get("roic")
            check(not r or r.get("summary_label") != "ทุนใหม่",
                  "and HIMS no longer shows one on the real stored facts",
                  str(r and (r.get("summary_label"), r.get("summary"))))


def test_summary_declares_its_own_unit():
    """B. The dashboard chose the suffix from the ROW's unit, so every pct row got
    'pp' — printing the ROIC row's incremental RETURN as a point CHANGE."""
    print("T5-K each summary says what unit it is in")
    by = {r["key"]: r for r in trend.build(_facts("MSFT"))["rows"]}
    check(by["revenue"]["summary_unit"] == "%", "revenue CAGR is a rate")
    check(by["gross_margin"]["summary_unit"] == "pp", "margin change is in points")
    check(by["fcf"]["summary_unit"] == "%", "FCF CAGR is a rate")
    roic = by.get("roic")
    if roic:
        want = "%" if roic["summary_label"] == "ทุนใหม่" else "pp"
        check(roic["summary_unit"] == want,
              f"ROIC row labelled '{roic['summary_label']}' carries '{want}'",
              str(roic["summary_unit"]))
    html = open(os.path.join(os.path.dirname(HERE), "index.html"), encoding="utf-8").read()
    check("r.summary_unit" in html, "and index.html reads it instead of guessing")


def test_the_strip_states_its_own_period():
    """D. Every figure in the strip is a FISCAL YEAR; the valuation on the same card is
    TTM. They differ by roughly (months since the year ended) x (growth) — 0% for MSFT,
    +18% for AVGO whose year ended nine months ago — and the card said which was which
    nowhere.

    Converting the strip to TTM was rejected on evidence: ASML, NVO and TSM file NO
    quarterly data to SEC, so a TTM series would delete the strip for exactly the
    foreign filers this module was extended to serve. So the period is declared instead,
    and it travels in the PAYLOAD: a label the dashboard derives stops being true the
    moment the dashboard changes, while a fact the backend states can be tested."""
    print("T5-L the strip declares the period it is measuring")
    out = trend.build(_facts("MSFT"), today=dt.date(2026, 8, 11))
    check(out.get("as_of") == "2025-06-30" or (out.get("as_of") or "").endswith("-06-30"),
          "as_of is the newest fiscal-year END, not a year number", str(out.get("as_of")))
    check(out.get("fy_to") and out.get("fy_from") and out["fy_from"] < out["fy_to"],
          "and the span is stated", f"{out.get('fy_from')}-{out.get('fy_to')}")
    check(isinstance(out.get("lag_months"), float),
          "lag to today is measured in the backend", str(out.get("lag_months")))

    # the lag is a function of the reference date, not a constant baked in
    a = trend.build(_facts("MSFT"), today=dt.date(2026, 8, 11))["lag_months"]
    b = trend.build(_facts("MSFT"), today=dt.date(2026, 11, 11))["lag_months"]
    check(round(b - a) == 3, "and it moves with the calendar", f"{a} -> {b}")

    html = open(os.path.join(os.path.dirname(HERE), "index.html"), encoding="utf-8").read()
    for needle in ("function trendPeriod", "t.fy_to", "t.lag_months", "trendPeriod(t)"):
        check(needle in html, f"index.html renders it: {needle}")


def test_frontend_renders_the_strip():
    print("T5-J dashboard renders the strip")
    html = open(os.path.join(os.path.dirname(HERE), "index.html"), encoding="utf-8").read()
    for needle in ("function trendStrip", "function spark", "x.trend5y", "vs_average_pp"):
        check(needle in html, f"index.html has {needle}")


def main():
    for t in (test_alignment_is_by_date_not_index, test_partial_year_is_dropped,
              test_gap_breaks_the_window, test_stale_rows_are_not_shown_as_current,
              test_watchlist_and_holdings_paths_agree, test_cagr_needs_a_positive_base,
              test_incremental_roic_needs_a_real_capital_change,
              test_incremental_roic_needs_a_profitable_start,
              test_summary_declares_its_own_unit, test_the_strip_states_its_own_period,
              test_fcf_durability_leg,
              test_engine_degrades_silently_without_the_new_series,
              test_fixture_values_match_the_filings, test_frontend_renders_the_strip):
        t()
    print()
    if FAIL:
        print("test_trend FAILED:", "; ".join(FAIL))
        sys.exit(1)
    print("test_trend OK - the 5-year strip cannot lie in any of the ways checked")


if __name__ == "__main__":
    main()
