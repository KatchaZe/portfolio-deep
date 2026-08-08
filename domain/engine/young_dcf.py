"""
Young / pre-profit forward intrinsic DCF — Damodaran S20 (REV-16).

WHY THIS EXISTS
---------------
Before this module a pre-profit name got `anchor_value = None` and no Price
pillar. The only thing on its card was the Terminal-Anchored Reverse DCF — and
that is a PRICING check, not a valuation: it answers "what growth has the market
priced in?", never "what is this worth?". So the app had no opinion on the
cheapness of exactly the names where opinion is hardest and most valuable.

S20 gives the method, and it turns on two rules the reverse DCF cannot express:

  1. FAILURE RISK IS MODELLED SEPARATELY, never buried in the discount rate —
     Damodaran calls the discount rate a "blunt instrument" for this job:

         value/share = p_survival x going_concern + (1 - p_survival) x distress

  2. GROWTH IS FUNDED THROUGH THE SALES-TO-CAPITAL RATIO:

         reinvestment_t = (rev_t - rev_{t-1}) / sales_to_capital

     so a faster revenue ramp automatically costs more cash. Growth cannot be
     free here the way it was in the paths REV-2/REV-3 had to be patched.

  3. The answer is a DISTRIBUTION, not a point ("less detail, more precision").

EVERY INPUT IS DERIVED FROM FILED DATA
--------------------------------------
The whole point of this codebase's last three reviews is that hand-picked
constants fail silently. So nothing here is a hand-picked number: sales-to-capital
comes from the firm's own revenue/invested-capital, dilution from its own SBC,
distress value from its own net cash, survival from its own cash runway. Each
carries a source label in `inputs`, and anything that had to be clamped says so.

The one irreducible assumption is `target_margin` (a pre-profit firm has no
margin to extrapolate). It is taken from the shared `terminal_margin()` helper,
flagged as an assumption, and Monte-Carlo'd +/- 6pp.
"""
import random
import statistics

# Sales-to-capital is a ratio of two filed numbers, but a near-zero invested
# capital sends it to infinity. Two-sided band, and a bind is always reported —
# never a silent clamp (this file exists because of silent clamps).
S2C_LO, S2C_HI = 0.4, 6.0
# A young company can genuinely grow faster than the mature GROWTH_CAP of 30%;
# growth fades to g_stable over the horizon and is paid for via sales-to-capital,
# so a high starting rate is self-financing here rather than free.
G_HIGH_CAP = 0.60
MC_SIMS = 1000              # p10/p50/p90 are stable well below the skill's 5000
MC_SEED = 42                # deterministic: the same facts must value the same twice
# P2-4: how wide to sample the target margin. This is the single most consequential
# number in the module — the band it produces is what the promotion gate then judges,
# so a hardcoded width means the gate is measuring OUR sampling choice rather than the
# company. Sensitivity is brutal: on the reference case, target margin 10% -> 35%
# swings going-concern value -88% to +59%. A flat +/-6pp therefore covered only
# ~19-31%, produced a ~1.8x band, and sailed through a 4x gate on every name.
#
# So the width is now tied to WHERE the margin came from:
#   ANCHORED  - derived from the firm's own positive operating margin. We measured it;
#               the remaining uncertainty is how far it drifts, not what it is.
#   ASSUMED   - the firm is loss-making, so the margin is a table entry or the generic
#               25%. We do not know it at all, and the band must say so.
MC_MARGIN_SPAN_ANCHORED = 0.04
MC_MARGIN_SPAN_ASSUMED = 0.10
# Promotion gate (user decision, 2026-08-04): a valuation only earns the right to
# move a recommendation if we actually know something. A p90/p10 spread wider than
# this means the inputs, not the company, are driving the answer.
BAND_MAX_RATIO = 4.0


