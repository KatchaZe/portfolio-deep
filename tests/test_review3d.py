"""test_review3d — guards for the round-3 JUDGEMENT items D1-D5 (2026-08-08). Offline.

These were the five points held back from the first pass because each needed a
decision rather than an obvious fix. Four changed behaviour; one was investigated,
implemented, measured, and reverted. All five are pinned here, including the reverted
one — a decision not to change something is only durable if it is written down with
the evidence, otherwise the same "improvement" gets reapplied next time.

  D1  a missing pillar must be scored NEUTRAL, never allowed to inherit the average
      of the pillars that happened to be measurable.
  D2  the operating-margin trend must compare two fiscal years, not a TTM against a
      fiscal year two periods back.
  D3  the ROIC trend must compare two ROICs measured the same way — same period AND
      the same R&D treatment. The raw-vs-R&D-capitalized mismatch alone was showing
      ASML a -25.4pp "collapse" that never happened.
  D4  a [low, high] pair built from two disagreeing methods is not a confidence band.
  D5  reinvestment = revenue CAGR / ROIC is CORRECT and must stay. Margin expansion
      costs no capital.

Run: python -m tests.test_review3d
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from domain.engine import deep_v82 as E          # noqa: E402
from domain.facts import FinancialFacts          # noqa: E402

FAIL = []


def check(ok, label, detail=""):
    print(("  ok   " if ok else "  FAIL ") + label + (("  -> " + detail) if detail and not ok else ""))
    if not ok:
        FAIL.append(label)


# --------------------------------------------------------------------------
# D1 · a data gap must never help
# --------------------------------------------------------------------------
def test_missing_pillar_scored_neutral():
    print("D1 a missing pillar is scored NEUTRAL, not the average of the others")
    full = {"D": 4.0, "E_exec": 4.0, "E_econ": 4.0, "P": 4.0}
    check(abs(E.composite(full) - 4.0) < 1e-9,
          "a fully measured row is untouched", str(E.composite(full)))

    strong = {"D": 5.0, "E_exec": 5.0, "E_econ": None, "P": 4.0}
    got = E.composite(strong)
    renorm = (5.0 * .20 + 5.0 * .20 + 4.0 * .30) / .70
    neutral = 5.0 * .20 + 5.0 * .20 + E.NEUTRAL_SCORE * .30 + 4.0 * .30
    check(abs(got - min(renorm, neutral)) < 1e-9, "composite = min(renormalised, neutral)",
          f"got {got}, renorm {renorm:.2f}, neutral {neutral:.2f}")
    check(got < renorm, "and on a strong row that BINDS", f"{got:.2f} vs renorm {renorm:.2f}")

    # one-sided: a WEAK row must not be lifted by the neutral imputation
    weak = {"D": 1.0, "E_exec": 1.0, "E_econ": None, "P": 1.0}
    check(abs(E.composite(weak) - 1.0) < 1e-9,
          "a weak row is never RAISED by the bound (min, not blend)", str(E.composite(weak)))

    # The guarantee, stated exactly. "Unmeasured" is NOT the same claim as "measured
    # and terrible" — treating a data gap as a 0 would be its own distortion, and the
    # codebase already rejects that pattern (REV-2). What it must never be is BETTER
    # than neutral. So the bound is: missing <= the same row with that pillar at 2.5.
    for row in ({"D": 5.0, "E_exec": 5.0, "E_econ": 0.0, "P": 3.0},
                {"D": 4.0, "E_exec": 4.0, "E_econ": 4.0, "P": 4.0},
                {"D": 2.0, "E_exec": 3.0, "E_econ": 1.0, "P": 2.0},
                {"D": 5.0, "E_exec": 4.5, "E_econ": 5.0, "P": 4.0}):
        for pillar in ("E_econ", "P", "D", "E_exec"):
            gone = E.composite(dict(row, **{pillar: None}))
            at_neutral = E.composite(dict(row, **{pillar: E.NEUTRAL_SCORE}))
            check(gone <= at_neutral + 1e-9,
                  f"missing {pillar} never scores above that pillar being neutral",
                  f"row {row}: missing {gone:.3f} vs neutral {at_neutral:.3f}")


# --------------------------------------------------------------------------
# D2 · margin trend spans one year, and never degenerates
# --------------------------------------------------------------------------
def _facts(**kw):
    base = dict(ticker="T", price=100.0, shares_diluted=1e9, beta=1.0, market_cap=100e9,
                total_debt=10e9, cash=5e9, equity=40e9, capex=5e9, dep_amort=4e9,
                cfo=9e9, total_assets=80e9, forward_eps=5.0, growth_lt=0.10,
                income_before_tax=10e9, tax_expense=2.1e9, net_income=8e9)
    base.update(kw)
    return FinancialFacts(**base)


def test_margin_trend_is_fiscal_year_over_year():
    print("D2 margin trend = FY0 vs FY1, and never degenerates to 'flat'")
    # margin FY0 20/100 = 20%, FY1 15/90 = 16.7% -> up. TTM is a HOTTER 26/110 = 23.6%,
    # and the old code compared that against FY1 — same direction here, so use a case
    # where the TTM and the fiscal year disagree in DIRECTION (NVDA's shape).
    f = _facts(revenue=110e9, operating_income=26e9,
               revenue_annuals=[100e9, 90e9, 80e9], operating_income_annuals=[19e9, 18e9])
    # FY0 19/100 = 19.0%  vs FY1 18/90 = 20.0% -> DOWN (a real compression)
    # TTM     26/110 = 23.6% vs FY1 20.0%      -> up   (what the old code said)
    v = E.DeepV82Engine().evaluate(f, rf=0.045)
    note = next((n for n in v.subscores["breakdown"] if "margin-trend" in n), "")
    check("down" in note, "reads the fiscal-year compression, not the TTM", note)

    # a filer whose TTM IS its last fiscal year (ORCL/TSM/NVO/ASML shape) must still
    # produce a real trend — this is why TTM-vs-FY0 was rejected as the fix
    f2 = _facts(revenue=100e9, operating_income=25e9,
                revenue_annuals=[100e9, 90e9], operating_income_annuals=[25e9, 18e9])
    v2 = E.DeepV82Engine().evaluate(f2, rf=0.045)
    note2 = next((n for n in v2.subscores["breakdown"] if "margin-trend" in n), "")
    check("up" in note2, "TTM == FY0 still yields a real trend, not 'flat'", note2)


# --------------------------------------------------------------------------
# D3 · the ROIC trend compares like with like
# --------------------------------------------------------------------------
def test_roic_trend_matches_rd_basis():
    print("D3 ROIC trend uses the same R&D basis on both sides")
    # heavy, stable R&D: capitalization lifts ROIC materially, and the LEVEL is
    # unchanged year to year, so an honest trend is ~0. The old code compared an
    # adjusted current ROIC against a raw prior one and printed a large fake drop.
    rnd = [8e9] * 8
    f = _facts(revenue=100e9, operating_income=20e9,
               revenue_annuals=[100e9, 92e9], operating_income_annuals=[20e9, 18.4e9],
               rnd_annuals=rnd, rnd_expense=8e9,
               equity_prior=37e9, cash_prior=4.6e9, total_debt_prior=9.2e9)
    v = E.DeepV82Engine().evaluate(f, rf=0.045)
    note = next((n for n in v.subscores["breakdown"] if "ROIC-trend" in n), "")
    check("ROIC-trend" in note, "trend is reported", note)
    val = float(note.split("d ")[1].split("pp")[0]) if "d " in note else None
    check(val is not None and abs(val) < 6.0,
          "a steady R&D-heavy firm shows no large fake move", f"{note} (delta {val}pp)")
    check("FY0 vs FY1" in note, "and the note says which periods it used", note)


def test_roic_trend_skipped_when_basis_cannot_match():
    print("D3b trend is dropped, not faked, when the prior year cannot be capitalized")
    # A filer that only reports R&D in the latest year: the current side capitalizes
    # (one positive value, padded), the prior side has nothing positive to work with.
    # Reporting a delta here would compare an adjusted ROIC against a raw one — the
    # exact mismatch D3 exists to remove — so there must be NO delta at all.
    f = _facts(revenue=100e9, operating_income=20e9,
               revenue_annuals=[100e9, 92e9], operating_income_annuals=[20e9, 18.4e9],
               rnd_annuals=[8e9, 0, 0, 0, 0, 0, 0], rnd_expense=8e9,
               equity_prior=37e9, cash_prior=4.6e9, total_debt_prior=9.2e9)
    v = E.DeepV82Engine().evaluate(f, rf=0.045)
    notes = " ".join(v.subscores["breakdown"])
    check("skipped" in notes, "no trend adjustment is applied", notes)
    check(any("ROIC trend skipped" in fl for fl in v.flags),
          "and the card says why", str([fl for fl in v.flags if "trend" in fl]))


# --------------------------------------------------------------------------
# D4 · a disagreement is labelled, not smoothed away
# --------------------------------------------------------------------------
def test_fv_method_disagreement_flagged():
    print("D4 far-apart methods are labelled as a conflict, not a confidence band")
    # ABBV's shape: a healthy PEG value and an FVP that survives the 10%-of-price
    # guard while sitting nowhere near it
    f = _facts(price=254.49, revenue=62.8e9, operating_income=15.3e9, net_income=4.3e9,
               shares_diluted=1.78e9, eps_gaap=2.42, market_cap=453e9,
               total_debt=67e9, cash=5.5e9, equity=3.3e9,
               revenue_annuals=[61.2e9, 56.3e9], operating_income_annuals=[15.1e9, 9.1e9],
               capex=1.0e9, dep_amort=8.0e9, cfo=22e9, total_assets=135e9,
               forward_eps=14.05, growth_lt=0.10, beta=0.28,
               income_before_tax=5.0e9, tax_expense=0.7e9)
    v = E.DeepV82Engine().evaluate(f, rf=0.045)
    if v.fv_peg and v.fv_fvp and max(v.fv_peg, v.fv_fvp) / min(v.fv_peg, v.fv_fvp) >= E.FV_DISAGREE_RATIO:
        check(v.key_metrics.get("fv_disagreement_x") is not None,
              "ratio is exposed on key_metrics", str(v.key_metrics.get("fv_disagreement_x")))
        check(any("methods disagree" in fl for fl in v.flags), "and flagged in words",
              str([fl for fl in v.flags if "disagree" in fl]))
        check(v.range_low is not None and v.range_high is not None,
              "the range itself is NOT suppressed - a real disagreement is information")
    else:
        check(False, "fixture no longer produces a method disagreement",
              f"peg {v.fv_peg} fvp {v.fv_fvp}")


def test_no_false_disagreement_when_methods_agree():
    print("D4b methods that agree are not flagged")
    f = _facts(revenue=100e9, operating_income=25e9,
               revenue_annuals=[100e9, 90e9], operating_income_annuals=[25e9, 22e9])
    v = E.DeepV82Engine().evaluate(f, rf=0.045)
    if v.fv_peg and v.fv_fvp:
        ratio = max(v.fv_peg, v.fv_fvp) / min(v.fv_peg, v.fv_fvp)
        if ratio < E.FV_DISAGREE_RATIO:
            check(v.key_metrics.get("fv_disagreement_x") is None,
                  "no flag when the two methods are close", f"ratio {ratio:.2f}")


def test_frontend_shows_the_conflict():
    print("D4c dashboard renders the conflict marker")
    here = os.path.dirname(os.path.abspath(__file__))
    html = open(os.path.join(os.path.dirname(here), "index.html"), encoding="utf-8").read()
    check("fv_disagreement_x" in html, "index.html reads key_metrics.fv_disagreement_x")


# --------------------------------------------------------------------------
# D5 · reinvestment stays on the revenue CAGR — the reverted change
# --------------------------------------------------------------------------
def test_reinvestment_is_revenue_driven():
    """Reinvestment is dCapital/NOPAT. Under a constant sales-to-capital ratio,
    dCapital = Capital x (REVENUE growth) and ROIC = NOPAT/Capital, so the charge is
    exactly revenue_growth / ROIC. Charging NOPAT growth instead bills the firm for
    margin recovery, which consumes no capital — measured, it pushed HIMS from 16.7%
    to 36.2% implied CAGR and sent ELV / RKLB / AXON out of band. Reverted; pinned."""
    print("D5 reinvestment tracks REVENUE growth (margin expansion costs no capital)")
    sc, roic, x = 2.5, 0.25, 0.20
    rev, margin = 1000.0, 0.10
    cap, nopat = rev / sc, rev * margin
    d_cap = (rev * (1 + x)) / sc - cap
    check(abs(d_cap / nopat - x / roic) < 1e-9,
          "dCapital/NOPAT == revenue_growth/ROIC under constant sales-to-capital",
          f"{d_cap / nopat:.4f} vs {x / roic:.4f}")

    # behavioural pin: a company mid-margin-ramp must not be charged for the ramp.
    # Same firm, same revenue path; only the margin ramp differs.
    common = dict(price=30.0, shares=250e6, revenue=2.3e9, rev_1y=1.5e9,
                  total_debt=0.1e9, cash=0.3e9, wacc_val=0.10, g=0.045, tax=0.21,
                  margin=0.25, market_cap=7.5e9, roic_term=0.30, wacc_term=0.09)
    flat = E.reverse_dcf(**common, margin_now=0.25)
    ramp = E.reverse_dcf(**common, margin_now=-0.013)
    check(flat.get("implied_cagr_pct") is not None and ramp.get("implied_cagr_pct") is not None,
          "both scenarios solve inside the band",
          f"flat {flat.get('verdict')} / ramp {flat.get('verdict')}")
    if flat.get("implied_cagr_pct") is not None and ramp.get("implied_cagr_pct") is not None:
        # a ramp DELAYS profit, so it needs MORE growth than the already-profitable
        # case, but the difference must stay bounded - not the blow-up the NOPAT-growth
        # version produced (HIMS 16.7 -> 36.2, and three names pushed out of band).
        d = ramp["implied_cagr_pct"] - flat["implied_cagr_pct"]
        check(0 <= d < 15, "a margin ramp raises the bar, but does not explode it",
              f"flat {flat['implied_cagr_pct']}% vs ramp {ramp['implied_cagr_pct']}% (d {d:+.1f}pp)")


def main():
    test_missing_pillar_scored_neutral()
    test_margin_trend_is_fiscal_year_over_year()
    test_roic_trend_matches_rd_basis()
    test_roic_trend_skipped_when_basis_cannot_match()
    test_fv_method_disagreement_flagged()
    test_no_false_disagreement_when_methods_agree()
    test_frontend_shows_the_conflict()
    test_reinvestment_is_revenue_driven()
    print()
    if FAIL:
        print("test_review3d FAILED:", "; ".join(FAIL))
        sys.exit(1)
    print("test_review3d OK - D1-D5 decisions hold")


if __name__ == "__main__":
    main()
