"""
test_engine_v82 — locks the v8.2 finance fixes so they can't silently regress.
Offline/synthetic — no network.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from domain.engine import get_engine, available_versions
from domain.engine import deep_v82 as E
from domain.facts import FinancialFacts


def approx(a, b, tol=1e-6):
    return abs(a - b) <= tol


def test_cost_of_equity_and_erp():
    assert approx(E.ERP, 0.0445), E.ERP          # Damodaran US ERP, 2026-07 refresh
    assert approx(E.cost_of_equity(0.045, 1.0), 0.045 + 0.0445)
    print("cost of equity + ERP 4.45% OK")


def test_true_wacc_weights_debt():
    w, ke, kd, note = E.wacc_true(0.045, 1.10, equity_mktcap=200e9, total_debt=50e9,
                                  cash=0, tax=0.21, interest_expense=1e9, operating_income=30e9)
    assert ke > w, (ke, w)
    assert kd is not None and kd > 0.045, kd
    w2, ke2, kd2, note2 = E.wacc_true(0.045, 1.10, 200e9, 0, 0, 0.21, None, None)
    assert approx(w2, ke2) and "WACC=Ke" in note2, note2
    print("true WACC weights debt + collapses to Ke OK")


def test_ev_bridge_reverse_dcf():
    out = E.reverse_dcf(price=150, shares=3.0e9, revenue=8e9, rev_1y=6e9,
                        total_debt=5e9, cash=20e9, wacc_val=0.10, g=0.043,
                        tax=0.21, margin=0.20)
    assert out["triggered"]
    assert approx(out["enterprise_value"], 150 * 3.0e9 + 5e9 - 20e9, tol=1.0), out["enterprise_value"]
    print("EV-bridge reverse DCF OK (EV = mktcap + debt - cash)")


def test_rd_capitalization():
    rd = E.rd_capitalize([5e9, 4e9, 3e9, 2e9, 1e9, 1e9], reported_oi=8e9, reported_ic=30e9, tax=0.21)
    assert rd is not None
    adj_oi, adj_ic, adj_roic = rd
    assert adj_ic > 30e9 and adj_oi != 8e9 and adj_roic > 0
    assert E.rd_capitalize([], 8e9, 30e9, 0.21) is None
    print("R&D capitalization OK")


def test_two_stage_pe_clamp():
    price, detail = E.fundamental_peg_price(g_high=0.12, g_stable=0.04, ke=0.07,
                                            roic_high=0.30, roic_stable=0.15, forward_eps=10.0)
    assert price is not None
    assert E.PE_FLOOR <= detail["fair_pe"] <= E.PE_CEIL, detail
    assert E.fundamental_peg_price(0.12, 0.04, 0.09, 0.3, 0.15, 0)[0] is None
    print("fundamental 2-stage PEG + clamp OK")


def test_earnings_quality():
    assert E.earnings_quality(1.0e9, 1.2e9, 40e9, 0.05e9, 18e9)[0] == "CLEAN"
    v, flags, cc = E.earnings_quality(1.0e9, 0.5e9, 40e9, 3e9, 18e9)
    assert v in ("REVIEW", "LOW") and flags, (v, flags)
    assert E.earnings_quality(1.0e9, None, None, None, None)[0] is None
    print("earnings quality OK")


def _facts():
    ff = FinancialFacts("TEST")
    ff.revenue = 100e9; ff.operating_income = 30e9; ff.net_income = 25e9
    ff.shares_diluted = 1e9; ff.price = 200.0; ff.forward_eps = 8.0
    ff.total_debt = 20e9; ff.cash = 10e9; ff.equity = 50e9
    ff.beta = 1.1; ff.growth_lt = 0.12
    ff.capex = 5e9; ff.dep_amort = 4e9
    ff.income_before_tax = 28e9; ff.tax_expense = 5.6e9
    ff.revenue_annuals = [100e9, 88e9, 78e9, 70e9]
    ff.cfo = 27e9; ff.total_assets = 120e9; ff.interest_expense = 1e9; ff.sbc = 3e9
    ff.rnd_annuals = [6e9, 5e9, 4e9, 3e9, 2e9, 2e9]
    ff.operating_income_annuals = [30e9, 26e9, 22e9, 19e9]
    ff.equity_prior = 45e9; ff.total_debt_prior = 22e9; ff.cash_prior = 9e9
    ff.acquisitions_net = 2e9; ff.deferred_revenue = 12e9; ff.deferred_revenue_prior = 9e9
    ff.fwd_growth_near = 0.14; ff.fwd_growth_far = 0.10; ff.n_analysts = 20
    ff.peer_median_growth = 0.10
    ff.own_pe_pctile = 0.20
    ff.confidence = 90
    return ff


def test_engine_end_to_end():
    v = get_engine("8.2").evaluate(_facts(), rf=0.045)
    assert v.version == "8.2"
    for s in (v.D, v.E_exec, v.E_econ, v.P):
        assert s is None or 0 <= s <= 5, s
    assert v.composite is not None and 0 <= v.composite <= 5
    assert v.recommendation and v.verdict and v.signal in ("BUY", "HOLD", "SELL")
    assert v.cost_of_equity and approx(v.cost_of_equity, 0.045 + 1.1 * 0.0445, tol=1e-4)
    km = v.key_metrics
    assert km["wacc_pct"] < km["ke_pct"]
    assert v.eq_verdict in ("CLEAN", "REVIEW", "LOW")
    assert v.anchor_value is not None
    assert "breakdown" in v.subscores and v.subscores["breakdown"]
    print(f"end-to-end OK: {v.recommendation} comp {v.composite} "
          f"WACC {km['wacc_pct']}% < Ke {km['ke_pct']}% anchor {v.anchor_method} ${v.anchor_value}")


def test_registry():
    assert "8.2" in available_versions()
    assert get_engine("8.2").version == "8.2"
    print("registry exposes 8.2 OK")


def test_consensus_path():
    from sources import fmp
    est = [
        {"date": "2025-12-31", "revenueAvg": 100, "numberAnalystsEstimatedRevenue": 20},
        {"date": "2026-12-31", "revenueAvg": 120, "numberAnalystsEstimatedRevenue": 18},
        {"date": "2027-12-31", "revenueAvg": 132, "numberAnalystsEstimatedRevenue": 15},
    ]
    out = fmp.parse_estimate_path(est, latest_fy="2024-12-31")
    assert approx(out["fwd_growth_near"], 0.20, 1e-3), out
    assert approx(out["fwd_growth_far"], 0.10, 1e-3), out
    assert out["n_analysts"] == 20
    assert fmp.parse_estimate_path([], None) == {}
    print("consensus path (FMP estimates) OK")


def test_peer_medians():
    from pipeline.refresh import compute_peer_medians

    def mk(sec, g):
        ff = FinancialFacts("x"); ff.sector = sec; ff.revenue_annuals = [100 * (1 + g), 100]
        return (ff, None)
    fetched = {"A": mk("Tech", 0.30), "B": mk("Tech", 0.10), "C": mk("Tech", 0.20), "D": mk("Health", 0.05)}
    med = compute_peer_medians(fetched)
    assert approx(med["A"], 0.15, 1e-6), med
    assert "D" not in med
    print("peer-median (sector cohort) OK")


def test_demand_adjustments():
    notes = []
    s1 = E._r_demand(0.30, 0.10, 0.02, 0.8, notes)
    assert approx(s1, 4.75, 1e-6), s1
    notes = []
    s2 = E._r_demand(0.16, 0.30, 0.15, 0.30, notes)
    assert approx(s2, 1.0, 1e-6), s2
    print("demand rubric adjustments (organic/peer/durability) OK")


def test_pe_percentile():
    from sources import yahoo
    eps = [["2024-12-31", 5.0], ["2023-12-31", 4.0]]
    dates = ["2023-06-30", "2023-12-31", "2024-06-30", "2024-12-31"]
    closes = [36.0, 40.0, 80.0, 100.0]
    pct = yahoo.pe_percentile_5y(eps, closes, dates, price=110.0, current_eps=5.0)
    assert approx(pct, 1.0, 1e-6), pct
    assert yahoo.pe_percentile_5y([], closes, dates, 110, 5) is None
    print("own 5y P/E percentile OK")


def test_price_adjustment():
    # REV-7: the Price pillar scores MARGIN OF SAFETY = (Value - Price) / VALUE
    # (Damodaran S4/S12), not upside on price. V=150 / P=100 is a 33% margin, which
    # is the 15% band (4.0), +0.5 for a cheap own-multiple = 4.5. Under the old
    # divide-by-price this read 50% and took the top band — the +40% cut was really
    # only a 28.6% margin, so the pillar leaned generous.
    notes = []
    s = E._r_price(150, 100, 0.20, notes)
    assert approx(s, 4.5), s
    assert "of value" in notes[0], notes
    notes = []
    s2 = E._r_price(90, 100, 0.90, notes)
    assert approx(s2, 2.5), s2
    # pin the denominator: a 40% margin OF VALUE must reach the top band
    notes = []
    assert approx(E._r_price(100, 60, None, notes), 5.0), notes      # MoS 40.0%
    notes = []
    assert approx(E._r_price(100, 61, None, notes), 4.0), notes      # MoS 39.0%
    notes = []
    assert E._r_price(-5, 100, None, notes) is None, "negative fair value must not score"
    print("price margin-of-safety + own-multiple adjustment OK")


def test_review_2026_08_04_fixes():
    """Regression pins for the 2026-08-04 review (REV-1..REV-14)."""
    # REV-2: an unmeasurable ROIC falls back to the terminal ROIC, not GROWTH_CAP
    assert E.sustainable_growth_cap(None) == E.GROWTH_CAP
    assert approx(E.sustainable_growth_cap(None, roic_fallback=0.11), 0.11)
    assert approx(E.sustainable_growth_cap(-0.04, roic_fallback=0.09), 0.09)
    assert approx(E.sustainable_growth_cap(0.137, roic_fallback=0.30), 0.137)

    # REV-1: SBC dilution proxy + its cap
    assert E.sbc_dilution_rate(None, 100e9) is None
    assert E.sbc_dilution_rate(1e9, None) is None
    assert approx(E.sbc_dilution_rate(2e9, 100e9), 0.02)
    assert approx(E.sbc_dilution_rate(50e9, 100e9), E.SBC_DILUTION_CAP)   # capped
    assert approx(E.dilute(10.0, 0.02, years=1), 10 / 1.02)
    assert E.dilute(10.0, None) == 10.0                                   # identity

    # REV-3/REV-28: reinvestment is UNCAPPED, so growth is genuinely paid for and
    # pv_at becomes unimodal — the solver must find the peak, not assume monotonicity.
    kw = dict(shares=1e9, revenue=10e9, rev_1y=9e9, total_debt=0, cash=0,
              wacc_val=0.09, g=0.045, tax=0.21, margin=0.15, roic_term=0.30)
    out = [E.reverse_dcf(price=float(p), **kw)["implied_cagr_pct"] for p in (12, 20, 45, 120)]
    assert all(v is not None for v in out), out
    assert all(b > a for a, b in zip(out, out[1:])), ("implied CAGR must rise with price", out)

    # a thin-spread firm (ROIC 10% vs WACC 9%) genuinely cannot justify a rich price
    # by growing — value PEAKS inside the band. That must be SAID, not reported as
    # "beyond the model band", which means something else entirely.
    thin = E.reverse_dcf(price=45.0, **dict(kw, roic_term=0.10))
    assert thin["implied_cagr_pct"] is None and thin["out_of_band"]
    assert "no growth rate justifies" in thin["verdict"], thin["verdict"]
    # and the same firm at a modest price still solves
    assert E.reverse_dcf(price=12.0, **dict(kw, roic_term=0.10))["implied_cagr_pct"] is not None

    # REV-8: no price opinion => no BUY
    assert E.cap_reco_without_price("BUY", None)[0] == "HOLD / Accumulate"
    assert E.cap_reco_without_price("BUY", 4.0)[0] == "BUY"
    assert E.cap_reco_without_price("SELL / AVOID", None)[0] == "SELL / AVOID"

    # REV-9: acquisitions are reinvestment
    g_no = E.fundamental_growth(1e9, 0.6e9, 5e9, 0.20)
    g_acq = E.fundamental_growth(1e9, 0.6e9, 5e9, 0.20, acquisitions=1.5e9)
    assert g_acq > g_no, (g_no, g_acq)

    # REV-18: dWC completes capex + acquisitions + dWC - D&A, with an unambiguous sign
    from domain.facts import FinancialFacts as FF
    d, lbl = E.working_capital_change(FF("X", receivables=3e9, receivables_prior=2.2e9,
                                         inventory=1.5e9, inventory_prior=1.2e9,
                                         accounts_payable=1.0e9, accounts_payable_prior=0.9e9))
    assert approx(d, 1.0e9, 1), (d, lbl)          # +0.8 AR +0.3 inv -0.1 AP
    assert "AR+inventory+AP" in lbl, lbl
    # P2-3: a PARTIAL delta is worse than none. AP alone would read NEGATIVE ("working
    # capital released cash") purely because the offsetting AR and inventory legs were
    # not filed — understating reinvestment and inflating FCFF, in the company's
    # favour. Receivables is required; the other two only refine.
    d2, why2 = E.working_capital_change(FF("Y", accounts_payable=2e9, accounts_payable_prior=1e9))
    assert d2 is None and "receivables" in why2, (d2, why2)
    d3, lbl3 = E.working_capital_change(FF("Z2", receivables=3e9, receivables_prior=2.2e9))
    assert approx(d3, 0.8e9, 1) and lbl3 == "dWC from AR", (d3, lbl3)
    # AP still RELEASES cash when it is present alongside AR
    d4, _ = E.working_capital_change(FF("Z3", receivables=3e9, receivables_prior=3e9,
                                        accounts_payable=2e9, accounts_payable_prior=1e9))
    assert d4 < 0, d4
    assert E.working_capital_change(FF("Z"))[0] is None       # nothing filed -> honest None

    # P2-1: real share-count history beats the SBC/market-cap proxy, and a company
    # that RETIRES more stock than it grants must not be charged dilution at all.
    buyback = FF("BB", shares_diluted_annuals=[7.43e9, 7.47e9, 7.54e9, 7.61e9], sbc=12e9)
    rate, lbl = E.dilution_rate(buyback, 3100e9)
    assert rate == 0.0 and "actual diluted share count" in lbl, (rate, lbl)
    assert E.sbc_dilution_rate(12e9, 3100e9) > 0, "the old proxy would have charged this"
    dilutive = FF("DD", shares_diluted_annuals=[4.99e9, 4.92e9, 4.83e9, 4.74e9], sbc=7.6e9)
    r2, l2 = E.dilution_rate(dilutive, 1500e9)
    assert 0.015 < r2 < 0.02 and "actual" in l2, (r2, l2)
    assert r2 > E.sbc_dilution_rate(7.6e9, 1500e9), "proxy understated real dilution 3x"
    # no series (IFRS filer) -> proxy survives as the documented fallback
    r3, l3 = E.dilution_rate(FF("IF", sbc=1e9), 1000e9)
    assert r3 == 0.001 and "proxy" in l3, (r3, l3)
    assert E.dilution_rate(FF("NN"), 1000e9) == (None, None)
    assert E.share_count_growth([1e9]) == (None, None)         # too short -> honest None

    # REV-19: the AR-vs-revenue check `receivables` was always fetched for
    clean = E.earnings_quality(5e9, 6e9, 50e9, None, 10e9, receivables=2.2e9,
                               receivables_prior=2.0e9, revenue_prior=9e9)
    stuffed = E.earnings_quality(5e9, 6e9, 50e9, None, 10e9, receivables=3.4e9,
                                 receivables_prior=2.0e9, revenue_prior=9e9)
    assert clean[1] == [], clean
    assert any("receivables" in f for f in stuffed[1]), stuffed
    # without the prior it must stay silent rather than guess
    assert E.earnings_quality(5e9, 6e9, 50e9, None, 10e9, receivables=3.4e9)[1] == []

    # REV-20: the terminal-margin band is the last silent universal constant
    _, cap_lbl, anch = E.terminal_margin("AAA", 5.5e9, 10e9)      # 55% -> 40%
    assert "CAPPED" in cap_lbl and anch is True, cap_lbl
    _, flr_lbl, _ = E.terminal_margin("AAA", 0.2e9, 10e9)         # 2% -> 5%
    assert "FLOORED" in flr_lbl, flr_lbl
    _, ok_lbl, _ = E.terminal_margin("AAA", 2.5e9, 10e9)          # inside the band
    assert "CAPPED" not in ok_lbl and "FLOORED" not in ok_lbl, ok_lbl

    # REV-5: a price the solver cannot reach is reported, not silently "Unknown"
    out = E.reverse_dcf(price=5000.0, shares=1e9, revenue=1e9, rev_1y=0.9e9,
                        total_debt=0, cash=0, wacc_val=0.09, g=0.045, tax=0.21,
                        margin=0.10, roic_term=0.12)
    assert out["out_of_band"] is True and out["verdict"], out
    assert out["implied_cagr_pct"] is None

    # REV-24: the margin RAMPS from today's to the terminal one. Holding an assumed
    # 25% from year 1 credits a loss-making firm with a margin it does not have.
    ramp_kw = dict(price=28.0, shares=0.24e9, revenue=2.1e9, rev_1y=1.5e9,
                   total_debt=0.15e9, cash=1.15e9, wacc_val=0.1319, g=0.045,
                   tax=0.21, margin=0.25, wacc_term=0.1108, roic_term=0.15)
    flat = E.reverse_dcf(**ramp_kw)["implied_cagr_pct"]
    ramped_out = E.reverse_dcf(margin_now=-0.152, **ramp_kw)
    ramped = ramped_out["implied_cagr_pct"]
    # The ramp must CHANGE the answer, and change it in the HARSH direction. With a
    # terminal ROIC of 15% against a 13.19% WACC the spread is so thin that, once
    # the loss years are actually paid for, no growth rate clears EV — reported
    # explicitly (REV-5) rather than silently dropped.
    assert ramped != flat, (flat, ramped)
    assert ramped is None and ramped_out["out_of_band"] is True, ramped_out
    assert ramped_out["verdict"], ramped_out

    # 2026-08-16 SIGN-REVERSAL GUARD. Reinvestment is x/ROIC and routinely exceeds 1
    # for a pre-profit name (x=30%, ROIC=15% -> 2.0). The old path wrote
    # FCFF = NOPAT * (1 - reinvest), so a NEGATIVE NOPAT times a NEGATIVE factor
    # booked the loss year as POSITIVE cash and made the ramp KINDER than the flat
    # path — the exact inversion the ramp exists to prevent. With a workable spread
    # the invariant is monotone: the deeper today's loss, the more growth the price
    # implies, and always more than the flat path.
    wide = dict(ramp_kw, roic_term=0.25)
    flat_w = E.reverse_dcf(**wide)["implied_cagr_pct"]
    prev = flat_w
    for mn in (0.10, 0.0, -0.05, -0.152, -0.30):
        cur = E.reverse_dcf(margin_now=mn, **wide)["implied_cagr_pct"]
        assert cur is not None, mn
        assert cur > flat_w, (mn, cur, flat_w)    # ramping is never a discount
        assert cur > prev, (mn, cur, prev)        # deeper loss -> more growth needed
        prev = cur

    # REV-25: EV uses the REPORTED market cap when supplied, not price x shares
    a = E.reverse_dcf(price=10.0, shares=1e9, revenue=5e9, rev_1y=4e9, total_debt=0,
                      cash=0, wacc_val=0.09, g=0.045, tax=0.21, margin=0.20,
                      roic_term=0.25)
    b = E.reverse_dcf(price=10.0, shares=1e9, revenue=5e9, rev_1y=4e9, total_debt=0,
                      cash=0, wacc_val=0.09, g=0.045, tax=0.21, margin=0.20,
                      roic_term=0.25, market_cap=5e9)        # ADR: half the product
    assert b["enterprise_value"] == 5e9 and a["enterprise_value"] == 10e9
    assert b["implied_cagr_pct"] < a["implied_cagr_pct"]
    print("2026-08-04 review fixes OK (REV-1/2/3/5/7/8/9/18/19/20 + P2-1/P2-3)")


def test_heavy_capex_stays_on_earnings_path():
    """P2-5: negative FCFF from heavy investment must NOT reroute a profitable
    company to the young-company model.

    REV-23 removed the 0.8 reinvestment clamp that had made the `fcf_pos` leg of the
    routing gate vacuous. That was right on its own terms, but it silently changed
    where a profitable heavy-investor gets valued. ORCL — $48.2B capex against $16.4B
    NOPAT — started coming back through the S20 path (which ramps margins up from a
    loss and derives survival from cash runway) at $25 against a $213 price.
    """
    from domain.facts import FinancialFacts as FF
    heavy = dict(beta=1.0, price=213.0, revenue=64e9, operating_income=20.7e9,
                 net_income=16.2e9, shares_diluted=2.9e9, total_debt=90e9, cash=11e9,
                 equity=8e9, capex=48.2e9, dep_amort=6.4e9, interest_expense=3.6e9,
                 income_before_tax=18.7e9, tax_expense=2.5e9, forward_eps=6.5,
                 growth_lt=0.11, cfo=20.8e9, total_assets=168e9, market_cap=600e9,
                 revenue_annuals=[64e9, 57e9, 53e9, 50e9], operating_income_annuals=[20.7e9, 18e9])
    v = E.DeepV82Engine().evaluate(FF("HEAVY", **heavy), rf=0.045)
    assert not v.anchor_method.startswith("Young-Company"), (
        "a profitable company must not be valued as a young company", v.anchor_method)
    assert v.young_dcf == {}, "the S20 model should not even run here"
    assert v.anchor_value is not None and v.anchor_value > 0
    # the cash burn is still reported — REV-23's real benefit is kept
    assert any("FCFF negative" in x for x in v.flags), v.flags
    assert any("reinvestment" in x and "% of NOPAT" in x for x in v.flags), v.flags
    # and a genuinely pre-profit company still routes to S20
    pre = E.DeepV82Engine().evaluate(
        FF("PRE", **dict(heavy, operating_income=-2e9, net_income=-2.5e9,
                         forward_eps=-0.5, cfo=-1.8e9, eps_gaap=-0.86,
                         operating_income_annuals=[-2e9, -2.6e9])), rf=0.045)
    assert pre.young_dcf != {}, "a loss-making company must still reach the S20 model"
    print("heavy-capex routing OK (%s $%s, S20 skipped; loss-maker still routed)"
          % (v.anchor_method, v.anchor_value))


def test_sbc_no_double_count():
    """P2-2: dilution is charged only where the EPS does NOT already expense SBC.

    `fv_peg` runs on forward_eps — the NTM *adjusted* consensus, i.e. non-GAAP with SBC
    added back — so the cost is missing and dilution belongs there.
    `fv_fvp` runs on eps0, built from GAAP net income, where SBC is already a deducted
    expense. Charging dilution on top billed the same compensation twice (~12% of the
    FVP over five years) and broke the framework's own "no double counting" rule.
    """
    from domain.facts import FinancialFacts as FF
    base = dict(beta=1.4, price=64.0, revenue=7.5e9, operating_income=0.62e9,
                net_income=0.40e9, shares_diluted=0.62e9, total_debt=0.4e9, cash=2.6e9,
                equity=4.2e9, capex=0.12e9, dep_amort=0.09e9, interest_expense=0.02e9,
                income_before_tax=0.55e9, tax_expense=0.12e9, forward_eps=0.98,
                growth_lt=0.27, cfo=1.1e9, total_assets=8.8e9, market_cap=39.7e9,
                revenue_annuals=[7.5e9, 5.6e9, 4.0e9], operating_income_annuals=[0.62e9, 0.31e9],
                shares_diluted_annuals=[0.62e9, 0.60e9, 0.58e9, 0.56e9], sbc=1.05e9)
    eng = E.DeepV82Engine()
    heavy = eng.evaluate(FF("SBC", **base), rf=0.045)
    none_ = eng.evaluate(FF("NOSBC", **dict(base, shares_diluted_annuals=[], sbc=None)), rf=0.045)

    # the GAAP path must be IDENTICAL with and without dilution — it never sees it
    assert heavy.fv_fvp == none_.fv_fvp, (
        "FVP moved with dilution: eps0 is GAAP and must not be diluted again",
        heavy.fv_fvp, none_.fv_fvp)
    # the non-GAAP path must still be charged
    assert heavy.fv_peg is not None and none_.fv_peg is not None
    assert heavy.fv_peg < none_.fv_peg, ("PEG should carry the dilution",
                                         heavy.fv_peg, none_.fv_peg)
    km = heavy.key_metrics
    assert km["sbc_dilution_pct"] > 0 and "actual diluted share count" in km["dilution_source"]

    # a SEC-derived (GAAP) forward EPS must not be diluted either
    f2 = FF("GAAPFWD", **base)
    f2.provenance["forward_eps"] = "sec-derived (consensus rejected)"
    gaap_fwd = eng.evaluate(f2, rf=0.045)
    assert gaap_fwd.fv_peg == none_.fv_peg, (
        "a GAAP-derived forward EPS already expenses SBC", gaap_fwd.fv_peg, none_.fv_peg)
    assert any("double-count" in x for x in gaap_fwd.flags), gaap_fwd.flags
    print("SBC charged once, on the non-GAAP path only OK "
          "(PEG %.2f vs %.2f undiluted; FVP unchanged at %.2f)"
          % (heavy.fv_peg, none_.fv_peg, heavy.fv_fvp))


if __name__ == "__main__":
    test_cost_of_equity_and_erp()
    test_true_wacc_weights_debt()
    test_ev_bridge_reverse_dcf()
    test_rd_capitalization()
    test_two_stage_pe_clamp()
    test_earnings_quality()
    test_engine_end_to_end()
    test_review_2026_08_04_fixes()
    test_heavy_capex_stays_on_earnings_path()
    test_sbc_no_double_count()
    test_registry()
    test_consensus_path()
    test_peer_medians()
    test_demand_adjustments()
    test_pe_percentile()
    test_price_adjustment()
    print("\nALL v8.2 ENGINE TESTS PASSED OK")
