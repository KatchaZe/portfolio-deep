"""
DEEP Framework v8.2 engine — implements the DeepEngine contract.
Free-data limits handled per skill invariant 17 (skip + flag, never fabricate).
"""
import datetime

import config
from .contract import DeepEngine, Valuation

# --- constants (v8) ---------------------------------------------------------
# NOTE (assumptions feature): functions below read config.ERP / config.ERP_AS_OF
# at CALL time, so a store-backed manual override (dashboard "Assumptions" form,
# applied in store.load()) takes effect without a restart. The module-level
# snapshot is kept only for back-compat/reference.
ERP = config.ERP             # snapshot at import — do NOT use in calculations
ERP_AS_OF = config.ERP_AS_OF
ROIC_TERMINAL = 0.15
REVERSE_HORIZON = 10
GROWTH_CAP = 0.30
RD_LIFE = 5
DEFAULT_KD_SPREAD = 0.013

SYNTH_SPREAD = [
    (8.50, 0.0069), (6.50, 0.0085), (5.50, 0.0103), (4.25, 0.0124),
    (3.00, 0.0150), (2.50, 0.0185), (2.25, 0.0238), (2.00, 0.0307),
    (1.75, 0.0400), (1.50, 0.0510), (1.25, 0.0620), (0.80, 0.0840),
    (0.65, 0.1080), (0.20, 0.1400), (-1e9, 0.1900),
]

# Fallback terminal operating margins for PRE-PROFIT names only. Profitable names
# now DERIVE terminal margin from their own current SEC op margin — see terminal_margin().
TERMINAL_MARGIN = {
    "NVDA": 0.35, "MSFT": 0.40, "AVGO": 0.35, "TSM": 0.40, "GOOGL": 0.35,
    "ORCL": 0.35, "MELI": 0.20, "ABBV": 0.30, "TMDX": 0.20, "LLY": 0.30,
    "TSLA": 0.15, "NVO": 0.35,
}
TERMINAL_MARGIN_CAP = 0.40
TERMINAL_MARGIN_FLOOR = 0.05


def terminal_margin(ticker, operating_income, revenue):
    """Terminal operating margin for the reverse DCF, DATA-ANCHORED where possible:
    the company's own current SEC operating margin (clamped). Falls back to the table,
    then generic 25%, ONLY when there is no positive current margin (pre-profit).
    Returns (margin, source_label, anchored_bool)."""
    if operating_income is not None and revenue:
        cur = operating_income / revenue
        if cur > 0:
            tm = max(TERMINAL_MARGIN_FLOOR, min(TERMINAL_MARGIN_CAP, cur))
            return tm, f"terminal margin {tm*100:.0f}% (from current op margin {cur*100:.0f}%)", True
    tm = TERMINAL_MARGIN.get(ticker)
    if tm is not None:
        return tm, f"terminal margin {tm*100:.0f}% (assumed table - pre-profit)", False
    return 0.25, "terminal margin 25% (generic assumption - pre-profit, no table)", False


def erp_months_old(as_of=None, today=None):
    """Months since the ERP assumption was set (for the staleness flag).
    Reads config.ERP_AS_OF at call time (store override aware)."""
    try:
        y, m = (int(x) for x in str(as_of or config.ERP_AS_OF).split("-")[:2])
        d = today or datetime.date.today()
        return (d.year - y) * 12 + (d.month - m)
    except Exception:
        return 0


def _clamp(x, lo=0.0, hi=5.0):
    return max(lo, min(hi, x))


def _add_flag(f, note):
    """Append a flag once. Replaces ~14 copies of the if-not-in-list-append idiom;
    a duplicated flag is harmless but a MISSED one is not, so this keeps every
    caller on the same shape."""
    if note and note not in f.flags:
        f.flags.append(note)


def _band(x, bands):
    for t, s in bands:
        if x >= t:
            return s
    return bands[-1][1]


def cost_of_equity(rf, beta):
    return rf + beta * config.ERP        # call-time read (assumptions override aware)


def _spread_from_coverage(cov):
    for lb, sp in SYNTH_SPREAD:
        if cov >= lb:
            return sp
    return SYNTH_SPREAD[-1][1]


TERMINAL_BETA_LO, TERMINAL_BETA_HI = 0.8, 1.2


def terminal_beta(beta):
    """Beta for the STABLE-GROWTH phase (P-K).

    Damodaran's stable-growth rules apply to risk as well as to returns: a company
    that has settled into perpetual ~risk-free growth cannot still be twice as
    risky as the whole market, and a defensive name cannot stay at beta 0.2 for
    ever either. His stated stable-growth band is 0.8-1.2, applied here in BOTH
    directions (NVDA 2.21 -> 1.2, REGN 0.24 -> 0.8).

    This closes the last gap in the terminal-phase story: ROIC fades to the
    industry, growth is capped at the risk-free rate, and now the discount rate
    fades too. Leaving beta un-faded was what made a 62%-ROIC company earn a
    stable-phase P/E of 8.3 — and the old PE floor of 12 was quietly papering
    over it."""
    if beta is None:
        return 1.0
    return min(TERMINAL_BETA_HI, max(TERMINAL_BETA_LO, beta))


