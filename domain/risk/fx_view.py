"""
The book's risk as a Thai-baht investor feels it. Holdings are priced in USD, so the
baht return of a day is (1 + USD return) x (1 + change in THB per USD) - 1: a falling
baht cushions a US sell-off, a rising one deepens it. Measured on the days the
portfolio series and the USD/THB closes share. Pure: no I/O.
"""
import math

from .returns import TRADING_DAYS
from .stress import historical_var

MIN_DAYS = 60


def _vol(rs):
    m = sum(rs) / len(rs)
    return math.sqrt(sum((r - m) ** 2 for r in rs) / (len(rs) - 1) * TRADING_DAYS)


def _max_drawdown(rs):
    peak = level = 1.0
    worst = 0.0
    for r in rs:
        level *= 1 + r
        peak = max(peak, level)
        worst = min(worst, level / peak - 1)
    return worst


def _corr(a, b):
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    return cov / math.sqrt(va * vb) if va > 0 and vb > 0 else None


def thb_view(port_rets, port_dates, fx_points, horizon_years=1.0):
    """USD vs THB: vol, historical VaR, max drawdown and the portfolio / USD-THB
    correlation, on the shared days. {"status": "insufficient"} below MIN_DAYS."""
    fx = {p["d"]: p["v"] for p in fx_points or []}
    days = sorted(fx)
    fx_ret = {d: fx[d] / fx[p] - 1 for p, d in zip(days, days[1:]) if fx[p]}
    pairs = [(r, fx_ret[d]) for r, d in zip(port_rets or [], port_dates or [])
             if r is not None and d in fx_ret]
    if len(pairs) < MIN_DAYS:
        return {"status": "insufficient", "n": len(pairs),
                "note": f"ต้องมีวันที่ตรงกันอย่างน้อย {MIN_DAYS} วัน (มี {len(pairs)})"}
    usd = [r for r, _ in pairs]
    f = [x for _, x in pairs]
    thb = [(1 + r) * (1 + x) - 1 for r, x in pairs]
    vu, vt = historical_var(usd, horizon_years), historical_var(thb, horizon_years)
    return {"status": "ok", "n": len(pairs),
            "vol_usd_pct": round(_vol(usd) * 100, 1), "vol_thb_pct": round(_vol(thb) * 100, 1),
            "var95_usd_pct": vu["var95_pct"], "var95_thb_pct": vt["var95_pct"],
            "max_dd_usd_pct": round(_max_drawdown(usd) * 100, 1),
            "max_dd_thb_pct": round(_max_drawdown(thb) * 100, 1),
            "return_usd_pct": round((math.prod(1 + r for r in usd) - 1) * 100, 1),
            "return_thb_pct": round((math.prod(1 + r for r in thb) - 1) * 100, 1),
            "corr_port_fx": round(_corr(usd, f), 2) if _corr(usd, f) is not None else None,
            "tag": "[CALC] THB = (1 + r_USD)(1 + Δ USD/THB) − 1 บนวันที่ตรงกัน"}