def survival_probability(cash, cfo, fcff, revenue):
    """p_survival from the firm's own CASH RUNWAY (REV-16).

    Damodaran derives this from sector survival statistics adjusted for runway and
    access to capital. On free data the runway is the honest half of that, and it
    is the half that moves: a company with eight years of cash is not the same bet
    as one with nine months, whatever its sector says.

    Returns (p, label). Never None — a firm that is not burning at all still gets
    a ceiling below 1.0, because survival is never certain.
    """
    burn = None
    if cfo is not None and cfo < 0:
        burn = -cfo
    elif fcff is not None and fcff < 0:
        burn = -fcff
    if not burn or burn <= 0:
        p, why = 0.90, "not burning cash (CFO >= 0)"
    elif not cash or cash <= 0:
        return 0.25, "burning cash with no cash balance"
    else:
        runway = cash / burn
        for yrs, prob in ((5, 0.85), (3, 0.75), (2, 0.65), (1, 0.50)):
            if runway >= yrs:
                p, why = prob, f"cash runway {runway:.1f}y"
                break
        else:
            p, why = 0.30, f"cash runway {runway:.1f}y (under a year)"
    # scale premium: a multi-billion revenue base is far harder to kill than a
    # pre-revenue one, and it is the other lever Damodaran adjusts for.
    if revenue and revenue >= 5e9:
        p, why = p + 0.05, why + ", >$5B revenue"
    elif revenue and revenue >= 1e9:
        p, why = p + 0.03, why + ", >$1B revenue"
    return max(0.25, min(0.95, p)), why


def sales_to_capital(revenue, invested_capital, total_assets, cash):
    """Revenue generated per dollar of invested capital. Returns (ratio, label)."""
    if not revenue or revenue <= 0:
        return None, "no revenue"
    base, src = None, None
    if invested_capital and invested_capital > 0:
        base, src = revenue / invested_capital, "revenue / invested capital"
    elif total_assets and (total_assets - (cash or 0)) > 0:
        # net-cash firms have no meaningful invested capital; operating assets are
        # the next-best denominator and stay a FILED number rather than a guess
        base, src = revenue / (total_assets - (cash or 0)), "revenue / (assets - cash)"
    if base is None:
        return None, "no usable capital base"
    clamped = max(S2C_LO, min(S2C_HI, base))
    if abs(clamped - base) > 1e-9:
        return clamped, f"{src} {base:.2f} clamped to {clamped:.2f} [{S2C_LO}-{S2C_HI}]"
    return clamped, f"{src} {base:.2f}"


def going_concern(d):
    """Forward FCFF build + Gordon terminal. Mirrors the skill script exactly
    (ifa-stock-analysis-v8/scripts/young_company_dcf.going_concern) — the two are
    pinned together by tests/test_skill_parity."""
    rev = d["current_revenue"]
    g_h, g_st = d["g_high"], d["g_stable"]
    n = int(d.get("horizon", 10))
    cur_m, tgt_m = d["current_margin"], d["target_margin"]
    s2c, tax, wacc = d["sales_to_capital"], d["tax"], d["wacc"]
    roic_st = d.get("roic_stable", 0.15)
    if wacc <= g_st or not s2c or s2c <= 0:
        return None
    use_nol = d.get("use_nol", True)
    nol = float(d.get("nol_initial", 0.0))
    pv_fcff, rev_prev, rows = 0.0, rev, []
    for t in range(1, n + 1):
        g_t = g_h + (g_st - g_h) * ((t - 1) / (n - 1)) if n > 1 else g_st
        m_t = cur_m + (tgt_m - cur_m) * (t / n)
        rev_t = rev_prev * (1 + g_t)
        ebit = rev_t * m_t
        if ebit <= 0:
            nopat = ebit                          # loss year: no tax, NOL accumulates
            if use_nol:
                nol += -ebit
        else:
            shield = min(nol, ebit) if use_nol else 0.0
            nol -= shield
            nopat = ebit - tax * (ebit - shield)
        reinv = (rev_t - rev_prev) / s2c
        fcff = nopat - reinv
        pv_fcff += fcff / (1 + wacc) ** t
        rows.append({"yr": t, "g_pct": round(g_t * 100, 1), "margin_pct": round(m_t * 100, 1),
                     "revenue": round(rev_t, 0), "fcff": round(fcff, 0)})
        rev_prev = rev_t
    rev_term = rev_prev * (1 + g_st)
    nopat_term = rev_term * tgt_m * (1 - tax)
    fcff_term = nopat_term * (1 - min(0.9, g_st / roic_st))
    pv_tv = (fcff_term / (wacc - g_st)) / (1 + wacc) ** n
    firm = pv_fcff + pv_tv
    equity = firm - d.get("net_debt", 0.0)
    shares = d["shares"] * (1 + d.get("annual_dilution", 0.0) or 0.0) ** n
    if not shares or shares <= 0:
        return None
    # Equity is a RESIDUAL claim carrying LIMITED LIABILITY. When the forward DCF
    # values the firm below its net debt the shareholder's claim is worth zero, not a
    # negative number — a share price cannot go below zero. The distress leg in
    # evaluate() has been floored since it was written (max(0.0, cash - debt)); this
    # leg never was, and the asymmetry showed: RKLB rendered "worth -$0.92 if it
    # survives, $1.83 if it fails" — worth more dead than alive, blending to -$0.51.
    #
    # `equity_value` deliberately stays RAW. How far underwater the firm is remains
    # the diagnostic, and evaluate() reads it to block promotion: flooring collapses
    # the downside variance, so a floored band is artificially tight and its width
    # says nothing about the company.
    return {"pv_fcff": pv_fcff, "pv_terminal": pv_tv, "firm_value": firm,
            "equity_value": equity, "diluted_shares": shares,
            "going_concern_per_share": max(0.0, equity / shares), "rows": rows}


