"""FCFF DCF — the primary intrinsic value for an operating company (Damodaran's
simple Ginzu: revenue growth, operating margin, sales-to-capital, cost of capital).

    revenue_t   = revenue_{t-1} x (1 + g_t)       g: growth_path year by year when given
                                                  (consensus years), else g_high for
                                                  years 1-5; then a straight line down
                                                  to g_stable by year 10
    margin_t    moves from today's margin to the target margin by `margin_years`
    tax_t       effective rate for years 1-5, then a straight line to the marginal rate
    NOPAT_t     = revenue_t x margin_t x (1 - tax_t)
    reinvest_t  = (revenue_t - revenue_{t-1}) / sales_to_capital
    FCFF_t      = NOPAT_t - reinvest_t
    discount    WACC for years 1-5, then a straight line to the stable-phase WACC
    terminal    NOPAT_11 x (1 - g_stable / ROIC_stable) / (WACC_stable - g_stable)
    equity      = PV(FCFF) + PV(terminal) + cash - debt (leases included)
    per share   = equity / diluted shares

The margin is the reported operating margin, so stock-based compensation is already an
expense; the share count is today's diluted count and is NOT grown again (that would
charge SBC twice). Pure: no I/O.
"""
from typing import Optional

HIGH_YEARS, TOTAL_YEARS = 5, 10


def _lerp(a, b, frac):
    return a + (b - a) * frac


def value(*, revenue, margin_now, margin_target, g_high, g_stable, sales_to_capital,
          tax_now, tax_marginal, wacc, wacc_stable, roic_stable, cash, debt, shares,
          margin_years=5, growth_path=None) -> Optional[dict]:
    """Per-share value and the projection behind it, or None when the inputs cannot
    support a DCF (no revenue, no shares, or a stable WACC at or below g)."""
    if not revenue or revenue <= 0 or not shares or shares <= 0:
        return None
    if margin_now is None or margin_target is None or sales_to_capital is None or sales_to_capital <= 0:
        return None
    if wacc_stable <= g_stable or wacc <= 0 or not roic_stable or roic_stable <= 0:
        return None
    explicit = list(growth_path or [])[:TOTAL_YEARS - 1] or [g_high] * HIGH_YEARS
    rows, pv_sum, disc = [], 0.0, 1.0
    rev_prev = revenue
    for t in range(1, TOTAL_YEARS + 1):
        k = len(explicit)
        g = explicit[t - 1] if t <= k else _lerp(explicit[-1], g_stable, (t - k) / (TOTAL_YEARS - k))
        rev = rev_prev * (1 + g)
        m = _lerp(margin_now, margin_target, min(1.0, t / margin_years)) if margin_years else margin_target
        tax = tax_now if t <= HIGH_YEARS else _lerp(tax_now, tax_marginal,
                                                    (t - HIGH_YEARS) / (TOTAL_YEARS - HIGH_YEARS))
        r = wacc if t <= HIGH_YEARS else _lerp(wacc, wacc_stable, (t - HIGH_YEARS) / (TOTAL_YEARS - HIGH_YEARS))
        ebit = rev * m
        nopat = ebit * (1 - tax) if ebit > 0 else ebit       # no tax on an operating loss
        reinvest = (rev - rev_prev) / sales_to_capital
        fcff = nopat - reinvest
        disc *= (1 + r)
        pv = fcff / disc
        pv_sum += pv
        rows.append({"year": t, "growth": round(g, 4), "revenue": round(rev, 0), "margin": round(m, 4),
                     "tax": round(tax, 4), "nopat": round(nopat, 0), "reinvestment": round(reinvest, 0),
                     "fcff": round(fcff, 0), "wacc": round(r, 4), "pv": round(pv, 0)})
        rev_prev = rev
    rev_t1 = rev_prev * (1 + g_stable)
    nopat_t1 = rev_t1 * margin_target * (1 - tax_marginal)
    reinvest_rate = min(0.9, max(0.0, g_stable / roic_stable))
    tv = nopat_t1 * (1 - reinvest_rate) / (wacc_stable - g_stable)
    pv_tv = tv / disc
    firm = pv_sum + pv_tv
    equity = firm + (cash or 0.0) - (debt or 0.0)
    per_share = equity / shares
    return {"per_share": round(per_share, 2) if per_share > 0 else None,
            "equity_value": round(equity, 0), "firm_value": round(firm, 0),
            "pv_fcff": round(pv_sum, 0), "pv_terminal": round(pv_tv, 0),
            "terminal_share_pct": round(pv_tv / firm * 100, 1) if firm > 0 else None,
            "terminal_reinvestment_rate": round(reinvest_rate, 3),
            "implied_roic_high": round(margin_target * (1 - tax_now) * sales_to_capital, 4),
            "inputs": {"g_high": round(explicit[0], 4), "g_stable": round(g_stable, 4),
                       "growth_path": [round(g, 4) for g in explicit],
                       "margin_now": round(margin_now, 4), "margin_target": round(margin_target, 4),
                       "sales_to_capital": round(sales_to_capital, 3), "wacc": round(wacc, 4),
                       "wacc_stable": round(wacc_stable, 4), "roic_stable": round(roic_stable, 4),
                       "tax_now": round(tax_now, 4), "tax_marginal": round(tax_marginal, 4)},
            "path": rows}