def wacc_true(rf, beta, equity_mktcap, total_debt, cash, tax, interest_expense, operating_income,
              beta_override=None):
    """True WACC. Returns (wacc, ke, kd_pretax, note).
    `beta_override` recomputes Ke at a different beta while holding the capital
    structure and cost of debt fixed — used for the terminal-phase WACC."""
    ke = cost_of_equity(rf, beta_override if beta_override is not None else beta)
    if not total_debt or not equity_mktcap:
        return ke, ke, None, "no debt/weights -> WACC=Ke"
    if interest_expense and interest_expense > 0 and operating_income:
        cov = operating_income / interest_expense
        spread = _spread_from_coverage(cov)
        note = f"Kd via coverage {cov:.1f}x"
    else:
        spread = DEFAULT_KD_SPREAD
        note = "Kd via default spread (no interest data)"
    kd_after = (rf + spread) * (1 - tax)
    v = equity_mktcap + total_debt
    we, wd = equity_mktcap / v, total_debt / v
    return ke * we + kd_after * wd, ke, rf + spread, note


def rd_capitalize(rnd_annuals, reported_oi, reported_ic, tax, life=RD_LIFE):
    rd = [x for x in (rnd_annuals or []) if isinstance(x, (int, float)) and x > 0]
    if not rd or reported_oi is None or not reported_ic or reported_ic <= 0:
        return None
    if len(rd) < life + 1:
        rd = rd + [rd[-1]] * (life + 1 - len(rd))
    research_asset = sum(rd[i] * (life - i) / life for i in range(0, life + 1))
    amort = sum(rd[i] / life for i in range(1, life + 1))
    adj_oi = reported_oi + rd[0] - amort
    adj_ic = reported_ic + research_asset
    if adj_ic <= 0:
        return None
    return adj_oi, adj_ic, adj_oi * (1 - tax) / adj_ic


def two_stage_pe(g_h, n, g_st, ke, roic_h, roic_st, ke_st=None):
    """Justified P/E over a high-growth stage then a perpetuity.

    P-B2: `ke` prices the high-growth years, `ke_st` capitalizes the perpetuity.
    They used to be the same number, which meant a stable-growth perpetuity was
    discounted at the company's CURRENT (high-growth) cost of equity for ever —
    the same inconsistency as freezing ROIC. The terminal cash flow is still
    discounted BACK across the high-growth years at `ke`, because those years
    really are that risky. Defaults to ke_st = ke for callers that don't care."""
    ke_st = ke if ke_st is None else ke_st
    if ke_st <= g_st:
        return None
    payout_h = max(0.0, 1 - g_h / roic_h) if roic_h else 0.0
    payout_st = max(0.0, 1 - g_st / roic_st) if roic_st else 0.0
    comp = ((1 + g_h) ** n) / ((1 + ke) ** n)
    if abs(ke - g_h) < 1e-9:
        term1 = payout_h * (1 + g_h) * n / (1 + ke)
    else:
        term1 = payout_h * (1 + g_h) * (1 - comp) / (ke - g_h)
    term2 = payout_st * ((1 + g_h) ** n) * (1 + g_st) / ((ke_st - g_st) * (1 + ke) ** n)
    return term1 + term2


# P-M: one band for both valuation paths (they clamp the SAME quantity — a
# justified P/E — and used to disagree: [8,30] for PEG vs [12,25] for the FVP
# exit multiple). The band is now a garbage catcher, not a value donor:
#   * the old FLOOR bound 11 of 21 holdings in the FVP path and gifted up to
#     +103% (RKLB 5.9 -> 12). A low justified P/E is the CORRECT answer for a
#     high-Ke, low-ROIC business, not a number to be overridden.
#   * the old ceilings (30 / 25) cut genuine answers on the highest-quality names.
# REVIEW-4: an earlier note here claimed ke_st - g_st >= 3% bounds the formula near
# 35 "by construction". That is FALSE — term2 carries ((1+g_h)/(1+ke))^n, which
# exceeds 1 whenever g_h > ke, so raw P/Es of 40-56 are reachable (g 25-30% with a
# high sector ROIC). 35 is therefore a real judgement cap, not a no-op, and it does
# bind (LLY raw 42.2). It is deliberately kept: an unbounded justified P/E on
# 5 years of extrapolated growth is a bigger risk than clipping the top decile.
# Whenever it binds the row is flagged as a boundary rather than an estimate.
PE_FLOOR, PE_CEIL = 5.0, 35.0


def fundamental_peg_price(g_high, g_stable, ke, roic_high, roic_stable, forward_eps, years=5,
                          ke_stable=None):
    # REVIEW-6: ZERO growth is a valid input, not a missing one. The old guard was
    # `g_high <= 0`, so once P-E started (correctly) reporting 0% for a shrinking
    # company, that company lost its PEG valuation entirely and fell through to a
    # trailing-EPS method — PFE swung from +37% to -60% on this alone. The two-stage
    # formula handles g=0 fine: payout_h becomes 1 (nothing needs reinvesting).
    if forward_eps is None or forward_eps <= 0 or g_high is None or g_high < 0:
        return None, None
    pe = two_stage_pe(g_high, years, g_stable, ke, roic_high, roic_stable, ke_st=ke_stable)
    if pe is None or pe <= 0:
        return None, None
    pe_clamped = _clamp(pe, PE_FLOOR, PE_CEIL)
    detail = {"fair_pe": round(pe_clamped, 2), "fair_pe_raw": round(pe, 2),
              # PEG is undefined at zero growth — report None rather than dividing by 0
              "fundamental_peg": round(pe_clamped / (g_high * 100), 3) if g_high > 0 else None,
              "pe_clamped": pe_clamped != pe}
    return pe_clamped * forward_eps, detail


