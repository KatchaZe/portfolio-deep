"""
Loss estimates for the Risk Desk: scenario and historical stress, VaR/CVaR, reverse
stress, rate stress, and the suitability verdict against the user's tolerance.
Every scenario is illustrative; the caller tags [JUDG-SCENARIO] "not a forecast".
"""
import math

from .portfolio import beta_exposure
from .returns import TRADING_DAYS

# Each scenario is an equity-market shock applied through beta, with optional sector
# multipliers and an extra hit for high-beta names.
SCENARIOS = [
    {"key": "broad_selloff", "label": "Broad equity sell-off", "mkt": -0.20},
    {"key": "tech_derate", "label": "Tech / Semiconductor de-rating", "mkt": -0.12,
     "sector_mult": {"Technology": 2.2, "Semiconductors": 2.6, "Information Technology": 2.2}},
    {"key": "rate_shock", "label": "Inflation / Rate shock", "mkt": -0.12, "highbeta_extra": -0.10},
    {"key": "liquidity_crisis", "label": "Liquidity crisis", "mkt": -0.30, "highbeta_extra": -0.08},
]

# Historical replays: the equity-market drawdown of each episode.
HISTORICAL = [
    {"key": "gfc_2008", "label": "GFC 2008", "mkt": -0.55},
    {"key": "covid_2020", "label": "COVID crash 2020-03", "mkt": -0.34},
    {"key": "rate_2022", "label": "Rate shock 2022", "mkt": -0.25},
    {"key": "carry_2024", "label": "Yen carry unwind 2024-08", "mkt": -0.08},
]


def _position_shock(beta, sector, scn, default_beta=1.0):
    """Estimated single-position loss for a scenario (fraction, negative)."""
    b = beta if beta is not None else default_beta
    loss = b * scn["mkt"]
    mult = (scn.get("sector_mult") or {}).get(sector)
    if mult:
        loss = b * scn["mkt"] * mult
    if scn.get("highbeta_extra") and b >= 1.3:
        loss += scn["highbeta_extra"]
    return max(loss, -0.95)         # floor a single name at -95%


def stress_test(weights, betas, sectors, scenarios=None, default_beta=1.0):
    """Portfolio loss per scenario = Σ w_i * shock_i, most negative first."""
    scenarios = scenarios or SCENARIOS
    out = []
    for scn in scenarios:
        contrib = {t: w * _position_shock(betas.get(t), sectors.get(t), scn, default_beta)
                   for t, w in weights.items()}
        loss = sum(contrib.values())
        worst = min(contrib.items(), key=lambda kv: kv[1])[0] if contrib else None
        out.append({"key": scn["key"], "label": scn["label"],
                    "loss_pct": round(loss * 100, 1), "worst_contributor": worst})
    out.sort(key=lambda x: x["loss_pct"])
    return out


def historical_var(port_returns, horizon_years=1.0, min_n=60):
    """VaR/CVaR from the realized daily portfolio returns: no normality assumption, so
    fat tails show as they happened. The daily percentile loss is scaled by
    sqrt(252*horizon). None-fields below `min_n` days."""
    rs = [r for r in (port_returns or []) if r is not None]
    n = len(rs)
    if n < min_n:
        return {"var95_pct": None, "var99_pct": None, "cvar95_pct": None,
                "n": n, "method": "historical (insufficient history)"}
    xs = sorted(rs)                                    # worst first
    scale = math.sqrt(TRADING_DAYS * horizon_years)

    def pct_loss(q):
        return -xs[max(0, min(n - 1, int(q * n)))]

    tail = xs[:max(1, int(0.05 * n))]
    c95 = -(sum(tail) / len(tail))
    return {"var95_pct": round(pct_loss(0.05) * scale * 100, 1),
            "var99_pct": round(pct_loss(0.01) * scale * 100, 1),
            "cvar95_pct": round(c95 * scale * 100, 1),
            "n": n, "method": "historical"}


def var_cvar(port_vol, horizon_years=1.0):
    """Parametric (normal) VaR/CVaR as % of value; `port_vol` is the ANNUAL sigma_p.
    z95 = 1.645, z99 = 2.326, CVaR95 factor = 2.063."""
    if not port_vol or port_vol <= 0:
        return {"var95_pct": None, "var99_pct": None, "cvar95_pct": None}
    s = port_vol * math.sqrt(horizon_years)
    return {
        "var95_pct": round(1.645 * s * 100, 1),
        "var99_pct": round(2.326 * s * 100, 1),
        "cvar95_pct": round(2.063 * s * 100, 1),
        "method": "parametric-normal",
    }


def reverse_stress(weights, betas, tolerance_pct, default_beta=1.0):
    """The broad equity move m that breaches the max-loss tolerance: Σw*beta * m = -tolerance."""
    wb = beta_exposure(weights, betas, default_beta)
    if wb <= 0 or not tolerance_pct:
        return {"market_move_pct": None, "port_beta": round(wb, 3)}
    m = -(tolerance_pct / 100.0) / wb
    return {"market_move_pct": round(m * 100, 1), "port_beta": round(wb, 3),
            "note": f"ตลาด (equity beta) ต้องลง ~{round(abs(m)*100,1)}% จึงแตะขีดรับขาดทุน {tolerance_pct}%"}


def effective_duration(bond_returns, yield_changes_bps):
    """Empirical duration from regressing bond daily returns on daily yield changes in
    bps: r ~ -(D/10000)*dy -> D = -slope*10000. None below 20 points or a flat yield."""
    n = min(len(bond_returns), len(yield_changes_bps))
    if n < 20:
        return None
    r, dy = bond_returns[-n:], yield_changes_bps[-n:]
    mr = sum(r) / n
    mdy = sum(dy) / n
    cov = sum((r[i] - mr) * (dy[i] - mdy) for i in range(n)) / n
    var = sum((dy[i] - mdy) ** 2 for i in range(n)) / n
    if var <= 0:
        return None
    return round(-(cov / var) * 10000, 1)


def rate_stress(weights, durations, bps):
    """Portfolio loss % from a parallel move of `bps`, on holdings with a duration only."""
    loss = 0.0
    for t, w in weights.items():
        d = durations.get(t)
        if d:
            loss += w * (-d * (bps / 10000.0))
    return round(loss * 100, 2)


def suitability(stress_rows, var_block, tolerance_pct):
    """The binding drawdown (the worse of the worst scenario and -VaR99) against the
    user's tolerance, surfaced as a headline finding."""
    worst = min((r["loss_pct"] for r in stress_rows), default=None) if stress_rows else None
    var99 = var_block.get("var99_pct")
    candidates = [x for x in (worst, (-var99 if var99 else None)) if x is not None]
    binding = min(candidates) if candidates else None
    ok = (binding is not None and tolerance_pct is not None and binding >= -tolerance_pct)
    return {
        "tolerance_pct": tolerance_pct,
        "binding_drawdown_pct": binding,
        "within_tolerance": ok,
        "verdict": ("ภายในระดับที่รับได้" if ok else
                    "เกินระดับที่ผู้ใช้รับได้ — FINDING หลัก" if binding is not None else
                    "ข้อมูลไม่พอประเมิน"),
    }
