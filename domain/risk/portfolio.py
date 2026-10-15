"""
Portfolio risk for the Risk Desk: weights, concentration, exposures, volatility,
diversification, risk attribution, the Diversification Score, and sizing/rebalancing.
"""
import math


def _clamp01(x):
    return max(0.0, min(1.0, x))


def _beta(betas, t, default_beta):
    return betas.get(t) if betas.get(t) is not None else default_beta


def beta_exposure(weights, betas, default_beta=1.0):
    """Sum of w_i * beta_i, unrounded (missing beta -> default)."""
    return sum(w * _beta(betas, t, default_beta) for t, w in weights.items())


# --------------------------------------------------------------------------- #
#  Weights, concentration, grouping, one-factor (beta) risk                    #
# --------------------------------------------------------------------------- #
def capital_weights(positions):
    """{ticker: market_value} -> {ticker: weight} summing to 1. Non-positive values dropped."""
    mv = {t: float(v) for t, v in (positions or {}).items() if v and v > 0}
    total = sum(mv.values())
    if total <= 0:
        return {}
    return {t: v / total for t, v in mv.items()}


def concentration(weights):
    """HHI, effective number of holdings, Top-5 / Top-10 shares."""
    ws = sorted([w for w in weights.values() if w > 0], reverse=True)
    if not ws:
        return {"hhi": None, "eff_n": None, "n": 0,
                "top5_pct": None, "top10_pct": None}
    hhi = sum(w * w for w in ws)
    return {
        "hhi": round(hhi, 4),
        "eff_n": round(1.0 / hhi, 2) if hhi > 0 else None,
        "n": len(ws),
        "top5_pct": round(sum(ws[:5]) * 100, 1),
        "top10_pct": round(sum(ws[:10]) * 100, 1),
    }


def group_exposure(weights, attribute):
    """Sum weights by a categorical attribute ({ticker: label}) -> [{label, value(%)}] descending."""
    agg = {}
    for t, w in weights.items():
        key = attribute.get(t) or "Unknown"
        agg[key] = agg.get(key, 0.0) + w
    return sorted(
        [{"label": k, "value": round(v * 100, 1)} for k, v in agg.items()],
        key=lambda x: -x["value"])


def portfolio_beta(weights, betas, default_beta=1.0):
    return round(beta_exposure(weights, betas, default_beta), 3)


def beta_risk_contribution(weights, betas, default_beta=1.0):
    """One-factor risk attribution for when no covariance matrix exists: each name's
    risk share is its share of total beta-dollar exposure, RC_i ∝ w_i*beta_i."""
    contrib = {t: w * _beta(betas, t, default_beta) for t, w in weights.items()}
    total = sum(contrib.values())
    rows = []
    for t, w in sorted(weights.items(), key=lambda kv: -kv[1]):
        rc = (contrib[t] / total) if total else None
        rows.append({
            "ticker": t,
            "capital_pct": round(w * 100, 1),
            "risk_pct": round(rc * 100, 1) if rc is not None else None,
            "diff_pp": round((rc - w) * 100, 1) if rc is not None else None,
            "beta": betas.get(t),
        })
    return rows


# --------------------------------------------------------------------------- #
#  Volatility, diversification, full risk contribution                         #
# --------------------------------------------------------------------------- #
def portfolio_vol(weights_vec, cov):
    """sigma_p = sqrt(w' Sigma w); weights_vec aligns with cov's ticker order."""
    n = len(weights_vec)
    var = 0.0
    for i in range(n):
        for j in range(n):
            var += weights_vec[i] * weights_vec[j] * cov[i][j]
    return math.sqrt(var) if var > 0 else 0.0


def diversification_ratio(weights_vec, vols, port_vol):
    """DR = (Σ w_i*sigma_i) / sigma_p. DR≈1 -> almost no diversification benefit."""
    wavg = sum(weights_vec[i] * vols[i] for i in range(len(weights_vec)))
    return (wavg / port_vol) if port_vol > 0 else None


def effective_bets(dr):
    """ENB = DR², ≈ 1/(w'Ρw) (exact when vols are equal). Derived from DR so the two
    never disagree: ENB <= 1/HHI when correlations are >= 0, ENB falls as correlations
    rise (crisis ENB < normal ENB), ENB -> 1 as every pair approaches rho = 1."""
    return round(dr * dr, 2) if (dr and dr > 0) else None


