"""
lastgood — reuse a value read live on an earlier refresh when every source fails now.

Rule (user decision 2026-10-21): the latest live value no older than MAX_AGE_DAYS,
always with a flag that names its date and source. Applied to the consensus revenue
path, the forward EPS and the S&P 500 P/E. Past the window the value is not reused,
and the documented fallback of each field applies (historical growth, SEC EPS, the
saved MARKET_PE). Pure: callers pass the previous record in.
"""
import datetime as dt

MAX_AGE_DAYS = 30


def date_of(iso):
    """A date from an ISO string, or None."""
    try:
        return dt.date.fromisoformat(str(iso)[:10])
    except (TypeError, ValueError):
        return None


def age_days(as_of, today=None):
    """Days since an ISO date (YYYY-MM-DD or YYYY-MM-DDT...), or None when unreadable."""
    try:
        d = dt.date.fromisoformat(str(as_of)[:10])
    except (TypeError, ValueError):
        return None
    return ((today or dt.date.today()) - d).days


def usable(as_of, today=None, max_age=MAX_AGE_DAYS):
    a = age_days(as_of, today)
    return a is not None and 0 <= a <= max_age


def consensus_path(prev, ff, today=None):
    """(rows, fields, source, as_of) from the previous facts dict, or None. Reused only
    for the same last reported fiscal year: once a year is reported, the old path's
    year 1 is history, not a forecast."""
    if not isinstance(prev, dict) or not prev.get("rev_growth_path"):
        return None
    as_of = prev.get("consensus_as_of")
    if not usable(as_of, today):
        return None
    last = sorted(ff.revenue_annuals_dated or [], reverse=True)
    was = sorted(prev.get("revenue_annuals_dated") or [], reverse=True)
    if not last or not was or str(last[0][0]) != str(was[0][0]):
        return None
    src = str((prev.get("provenance") or {}).get("rev_growth_path") or "consensus")
    src = src.split("last-good ")[-1].split(" (")[0]
    fields = {k: prev.get(k) for k in ("fwd_growth_y1", "n_analysts", "fwd_growth_near",
                                       "n_analysts_y2", "fwd_growth_far")}
    return prev["rev_growth_path"], fields, src, str(as_of)[:10]


def forward_eps(prev, today=None):
    """(eps, source, as_of) of a previous CONSENSUS forward EPS, or None."""
    if not isinstance(prev, dict) or not prev.get("forward_eps"):
        return None
    src = str((prev.get("provenance") or {}).get("forward_eps") or "")
    if not src.startswith(("consensus", "last-good")):
        return None                      # an SEC-derived EPS is not a consensus to keep
    as_of = prev.get("forward_eps_as_of")
    if not usable(as_of, today):
        return None
    return prev["forward_eps"], src.split("last-good ")[-1].split(" (")[0], str(as_of)[:10]


def market_pe(prev_market, today=None):
    """(pe, as_of) from the previous market overlay when that P/E was read live, or None."""
    val = (prev_market or {}).get("valuation") if isinstance(prev_market, dict) else None
    if not isinstance(val, dict) or not val.get("market_pe"):
        return None
    as_of = val.get("market_pe_live_date") or (prev_market.get("as_of") if val.get("market_pe_live") else None)
    if not usable(as_of, today):
        return None
    return val["market_pe"], str(as_of)[:10]
