"""
DEEP Framework v8.2 engine — implements the DeepEngine contract.
Free-data limits handled per skill invariant 17 (skip + flag, never fabricate).
"""
import datetime

import config
from .contract import DeepEngine, Valuation

# --- constants (v8) ---------------------------------------------------------
ERP = config.ERP             # market ERP, centralised in config (see config.ERP / ERP_AS_OF)
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


def erp_months_old(as_of=ERP_AS_OF, today=None):
    """Months since the ERP assumption was set (for the staleness flag)."""
    try:
        y, m = (int(x) for x in str(as_of).split("-")[:2])
        d = today or datetime.date.today()
        return (d.year - y) * 12 + (d.month - m)
    except Exception:
        return 0


def _clamp(x, lo=0.0, hi=5.0):
    return max(lo, min(hi, x))


def _band(x, bands):
    for t, s in bands:
        if x >= t:
            return s
    return bands[-1][1]


def cost_of_equity(rf, beta):
    return rf + beta * ERP


def _spread_from_coverage(cov):
    for lb, sp in SYNTH_SPREAD:
        if cov >= lb:
            return sp
    return SYNTH_SPREAD[-1][1]


def wacc_true(rf, beta, equity_mktcap, total_debt, cash, tax, interest_expense, operating_income):
    """True WACC. Returns (wacc, ke, kd_pretax, note)."""
    ke = cost_of_equity(rf, beta)
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


def two_stage_pe(g_h, n, g_st, ke, roic_h, roic_st):
    if ke <= g_st:
        return None
    payout_h = max(0.0, 1 - g_h / roic_h) if roic_h else 0.0
    payout_st = max(0.0, 1 - g_st / roic_st) if roic_st else 0.0
    comp = ((1 + g_h) ** n) / ((1 + ke) ** n)
    if abs(ke - g_h) < 1e-9:
        term1 = payout_h * (1 + g_h) * n / (1 + ke)
    else:
        term1 = payout_h * (1 + g_h) * (1 - comp) / (ke - g_h)
    term2 = payout_st * ((1 + g_h) ** n) * (1 + g_st) / ((ke - g_st) * (1 + ke) ** n)
    return term1 + term2


PE_FLOOR, PE_CEIL = 8.0, 30.0


def fundamental_peg_price(g_high, g_stable, ke, roic_high, roic_stable, forward_eps, years=5):
    if forward_eps is None or forward_eps <= 0 or g_high is None or g_high <= 0:
        return None, None
    pe = two_stage_pe(g_high, years, g_stable, ke, roic_high, roic_stable)
    if pe is None or pe <= 0:
        return None, None
    pe_clamped = _clamp(pe, PE_FLOOR, PE_CEIL)
    detail = {"fair_pe": round(pe_clamped, 2), "fair_pe_raw": round(pe, 2),
              "fundamental_peg": round(pe_clamped / (g_high * 100), 3),
              "pe_clamped": pe_clamped != pe}
    return pe_clamped * forward_eps, detail


def future_value_projection(eps0, growth, ke, exit_pe):
    if eps0 is None or eps0 <= 0 or exit_pe is None:
        return None
    pe = _clamp(exit_pe, 12, 25)
    return (eps0 * (1 + growth) ** 5 * pe) / (1 + ke) ** 5