def fundamental_growth(capex, dep_amort, nopat, roic_now):
    """Damodaran's fundamental growth (P-F): g = reinvestment rate x ROIC — growth
    is not free, it has to be bought with reinvestment.

    Used as a CROSS-CHECK, not as the driver: (capex - D&A)/NOPAT sees only
    physical reinvestment, so it badly understates asset-light R&D spenders
    (NVDA prints 0.0% while growing 65%), and capex is missing for several
    filers. Returns None when it cannot be computed honestly."""
    if capex is None or dep_amort is None or not nopat or nopat <= 0:
        return None
    if roic_now is None or roic_now <= 0:
        return None
    return _clamp((capex - dep_amort) / nopat, 0.0, 0.8) * roic_now


def sustainable_growth_cap(roic_now):
    """Growth ceiling for the high-growth stage (P-H).

    Sustainable growth = reinvestment rate x ROIC, and reinvestment rate cannot
    exceed 100% of earnings without raising outside capital. So g <= ROIC.

    Why this matters: the payout term is max(0, 1 - g/ROIC). When g > ROIC the
    required reinvestment exceeds earnings, and the max(0, .) silently floors the
    shortfall at zero instead of charging for it — MELI at g=30% with ROIC=13.7%
    needs to reinvest 219% of earnings, yet the model paid no dilution and still
    scaled the terminal earnings base by (1+g)^5 = 3.7x. Growth was free.

    Returns GROWTH_CAP unchanged when ROIC is unknown or non-positive (pre-profit
    names are valued off the reverse DCF, not off this path)."""
    if roic_now is None or roic_now <= 0:
        return GROWTH_CAP
    return min(GROWTH_CAP, roic_now)


def terminal_roic(roic_now, wacc_val, sector_cap=None):
    """Stable-phase ROIC (P-D). Damodaran's three rules for the terminal phase:
      1. competition erodes excess returns -> ROIC must FADE toward the cost of
         capital; nobody earns 40-60% forever,
      2. a firm with a DURABLE moat may keep part of its spread, but a modest part,
      3. the cost of capital is also the FLOOR — a business earning below it gets
         restructured, acquired or liquidated; it does not burn capital forever.
    So: keep half of today's spread, never above the industry ceiling, never
    above what the firm actually earns today (no free upgrade), never below the
    cost of capital.

    `sector_cap` is Damodaran's own industry ROIC (sources/damodaran.py); when it
    is unavailable we fall back to ROIC_TERMINAL = 15%, which is close to his
    market-wide lease & R&D adjusted figure (15.07%, Jan 2026) — i.e. "converge
    to the average company" when we have no industry view.

    Replaces the old hardcoded ROIC_TERMINAL=0.15 for every company, which handed
    a 5%-ROIC business a 15% perpetuity and made the model systematically rate
    LOW-quality firms as the cheapest (PFE +151%, REGN +110%, ELV +29%)."""
    cap = sector_cap if (sector_cap and sector_cap > 0) else ROIC_TERMINAL
    if roic_now is None or wacc_val is None or wacc_val <= 0:
        # REVIEW-2: with no ROIC of its own there is nothing to fade FROM, so the
        # industry number would become a gift — Tobacco 77.7%, Retail Building
        # Supply 39.3%. Fall back to the market-average default and let the
        # industry figure only pull it DOWN.
        return min(cap, ROIC_TERMINAL)
    half = wacc_val + 0.5 * max(0.0, roic_now - wacc_val)
    return max(wacc_val, min(roic_now, cap, half))


def stable_exit_pe(g_st, ke, roic_st):
    """Justified STABLE-period P/E (Damodaran, Gordon form):
    PE = payout_st * (1+g_st) / (Ke - g_st), payout_st = 1 - g_st/ROIC_st.
    Used as the exit multiple in the Future Value Projection — replaces the old
    'exit PE = growth x 100' PEG=1 heuristic (Philosophy-2026 alignment: exit
    multiples must come from stable-period fundamentals, S5/S19)."""
    if ke is None or g_st is None or ke <= g_st:
        return None
    payout = max(0.0, 1 - g_st / roic_st) if roic_st else 0.0
    return payout * (1 + g_st) / (ke - g_st)


def future_value_projection(eps0, growth, ke, exit_pe):
    if eps0 is None or eps0 <= 0 or exit_pe is None:
        return None
    pe = _clamp(exit_pe, PE_FLOOR, PE_CEIL)     # P-M: same band as the PEG path
    return (eps0 * (1 + growth) ** 5 * pe) / (1 + ke) ** 5