def risk_contributions(tickers, weights_vec, cov):
    """MCR_i = (Σ_j w_j*Sigma_ij)/sigma_p ; RC_i = w_i*MCR_i ; Σ RC_i = sigma_p.
    Returns (rows, summary). Signed %RC can be negative (a true diversifier)."""
    n = len(tickers)
    sp = portfolio_vol(weights_vec, cov)
    if sp <= 0:
        rows = [{"ticker": t, "capital_pct": round(weights_vec[i] * 100, 1),
                 "signed_risk_pct": None, "abs_risk_share_pct": None,
                 "diff_pp": None, "vol_pct": None} for i, t in enumerate(tickers)]
        return rows, {"port_vol_pct": None, "enb_abs": None}
    rc_list = [weights_vec[i] * (sum(weights_vec[j] * cov[i][j] for j in range(n)) / sp)
               for i in range(n)]
    abs_total = sum(abs(rc) for rc in rc_list)
    rows = []
    for i, t in enumerate(tickers):
        signed = rc_list[i] / sp                       # fraction of sigma_p
        rows.append({
            "ticker": t,
            "capital_pct": round(weights_vec[i] * 100, 1),
            "signed_risk_pct": round(signed * 100, 1),
            "abs_risk_share_pct": round(abs(rc_list[i]) / abs_total * 100, 1) if abs_total else None,
            "diff_pp": round((signed - weights_vec[i]) * 100, 1),
            "vol_pct": round(math.sqrt(cov[i][i]) * 100, 1) if cov[i][i] > 0 else None,
        })
    # Inverse-HHI of |RC| measures how EVENLY risk is spread. It is NOT the Effective
    # Number of Bets: it ignores correlation, so it RISES in a crisis and can exceed
    # Effective N. It only feeds diversification_score's risk-balance leg; the UI's ENB
    # comes from effective_bets(dr).
    q = [abs(rc) / abs_total for rc in rc_list] if abs_total else []
    rb = (1.0 / sum(x * x for x in q)) if q and sum(x * x for x in q) > 0 else None
    rb = round(rb, 2) if rb else None
    rows.sort(key=lambda r: -(r["abs_risk_share_pct"] or 0))
    return rows, {"port_vol_pct": round(sp * 100, 1),
                  "risk_balance_n": rb,
                  "enb_abs": rb}   # same value under its old payload key


# --------------------------------------------------------------------------- #
#  Diversification Score                                                       #
# --------------------------------------------------------------------------- #
_GAP_DIVERSIFIERS = {"bond", "gold", "intl", "defensive", "cash"}


def gap_coverage(weights, asset_class):
    """Fraction (0..1) of the desirable non-equity-like diversifiers actually held."""
    have = set()
    for t, w in weights.items():
        if w <= 0:
            continue
        ac = (asset_class.get(t) or "").lower()
        if "bond" in ac: have.add("bond")
        if "gold" in ac or "real" in ac: have.add("gold")
        if "intl" in ac or "em" in ac: have.add("intl")
        if "defensive" in ac or "low" in ac: have.add("defensive")
        if "cash" in ac: have.add("cash")
    return len(have & _GAP_DIVERSIFIERS) / len(_GAP_DIVERSIFIERS)


# (lower bound, band) — first match wins, highest first
_SCORE_BANDS = (
    (80, "ความเสี่ยงกระจายกว้าง (broadly diversified)"),
    (60, "กระจายพอสมควร แต่ยังกระจุกบางด้าน"),
    (40, "กระจายเชิงจำนวนชื่อ แต่ independent bets จำกัด"),
    (20, "ความเสี่ยงหลักกระจุกไม่กี่ bet"),
)


def _score_band(s):
    return next((band for lo, band in _SCORE_BANDS if s >= lo),
                "outcome ขึ้นกับ risk driver หลักเกือบทั้งหมด")


def diversification_score(dr_normal, dr_crisis, enb_abs, eff_n, n_holdings,
                          gap_coverage_frac):
    """Heuristic 0-100 [JUDG]: TrueDiv 35% (0.4*normal DR + 0.6*crisis DR)
    + RiskBalance 30% + GapCoverage 20% + Concentration 15%."""
    def dr_score(dr):
        return _clamp01(((dr or 1.0) - 1.0) / 1.0) * 100

    true_div = 0.4 * dr_score(dr_normal) + 0.6 * dr_score(dr_crisis)
    risk_bal = (_clamp01(((enb_abs or 1.0) - 1.0) / (n_holdings - 1)) * 100
                if n_holdings and n_holdings > 1 else 0.0)
    gap = _clamp01(gap_coverage_frac) * 100
    denom = (min(n_holdings, 10) - 1) if n_holdings and n_holdings > 1 else 1
    conc = _clamp01(((eff_n or 1.0) - 1.0) / denom) * 100 if denom > 0 else 0.0
    total = 0.35 * true_div + 0.30 * risk_bal + 0.20 * gap + 0.15 * conc
    return {
        "score": round(total),
        "sub": {
            "true_diversification": round(true_div),
            "risk_balance": round(risk_bal),
            "gap_coverage": round(gap),
            "concentration": round(conc),
        },
        "band": _score_band(total),
    }


