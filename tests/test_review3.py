"""test_review3 — round-3 review turned into a standing guard (2026-08-08). Offline.

Same intent as tests/audit2.py: the defects found in review round 3 were all of the
"code does exactly what it says and the answer is still wrong" kind, so a test that
merely pins today's numbers would not stop them coming back. Each check below encodes
the PROPERTY that was violated, not the value that happened to be wrong.

  P3-1  a growth rate labelled "1 year" must compare two periods one year apart.
        `actual_1y_pct` divided a TTM by the fiscal year BEFORE the last completed
        one — ~21 months of growth reported as 12. The error is one-directional
        (TTM >= last FY, so the actual always reads too high), it is the DENOMINATOR
        of the acceleration ratio that picks the RevDCF verdict, so verdicts skewed
        FORGIVING — and the card printed a growth rate the company never announced.
  P3-2  failing to MEASURE a pillar must never score better than measuring it and
        finding it poor. composite() renormalizes over available pillars, so a
        missing E_econ was worth up to +0.90 and a ratings upgrade. REV-8 closed
        exactly this hole on the Price pillar and left it open on the quality one.
  P3-3  a screen must not silently shorten the universe it screened.

Run: python -m tests.test_review3
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from domain.engine import deep_v82 as E          # noqa: E402
from domain.facts import FinancialFacts          # noqa: E402
from pipeline import screen                      # noqa: E402

FAIL = []


def check(ok, label, detail=""):
    print(("  ok   " if ok else "  FAIL ") + label + (("  -> " + detail) if detail and not ok else ""))
    if not ok:
        FAIL.append(label)


def _msft_like():
    """MSFT's real published shape: TTM has run past the last completed fiscal year.
    FY2025 revenue 281.724B (+14.9% on FY2024 245.122B — Microsoft announced +15%),
    TTM 318.273B. The old code reported 318.273/245.122-1 = +29.8% as "actual 1y"."""
    return FinancialFacts(
        ticker="MSFT", price=393.82, revenue=318.273e9, operating_income=148.957e9,
        net_income=110e9, shares_diluted=7.5e9, eps_gaap=14.6,
        income_before_tax=130e9, tax_expense=23e9,
        total_debt=60e9, cash=95e9, equity=340e9, operating_leases=22.2e9,
        capex=64e9, dep_amort=30e9, cfo=145e9, total_assets=620e9, sbc=11.9e9,
        interest_expense=2.0e9,
        revenue_annuals=[281.724e9, 245.122e9, 211.915e9, 198.270e9],
        operating_income_annuals=[128.528e9, 109.433e9],
        equity_prior=268e9, cash_prior=75e9, total_debt_prior=67e9,
        forward_eps=16.8, growth_lt=0.134, beta=1.05, market_cap=2900e9,
        shares_diluted_annuals=[7.5e9, 7.51e9, 7.54e9])


# --------------------------------------------------------------------------
# P3-1 · "1-year" growth must be a one-year comparison
# --------------------------------------------------------------------------
def test_actual_1y_is_fiscal_year_over_year():
    print("P3-1 actual_1y_pct is FY-over-FY, not TTM-over-FY-2")
    ttm, fy0, fy1 = 318.273e9, 281.724e9, 245.122e9
    fy_growth = fy0 / fy1 - 1                       # +14.9%, what was announced
    ttm_growth = ttm / fy1 - 1                      # +29.8%, the old bug

    out = E.reverse_dcf(price=393.82, shares=7.5e9, revenue=ttm, rev_1y=fy1,
                        total_debt=60e9, cash=95e9, wacc_val=0.094, g=0.045, tax=0.18,
                        margin=0.40, actual_growth=fy_growth)
    got = out.get("actual_1y_pct")
    check(got is not None and abs(got - fy_growth * 100) < 0.05,
          "reported actual growth == fiscal-year growth",
          f"got {got}, want {round(fy_growth * 100, 1)}")
    check(got is not None and got < ttm_growth * 100 - 5,
          "TTM-over-FY-2 inflation is gone",
          f"got {got}% (the old bug printed {round(ttm_growth * 100, 1)}%)")

    v = E.DeepV82Engine().evaluate(_msft_like(), rf=0.045)
    rd = v.reverse_dcf or {}
    if rd.get("triggered"):
        check(abs((rd.get("actual_1y_pct") or 0) - fy_growth * 100) < 0.05,
              "engine passes the FY rate into the reverse DCF", str(rd.get("actual_1y_pct")))

    # the invariant that was actually broken: ONE row showed TWO different
    # "actual revenue growth" figures — the Demand pillar's and the card's.
    d_note = next((n for n in (v.subscores or {}).get("breakdown", [])
                   if n.startswith("D base(growth")), "")
    check(f"{fy_growth * 100:.0f}%" in d_note,
          "Demand pillar and RevDCF agree on actual growth", f"D note: {d_note!r}")


def test_fallback_unchanged_when_no_growth_supplied():
    """Direct callers (skill-parity tests) pass no actual_growth; the old expression
    survives as the fallback so their behaviour is untouched."""
    print("P3-1b fallback path unchanged for callers that supply no growth")
    out = E.reverse_dcf(price=100.0, shares=1e9, revenue=110e9, rev_1y=100e9,
                        total_debt=10e9, cash=5e9, wacc_val=0.09, g=0.045, tax=0.21,
                        margin=0.25)
    check(out.get("actual_1y_pct") == 10.0, "fallback = revenue/rev_1y - 1",
          str(out.get("actual_1y_pct")))


def test_ar_check_not_desensitised():
    """The AR-vs-revenue (channel-stuffing) test compares receivables growth against
    REVENUE growth, so it inherited the same inflation — in the direction that HIDES
    the signal: `ar_g > rev_g + 0.15` only gets harder as rev_g is overstated."""
    print("P3-1c AR-vs-revenue check uses like-for-like growth")
    args = dict(net_income=10e9, cfo=9e9, total_assets=100e9, sbc=1e9, revenue=115e9,
                receivables=14e9, receivables_prior=10e9, revenue_prior=100e9)
    _, flags_fy, _ = E.earnings_quality(revenue_growth=0.15, **args)      # AR +40 vs rev +15
    check(any("receivables" in f for f in flags_fy), "fires on real FY growth", str(flags_fy))
    _, flags_ttm, _ = E.earnings_quality(revenue_growth=0.30, **args)     # the inflated read
    check(not any("receivables" in f for f in flags_ttm),
          "an overstated growth rate would have hidden it (this is why it matters)",
          str(flags_ttm))


def test_short_history_still_degrades_quietly():
    """audit3/B4: with <2 filed years there is no comparison to make and the check
    must simply not run — a newly listed name can never trip it. Unchanged here."""
    print("P3-1d short history still degrades to no-check")
    _, flags, _ = E.earnings_quality(net_income=3e9, cfo=3.5e9, total_assets=40e9, sbc=None,
                                     revenue=20e9, receivables=3.4e9, receivables_prior=2.0e9,
                                     revenue_prior=None, revenue_growth=None)
    check(not any("receivables" in f for f in flags), "no AR flag without history", str(flags))


# --------------------------------------------------------------------------
# P3-2 · unmeasured must never beat measured-and-poor
# --------------------------------------------------------------------------
def _reco(sc):
    c = E.composite(sc)
    r = E.recommendation(c)
    r, _ = E.cap_reco_without_price(r, sc["P"])
    r, _ = E.cap_reco_without_quality(r, sc["E_econ"])
    return r


RANK = {"SELL / AVOID": 0, "HOLD": 1, "HOLD / Accumulate": 2, "BUY": 3}


def test_missing_pillar_never_reads_as_buy():
    """What the caps guarantee: an UNTESTED 30%-weight pillar can never produce the
    strongest verdict. Both 30% pillars are covered — REV-8 did Price, P3-2 does
    Economics."""
    print("P3-2 a missing 30%-weight pillar can never read as BUY")
    for missing in ("E_econ", "P"):
        for base in ({"D": 4.0, "E_exec": 4.0, "E_econ": 4.0, "P": 4.0},
                     {"D": 5.0, "E_exec": 5.0, "E_econ": 4.5, "P": 4.5},
                     {"D": 4.5, "E_exec": 4.0, "E_econ": 3.0, "P": 5.0}):
            gone = dict(base, **{missing: None})
            check(_reco(gone) != "BUY", f"missing {missing} is not a BUY ({base})",
                  f"composite {E.composite(gone)} -> {_reco(gone)}")


def test_renormalisation_bounded_by_neutral():
    """CLOSED by D1 (2026-08-08) — see tests/test_review3d.py for the full guard.

    This started as a standing NOTE: `composite()` renormalized over the pillars that
    existed, so a missing pillar inherited the AVERAGE of the others and a data gap
    could lift a row one notch (2.90 HOLD -> 4.14 capped -> HOLD / Accumulate). The
    decision was taken and shipped: composite is now bounded by what the row would
    score with the missing pillar at NEUTRAL, which is one-sided and can only lower.

    Note what the guarantee is, because the first version of this check asserted the
    wrong one. It is NOT "missing <= measured-poor" — scoring an unmeasured pillar as
    if it were the worst possible is its own distortion, and REV-2 already rejected
    that pattern. It is "missing <= that pillar being neutral"."""
    print("P3-2c renormalisation is bounded by NEUTRAL (D1, closed)")
    for row in ({"D": 5.0, "E_exec": 5.0, "E_econ": 0.0, "P": 3.0},
                {"D": 4.0, "E_exec": 4.0, "E_econ": 4.0, "P": 4.0}):
        for pillar in ("E_econ", "P"):
            gone = E.composite(dict(row, **{pillar: None}))
            at_neutral = E.composite(dict(row, **{pillar: E.NEUTRAL_SCORE}))
            check(gone <= at_neutral + 1e-9,
                  f"missing {pillar} never scores above neutral",
                  f"{gone:.3f} vs {at_neutral:.3f}")


def test_unmeasurable_roic_row_is_not_a_buy():
    """End to end: invested capital <= 0 (cash > debt + equity — routine for a
    cash-rich software or biotech filer) makes ROIC unmeasurable, so the quality
    pillar goes missing on an otherwise strong-looking row."""
    print("P3-2b end-to-end: unmeasurable ROIC cannot produce a BUY")
    f = FinancialFacts(
        ticker="CASHY", price=40.0, revenue=10e9, operating_income=3e9, net_income=2.4e9,
        shares_diluted=1e9, eps_gaap=2.4, income_before_tax=3e9, tax_expense=0.63e9,
        total_debt=0.0, cash=30e9, equity=20e9,            # IC = -10B -> ROIC None
        capex=0.5e9, dep_amort=0.4e9, cfo=2.8e9, total_assets=40e9, sbc=0.2e9,
        revenue_annuals=[10e9, 8.0e9, 6.6e9, 5.5e9], operating_income_annuals=[3e9, 2.2e9],
        forward_eps=4.5, growth_lt=0.22, beta=1.0, market_cap=40e9,
        shares_diluted_annuals=[1e9, 1e9, 1e9])
    v = E.DeepV82Engine().evaluate(f, rf=0.045)
    check(v.E_econ is None, "quality pillar is genuinely unmeasurable here", str(v.E_econ))
    check(v.recommendation != "BUY", "row is not a BUY on an untested quality pillar",
          f"composite {v.composite} -> {v.recommendation}")
    check(any("Economics pillar" in fl for fl in v.flags), "and the card says why",
          str([fl for fl in v.flags if "pillar" in fl]))


# --------------------------------------------------------------------------
# P3-3 · the screen shows everything it screened
# --------------------------------------------------------------------------
def test_screen_keeps_unscreenable_names():
    print("P3-3 GARP screen does not silently drop names")
    items = [{"ticker": "REGN", "economics": 3.0, "price": 4.0},
             {"ticker": "TSM", "economics": 5.0, "price": None},     # best quality, no FV
             {"ticker": "LLY", "economics": None, "price": 1.5},
             {"ticker": "MSFT", "economics": 4.5, "price": 2.5}]
    out = screen.rank(items)
    check(len(out) == len(items), "every input name comes back", f"{len(out)}/{len(items)}")
    by = {r["ticker"]: r for r in out}
    check(by["TSM"]["garp_score"] is None and by["TSM"]["candidate"] is False,
          "unscreenable name carries no invented score")
    check("no point fair value" in (by["TSM"]["unscreenable"] or ""),
          "and says which axis is missing", str(by["TSM"]["unscreenable"]))
    check("ROIC-WACC unmeasurable" in (by["LLY"]["unscreenable"] or ""),
          "missing quality axis is named too", str(by["LLY"]["unscreenable"]))
    check(by["REGN"]["unscreenable"] is None and by["REGN"]["candidate"] is True,
          "a fully screened candidate is unaffected")
    check([r["ticker"] for r in out[:2]] == ["MSFT", "REGN"],
          "scored names still rank first, best-first", str([r["ticker"] for r in out]))
    check(all(r["garp_score"] is None for r in out[2:]), "unscreenable names sort last",
          str([(r["ticker"], r["garp_score"]) for r in out]))


def test_screen_frontend_renders_the_reason():
    """The backend keeping the row is only half the fix — the table has to show it."""
    print("P3-3b dashboard renders the unscreenable rows")
    here = os.path.dirname(os.path.abspath(__file__))
    html = open(os.path.join(os.path.dirname(here), "index.html"), encoding="utf-8").read()
    for needle in ("it.unscreenable", "x.unscreenable"):
        check(needle in html, f"index.html reads {needle}")


def main():
    test_actual_1y_is_fiscal_year_over_year()
    test_fallback_unchanged_when_no_growth_supplied()
    test_ar_check_not_desensitised()
    test_short_history_still_degrades_quietly()
    test_missing_pillar_never_reads_as_buy()
    test_renormalisation_bounded_by_neutral()
    test_unmeasurable_roic_row_is_not_a_buy()
    test_screen_keeps_unscreenable_names()
    test_screen_frontend_renders_the_reason()
    print()
    if FAIL:
        print("test_review3 FAILED:", "; ".join(FAIL))
        sys.exit(1)
    print("test_review3 OK - all round-3 defect classes closed")


if __name__ == "__main__":
    main()