def reverse_dcf(price, shares, revenue, rev_1y, total_debt, cash, wacc_val, g, tax, margin):
    if not (price and shares and revenue) or wacc_val <= g:
        return {"triggered": False}
    mcap = price * shares
    ev = mcap + (total_debt or 0) - (cash or 0)
    tv = ev * (1 + wacc_val) ** REVERSE_HORIZON
    fcff_t = tv * (wacc_val - g)
    reinvest = min(0.8, g / ROIC_TERMINAL)

    def implied(m):
        denom = m * (1 - tax) * (1 - reinvest)
        if denom <= 0:
            return None
        rev_t = fcff_t / denom
        return (rev_t / revenue) ** (1 / REVERSE_HORIZON) - 1 if rev_t > 0 else None

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
            _erp_note = f"ERP {ERP*100:.2f}% as-of {ERP_AS_OF} is {erp_months_old()}mo old - refresh (config.ERP)"
            if _erp_note not in f.flags:
                f.flags.append(_erp_note)

        nopat = f.operating_income * (1 - tax) if f.operating_income is not None else None
        ic = (f.total_debt or 0) + (f.equity or 0) - (f.cash or 0)
        roic = (nopat / ic) if (nopat is not None and ic and ic > 0) else None
        rd = rd_capitalize(f.rnd_annuals, f.operating_income, ic, tax)
        if rd:
            _, _, roic_adj = rd
            notes.append(f"R&D-capitalized ROIC {roic_adj*100:.1f}%")
        roic_used = rd[2] if rd else roic

        mcap = (f.price * f.shares_diluted) if (f.price and f.shares_diluted) else None
        w, ke, kd_pre, wacc_note = wacc_true(rf, beta, mcap, f.total_debt, f.cash, tax,
                                             f.interest_expense, f.operating_income)
        if "default spread" in wacc_note and f.total_debt:
            _n = "WACC: " + wacc_note
            if _n not in f.flags:
                f.flags.append(_n)
        spread = (roic_used - w) if roic_used is not None else None

        reinvest = 0.0
        if nopat and nopat > 0 and f.capex is not None and f.dep_amort is not None:
            reinvest = _clamp((f.capex - f.dep_amort) / nopat, 0.0, 0.8)
        fcff = nopat * (1 - reinvest) if nopat is not None else None

        growth = _clamp(f.growth_lt or 0.08, 0.0, GROWTH_CAP)
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
        g_stable = min(rf, ke_eff - 0.03)
        roic_high = roic_used if (roic_used and roic_used > 0) else ROIC_TERMINAL
        fv_peg, peg_d = fundamental_peg_price(growth, g_stable, ke_eff, roic_high, ROIC_TERMINAL, f.forward_eps)
        if peg_d and peg_d.get("pe_clamped"):
            note = "fundamental PE clamped to sane band (low-beta terminal sensitivity)"
            if note not in f.flags:
                f.flags.append(note)

        exit_pe = _clamp(growth * 100, 12, 25)
        fv_fvp = future_value_projection(eps0, growth, ke_eff, exit_pe)

        tmargin, tm_label, tm_anchored = terminal_margin(f.ticker, f.operating_income, f.revenue)
        rdcf = reverse_dcf(f.price, f.shares_diluted, f.revenue, rev_1y, f.total_debt, f.cash,
                           w, rf, tax, tmargin)
        if rdcf.get("triggered") and not tm_anchored:
            note = tm_label + " - RevDCF verdict approximate"
            if note not in f.flags:
                f.flags.append(note)

        eps_pos = eps0 is not None and eps0 > 0
        fcf_pos = fcff is not None and fcff > 0
        if (not eps_pos) or (not fcf_pos):
            anchor_method, anchor_value = "Terminal-Anchored Reverse DCF", None
        elif fv_fvp and f.price and fv_fvp < 0.1 * f.price:
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
            if fl not in f.flags:
                f.flags.append("EQ: " + fl)
        if f.deferred_revenue and f.deferred_revenue_prior and rev_1y and f.revenue:
            dr_g = f.deferred_revenue / f.deferred_revenue_prior - 1
            rev_g = f.revenue / rev_1y - 1
            sig = "positive" if dr_g > rev_g else "lagging"
            note = f"billings {sig} (deferred rev {dr_g*100:+.0f}% vs rev {rev_g*100:+.0f}%)"
            if note not in f.flags:
                f.flags.append(note)

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
                         "growth_pct": round(growth * 100, 1), "beta": round(beta, 2),
                         "justified_pe": (peg_d or {}).get("fair_pe"),
                         "erp_pct": round(ERP * 100, 2), "erp_as_of": ERP_AS_OF,
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
            return (f"{v.recommendation} {v.stars} - Pre-profit: market prices ~{rd.get('implied_cagr_pct')}% 10y CAGR "
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