def failure_adjusted(per_share_gc, p_survival, distress_per_share):
    """The S20 rule. Kept as its own function so it is impossible to 'simplify'
    into the discount rate later."""
    return p_survival * per_share_gc + (1 - p_survival) * (distress_per_share or 0.0)


def monte_carlo(d, n_sims=MC_SIMS, seed=MC_SEED, margin_span=MC_MARGIN_SPAN_ASSUMED):
    """Triangular resample of the four inputs that actually carry the uncertainty.
    Seeded, so a refresh with unchanged facts returns an unchanged band.
    `margin_span` is set by the caller from whether the terminal margin was MEASURED
    or ASSUMED — see the constants above for why it cannot be a fixed number."""
    rnd = random.Random(seed)
    vals = []
    for _ in range(n_sims):
        s = dict(d)
        s["target_margin"] = rnd.triangular(d["target_margin"] - margin_span,
                                            d["target_margin"] + margin_span,
                                            d["target_margin"])
        s["sales_to_capital"] = rnd.triangular(max(S2C_LO, d["sales_to_capital"] - 0.7),
                                               d["sales_to_capital"] + 0.7, d["sales_to_capital"])
        s["g_high"] = rnd.triangular(max(0.0, d["g_high"] - 0.12),
                                     d["g_high"] + 0.12, d["g_high"])
        p_surv = rnd.triangular(max(0.0, d["p_survival"] - 0.2),
                                min(1.0, d["p_survival"] + 0.2), d["p_survival"])
        gc = going_concern(s)
        if gc is None:
            continue
        vals.append(failure_adjusted(gc["going_concern_per_share"], p_surv,
                                     d.get("distress_value_per_share")))
    if len(vals) < n_sims * 0.5:
        return None
    vals.sort()

    def pct(q):
        return vals[int(q * (len(vals) - 1))]

    return {"n": len(vals), "p10": round(pct(0.10), 2), "p50": round(pct(0.50), 2),
            "p90": round(pct(0.90), 2), "mean": round(statistics.mean(vals), 2)}


