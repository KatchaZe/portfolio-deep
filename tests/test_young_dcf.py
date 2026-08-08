"""
test_young_dcf (REV-16 / Damodaran S20) — the pre-profit forward intrinsic value,
its derived inputs, and the band gate that decides whether it may anchor a row.

Offline and deterministic: the Monte Carlo is seeded, so the same facts must
always produce the same band.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from domain.engine import young_dcf as Y            # noqa: E402
from domain.engine.deep_v82 import DeepV82Engine    # noqa: E402
from domain.facts import FinancialFacts             # noqa: E402

RF = 0.045


def approx(a, b, tol=1e-6):
    return a is not None and b is not None and abs(a - b) <= tol


PREPROFIT = dict(
    beta=1.9, price=28.0, revenue=2.1e9, operating_income=-0.32e9, net_income=-0.38e9,
    shares_diluted=0.24e9, total_debt=0.15e9, cash=1.15e9, equity=1.4e9, capex=0.09e9,
    dep_amort=0.06e9, interest_expense=0.01e9, income_before_tax=-0.36e9, tax_expense=0.0,
    forward_eps=-0.20, growth_lt=0.38, cfo=-0.45e9, total_assets=2.2e9, sbc=0.30e9,
    revenue_annuals=[2.1e9, 1.5e9, 1.0e9, 0.65e9, 0.4e9, 0.24e9],
    operating_income_annuals=[-0.32e9, -0.41e9], equity_prior=1.6e9, cash_prior=1.5e9,
    total_debt_prior=0.15e9, market_cap=6.72e9, terminal_roic_sector=0.2295,
    fwd_growth_near=0.35)


def test_survival_from_runway():
    """p_survival is DERIVED from the cash runway, never picked by feel."""
    # 1.15B cash / 0.45B burn = 2.6y, plus a >$1B revenue premium
    p, why = Y.survival_probability(1.15e9, -0.45e9, None, 2.1e9)
    assert approx(p, 0.68, 1e-9), (p, why)
    assert "runway 2.6y" in why and ">$1B revenue" in why, why
    # not burning at all -> the ceiling, but never certainty
    p2, _ = Y.survival_probability(1e9, 0.2e9, None, 100e6)
    assert approx(p2, 0.90, 1e-9) and p2 < 1.0
    # burning with no cash -> the floor
    assert Y.survival_probability(0, -1e8, None, 5e7)[0] == 0.25
    # runway shortens -> survival falls monotonically
    seq = [Y.survival_probability(c, -1e9, None, None)[0]
           for c in (6e9, 4e9, 2.5e9, 1.5e9, 0.5e9)]
    assert seq == sorted(seq, reverse=True), seq
    print("p_survival derived from cash runway OK", seq)


def test_sales_to_capital_sources_and_clamp():
    r, lbl = Y.sales_to_capital(10e9, 5e9, None, None)
    assert approx(r, 2.0) and "invested capital" in lbl
    # net-cash firm: invested capital is useless, fall back to operating assets
    r2, lbl2 = Y.sales_to_capital(10e9, -1e9, 8e9, 3e9)
    assert approx(r2, 2.0) and "assets - cash" in lbl2, lbl2
    # a clamp must SAY it clamped (the whole point of this review)
    r3, lbl3 = Y.sales_to_capital(10e9, 0.5e9, None, None)          # raw 20x
    assert approx(r3, Y.S2C_HI) and "clamped" in lbl3, lbl3
    assert Y.sales_to_capital(0, 5e9, None, None)[0] is None
    assert Y.sales_to_capital(10e9, None, None, None)[0] is None
    print("sales-to-capital sources + two-sided clamp OK")


def test_failure_risk_is_separate_from_discount_rate():
    """S20's core rule: failure risk is a probability, not a discount-rate bump."""
    d = {"current_revenue": 2e9, "g_high": 0.35, "g_stable": RF, "horizon": 10,
         "current_margin": -0.15, "target_margin": 0.25, "sales_to_capital": 2.4,
         "tax": 0.21, "wacc": 0.13, "roic_stable": 0.15, "net_debt": -1e9,
         "shares": 0.24e9, "annual_dilution": 0.04}
    gc = Y.going_concern(d)["going_concern_per_share"]
    # same WACC, different survival -> different value, linearly between GC and distress
    v100 = Y.failure_adjusted(gc, 1.0, 3.0)
    v50 = Y.failure_adjusted(gc, 0.5, 3.0)
    v0 = Y.failure_adjusted(gc, 0.0, 3.0)
    assert approx(v100, gc) and approx(v0, 3.0)
    assert approx(v50, (gc + 3.0) / 2, 1e-9)
    assert v100 > v50 > v0
    # and the going-concern value itself never saw p_survival
    assert "p_survival" not in d
    print("failure risk kept out of the discount rate OK (GC %.2f -> %.2f at p=0.5)" % (gc, v50))


