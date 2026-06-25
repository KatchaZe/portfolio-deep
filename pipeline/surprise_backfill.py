"""
surprise_backfill — IMMEDIATE EPS / Revenue "estimate vs actual" history.

The build-forward tracks (rev_track) only fill after a snapshotted quarter
reports, and Yahoo's earningsHistory is often blocked from server IPs. This module
reconstructs the beat/meet/miss circles RIGHT AWAY by pairing two sources we get
reliably and for free:

  • ACTUAL   — SEC quarterly diluted EPS / revenue ({quarter_end: value})
  • ESTIMATE — FMP quarterly analyst-estimates ({quarter_end: {eps_est, rev_est}})

SEC fiscal quarter-ends and FMP estimate dates rarely match to the day, so we pair
each actual to the NEAREST estimate within ±TOL_DAYS. Same ±2% grade as the other
adapters, so the frontend's shared circle renderer colours it identically. Pure +
offline-testable: no network here.

Item shapes (oldest->newest, <=limit), identical to fmp/yahoo parse_earnings:
  EPS: {quarter, eps_actual, eps_estimate, surprise_pct, grade}
  Rev: {quarter, rev_actual, rev_estimate, surprise_pct, grade}
"""
import datetime as dt

THRESHOLD = 2.0    # percent; |surprise| <= 2% == "meet"
TOL_DAYS = 45      # max gap when pairing a SEC actual to an FMP estimate date


def grade(surprise_pct):
    if surprise_pct is None:
        return None
    if surprise_pct > THRESHOLD:
        return "beat"
    if surprise_pct < -THRESHOLD:
        return "miss"
    return "meet"


def _nearest(end, est_dates):
    """The estimate date closest to actual quarter-end `end` within ±TOL_DAYS."""
    try:
        target = dt.date.fromisoformat(end[:10])
    except Exception:
        return None
    best, gap = None, TOL_DAYS + 1
    for d in est_dates:
        try:
            g = abs((dt.date.fromisoformat(d[:10]) - target).days)
        except Exception:
            continue
        if g <= TOL_DAYS and g < gap:
            gap, best = g, d
    return best


def _pair(actuals, estimates, est_key, act_field, est_field, limit):
    """Generic pairing of {end: actual} with {end: {..est_key..}} -> graded list."""
    acts = actuals or {}
    ests = estimates or {}
    est_dates = list(ests.keys())
    out = []
    for end in sorted(acts):
        act = acts.get(end)
        md = _nearest(end, est_dates)
        est = (ests.get(md) or {}).get(est_key) if md else None
        if act is None or est in (None, 0):
            continue
        try:
            sp = (act - est) / abs(est) * 100
        except (TypeError, ZeroDivisionError):
            continue
        out.append({"quarter": end[:10], act_field: act, est_field: est,
                    "surprise_pct": round(sp, 1), "grade": grade(sp)})
    return out[-limit:]


def build_eps(eps_quarters, est_by_q, limit=4):
    """EPS beat/miss from SEC eps actuals x FMP quarterly eps estimates."""
    return _pair(eps_quarters, est_by_q, "eps_est", "eps_actual", "eps_estimate", limit)


def build_rev(revenue_quarters, est_by_q, limit=4):
    """Revenue beat/miss from SEC revenue actuals x FMP quarterly revenue estimates."""
    return _pair(revenue_quarters, est_by_q, "rev_est", "rev_actual", "rev_estimate", limit)
