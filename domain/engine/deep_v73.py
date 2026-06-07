"""
DEEP Framework v7.3 engine — implements the DeepEngine contract.

The math (WACC, ROIC, Justified PEG, Future Value Projection, Terminal-Anchored
Reverse DCF, weighted composite) is the version verified against the canonical
ifa-stock-analysis scripts. It consumes a validated FinancialFacts and returns a
Valuation. To make a v7.4: copy this file, change the math, register it — nothing
else in the app changes.
"""
from .contract import DeepEngine, Valuation

ERP = 0.0475
ROIC_TERMINAL = 0.15
REVERSE_HORIZON = 10
GROWTH_CAP = 0.30
TERMINAL_MARGIN = {
    "NVDA": 0.35, "MSFT": 0.40, "AVGO": 0.35, "TSM": 0.40, "GOOGL": 0.35,
    "ORCL": 0.35, "MELI": 0.20, "ABBV": 0.30, "TMDX": 0.20, "LLY": 0.30,
    "TSLA": 0.15, "NVO": 0.35,
}


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


# ---- canonical math (ports) ------------------------------------------------
def wacc(rf, beta):
    return rf + beta * ERP


def justified_peg(roic_spread, capital_light, growth_slowing, risk_adj, fwd_cagr_pct, forward_eps):
    if forward_eps is None or forward_eps <= 0:
        return None, None
    peg = 1.0
    peg += min(0.4, max(0.0, (roic_spread / 0.10) * 0.25)) if roic_spread and roic_spread > 0 else 0.0
    peg += 0.2 if capital_light else 0.0
    peg += -0.1 if growth_slowing else 0.0
    peg += risk_adj
    fair_pe = peg * fwd_cagr_pct
    fp = fair_pe * forward_eps
    return (fp if fp > 0 else None), {"peg": round(peg, 3), "fair_pe": round(fair_pe, 2)}


def future_value_projection(eps0, growth, wacc_val, exit_pe):
    if eps0 is None or eps0 <= 0 or exit_pe is None:
        return None
    pe = _clamp(exit_pe, 12, 25)
    return (eps0 * (1 + growth) ** 5 * pe) / (1 + wacc_val) ** 5


def reverse_dcf(price, shares, revenue, rev_1y, rev_3y, wacc_val, g, tax, margin):
    if not (price and shares and revenue) or wacc_val <= g:
        return {"triggered": False}
    mcap = price * shares
    tv = mcap * (1 + wacc_val) ** REVERSE_HORIZON
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
        verdict = "Cannot benchmark — shrinking"
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
    return {"triggered": True, "implied_cagr_pct": round(base * 100, 1) if base else None,
            "actual_1y_pct": round(a1 * 100, 1) if a1 is not None else None,
            "acceleration": round(accel, 2) if accel else None, "verdict": verdict,
            "sensitivity": {k: (round(implied(margin + dm) * 100, 1) if implied(margin + dm) else None)
                            for k, dm in (("bear", -0.05), ("base", 0.0), ("bull", 0.05))}}


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


# ---- DEEP sub-scores (proxies; no canonical script) ------------------------
def _demand(g):
    if g is None:
        return None
    return 4.5 if g > 0.40 else 3.5 if g > 0.20 else 3.0 if g > 0.10 else 2.5 if g > 0 else 1.5


def _execution(net_income, op_margin):
    s = 3.0 + (0.5 if (net_income or 0) > 0 else 0) + (0.5 if (op_margin or 0) > 0.20 else 0)
    return _clamp(s, 0, 5)


def _economics(roic, wacc_val):
    if roic is None:
        return None
    sp = roic - wacc_val
    return 5.0 if sp > 0.50 else 4.0 if sp > 0.20 else 3.0 if sp > 0.05 else 2.0 if sp > 0 else 1.0


def _tier(upside_pct):
    if upside_pct is None:
        return None
    return 5.0 if upside_pct > 30 else 4.0 if upside_pct >= 15 else 3.0 if upside_pct >= 0 \
        else 2.0 if upside_pct >= -10 else 1.0 if upside_pct >= -20 else 0.0