def reverse_dcf(price, shares, revenue, rev_1y, total_debt, cash, wacc_val, g, tax, margin,
                wacc_term=None, roic_term=None):
    """Full-path reverse DCF (Philosophy-2026 fix): solve the revenue CAGR x such
    that PV(interim FCFF years 1..H) + PV(terminal value) = Enterprise Value.
    The old closed form assumed ALL of EV compounds into the year-H terminal value
    (no credit for interim cash flows) which OVERSTATED the implied CAGR — verdicts
    skewed toward 'Aggressive/Exceptional'. Damodaran reverse DCF discounts the
    whole FCFF path (S19/S21: what growth is the market pricing in?).

    P-L, two terminal-phase fixes — both matter a lot here because the terminal
    value is 90-97% of total PV in this model, so it is barely a 10-year DCF at all:
      * `wacc_term` capitalizes the perpetuity (Ke rebuilt at a stable-growth beta,
        same capital structure and Kd). Using the current high-growth WACC for a
        PERPETUITY shrank TV for high-beta names, so the solver had to invent extra
        growth to reach EV and the verdict came out too harsh — and too lenient for
        low-beta names. HIMS (beta 2.34) reads 22.8% implied CAGR the old way, 10.9%
        this way; LLY (beta 0.51) moves the other way, 13.7% -> 16.7%.
      * `roic_term` replaces a hardcoded ROIC_TERMINAL=15% in the reinvestment
        term — a leftover the terminal-ROIC work missed. Reinvestment is x/ROIC, so
        assuming 15% for a 23%-ROIC filer overstated what growth costs it.
    Both fall back to the old behaviour when not supplied.

    Margin is held at the terminal margin for the whole path. Tested: ramping it
    from today's margin instead moves the answer by <0.6pp (HIMS 22.8% -> 23.3%),
    because the terminal value dominates — so the simplification is harmless. It is
    NOT, as previously documented, a conservative bound in either direction."""
    wacc_term = wacc_val if wacc_term is None else wacc_term
    roic_t = roic_term if (roic_term and roic_term > 0) else ROIC_TERMINAL
    if not (price and shares and revenue) or wacc_val <= g or wacc_term <= g:
        return {"triggered": False}
    mcap = price * shares
    ev = mcap + (total_debt or 0) - (cash or 0)

    def pv_at(x, m):
        """PV of the FCFF path at revenue CAGR x with operating margin m."""
        if m <= 0:
            return None
        reinvest = min(0.9, max(0.0, x / roic_t)) if x > 0 else 0.0
        reinvest_t = min(0.8, g / roic_t)
        pv = 0.0
        rev_t = revenue
        for t in range(1, REVERSE_HORIZON + 1):
            rev_t *= (1 + x)
            fcff = rev_t * m * (1 - tax) * (1 - reinvest)
            pv += fcff / (1 + wacc_val) ** t
        fcff_next = rev_t * (1 + g) * m * (1 - tax) * (1 - reinvest_t)
        tv = fcff_next / (wacc_term - g)          # perpetuity at the STABLE rate...
        return pv + tv / (1 + wacc_val) ** REVERSE_HORIZON   # ...discounted back at the risky one

    def implied(m):
        """Bisection for x where pv_at(x) == EV. None if EV unreachable in band."""
        if m <= 0 or ev <= 0:
            return None
        lo, hi = -0.50, 1.00
        plo, phi = pv_at(lo, m), pv_at(hi, m)
        if plo is None or phi is None or plo > ev or phi < ev:
            return None
        for _ in range(60):
            mid = (lo + hi) / 2
            if (pv_at(mid, m) or 0) < ev:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2

    base = implied(margin)
    a1 = (revenue / rev_1y - 1) if rev_1y else None
    accel = (base / a1) if (base and a1 and a1 > 0) else None
    if a1 is not None and a1 <= 0:
        verdict = "Cannot benchmark - shrinking"
    elif accel is None:
        verdict = "Unknown"
    elif accel < 1.5:
        verdict = "Plausible"
    elif accel < 3:
        verdict = "Ambitious"
    elif accel < 5:
        verdict = "Aggressive"
    else:
        verdict = "Exceptional"
    return {"triggered": True, "enterprise_value": round(ev, 0),
            "implied_cagr_pct": round(base * 100, 1) if base else None,
            "actual_1y_pct": round(a1 * 100, 1) if a1 is not None else None,
            "acceleration": round(accel, 2) if accel else None, "verdict": verdict,
            "sensitivity": {k: (round(implied(margin + dm) * 100, 1) if implied(margin + dm) else None)
                            for k, dm in (("bear", -0.05), ("base", 0.0), ("bull", 0.05))}}


def earnings_quality(net_income, cfo, total_assets, sbc, revenue):
    if cfo is None or net_income is None:
        return None, [], None
    flags = []
    cc = (cfo / net_income) if net_income > 0 else None
    if cc is not None and cc < 0.8:
        flags.append("low cash conversion <0.8")
    if total_assets:
        acc = (net_income - cfo) / total_assets
        if acc > 0.05:
            flags.append("high accruals >5% of assets")
    if sbc is not None and revenue:
        if sbc / revenue > 0.10:
            flags.append("SBC >10% of revenue")
    verdict = "CLEAN" if not flags else "REVIEW" if len(flags) <= 2 else "LOW"
    return verdict, flags, cc


def _r_demand(g, peer_median, acq_intensity, fade_ratio, notes):
    if g is None:
        return None
    base = _band(g, [(0.35, 5.0), (0.25, 4.0), (0.15, 3.0), (0.08, 2.0), (0.0, 1.0), (-1e9, 0.0)])
    notes.append(f"D base(growth {g*100:.0f}%)={base}")
    score = base
    if acq_intensity is not None:
        pen = -1.0 if acq_intensity > 0.10 else (-0.5 if acq_intensity > 0.05 else 0.0)
        score += pen; notes.append(f"D organic(acq {acq_intensity*100:.1f}% of rev)={pen}")
    else:
        notes.append("D organic adj=skipped(no acquisitions data)")
    if peer_median is not None:
        adj = 0.5 if g > peer_median else (-0.5 if g < peer_median * 0.8 else 0.0)
        score += adj; notes.append(f"D peer(vs {peer_median*100:.0f}%)={adj}")
    else:
        notes.append("D peer adj=skipped(no peer data)")
    if fade_ratio is not None:
        adj = -0.5 if fade_ratio < 0.4 else (0.25 if fade_ratio > 0.7 else 0.0)
        score += adj; notes.append(f"D durability(fade {fade_ratio:.2f})={adj}")
    else:
        notes.append("D durability adj=skipped(no consensus path)")
    return _clamp(score)