# --------------------------------------------------------------------------- #
#  Position sizing and rebalancing                                             #
# --------------------------------------------------------------------------- #
def _hard_cap(w, arc, asset_class, single_name_cap, risk_share_cap, pricing_cap):
    """(hard cap, binding constraint) for one holding: the tightest of the single-name
    cap, the pricing-asset cap (crypto/collectible have no cash flow, S40-41), and the
    capital that keeps its risk share within budget."""
    hard, binding = single_name_cap, "single-name cap"
    if asset_class in ("crypto", "collectible") and pricing_cap and pricing_cap < hard:
        hard, binding = pricing_cap, "pricing-asset cap (S40-41: no cash flow)"
    if arc and arc > risk_share_cap and arc > 0:
        implied = w * (risk_share_cap / arc)
        if implied < hard:
            hard, binding = implied, "risk-contribution budget"
    return hard, binding


def _sizing_action(w, hard, soft):
    if w > hard * 1.001:
        return "TRIM"
    if w < soft * 0.5:
        return "ADD ได้ (ถ้า thesis ยังดี)"
    return "HOLD"


def position_sizing(rc_rows, sectors, single_name_cap=0.20, sector_cap=0.40,
                    risk_share_cap=0.30, asset_class=None, pricing_cap=0.05):
    """Per holding: a soft/hard target band from the binding cap, current vs target,
    and an action verdict. `sectors` and `sector_cap` are accepted, but no sector cap
    is applied."""
    asset_class = asset_class or {}
    out = []
    for r in rc_rows:
        w = (r["capital_pct"] or 0) / 100.0
        arc = (r.get("abs_risk_share_pct") or r.get("risk_pct") or 0) / 100.0
        ac = (asset_class.get(r["ticker"]) or "equity").lower()
        hard, binding = _hard_cap(w, arc, ac, single_name_cap, risk_share_cap, pricing_cap)
        soft = hard * 0.8
        out.append({
            "ticker": r["ticker"],
            "current_pct": round(w * 100, 1),
            "soft_max_pct": round(soft * 100, 1),
            "hard_max_pct": round(hard * 100, 1),
            "binding_cap": binding,
            "abs_risk_share_pct": r.get("abs_risk_share_pct"),
            "action": _sizing_action(w, hard, soft),
        })
    return out


def rebalance(weights, sizing_rows, min_cash_pct=0.0):
    """Trim over-cap names to their hard cap, hand the freed weight to names below their
    soft max in proportion to their room, renormalize. Capital-only: the caller re-runs
    the full risk engine on the proposed weights."""
    proposed = dict(weights)
    trades = []
    freed = 0.0
    for s in sizing_rows:
        t = s["ticker"]
        cur = weights.get(t, 0.0)
        cap = (s["hard_max_pct"] or 100) / 100.0
        if cur > cap + 1e-6:
            cut = cur - cap
            proposed[t] = cap
            freed += cut
            trades.append({"ticker": t, "action": "TRIM",
                           "from_pct": round(cur * 100, 1), "to_pct": round(cap * 100, 1),
                           "amount_pct": round(cut * 100, 1), "priority": "Must"})
    if freed > 1e-6:
        room = [(s["ticker"], (s["soft_max_pct"] or 0) / 100.0 - proposed.get(s["ticker"], 0))
                for s in sizing_rows]
        room = [(t, r) for t, r in room if r > 0]
        room_tot = sum(r for _, r in room)
        for t, r in room:
            add = freed * (r / room_tot) if room_tot else 0
            if add > 1e-6:
                proposed[t] = proposed.get(t, 0) + add
                trades.append({"ticker": t, "action": "ADD",
                               "from_pct": round(weights.get(t, 0) * 100, 1),
                               "to_pct": round(proposed[t] * 100, 1),
                               "amount_pct": round(add * 100, 1), "priority": "Should"})
    tot = sum(proposed.values())
    if tot > 0:
        proposed = {t: w / tot for t, w in proposed.items()}
    return {
        "no_trade": len(trades) == 0,
        "proposed_weights": {t: round(w, 4) for t, w in proposed.items()},
        "trades": trades,
    }
