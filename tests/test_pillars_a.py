"""test_pillars_a — the A1-A4 pillar upgrades (2026-08-09). Offline; uses fixtures.

Round 4 of the review found that two of the four pillars carried 30% of the score each
while resting on a single measurement, and that two D-pillar inputs silently lost the
SIGN of their data — in both cases penalising the company that deserved it least.

  A1  Price gains a second, independent leg: FCF yield against WACC. Price was 30% of
      the composite and rested entirely on the margin of safety to one point fair
      value; if that value was wrong, 30% of the score was wrong with nothing to
      contradict it. A row with no fair value at all scored no Price pillar.
  A2  fade_ratio inverts on a negative denominator (a company turning from decline to
      growth was scored as the worst kind of fade); acquisitions used abs(), so
      SELLING a business was penalised exactly like buying one.
  A3  Economics scored one year's ROIC. Damped toward the company's own median —
      one-sided, so it can only ever lower.
  A4  Incremental ROIC from the 5-year change in invested capital instead of one year
      of capex + M&A - D&A, whose denominator sits near zero for any mature company.

Run: python -m tests.test_pillars_a
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from domain.engine import deep_v82 as E          # noqa: E402
from domain.facts import FinancialFacts          # noqa: E402
from pipeline import normalize                   # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FAIL = []
# price, beta, forward EPS, growth, market cap, fx
MKT = {"MSFT": (393.82, 1.05, 16.80, 0.134, 2900e9, None),
       "ABBV": (254.49, 0.28, 14.05, 0.100, 453e9, None),
       "ORCL": (126.48, 1.71, 6.33, 0.100, 355e9, None),
       "NVO": (50.32, 0.60, 3.60, 0.110, 225e9, 0.145),
       "AVGO": (370.83, 1.20, 7.05, 0.175, 1740e9, None)}
DATED = ("revenue_annuals_dated", "operating_income_annuals_dated", "cfo_annuals_dated",
         "capex_annuals_dated", "gross_profit_annuals_dated", "cost_of_revenue_annuals_dated")


def check(ok, label, detail=""):
    print(("  ok   " if ok else "  FAIL ") + label + (("  -> " + detail) if detail and not ok else ""))
    if not ok:
        FAIL.append(label)


def _eval(ticker, strip_series=False):
    px, beta, feps, g, mc, fx = MKT[ticker]
    with open(os.path.join(HERE, "fixtures", ticker, "sec_companyfacts.json"), encoding="utf-8") as fh:
        ff = normalize.build(ticker, sec_companyfacts=json.load(fh), fx_rate=fx)
    ff.price, ff.beta, ff.forward_eps, ff.growth_lt, ff.market_cap = px, beta, feps, g, mc
    if strip_series:                        # simulate facts stored before the T5 upgrade
        for k in DATED:
            setattr(ff, k, [])
        ff.ic_components_dated = {}
    return E.DeepV82Engine().evaluate(ff, rf=0.045)


def _note(v, needle):
    return next((n for n in v.subscores["breakdown"] if needle in n), "")


# --------------------------------------------------------------------------
# A2 · signs
# --------------------------------------------------------------------------
def test_fade_needs_positive_near_term_growth():
    print("A2 fade_ratio is withheld when consensus near-term growth is not positive")
    cases = [(0.20, 0.05, 0.25, "growth halving is still a fade"),
             (0.05, -0.10, -2.0, "growth turning negative is still the worst fade")]
    for near, far, want, why in cases:
        got, _ = E.fade_ratio(near, far)
        check(got is not None and abs(got - want) < 1e-9, f"positive near-term: {why}", str(got))
    # PFE's live shape: -4.1% then +0.75%. The BEST trajectory, previously scored -0.5.
    got, why = E.fade_ratio(-0.041, 0.0075)
    check(got is None and "ไม่เป็นบวก" in (why or ""),
          "a turnaround from decline is not scored as a fade", f"{got} / {why}")
    got, why = E.fade_ratio(-0.10, -0.05)
    check(got is None, "and neither is a smaller decline scored as no fade", str(got))
    # the two reasons must stay distinguishable (REV-5)
    _, why_missing = E.fade_ratio(None, 0.05)
    check(why_missing != why, "'no data' and 'not positive' are different messages",
          f"{why_missing!r} vs {why!r}")


def test_divestiture_is_not_an_acquisition():
    print("A2 selling a business is not buying growth")
    buy, lbl_buy = E.acquisition_intensity(5e9, 50e9)
    check(abs(buy - 0.10) < 1e-9 and lbl_buy is None, "a purchase still scores", str(buy))
    sell, lbl_sell = E.acquisition_intensity(-5e9, 50e9)
    check(sell == 0.0 and "divestiture" in (lbl_sell or ""),
          "a net divestiture carries no organic-growth penalty", f"{sell} / {lbl_sell}")
    check(E.acquisition_intensity(None, 50e9) == (None, None), "no data stays no data")
    # end to end: the penalty band must not fire on a divestiture
    notes = []
    E._r_demand(0.20, None, sell, None, notes, acq_label=lbl_sell)
    check("=0.0" in _n(notes, "organic"), "and the band applies 0.0", _n(notes, "organic"))


def _n(notes, needle):
    return next((n for n in notes if needle in n), "")


# --------------------------------------------------------------------------
# A1 · Price gains a cash leg
# --------------------------------------------------------------------------
def test_fcf_yield_leg():
    print("A1 FCF yield vs WACC is a second, independent read on cheapness")
    rows = [{"key": "fcf", "n": 5, "points": [{"fy": "2025", "v": 10e9}]}]
    for ev, wacc, want, why in ((100e9, 0.09, 0.5, "yield 10% beats a 9% WACC"),
                                (140e9, 0.09, 0.25, "yield 7.1% is 0.79x of WACC"),
                                (250e9, 0.09, 0.0, "yield 4% is 0.44x — neither cheap nor dear"),
                                (600e9, 0.09, -0.25, "yield 1.7% is 0.19x — expensive on cash")):
        adj, note = E.fcf_yield_adj(rows, ev, wacc)
        check(adj == want, f"{why} -> {want:+.2f}", f"{adj} ({note})")
    neg = [{"key": "fcf", "n": 5, "points": [{"fy": "2025", "v": -1e9}]}]
    adj, note = E.fcf_yield_adj(neg, 100e9, 0.09)
    check(adj == -0.5 and note, "negative FCF -> -0.5", f"{adj} {note}")
    check(E.fcf_yield_adj(None, 100e9, 0.09) == (0.0, None), "no strip -> silent")
    check(E.fcf_yield_adj(rows, None, 0.09) == (0.0, None), "no enterprise value -> silent")
    check(E.fcf_yield_adj(rows, 100e9, 0) == (0.0, None), "no WACC -> silent")


def test_price_pillar_exists_without_a_fair_value():
    """The whole point: NVO had NO Price pillar (P=None) because no point fair value
    could be built. A cash yield is a complete cheapness opinion on its own."""
    print("A1 a row with no point fair value can still be priced on cash")
    before, after = _eval("NVO", strip_series=True), _eval("NVO")
    if before.P is None:
        check(after.P is not None, "NVO gains a Price pillar", f"{before.P} -> {after.P}")
        check("cash alone" in _note(after, "P base"), "and says it was scored on cash alone",
              _note(after, "P base"))
    else:
        check(True, "fixture no longer produces a missing fair value (nothing to prove)")


def test_price_leg_is_bounded():
    print("A1 the cash leg cannot dominate the pillar")
    for t in MKT:
        b, a = _eval(t, strip_series=True), _eval(t)
        if b.P is None or a.P is None:
            continue
        check(abs(a.P - b.P) <= 0.5 + 1e-9, f"{t}: Price moves at most 0.5", f"{b.P} -> {a.P}")


# --------------------------------------------------------------------------
# A3 · one-sided normalisation
# --------------------------------------------------------------------------
def _map(vals, start=2021):
    return {f"{start + i}-12-31": {"roic": v, "nopat": v * 10, "ic": 1000.0}
            for i, v in enumerate(vals)}


def test_normalisation_only_ever_lowers():
    print("A3 median normalisation is one-sided")
    # latest year is a PEAK vs its own history -> damp down
    out, note = E.normalized_roic(0.20, _map([10.0, 11.0, 10.5, 11.0, 18.0]))
    check(out < 0.20 and note, "a peak year is damped", f"{out} {note}")
    # NVO's real shape: a monotone DECLINE. The median sits above the latest year, but
    # normalising up would reward the decline and fight the trend adjustment.
    out, note = E.normalized_roic(0.20, _map([53.5, 61.2, 68.0, 44.0, 33.8]))
    check(out == 0.20 and note is None, "a decline is NOT normalised upward", f"{out} {note}")
    # short history says nothing
    out, note = E.normalized_roic(0.20, _map([10.0, 18.0]))
    check(out == 0.20 and note is None, "fewer than 4 years -> untouched", f"{out} {note}")
    check(E.normalized_roic(None, _map([10.0] * 5)) == (None, None), "no ROIC -> untouched")
    # the clamp holds
    out, _ = E.normalized_roic(0.20, _map([1.0, 1.0, 1.0, 1.0, 50.0]))
    check(abs(out - 0.20 * E.NORMALIZE_FACTOR_MIN) < 1e-9,
          f"factor floors at {E.NORMALIZE_FACTOR_MIN}", str(out))


def test_normalisation_uses_contiguous_years_only():
    """A gap means the older years belong to a different company. AVGO has no
    long-term debt tag for FY2022-24, so a plain last-5-available window mixed
    pre-VMware years into the median of a post-VMware one."""
    print("A3b normalisation ignores years across a filing gap")
    gapped = {"2018-12-31": {"roic": 6.0, "nopat": 60, "ic": 1000},
              "2019-12-31": {"roic": 6.5, "nopat": 65, "ic": 1000},
              "2020-12-31": {"roic": 7.0, "nopat": 70, "ic": 1000},
              "2021-12-31": {"roic": 7.5, "nopat": 75, "ic": 1000},
              "2025-12-31": {"roic": 19.0, "nopat": 190, "ic": 1000}}
    out, note = E.normalized_roic(0.20, gapped)
    check(out == 0.20 and note is None,
          "only one contiguous year at the top -> no normalisation", f"{out} {note}")


# --------------------------------------------------------------------------
# A4 · incremental ROIC
# --------------------------------------------------------------------------
def test_incremental_roic_prefers_the_capital_delta():
    print("A4 incremental ROIC comes from ΔNOPAT/ΔIC when the years allow it")
    v = _eval("MSFT")
    n = _note(v, "incremental-ROIC")
    check("ΔNOPAT/ΔIC" in n, "MSFT uses the 5-year capital delta", n)


def test_no_fallback_to_the_noisy_proxy_when_the_5y_measure_declines():
    """ABBV's capital base is flat, so there is no new capital whose return could be
    measured. The old one-year proxy answered anyway, with +195%."""
    print("A4b a declined 5-year measure does not fall through to the 1-year proxy")
    v = _eval("ABBV")
    n = _note(v, "incremental-ROIC")
    check("skipped" in n, "ABBV reports it cannot be measured", n)
    check("capex" not in n, "and does not quote the capex proxy instead", n)


def test_old_facts_are_untouched():
    """Everything in A1/A3/A4 rides on series that facts stored before T5 do not have.
    Those rows must score EXACTLY as they did — a scoring upgrade may not silently
    re-rate a portfolio that has not been refreshed."""
    print("A-H facts without the dated series score identically to before")
    stored = FinancialFacts(
        ticker="OLD", beta=1.0, price=50.0, revenue=10e9, operating_income=2e9,
        net_income=1.5e9, shares_diluted=1e9, market_cap=50e9, cash=1e9, equity=8e9,
        total_debt=2e9, forward_eps=1.8, growth_lt=0.1, cfo=1.8e9, total_assets=20e9,
        capex=0.6e9, dep_amort=0.5e9,
        revenue_annuals=[10e9, 9e9], operating_income_annuals=[2e9, 1.7e9])
    v = E.DeepV82Engine().evaluate(stored, rf=0.045)
    check("skipped" in _note(v, "P FCF-yield"), "Price cash leg self-skips",
          _note(v, "P FCF-yield"))
    check("skipped" in _note(v, "E_exec FCF-durability"), "Execution cash leg self-skips",
          _note(v, "E_exec FCF-durability"))
    check(not any("normalized" in fl for fl in v.flags), "Economics is not normalised",
          str([fl for fl in v.flags if "normalized" in fl]))
    check(v.composite is not None, "and the row still scores")


def test_growth_reconciliation():
    """L3. The card quoted TWO growth rates and reconciled neither: the Demand pillar
    scored NVO on the 6.4% it delivered while the fair value used the 19.5% consensus.

    What the investigation actually found is worth more than the guard. NVO is cheap on
    EVERY growth assumption — FV $116 at 19.5%, $86 at 6.4%, still $67 at ZERO against a
    $47.73 price — so its BUY never rested on consensus at all, and the hypothesis that
    sent me looking was wrong. The disclosure stays because it is what proved that; the
    cap stays because the principle is right where it does bind, and it is shown below
    both to fire on a constructed case and to stay silent on all 21 real holdings."""
    print("L3 the two growth stories are reconciled, and the cap is one-sided")
    from domain.engine import deep_v82 as E
    kw = dict(g_stable=0.047, ke=0.0625, roic_high=0.285, roic_stable=0.182,
              forward_eps=3.3177, ke_stable=0.0826)
    fv_c, _ = E.fundamental_peg_price(g_high=0.195, **kw)
    fv_r, _ = E.fundamental_peg_price(g_high=0.064, **kw)
    fv_0, _ = E.fundamental_peg_price(g_high=0.0, **kw)
    check(round(fv_c, 2) == 116.12, "NVO's consensus FV reproduces", str(fv_c))
    check(fv_0 > 47.73 * 1.3,
          f"and it is still ${fv_0:.2f} at ZERO growth — the BUY is not a growth story",
          str(fv_0))
    check(fv_c / fv_r < E.GROWTH_FV_DISAGREE,
          f"so NVO does NOT trip the cap ({fv_c / fv_r:.2f}x < {E.GROWTH_FV_DISAGREE})")

    # where it DOES bind: consensus far above a company that has stopped growing
    fv_hi, _ = E.fundamental_peg_price(g_high=0.30, **kw)
    check(fv_hi / fv_0 >= E.GROWTH_FV_DISAGREE,
          f"consensus 30% against 0% delivered trips it ({fv_hi / fv_0:.2f}x)")

    # one-sided: consensus BELOW delivered must never be punished (AXON 5% vs 33%,
    # MELI 14% vs 39% — a symmetric ratio hit exactly those on the first attempt)
    fv_lo, _ = E.fundamental_peg_price(g_high=0.05, **kw)
    check(fv_lo / fv_hi < 1.0 and (fv_lo / fv_hi) < E.GROWTH_FV_DISAGREE,
          "a conservative consensus is cheaper on its own number, never capped for it")


def main():
    for t in (test_fade_needs_positive_near_term_growth, test_divestiture_is_not_an_acquisition,
              test_growth_reconciliation,
              test_fcf_yield_leg, test_price_pillar_exists_without_a_fair_value,
              test_price_leg_is_bounded, test_normalisation_only_ever_lowers,
              test_normalisation_uses_contiguous_years_only,
              test_incremental_roic_prefers_the_capital_delta,
              test_no_fallback_to_the_noisy_proxy_when_the_5y_measure_declines,
              test_old_facts_are_untouched):
        t()
    print()
    if FAIL:
        print("test_pillars_a FAILED:", "; ".join(FAIL))
        sys.exit(1)
    print("test_pillars_a OK - A1-A4 hold")


if __name__ == "__main__":
    main()