def _r_execution(eq_verdict, margin_trend, inc_roic, wacc_val, notes):
    base = {"CLEAN": 4.0, "REVIEW": 3.0, "LOW": 1.5}.get(eq_verdict)
    if base is None:
        base = 3.0; notes.append("E_exec base(no earnings-quality data)=3.0")
    else:
        notes.append(f"E_exec base(EQ {eq_verdict})={base}")
    score = base
    if margin_trend:
        adj = {"up": 0.5, "flat": 0.0, "down": -0.5}.get(margin_trend, 0.0)
        score += adj; notes.append(f"E_exec margin-trend({margin_trend})={adj}")
    if inc_roic is not None and wacc_val is not None:
        inc = inc_roic - wacc_val
        adj = 1.0 if inc >= 0.10 else 0.5 if inc >= 0 else -0.5 if inc >= -0.05 else -1.0
        score += adj; notes.append(f"E_exec incremental-ROIC(inc-spread {inc*100:.0f}%)={adj}")
    else:
        notes.append("E_exec incremental-ROIC adj=skipped(no prior op income)")
    return _clamp(score)


def _r_economics(spread, spread_prior, notes):
    if spread is None:
        return None
    base = _band(spread, [(0.20, 5.0), (0.10, 4.0), (0.05, 3.0), (0.0, 2.0), (-0.05, 1.0), (-1e9, 0.0)])
    notes.append(f"E_econ base(spread {spread*100:.1f}%)={base}")
    score = base
    if spread_prior is not None:
        delta = spread - spread_prior
        adj = 0.5 if delta > 0.02 else (-1.0 if (delta < -0.02 and spread < 0.05)
                                        else -0.5 if delta < -0.02 else 0.0)
        score += adj; notes.append(f"E_econ 5y-spread-trend(d {delta*100:+.1f}pp)={adj}")
    else:
        notes.append("E_econ spread-trend adj=skipped(no prior ROIC)")
    return _clamp(score)


def _r_price(fv_anchor, price, own_pe_pctile, notes):
    if fv_anchor is None or not price:
        notes.append("P base=skipped(no point fair value)")
        return None
    mos = (fv_anchor - price) / price
    base = _band(mos, [(0.40, 5.0), (0.15, 4.0), (-0.15, 3.0), (-0.40, 2.0), (-1e9, 1.0)])
    notes.append(f"P base(margin-of-safety {mos*100:+.0f}%)={base}")
    score = base
    if own_pe_pctile is not None:
        adj = 0.5 if own_pe_pctile <= 0.33 else (-0.5 if own_pe_pctile >= 0.67 else 0.0)
        score += adj; notes.append(f"P own-5y-multiple(pctile {own_pe_pctile*100:.0f}%)={adj}")
    else:
        notes.append("P own-5y-multiple adj=skipped(no P/E history)")
    return _clamp(score)


WEIGHTS = {"D": 0.20, "E_exec": 0.20, "E_econ": 0.30, "P": 0.30}


def composite(scores):
    avail = {k: scores[k] for k in WEIGHTS if scores.get(k) is not None}
    if not avail:
        return None
    wsum = sum(WEIGHTS[k] for k in avail)
    return sum(avail[k] * WEIGHTS[k] for k in avail) / wsum


def stars(c):
    half = round(c * 2) / 2
    full = int(half)
    h = (half - full) == 0.5
    return "★" * full + ("½" if h else "") + "☆" * (5 - full - (1 if h else 0))


def recommendation(c):
    return "BUY" if c >= 4 else "HOLD / Accumulate" if c >= 3 else "HOLD" if c >= 2 else "SELL / AVOID"


def _signal(reco):
    if reco is None:
        return None
    return "BUY" if reco == "BUY" else "HOLD" if reco.startswith("HOLD") else "SELL"


