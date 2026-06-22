"""
margin_track — operating-margin TREND per quarter, from SEC quarterly data only.

For each ~90-day quarter SEC reports we compute operating margin
(operating_income_q / revenue_q) and grade its YoY change (vs the same quarter a
year earlier) as expand / flat / contract.

Free data has no analyst *operating-margin estimate*, so — unlike EPS/Revenue —
this is a TREND, not a beat/miss. It needs no forward-building and no extra
network: it is derived from the SEC companyfacts we already pull, so it fills
immediately. Pure; unit-testable offline.

Item shape (oldest->newest, <=limit):
  {quarter, op_margin, op_margin_yoy, delta_pp, trend, grade}
`grade` mirrors `trend` so the frontend's shared circle renderer colours it.
"""
import datetime as dt

THRESHOLD_PP = 0.5   # percentage points; |Δ| <= 0.5pp counts as "flat"


def grade(delta_pp):
    """expand / flat / contract from a YoY margin change in percentage points."""
    if delta_pp is None:
        return "flat"
    if delta_pp > THRESHOLD_PP:
        return "expand"
    if delta_pp < -THRESHOLD_PP:
        return "contract"
    return "flat"


def _yoy_match(end, ends, tol_days=25):
    """The quarter-end ~365 days before `end` (within ±tol_days), or None.
    Matches by date (not position) so a gap in the quarterly series can't pair
    the wrong quarters."""
    try:
        target = dt.date.fromisoformat(end) - dt.timedelta(days=365)
    except Exception:
        return None
    best, gap = None, tol_days + 1
    for e in ends:
        if e == end:
            continue
        try:
            g = abs((dt.date.fromisoformat(e) - target).days)
        except Exception:
            continue
        if g <= tol_days and g < gap:
            gap, best = g, e
    return best


def build(op_quarters, rev_quarters, limit=4):
    """Operating-margin trend history (oldest->newest, <=limit) from two
    {end_date: value} dicts. Only quarters present in BOTH (and with non-zero
    revenue) are used."""
    op = op_quarters or {}
    rev = rev_quarters or {}
    margins = {}
    for e in set(op) & set(rev):
        r, o = rev.get(e), op.get(e)
        if r and o is not None and r != 0:
            margins[e] = o / r
    out = []
    keys = margins.keys()
    for e in sorted(margins):
        ym = _yoy_match(e, keys)
        prev = margins.get(ym) if ym else None
        delta_pp = round((margins[e] - prev) * 100, 1) if prev is not None else None
        g = grade(delta_pp)
        out.append({"quarter": e,
                    "op_margin": round(margins[e], 4),
                    "op_margin_yoy": round(prev, 4) if prev is not None else None,
                    "delta_pp": delta_pp,
                    "trend": g, "grade": g})
    return out[-limit:]