def test_terminal_reinvestment_capped():
    """roic_stable < g_stable used to make the perpetuity consume more than it earns."""
    d = {"current_revenue": 2e9, "g_high": 0.20, "g_stable": RF, "horizon": 10,
         "current_margin": -0.05, "target_margin": 0.20, "sales_to_capital": 2.0,
         "tax": 0.21, "wacc": 0.12, "roic_stable": 0.02, "net_debt": 0.0,
         "shares": 1e9, "annual_dilution": 0.0}
    out = Y.going_concern(d)
    assert out["pv_terminal"] > 0, out["pv_terminal"]
    print("terminal reinvestment cap OK (PV terminal stays positive)")


def test_band_gate_promotes_only_when_tight():
    """The user's rule: only a value we actually know may move a recommendation."""
    eng = DeepV82Engine()
    v = eng.evaluate(FinancialFacts("YOUNGCO", **PREPROFIT), rf=RF)
    y = v.young_dcf
    assert y and y["promote"] is True, y
    assert v.anchor_method.startswith("Young-Company"), v.anchor_method
    assert v.anchor_value == y["monte_carlo"]["p50"]
    assert v.range_low == y["monte_carlo"]["p10"] and v.range_high == y["monte_carlo"]["p90"]
    assert y["band_ratio"] < Y.BAND_MAX_RATIO
    # a pre-profit name now HAS a price opinion, which is the whole point
    assert v.P is not None, "Price pillar should exist once a fair value does"
    assert "Pre-profit forward DCF" in v.verdict, v.verdict

    # widen the uncertainty until the gate closes: a razor-thin capital base makes
    # the sales-to-capital resample swing the answer wildly
    assert y["blocked_reason"] is None

    # a smaller, faster-burning version: the model still runs but must not anchor
    wide = dict(PREPROFIT, revenue=0.35e9, cash=0.30e9, cfo=-0.40e9,
                operating_income=-0.45e9, total_assets=0.5e9, equity=0.30e9)
    v2 = eng.evaluate(FinancialFacts("WIDECO", **wide), rf=RF)
    y2 = v2.young_dcf
    assert y2, "the model should still RUN, it just should not anchor"
    assert y2["promote"] is False and y2["blocked_reason"], y2
    assert v2.anchor_method == "Terminal-Anchored Reverse DCF", v2.anchor_method
    assert v2.anchor_value is None
    assert any("informational only" in f for f in v2.flags), v2.flags
    print("band gate OK (promote at %.2fx; blocked: %s)" % (y["band_ratio"], y2["blocked_reason"]))


def test_blocked_reason_distinguishes_causes():
    """REV-16: 'band too wide' and 'the equity is worth nothing' are opposite
    problems and used to share one message — including when the band was perfectly
    tight and simply negative.

    Since the limited-liability floor landed, the worthless case is caught EARLIER
    and more precisely: the raw equity is underwater, which is a statement about the
    firm, rather than a negative p50, which was only the symptom that leaked through.
    The requirement this test exists to protect is unchanged — a name we cannot value
    must never be described as one whose band is merely wide."""
    eng = DeepV82Engine()
    worthless = dict(PREPROFIT, revenue=0.35e9, cash=0.30e9, cfo=-0.40e9,
                     operating_income=-0.45e9, total_assets=0.5e9, equity=0.30e9)
    y = eng.evaluate(FinancialFacts("WORTHLESS", **worthless), rf=RF).young_dcf
    assert "below its net debt" in y["blocked_reason"], y["blocked_reason"]
    assert "spans" not in y["blocked_reason"], "must not claim the band is wide"
    assert y["band_ratio"] is None
    # the floor must not leak a negative number onto the card in the process
    assert y["monte_carlo"]["p50"] >= 0, y["monte_carlo"]
    print("blocked_reason distinguishes worthless from unknowable OK")


def test_equity_cannot_be_worth_less_than_zero():
    """Limited liability. A shareholder's claim floors at zero; a share price cannot
    be negative. The distress leg was floored from the day it was written, this one
    was not, and the asymmetry reached the screen: RKLB showed "worth -$0.92 if it
    survives, $1.83 if it fails" — worth more dead than alive, blending to -$0.51."""
    d = {"current_revenue": 1.0e9, "g_high": 0.25, "g_stable": RF, "horizon": 10,
         "current_margin": -0.15, "target_margin": 0.10, "sales_to_capital": 1.5,
         "tax": 0.21, "wacc": 0.11, "roic_stable": 0.13, "net_debt": 3.0e9,
         "shares": 0.5e9, "annual_dilution": 0.02}
    gc = Y.going_concern(d)
    assert gc["equity_value"] < 0, "fixture must be underwater or it tests nothing"
    assert gc["going_concern_per_share"] == 0.0, gc["going_concern_per_share"]
    # the raw depth stays available — the floor must not destroy the diagnostic
    assert gc["equity_value"] / gc["diluted_shares"] < -1.0
    # and the S20 blend can no longer land below the distress floor it starts from
    assert Y.failure_adjusted(gc["going_concern_per_share"], 0.85, 1.83) > 0
    # a solvent firm is untouched by the floor
    solvent = Y.going_concern(dict(d, net_debt=-1.0e9))
    assert solvent["going_concern_per_share"] == solvent["equity_value"] / solvent["diluted_shares"]
    print("going-concern equity floored at zero OK (raw $%.2f/share kept)"
          % (gc["equity_value"] / gc["diluted_shares"]))