class DeepV82Engine(DeepEngine):
    version = "8.2"

    def evaluate(self, facts, rf=0.045):
        f = facts
        notes = []
        beta = f.beta or 1.0
        tax = f.tax_rate
        if erp_months_old() > config.ERP_STALE_MONTHS:
            _erp_note = (f"ERP {config.ERP*100:.2f}% as-of {config.ERP_AS_OF} is {erp_months_old()}mo old "
                         f"- refresh (Assumptions form / config.ERP)")
            _add_flag(f, _erp_note)

        nopat = f.operating_income * (1 - tax) if f.operating_income is not None else None
        # P1-5 (S5): capitalize operating leases — lease liability is debt (Damodaran)
        lease = getattr(f, "operating_leases", None) or 0
        debt_eff = (f.total_debt or 0) + lease
        ic = debt_eff + (f.equity or 0) - (f.cash or 0)
        if lease:
            _ln = f"operating leases ${lease/1e9:.1f}B capitalized into debt+IC (S5)"
            _add_flag(f, _ln)
        roic = (nopat / ic) if (nopat is not None and ic and ic > 0) else None
        rd = rd_capitalize(f.rnd_annuals, f.operating_income, ic, tax)
        if rd:
            _, _, roic_adj = rd
            notes.append(f"R&D-capitalized ROIC {roic_adj*100:.1f}%")
        roic_used = rd[2] if rd else roic

        # T4: prefer the REPORTED market cap — one measurement instead of a product
        # of two, and for a depositary listing it is the only one in the right unit.
        # normalize._resolve_shares keeps shares consistent with it, so for ordinary
        # US listings the two agree to ~0.000% and this changes nothing.
        mcap = getattr(f, "market_cap", None) or (
            (f.price * f.shares_diluted) if (f.price and f.shares_diluted) else None)
        w, ke, kd_pre, wacc_note = wacc_true(rf, beta, mcap, debt_eff or None, f.cash, tax,
                                             f.interest_expense, f.operating_income)
        # S3: surface EVERY degraded-WACC path, not just the missing-interest one.
        # "no debt/weights -> WACC=Ke" used to pass silently, so TSM/NVO carried an
        # unweighted WACC with nothing on the card to say so.
        if ("default spread" in wacc_note and debt_eff) or "no debt/weights" in wacc_note:
            _n = "WACC: " + wacc_note
            _add_flag(f, _n)
        # P-K: same capital structure and Kd, Ke rebuilt at a stable-growth beta.
        beta_t = terminal_beta(beta)
        w_term, ke_term, _, _ = wacc_true(rf, beta, mcap, debt_eff or None, f.cash, tax,
                                          f.interest_expense, f.operating_income,
                                          beta_override=beta_t)
        if abs(beta_t - beta) > 0.4:
            _bn = (f"terminal beta {beta:.2f} -> {beta_t:.2f} (stable-phase risk fade) - "
                   f"FV leans on this assumption")
            _add_flag(f, _bn)
        spread = (roic_used - w) if roic_used is not None else None

        reinvest = 0.0
        if nopat and nopat > 0 and f.capex is not None and f.dep_amort is not None:
            reinvest = _clamp((f.capex - f.dep_amort) / nopat, 0.0, 0.8)
        fcff = nopat * (1 - reinvest) if nopat is not None else None

        # P-H: g <= ROIC — you cannot grow faster than your return on capital
        # without raising outside capital, which this model does not charge for.
        g_cap = sustainable_growth_cap(roic_used)
        # REVIEW-1: `growth_lt or 0.08` treated a measured ZERO as missing, so every
        # shrinking company silently got the 8% default. P-E now floors the CAGR at 0
        # precisely so decline reads as no-growth — that has to survive to here.
        _g_lt = f.growth_lt if f.growth_lt is not None else 0.08
        growth_raw = _clamp(_g_lt, 0.0, GROWTH_CAP)
        growth = min(growth_raw, g_cap)
        if growth_raw > growth + 1e-9:
            _gn = (f"growth capped {growth_raw*100:.0f}% -> {growth*100:.0f}% "
                   f"(g <= ROIC: faster needs outside capital)")
            _add_flag(f, _gn)
        ann = f.revenue_annuals or []
        rev_1y = ann[1] if len(ann) > 1 else None
        rev_3y = ann[3] if len(ann) > 3 else None
        rev_growth_yoy = (ann[0] / rev_1y - 1) if (ann and rev_1y) else None
        actual_3y = ((ann[0] / rev_3y) ** (1 / 3) - 1) if (ann and rev_3y) else None

        eps0 = None
        if f.net_income and f.shares_diluted:
            earn = min(f.net_income, nopat) if (nopat and f.net_income > nopat) else f.net_income
            eps0 = earn / f.shares_diluted
        elif f.eps_gaap:
            eps0 = f.eps_gaap

        ke_eff = max(ke, rf + 0.035)
        # P-B2/P-K: the perpetuity is discounted at the STABLE-phase Ke, and the
        # stable growth cap keys off that rate (it is a stable-phase quantity).
        ke_st_eff = max(ke_term, rf + 0.035)
        g_stable = min(rf, ke_st_eff - 0.03)
        roic_high = roic_used if (roic_used and roic_used > 0) else ROIC_TERMINAL
        # P-D: terminal ROIC fades from the firm's OWN spread instead of being
        # handed a flat 15% — see terminal_roic() for the Damodaran rules.
        # P-J: industry ceiling from Damodaran's ROC-by-sector table when known.
        _sec_cap = getattr(f, "terminal_roic_sector", None)
        # REVIEW-3: floor against the TERMINAL cost of capital, not today's. The
        # perpetuity is capitalized at w_term, so flooring at w let low-beta names
        # (whose beta fades UP, so w_term > w) end up with a terminal ROIC below
        # their terminal cost of capital — breaking the function's own rule 3.
        roic_term = terminal_roic(roic_used if (roic_used and roic_used > 0) else None,
                                  max(w, w_term), _sec_cap)
        if _sec_cap and abs(roic_term - _sec_cap) < 1e-9 and roic_term > w_term:
            # the INDUSTRY ceiling is what set the perpetuity here, not the firm's
            # own numbers — worth knowing, since it moves with an external table
            _sn = f"terminal ROIC {roic_term*100:.1f}% set by industry ceiling (Damodaran ROC)"
            _add_flag(f, _sn)
        # P-F: growth has to be bought with reinvestment — warn when the growth we
        # are paying for is far above what the firm's reinvestment can fund.
        g_fund = fundamental_growth(f.capex, f.dep_amort, nopat, roic_used)
        if g_fund is not None and growth > 0.02 and growth > 2 * max(g_fund, 0.0):
            note = (f"growth {growth*100:.0f}% vs fundamental growth {g_fund*100:.1f}% "
                    f"(reinvest x ROIC) - unfunded by physical reinvestment"
                    + ("; asset-light? R&D not counted" if g_fund < 0.01 else ""))
            _add_flag(f, note)
        fv_peg, peg_d = fundamental_peg_price(growth, g_stable, ke_eff, roic_high, roic_term,
                                              f.forward_eps, ke_stable=ke_st_eff)
        if peg_d and peg_d.get("pe_clamped"):
            note = (f"fundamental PE clamped to [{PE_FLOOR:.0f}, {PE_CEIL:.0f}] "
                    f"(raw {peg_d.get('fair_pe_raw')}) - FV is a boundary, not an estimate")
            _add_flag(f, note)

        # exit multiple from STABLE-period fundamentals (payout/(Ke-g)), not the
        # old PEG=1 heuristic (growth x 100) — Philosophy-2026 alignment (S5/S19)
        # P-B2: the exit multiple is a STABLE-phase multiple, so it is built at the
        # stable-phase Ke; the 5 high-growth years are still discounted at ke_eff.
        exit_pe = stable_exit_pe(g_stable, ke_st_eff, roic_term)
        fv_fvp = future_value_projection(eps0, growth, ke_eff, exit_pe)

        tmargin, tm_label, tm_anchored = terminal_margin(f.ticker, f.operating_income, f.revenue)
        # P-A: surface when EBIT was approximated because the filer tags no
        # operating-income subtotal (LLY/PFE) — ROIC/margins carry that caveat.
        if str((getattr(f, "provenance", None) or {}).get("operating_income", "")).startswith("sec-derived"):
            note = "EBIT derived (pretax + interest) - filer tags no operating income"
            _add_flag(f, note)

        # P-L: perpetuity capitalized at the stable-phase WACC, reinvestment costed
        # at the firm's own terminal ROIC (was a hardcoded 15%).
        rdcf = reverse_dcf(f.price, f.shares_diluted, f.revenue, rev_1y, f.total_debt, f.cash,
                           w, rf, tax, tmargin, wacc_term=w_term, roic_term=roic_term)
        if rdcf.get("triggered") and not tm_anchored:
            note = tm_label + " - RevDCF verdict approximate"
            _add_flag(f, note)

        eps_pos = eps0 is not None and eps0 > 0
        fcf_pos = fcff is not None and fcff > 0
        # P-G: an FVP below 10% of price is a broken input, not a valuation. It used
        # to nuke the whole anchor — throwing away a perfectly good Fundamental PEG
        # with it (ABBV: FVP $23 killed a $210 PEG). Discard the METHOD, not the row.
        if fv_fvp and f.price and fv_fvp < 0.1 * f.price:
            note = "FVP discarded (below 10% of price - unreliable inputs)"
            _add_flag(f, note)
            fv_fvp = None
        if (not eps_pos) or (not fcf_pos):
            anchor_method, anchor_value = "Terminal-Anchored Reverse DCF", None
        elif spread is not None and spread > 0.10 and (rev_growth_yoy or 0) > 0.15:
            anchor_method, anchor_value = "Future Value Projection", fv_fvp
        else:
            anchor_method, anchor_value = "Fundamental PEG", fv_peg
        if anchor_value is None and anchor_method != "Terminal-Anchored Reverse DCF":
            if anchor_method == "Fundamental PEG" and fv_fvp:
                anchor_method, anchor_value = "Future Value Projection", fv_fvp
            elif anchor_method == "Future Value Projection" and fv_peg:
                anchor_method, anchor_value = "Fundamental PEG", fv_peg
            if anchor_value is None:
                anchor_method = "Terminal-Anchored Reverse DCF"

        methods = {"Fundamental PEG": fv_peg, "Future Value Projection": fv_fvp}
        avail = {k: v for k, v in methods.items() if v and v > 0}
        range_low = min(avail.values()) if avail else None
        range_high = max(avail.values()) if avail else None

        eq_verdict, eq_flags, cc = earnings_quality(f.net_income, f.cfo, f.total_assets, f.sbc, f.revenue)
        for fl in eq_flags:
            _add_flag(f, "EQ: " + fl)
        if f.deferred_revenue and f.deferred_revenue_prior and rev_1y and f.revenue:
            dr_g = f.deferred_revenue / f.deferred_revenue_prior - 1
            rev_g = f.revenue / rev_1y - 1
            sig = "positive" if dr_g > rev_g else "lagging"
            note = f"billings {sig} (deferred rev {dr_g*100:+.0f}% vs rev {rev_g*100:+.0f}%)"
            _add_flag(f, note)

        oia = f.operating_income_annuals or []
        inc_roic = None
        if len(oia) > 1 and oia[1] and f.capex is not None and f.dep_amort is not None:
            d_nopat = (oia[0] - oia[1]) * (1 - tax)
            reinvest_1y = (f.capex - f.dep_amort)
            if reinvest_1y and reinvest_1y > 0:
                inc_roic = d_nopat / reinvest_1y
        margin_trend = None
        if len(oia) > 1 and oia[1] and rev_1y and f.operating_income and f.revenue:
            m_now, m_prior = f.operating_income / f.revenue, oia[1] / rev_1y
            if m_prior:
                margin_trend = "up" if m_now > m_prior * 1.02 else "down" if m_now < m_prior * 0.98 else "flat"

        spread_prior = None
        if f.equity_prior is not None and f.cash_prior is not None and len(oia) > 1 and oia[1]:
            ic_prior = (f.total_debt_prior or 0) + f.equity_prior - f.cash_prior
            if ic_prior and ic_prior > 0:
                roic_prior = oia[1] * (1 - tax) / ic_prior
                spread_prior = roic_prior - w

        acq_int = (abs(f.acquisitions_net) / f.revenue) if (f.acquisitions_net is not None and f.revenue) else None
        fade = None
        if f.fwd_growth_near and f.fwd_growth_far is not None and f.fwd_growth_near != 0:
            fade = f.fwd_growth_far / f.fwd_growth_near
        D = _r_demand(rev_growth_yoy, getattr(f, "peer_median_growth", None), acq_int, fade, notes)
        E_exec = _r_execution(eq_verdict, margin_trend, inc_roic, w, notes)
        E_econ = _r_economics(spread, spread_prior, notes)
        P = _r_price(anchor_value, f.price, getattr(f, "own_pe_pctile", None), notes)
        comp = composite({"D": D, "E_exec": E_exec, "E_econ": E_econ, "P": P})
        reco = recommendation(comp) if comp is not None else None
        st = stars(comp) if comp is not None else ""
        sig = _signal(reco)

        eva = (spread * ic) if (spread is not None and ic and ic > 0) else None

        v = Valuation(
            version=self.version, D=D, E_exec=E_exec, E_econ=E_econ, P=P,
            composite=round(comp, 2) if comp is not None else None, stars=st,
            recommendation=reco, signal=sig,
            anchor_method=anchor_method,
            anchor_value=round(anchor_value, 2) if anchor_value else None,
            range_low=round(range_low, 2) if range_low else None,
            range_high=round(range_high, 2) if range_high else None,
            fv_peg=round(fv_peg, 2) if fv_peg else None,
            fv_fvp=round(fv_fvp, 2) if fv_fvp else None,
            reverse_dcf=rdcf,
            cost_of_equity=round(ke, 4),
            eva=round(eva, 0) if eva is not None else None,
            eq_verdict=eq_verdict,
            subscores={"breakdown": notes},
            key_metrics={"wacc_pct": round(w * 100, 2),
                         "ke_pct": round(ke * 100, 2),
                         "kd_pct": round(kd_pre * 100, 2) if kd_pre is not None else None,
                         "roic_pct": round(roic * 100, 2) if roic is not None else None,
                         "roic_adj_pct": round(roic_used * 100, 2) if (rd and roic_used is not None) else None,
                         "spread_pct": round(spread * 100, 2) if spread is not None else None,
                         "incremental_roic_pct": round(inc_roic * 100, 1) if inc_roic is not None else None,
                         "terminal_roic_pct": round(roic_term * 100, 1),
                         "terminal_beta": round(beta_t, 2),
                         "terminal_ke_pct": round(ke_st_eff * 100, 2),
                         "terminal_wacc_pct": round(w_term * 100, 2),
                         "growth_pct": round(growth * 100, 1), "beta": round(beta, 2),
                         "justified_pe": (peg_d or {}).get("fair_pe"),
                         "erp_pct": round(config.ERP * 100, 2), "erp_as_of": config.ERP_AS_OF,
                         "operating_leases": round(lease, 0) if lease else None,
                         "terminal_margin_pct": round(tmargin * 100, 1),
                         "terminal_margin_anchored": tm_anchored},
            flags=list(f.flags))
        try:
            v.verdict = _verdict(f, v)
        except Exception as e:                       # guard: never let a verdict typo/bad field crash a ticker
            v.verdict = (f"{v.recommendation or 'N/A'} {v.stars or ''} - verdict unavailable ({type(e).__name__})").strip()
        return v


