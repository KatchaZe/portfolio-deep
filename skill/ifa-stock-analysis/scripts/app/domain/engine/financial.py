"""Excess-return model for banks and insurers (Damodaran, eqexret.xls).

Deposits and insurance float are a financial firm's raw material, not its capital,
so free cash flow to the firm, invested capital and WACC do not describe it. Value is
equity instead:

    value of equity = book equity today
                    + PV of excess returns, (ROE_t - Ke) x book equity_{t-1}, years 1..n
                    + PV of the terminal excess return,
                      (ROE_stable - Ke) x book equity_n / (Ke - g_stable)

Book equity grows at the expected growth rate, never faster than the year's ROE (all
earnings retained is the most it can do); ROE moves in a straight line from today's to
the stable ROE, which closes half the gap to Ke (excess returns fade, Damodaran) and is
floored a little below Ke (a bank that stays far under its cost of equity is wound
down, not run forever). Stable growth is held to the stable ROE, since g = ROE x
retention and retention cannot exceed 1. Pure: no I/O.
"""
from typing import Optional

YEARS = 5


STABLE_ROE_GAP = 0.03          # the stable ROE may sit at most this far below Ke


def stable_roe(roe, ke):
    """Half the distance from today's ROE to the cost of equity, in either direction,
    floored at Ke - 3 points."""
    return max(ke - STABLE_ROE_GAP, ke + 0.5 * (roe - ke))


def value(*, book_equity, roe, ke, g_high, g_stable, shares, years=YEARS) -> Optional[dict]:
    if not book_equity or book_equity <= 0 or not shares or shares <= 0 or roe is None:
        return None
    if ke <= g_stable:
        return None
    roe_st = stable_roe(roe, ke)
    bv, pv, rows = book_equity, 0.0, []
    for t in range(1, years + 1):
        roe_t = roe + (roe_st - roe) * (t / years)
        excess = (roe_t - ke) * bv
        pv_t = excess / (1 + ke) ** t
        pv += pv_t
        g_t = min(g_high, max(roe_t, 0.0))          # equity cannot grow faster than it earns
        rows.append({"year": t, "roe": round(roe_t, 4), "book_equity": round(bv, 0),
                     "excess_return": round(excess, 0), "pv": round(pv_t, 0)})
        bv *= (1 + g_t)
    g_st = min(g_stable, max(0.0, roe_st))
    tv = (roe_st - ke) * bv / (ke - g_st)
    pv_tv = tv / (1 + ke) ** years
    equity = book_equity + pv + pv_tv
    per_share = equity / shares
    return {"per_share": round(per_share, 2) if per_share > 0 else None,
            "equity_value": round(equity, 0), "book_equity": round(book_equity, 0),
            "pv_excess": round(pv, 0), "pv_terminal": round(pv_tv, 0),
            "roe": round(roe, 4), "roe_stable": round(roe_st, 4), "ke": round(ke, 4),
            "g_high": round(g_high, 4), "g_stable": round(g_st, 4), "path": rows}