def evaluate(f, *, rf, wacc, roic_term, invested_capital, debt_eff, tax,
             target_margin, target_margin_label, annual_dilution, fcff,
             growth_fallback, target_margin_anchored=False):
    """Value a pre-profit company from a FinancialFacts. Returns a dict ready to
    hang off the Valuation, or None when the inputs cannot support a valuation.

    `promote` in the result says whether this number earned the right to become the
    row's anchor: only when the Monte Carlo band is tight enough to mean something
    (p10 > 0 and p90/p10 < BAND_MAX_RATIO). A wide band is a real answer too — it
    says the value is unknowable on this data — and it stays supplementary.
    """
    if not f.revenue or f.revenue <= 0 or not f.shares_diluted or not f.price:
        return None
    s2c, s2c_label = sales_to_capital(f.revenue, invested_capital, f.total_assets, f.cash)
    if not s2c:
        return None

    # growth: consensus first (the reference is explicit that g_high must not be
    # hand-picked), then the engine's own long-term input, then nothing.
    g_src = "fmp/estimates next-FY revenue"
    g_high = f.fwd_growth_near
    if g_high is None:
        g_high, g_src = growth_fallback, "sec revenue CAGR (min 3y/5y)"
    if g_high is None:
        return None
    g_raw = g_high
    g_high = max(0.0, min(G_HIGH_CAP, g_high))
    if abs(g_high - g_raw) > 1e-9:
        g_src += f" {g_raw*100:.0f}% clamped to {g_high*100:.0f}%"

    cur_margin = (f.operating_income / f.revenue) if f.operating_income is not None else -0.10
    p_surv, p_why = survival_probability(f.cash, f.cfo, fcff, f.revenue)
    # liquidation floor: operations worth nothing, cash net of debt is what is left.
    distress = max(0.0, (f.cash or 0) - (debt_eff or 0)) / f.shares_diluted

    d = {"current_revenue": f.revenue, "g_high": g_high, "g_stable": rf, "horizon": 10,
         "current_margin": cur_margin, "target_margin": target_margin,
         "sales_to_capital": s2c, "tax": tax, "wacc": wacc, "roic_stable": roic_term,
         "net_debt": (debt_eff or 0) - (f.cash or 0), "shares": f.shares_diluted,
         "annual_dilution": annual_dilution or 0.0, "p_survival": p_surv,
         "distress_value_per_share": distress}

    gc = going_concern(d)
    if gc is None:
        return None
    point = failure_adjusted(gc["going_concern_per_share"], p_surv, distress)
    span = MC_MARGIN_SPAN_ANCHORED if target_margin_anchored else MC_MARGIN_SPAN_ASSUMED
    mc = monte_carlo(d, margin_span=span)

    # Why a value may NOT anchor a row. Three different situations that all used to
    # collapse into one "band too wide" message — including the case where the band
    # is perfectly TIGHT and simply negative, which is the opposite problem and a far
    # more interesting thing to tell the user.
    band_ratio, blocked = None, None
    if not mc:
        blocked = "simulation did not converge"
    elif gc["equity_value"] <= 0:
        # Must be tested BEFORE the band gates, and on the RAW equity. Flooring the
        # going-concern leg at zero collapses the downside variance — every scenario
        # that lands underwater now reports the same 0 — so the band tightens and p10
        # can no longer fall <= 0. On a real case this turned a p10/p50/p90 of
        # -3.80/-2.48/-1.43 (correctly blocked) into 0.13/0.29/0.47, a 3.62x band that
        # sails through BAND_MAX_RATIO and would anchor the row at $0.29. That
        # tightness is the width of our own clamp, not knowledge about the company —
        # the same mistake P2-4 fixed one gate further on.
        blocked = (f"the forward DCF values the whole firm below its net debt "
                   f"(equity ${gc['equity_value'] / gc['diluted_shares']:.2f}/share before the "
                   f"zero floor) - the equity is worth the distress floor "
                   f"${round(distress, 2)} and no more")
    elif mc["p50"] <= 0:
        blocked = (f"the model values the equity at or below zero across the band "
                   f"(p50 ${mc['p50']}) - the story does not support a positive value")
    elif mc["p10"] <= 0:
        blocked = (f"the downside case is a total loss (p10 ${mc['p10']}) - a p50 of "
                   f"${mc['p50']} cannot anchor a position that can go to zero")
    else:
        band_ratio = round(mc["p90"] / mc["p10"], 2)
        if band_ratio >= BAND_MAX_RATIO:
            blocked = (f"value band ${mc['p10']}-${mc['p90']} spans {band_ratio}x - the "
                       f"assumptions, not the company, are driving the answer")
    promote = blocked is None

    return {
        "going_concern_per_share": round(gc["going_concern_per_share"], 2),
        "failure_adjusted_per_share": round(point, 2),
        "p_survival": round(p_surv, 2),
        "distress_per_share": round(distress, 2),
        "monte_carlo": mc,
        "band_ratio": band_ratio,
        "promote": promote,
        "blocked_reason": blocked,
        "terminal_share_of_value_pct": (round(gc["pv_terminal"] / gc["firm_value"] * 100, 1)
                                        if gc["firm_value"] else None),
        "inputs": {
            "g_high_pct": round(g_high * 100, 1), "g_high_src": g_src,
            "current_margin_pct": round(cur_margin * 100, 1),
            "target_margin_pct": round(target_margin * 100, 1),
            "target_margin_src": target_margin_label,
            # P2-4: the band's width is an input too, so it is reported like any other
            "target_margin_sampled_pp": round(span * 100, 1),
            "target_margin_anchored": bool(target_margin_anchored),
            "sales_to_capital": round(s2c, 2), "sales_to_capital_src": s2c_label,
            "p_survival_src": p_why,
            "annual_dilution_pct": round((annual_dilution or 0) * 100, 2),
            "wacc_pct": round(wacc * 100, 2), "roic_stable_pct": round(roic_term * 100, 1),
        },
    }