def _verdict(f, v):
    km = v.key_metrics
    if v.anchor_method == "Terminal-Anchored Reverse DCF":
        rd = v.reverse_dcf or {}
        if rd.get("triggered"):
            # P-C: this branch also catches PROFITABLE companies whose FV inputs are
            # missing (e.g. no operating-income tag). Calling those "Pre-profit" is a lie.
            lbl = "Pre-profit" if not (f.net_income and f.net_income > 0) else "No point FV (insufficient data)"
            return (f"{v.recommendation} {v.stars} - {lbl}: market prices ~{rd.get('implied_cagr_pct')}% 10y CAGR "
                    f"vs actual {rd.get('actual_1y_pct')}% ({rd.get('verdict')}). conf {f.confidence}")
        return (f"{v.recommendation or 'N/A'} {v.stars} - insufficient data for a point fair value. conf {f.confidence}").strip()
    fv = v.anchor_value
    if fv is None:
        return (f"{v.recommendation or 'N/A'} {v.stars} - no fair value computed. conf {f.confidence}").strip()
    up = (f"{((fv - f.price) / f.price * 100):+.0f}% upside" if f.price else "")
    band = f"range ${v.range_low}-${v.range_high}" if v.range_low else ""
    eq = f", EQ {v.eq_verdict}" if v.eq_verdict else ""
    return (f"{v.recommendation} {v.stars} - {v.anchor_method} ${fv} ({up}); {band}. "
            f"ROIC {km.get('roic_adj_pct') or km.get('roic_pct')}% vs WACC {km.get('wacc_pct')}% "
            f"(Ke {km.get('ke_pct')}%), growth {km.get('growth_pct')}%{eq}. conf {f.confidence}").strip()