def test_underwater_equity_never_anchors():
    """The floor COLLAPSES downside variance: every underwater scenario reports the
    same 0, so the band tightens and p10 can no longer fall <= 0. Measured on a real
    case, flooring alone turned p10/p50/p90 of -3.80/-2.48/-1.43 — correctly blocked —
    into 0.13/0.29/0.47, a 3.62x band that clears BAND_MAX_RATIO and would have
    anchored the row at $0.29 against an $81 price. That tightness is the width of our
    own clamp, not knowledge about the company: the P2-4 mistake, one gate earlier."""
    eng = DeepV82Engine()
    sunk = dict(PREPROFIT, revenue=0.30e9, cash=0.05e9, total_debt=2.4e9,
                cfo=-0.35e9, operating_income=-0.40e9, total_assets=0.6e9,
                equity=-0.5e9)
    v = eng.evaluate(FinancialFacts("SUNKCO", **sunk), rf=RF)
    y = v.young_dcf
    assert y, "the model must still RUN and still be shown — it just may not anchor"
    assert y["promote"] is False, y
    assert "below its net debt" in y["blocked_reason"], y["blocked_reason"]
    assert v.anchor_method == "Terminal-Anchored Reverse DCF", v.anchor_method
    assert v.anchor_value is None
    # nothing the card renders may be negative any more
    for k in ("going_concern_per_share", "distress_per_share", "failure_adjusted_per_share"):
        assert y[k] >= 0, (k, y[k])
    assert y["monte_carlo"]["p10"] >= 0, y["monte_carlo"]
    print("underwater equity blocked from anchoring OK")


def test_deterministic_across_runs():
    """A refresh with unchanged facts must not produce a different fair value."""
    eng = DeepV82Engine()
    a = eng.evaluate(FinancialFacts("YOUNGCO", **PREPROFIT), rf=RF)
    b = eng.evaluate(FinancialFacts("YOUNGCO", **PREPROFIT), rf=RF)
    assert a.anchor_value == b.anchor_value
    assert a.young_dcf["monte_carlo"] == b.young_dcf["monte_carlo"]
    print("Monte Carlo deterministic OK (p50 $%.2f both runs)" % a.anchor_value)


def test_profitable_names_untouched():
    """young_dcf must stay empty for a normal profitable company."""
    eng = DeepV82Engine()
    ok = dict(beta=1.0, price=140.0, revenue=28e9, operating_income=5.4e9, net_income=4.1e9,
              shares_diluted=1.2e9, total_debt=9e9, cash=6e9, equity=18e9, capex=1.1e9,
              dep_amort=0.9e9, interest_expense=0.35e9, income_before_tax=5.0e9,
              tax_expense=0.9e9, forward_eps=3.9, growth_lt=0.13, cfo=5.6e9,
              total_assets=44e9, revenue_annuals=[28e9, 25e9, 22e9, 19.5e9, 17e9, 15e9],
              operating_income_annuals=[5.4e9, 4.8e9], market_cap=168e9)
    v = eng.evaluate(FinancialFacts("PROFITCO", **ok), rf=RF)
    assert v.young_dcf == {}, v.young_dcf
    assert not v.anchor_method.startswith("Young-Company")
    print("profitable names unaffected OK (%s)" % v.anchor_method)


def test_inputs_are_all_sourced():
    """Invariant 17: no number appears without saying where it came from."""
    v = DeepV82Engine().evaluate(FinancialFacts("YOUNGCO", **PREPROFIT), rf=RF)
    inp = v.young_dcf["inputs"]
    for k in ("g_high_src", "target_margin_src", "sales_to_capital_src", "p_survival_src"):
        assert inp.get(k), (k, inp)
    assert inp["g_high_src"].startswith("fmp/estimates"), inp["g_high_src"]
    # dilution comes from the firm's own SBC (REV-1), not a constant
    assert inp["annual_dilution_pct"] > 0
    print("every young-DCF input carries a source OK")


if __name__ == "__main__":
    test_survival_from_runway()
    test_sales_to_capital_sources_and_clamp()
    test_failure_risk_is_separate_from_discount_rate()
    test_terminal_reinvestment_capped()
    test_band_gate_promotes_only_when_tight()
    test_blocked_reason_distinguishes_causes()
    test_equity_cannot_be_worth_less_than_zero()
    test_underwater_equity_never_anchors()
    test_deterministic_across_runs()
    test_profitable_names_untouched()
    test_inputs_are_all_sourced()
    print("\nALL test_young_dcf PASSED")
