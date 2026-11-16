"""
peer_panel — the peer table and the industry revenue share beside the relative
valuation (docs/peer-panel/SPEC.md). Display only: nothing here feeds a score, a fair
value or the recommendation (invariant PP1 in tests/test_invariants.py).

Pure: no network, no clock of its own. Every number sits on one declared clock
(domain/contracts.PANEL_CLOCK):
  TTM  revenue, EBIT, net income and the multiples on them (EV and market cap at T-1)
  FY   growth, 3-year CAGR and gross margin, from each company's own fiscal years,
       joined by DATE through trend._window (never by list position)
  CY   industry share, from the SEC frames calendar years
"""
import datetime as dt

from domain import trend

FY_GAP = (330, 400)              # one fiscal year apart, in days (relative.FY_GAP)
FY3_GAP = (3 * 330, 3 * 400)     # three fiscal years apart


def _d(s):
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def _days(a, b):
    da, db = _d(a), _d(b)
    return (da - db).days if da and db else None


def fy_snapshot(rev_dated, gp_dated=None, cost_dated=None, oi_dated=None, as_of=None):
    """{fy0, fy1, fy3} for one filer, each {end, revenue, gross_profit, operating_income}
    or None, plus gp_basis. FY0 is the newest fiscal year ending on or before `as_of`;
    FY1 and FY3 come from the contiguous run of fiscal-year ends behind it (trend._window),
    each checked to be about one / three years before FY0 by date. A leg with no value
    filed AT THAT DATE is None: gross profit from another year never fills it."""
    rev = trend._pairs(rev_dated)
    if as_of:
        rev = {d: v for d, v in rev.items() if _d(d) and _d(d) <= _d(as_of)}
    win = trend._window(rev, 4)
    gp, gp_basis = trend.gross_profit(gp_dated, rev_dated, cost_dated)
    oi = trend._pairs(oi_dated)

    def leg(d):
        return {"end": d, "revenue": rev.get(d), "gross_profit": gp.get(d),
                "operating_income": oi.get(d)}

    out = {"fy0": None, "fy1": None, "fy3": None, "gp_basis": gp_basis}
    if not win:
        return out
    fy0 = win[-1]
    out["fy0"] = leg(fy0)
    if len(win) >= 2 and FY_GAP[0] <= _days(fy0, win[-2]) <= FY_GAP[1]:
        out["fy1"] = leg(win[-2])
    if len(win) == 4 and FY3_GAP[0] <= _days(fy0, win[0]) <= FY3_GAP[1]:
        out["fy3"] = leg(win[0])
    return out


# what a stored peer point carries for the table, beyond relative.point's own keys
POINT_KEYS = ("symbol", "source", "sic", "currency", "accounting_standard", "period_end",
              "price_date", "price", "revenue_ttm", "ebit_ttm", "net_income_ttm", "ev",
              "market_cap", "addback", "fy0", "fy1", "fy3", "gp_basis")


def point_fields(x, as_of=None):
    """The panel's fields from a sec_edgar.extract() dict (EBIT already normalized),
    to merge into a relative.point."""
    snap = fy_snapshot(x.get("revenue_annuals_dated"), x.get("gross_profit_annuals_dated"),
                       x.get("cost_of_revenue_annuals_dated"),
                       x.get("operating_income_annuals_dated"), as_of)
    return {"currency": x.get("currency") or "USD",
            "accounting_standard": x.get("accounting_standard"),
            "revenue_ttm": x.get("revenue"), "ebit_ttm": x.get("operating_income"),
            "net_income_ttm": x.get("net_income"), **snap}


def compact(p):
    """A point reduced to POINT_KEYS (the stored form)."""
    return {k: p.get(k) for k in POINT_KEYS}
