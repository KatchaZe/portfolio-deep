"""test_pillars_b — the B5-B8 pillar factors (2026-08-09). Offline.

Round 5 adds the four factors the audit said were missing, and fixes an ordering fault
found on the way in:

  B5  Demand: 3-year CAGR consistency. The band was read off ONE year, so a single
      40% year scored 5.0/5 whether or not the company had ever grown like that.
  B6  Execution: the beat/miss record — fetched, stored and drawn since v8.2, scored
      nowhere, though the v7.1 framework names it the Execution base.
  B7  Economics: ROIC relative to the Damodaran industry ROC, not just absolute.
  B8  Economics: CAP — how many of the last five years cleared the cost of capital.
  --  ORDERING: `[-1]` was taken as "the latest quarter" on a list whose order depends
      on the SOURCE. The Yahoo parser delivers newest-first and everything else
      oldest-first, so on the committed portfolio TSLA and REGN had PEAD, the
      source-reconciliation tie-break and the earnings circles all reading a quarter
      nine months stale.
  --  BUDGET: with four new adjustments, the per-pillar net is capped so that adding a
      factor cannot silently re-weight the ones already there.

Run: python -m tests.test_pillars_b
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from domain import pead                          # noqa: E402
from domain.engine import deep_v82 as E          # noqa: E402
from pipeline import consensus                   # noqa: E402

FAIL = []


def check(ok, label, detail=""):
    print(("  ok   " if ok else "  FAIL ") + label + (("  -> " + detail) if detail and not ok else ""))
    if not ok:
        FAIL.append(label)


# --------------------------------------------------------------------------
# ordering — the prerequisite for B6
# --------------------------------------------------------------------------
NEWEST_FIRST = [{"quarter": "2026-03-31", "grade": "beat", "surprise_pct": 17.1},
                {"quarter": "2025-12-31", "grade": "beat", "surprise_pct": 11.0},
                {"quarter": "2025-09-30", "grade": "miss", "surprise_pct": -10.5},
                {"quarter": "2025-06-30", "grade": "meet", "surprise_pct": -1.1}]


def test_latest_quarter_is_picked_by_date():
    print("ORD the newest quarter is found by date, not by list position")
    check(pead.latest(NEWEST_FIRST)["quarter"] == "2026-03-31",
          "newest-first list still yields the newest quarter",
          str(pead.latest(NEWEST_FIRST)))
    ordered = pead.chronological(NEWEST_FIRST)
    check([r["quarter"] for r in ordered] == sorted(r["quarter"] for r in NEWEST_FIRST),
          "chronological() sorts oldest -> newest", str([r["quarter"] for r in ordered]))
    sig = pead.signal(NEWEST_FIRST)
    check(sig["bias"] == "up" and sig.get("quarter") == "2026-03-31",
          "PEAD reads the real latest quarter (was reading one 9 months stale)", str(sig))
    # an already-correct list must be untouched
    old_first = list(reversed(NEWEST_FIRST))
    check(pead.chronological(old_first) == pead.chronological(NEWEST_FIRST),
          "both input orders converge on the same output")
    check(pead.latest([]) is None and pead.signal([])["bias"] is None, "empty stays empty")
    # rows with no date must not be able to claim to be newest
    undated = [{"grade": "miss"}, {"quarter": "2026-03-31", "grade": "beat"}]
    check(pead.latest(undated)["grade"] == "miss" or
          pead.chronological(undated)[0]["quarter"] == "2026-03-31",
          "undated rows sort last, never ahead of a dated one",
          str(pead.chronological(undated)))


def test_source_reconciliation_uses_the_same_rule():
    print("ORD reconcile_earnings compares the same quarter across sources")
    out = consensus.reconcile_earnings({"yahoo": NEWEST_FIRST,
                                        "finnhub": list(reversed(NEWEST_FIRST))})
    qs = [r["quarter"] for r in out["list"]]
    check(qs == sorted(qs), "the stored primary list comes out chronological", str(qs))
    check(out["disagree"] is False,
          "the same data in two orders is not a disagreement", str(out))


# --------------------------------------------------------------------------
# B5 · growth consistency
# --------------------------------------------------------------------------
def test_growth_consistency():
    print("B5 a one-year spike is separated from sustained growth")
    adj, note = E.growth_consistency(0.40, 0.38)
    check(adj == 0.25 and "ต่อเนื่อง" in note, "38% over 3y behind a 40% year -> sustained", note)
    adj, note = E.growth_consistency(0.087, 0.017)      # ABBV's real shape
    check(adj == -0.5 and "เร็วกว่า" in note, "1.7% over 3y behind an 8.7% year -> spike", note)
    # NVO's real shape: 20% over 3 years, 6% last year. Growth COLLAPSING. The first
    # version of this rule paid +0.25 here and called it "โตต่อเนื่องจริง".
    adj, note = E.growth_consistency(0.06, 0.20)
    check(adj == 0.0 and "ชะลอ" in note, "deceleration earns nothing, and says so", note)
    check(E.growth_consistency(-0.05, 0.20) == (0.0, None), "a decline is left to the base band")
    check(E.growth_consistency(0.10, None) == (0.0, None), "no 3y history -> silent")


# --------------------------------------------------------------------------
# B6 · beat consistency, asymmetric
# --------------------------------------------------------------------------
def _q(grades):
    return [{"quarter": f"2025-{i + 1:02d}-01", "grade": g} for i, g in enumerate(grades)]


def test_beat_consistency_is_asymmetric():
    print("B6 missing repeatedly costs twice what beating repeatedly earns")
    up, _ = E.beat_consistency(_q(["beat"] * 4))
    down, _ = E.beat_consistency(_q(["miss"] * 4))
    check(up == 0.25, "4/4 beats -> +0.25 (a guidance game, so weak evidence)", str(up))
    check(down == -0.5, "0/4 beats -> -0.5 (nobody misses their own number on purpose)", str(down))
    check(abs(down) > abs(up), "the penalty is the larger of the two", f"{up} vs {down}")
    mid, note = E.beat_consistency(_q(["beat", "miss", "beat", "miss"]))
    check(mid == 0.0 and note, "a mixed record is reported but not scored", f"{mid} {note}")
    # 'meet' is the intended outcome and must not dilute the rate
    only_meets = E.beat_consistency(_q(["meet"] * 5))
    check(only_meets == (0.0, None), "all-meet is not a track record either way", str(only_meets))
    val, note = E.beat_consistency(_q(["beat", "beat", "beat", "meet"]))
    check(val == 0.25 and "3/3" in note, "'meet' is excluded from the denominator", note)
    check(E.beat_consistency(_q(["beat", "miss"])) == (0.0, None),
          f"fewer than {E.BEAT_MIN_QUARTERS} graded quarters -> silent")
    check(E.beat_consistency(None) == (0.0, None), "no record -> silent")


def test_beat_consistency_is_order_independent():
    print("B6b the record reads the same whichever way the source ordered it")
    a = E.beat_consistency(NEWEST_FIRST)
    b = E.beat_consistency(list(reversed(NEWEST_FIRST)))
    check(a == b, "same answer both ways", f"{a} vs {b}")


# --------------------------------------------------------------------------
# B7 / B8 · Economics gains a peer group and a duration
# --------------------------------------------------------------------------
def _rmap(vals, start=2021):
    return {f"{start + i}-12-31": {"roic": v, "nopat": v * 10, "ic": 1000.0}
            for i, v in enumerate(vals)}


def test_sector_relative():
    print("B7 the same spread is judged against the industry it was earned in")
    check(E.sector_relative(0.30, 0.15)[0] == 0.5, "2x the industry -> +0.5")
    check(E.sector_relative(0.06, 0.15)[0] == -0.5, "0.4x the industry -> -0.5")
    check(E.sector_relative(0.16, 0.15)[0] == 0.0, "in line -> 0.0")
    check(E.sector_relative(0.20, None) == (0.0, None), "no industry figure -> silent")
    check(E.sector_relative(None, 0.15) == (0.0, None), "no ROIC -> silent")
    check(E.sector_relative(-0.05, 0.15) == (0.0, None), "a negative ROIC is not a ratio")


def test_competitive_advantage_period():
    print("B8 moat has a duration, not only a size")
    adj, note = E.competitive_advantage_period(_rmap([12.0, 11.0, 13.0, 14.0, 15.0]), 0.09)
    check(adj == 0.5 and "5/5" in note, "cleared the hurdle every year -> +0.5", note)
    adj, note = E.competitive_advantage_period(_rmap([5.0, 6.0, 4.0, 7.0, 12.0]), 0.09)
    check(adj == -0.5 and "1/5" in note, "cleared it once -> -0.5", note)
    adj, note = E.competitive_advantage_period(_rmap([5.0, 12.0, 4.0, 13.0, 14.0]), 0.09)
    check(adj == 0.0 and "3/5" in note, "three of five -> reported, not scored", note)
    check(E.competitive_advantage_period(_rmap([12.0, 13.0]), 0.09) == (0.0, None),
          f"fewer than {E.CAP_MIN_YEARS} years -> silent")
    check(E.competitive_advantage_period(None, 0.09) == (0.0, None), "no series -> silent")
    check(E.competitive_advantage_period(_rmap([12.0] * 5), 0) == (0.0, None), "no WACC -> silent")


def test_cap_uses_contiguous_years():
    """Same rule as everywhere else: a filing gap is not five years of evidence."""
    print("B8b CAP counts contiguous years only")
    gapped = {"2016-12-31": {"roic": 20.0, "nopat": 1, "ic": 1},
              "2017-12-31": {"roic": 20.0, "nopat": 1, "ic": 1},
              "2018-12-31": {"roic": 20.0, "nopat": 1, "ic": 1},
              "2019-12-31": {"roic": 20.0, "nopat": 1, "ic": 1},
              "2025-12-31": {"roic": 20.0, "nopat": 1, "ic": 1}}
    check(E.competitive_advantage_period(gapped, 0.09) == (0.0, None),
          "one contiguous year at the top is not a 5-year record",
          str(E.competitive_advantage_period(gapped, 0.09)))


# --------------------------------------------------------------------------
# adjustment budget
# --------------------------------------------------------------------------
def test_adjustment_budget():
    print("BUD adding a factor cannot silently re-weight the existing ones")
    notes = []
    got = E._apply_budget(2.0, 2.0 - 3.0, notes, "X")
    check(abs(got - (2.0 - E.ADJ_BUDGET)) < 1e-9, "a -3.0 net is capped to the budget", str(got))
    check(any("budget" in n for n in notes), "and the cap is stated in the breakdown", str(notes))
    notes = []
    got = E._apply_budget(2.0, 2.0 - E.ADJ_BUDGET, notes, "X")
    check(abs(got - (2.0 - E.ADJ_BUDGET)) < 1e-9 and not notes,
          "exactly at the budget is untouched and silent", f"{got} {notes}")
    # the pre-existing D stack (organic -1.0, peer -0.5, fade -0.5) must be unchanged
    notes = []
    s = E._r_demand(0.16, 0.30, 0.15, 0.30, notes)
    check(abs(s - 1.0) < 1e-9,
          "the rubric's own worst-case D stack still scores exactly as before", str(s))


def main():
    for t in (test_latest_quarter_is_picked_by_date, test_source_reconciliation_uses_the_same_rule,
              test_growth_consistency, test_beat_consistency_is_asymmetric,
              test_beat_consistency_is_order_independent, test_sector_relative,
              test_competitive_advantage_period, test_cap_uses_contiguous_years,
              test_adjustment_budget):
        t()
    print()
    if FAIL:
        print("test_pillars_b FAILED:", "; ".join(FAIL))
        sys.exit(1)
    print("test_pillars_b OK - B5-B8 hold")


if __name__ == "__main__":
    main()