def _price_score(price, fv_peg, fv_fvp, fcff, mcap, wacc_val, rdcf, eps_pos):
    parts = []
    if eps_pos:
        for w, fv in ((0.20, fv_peg), (0.30, fv_fvp)):
            up = ((fv - price) / price * 100) if (fv and price) else None
            s = _tier(up)
            if s is not None:
                parts.append((w, s))
    if fcff and fcff > 0 and mcap:
        sp = fcff / mcap - wacc_val
        parts.append((0.20, 5.0 if sp > 0.02 else 4.0 if sp > 0 else 3.0 if sp > -0.02 else 2.0 if sp > -0.04 else 1.0))
    if rdcf and rdcf.get("triggered"):
        v = rdcf.get("verdict") or ""
        parts.append((0.30, 5.0 if v.startswith("Plausible") else 3.5 if v.startswith("Ambitious")
                      else 2.0 if v.startswith("Aggressive") else 0.5 if v.startswith("Exceptional") else 2.5))
    if not parts:
        return None
    return round(sum(w * s for w, s in parts) / sum(w for w, _ in parts), 2)


def _signal(reco):
    if reco is None:
        return None
    return "BUY" if reco == "BUY" else "HOLD" if reco.startswith("HOLD") else "SELL"


class DeepV73Engine(DeepEngine):
    version = "7.3"

    def evaluate(self, facts, rf=0.045):
        f = facts
        beta = f.beta or 1.0
        tax = f.tax_rate
        nopat = f.operating_income * (1 - tax) if f.operating_income is not None else None
        ic = (f.total_debt or 0) + (f.equity or 0) - (f.cash or 0)
        roic = (nopat / ic) if (nopat is not None and ic and ic > 0) else None
        reinvest = 0.0
        if nopat and nopat > 0 and f.capex is not None and f.dep_amort is not None:
            reinvest = _clamp((f.capex - f.dep_amort) / nopat, 0.0, 0.8)
        fcff = nopat * (1 - reinvest) if nopat is not None else None
        w = wacc(rf, beta)
        spread = (roic - w) if roic is not None else None

        growth = _clamp(f.growth_lt or 0.08, 0.0, GROWTH_CAP)
        ann = f.revenue_annuals or []
        rev_1y = ann[1] if len(ann) > 1 else None
        rev_3y = ann[3] if len(ann) > 3 else None
        rev_growth_yoy = (ann[0] / rev_1y - 1) if (ann and rev_1y) else None
        actual_3y = ((ann[0] / rev_3y) ** (1 / 3) - 1) if (ann and rev_3y) else None
        growth_slowing = (rev_growth_yoy is not None and actual_3y is not None and rev_growth_yoy < actual_3y)
        capital_light = (((f.capex or 0) / f.revenue) if f.revenue else 1.0) < 0.08
        risk_adj = -0.05 if beta < 1 else -0.10 if beta <= 1.5 else -0.15

        # eps0 (operating-earnings basis): cap net income at NOPAT to strip non-op gains
        eps0 = None
        if f.net_income and f.shares_diluted:
            earn = min(f.net_income, nopat) if (nopat and f.net_income > nopat) else f.net_income
            eps0 = earn / f.shares_diluted
        elif f.eps_gaap:
            eps0 = f.eps_gaap

        fv_peg, peg_d = justified_peg(spread or 0, capital_light, growth_slowing, risk_adj, growth * 100, f.forward_eps)
        exit_pe = _clamp(growth * 100, 12, 25)
        fv_fvp = future_value_projection(eps0, growth, w, exit_pe)
        mcap = (f.price * f.shares_diluted) if (f.price and f.shares_diluted) else None
        rdcf = reverse_dcf(f.price, f.shares_diluted, f.revenue, rev_1y, rev_3y, w, rf, tax,
                           TERMINAL_MARGIN.get(f.ticker, 0.25))

        eps_pos = eps0 is not None and eps0 > 0
        fcf_pos = fcff is not None and fcff > 0
        if (not eps_pos) or (not fcf_pos):
            anchor_method, anchor_value = "Terminal-Anchored Reverse DCF", None
        elif fv_fvp and f.price and fv_fvp < 0.1 * f.price:
            anchor_method, anchor_value = "Terminal-Anchored Reverse DCF", None
        elif spread is not None and spread > 0.10 and (rev_growth_yoy or 0) > 0.15:
            anchor_method, anchor_value = "Future Value Projection", fv_fvp
        else:
            anchor_method, anchor_value = "Justified PEG", fv_peg
        # fallback: if the chosen point method produced no value, try the other;
        # if still none, defer to the Terminal-Anchored Reverse DCF (no blank anchor)
        if anchor_value is None and anchor_method != "Terminal-Anchored Reverse DCF":
            if anchor_method == "Justified PEG" and fv_fvp:
                anchor_method, anchor_value = "Future Value Projection", fv_fvp
            elif anchor_method == "Future Value Projection" and fv_peg:
                anchor_method, anchor_value = "Justified PEG", fv_peg
            if anchor_value is None:
                anchor_method = "Terminal-Anchored Reverse DCF"

        methods = {"Justified PEG": fv_peg, "Future Value Projection": fv_fvp}
        avail = {k: v for k, v in methods.items() if v and v > 0}
        range_low = min(avail.values()) if avail else None
        range_high = max(avail.values()) if avail else None

        op_margin = (f.operating_income / f.revenue) if (f.operating_income and f.revenue) else None
        D = _demand(rev_growth_yoy)
        E_exec = _execution(f.net_income, op_margin)
        E_econ = _economics(roic, w)
        P = _price_score(f.price, fv_peg, fv_fvp, fcff, mcap, w, rdcf, eps_pos)
        comp = composite({"D": D, "E_exec": E_exec, "E_econ": E_econ, "P": P})
        reco = recommendation(comp) if comp is not None else None
        st = stars(comp) if comp is not None else ""
        sig = _signal(reco)

        v = Valuation(version=self.version, D=D, E_exec=E_exec, E_econ=E_econ, P=P,
                      composite=round(comp, 2) if comp is not None else None, stars=st,
                      recommendation=reco, signal=sig,
                      anchor_method=anchor_method,
                      anchor_value=round(anchor_value, 2) if anchor_value else None,
                      range_low=round(range_low, 2) if range_low else None,
                      range_high=round(range_high, 2) if range_high else None,
                      fv_peg=round(fv_peg, 2) if fv_peg else None,
                      fv_fvp=round(fv_fvp, 2) if fv_fvp else None,
                      reverse_dcf=rdcf,
                      key_metrics={"wacc_pct": round(w * 100, 2),
                                   "roic_pct": round(roic * 100, 2) if roic is not None else None,
                                   "spread_pct": round(spread * 100, 2) if spread is not None else None,
                                   "growth_pct": round(growth * 100, 1), "beta": round(beta, 2),
                                   "justified_pe": (peg_d or {}).get("fair_pe")},
                      flags=list(f.flags))
        v.verdict = _verdict(f, v)
        return v


def _verdict(f, v):
    fv = v.anchor_value
    up = (f"{((fv - f.price) / f.price * 100):+.0f}% upside" if (fv and f.price) else "")
    km = v.key_metrics
    if v.anchor_method == "Terminal-Anchored Reverse DCF" and v.reverse_dcf.get("triggered"):
        rd = v.reverse_dcf
        return (f"{v.recommendation} {v.stars} — Pre-profit: market prices ~{rd.get('implied_cagr_pct')}% 10y CAGR "
                f"vs actual {rd.get('actual_1y_pct')}% ({rd.get('verdict')}). conf {f.confidence}")
    band = f"range ${v.range_low}–${v.range_high}" if v.range_low else ""
    return (f"{v.recommendation} {v.stars} — {v.anchor_method} ${fv} ({up}); {band}. "
            f"ROIC {km.get('roic_pct')}% vs WACC {km.get('wacc_pct')}%, growth {km.get('growth_pct')}%. conf {f.confidence}").strip()
