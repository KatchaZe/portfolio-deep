"""
consensus — blend multiple FREE consensus sources into one robust view.

Two pure helpers (no network, fully unit-testable):

  blend_forward_eps({source: eps})   -> median forward EPS + dispersion (low/high/
                                        spread%/n). Median resists one bad/stale
                                        source; the spread feeds a confidence nudge.
  reconcile_earnings(by_source)       -> pick a primary EPS-surprise list + an
                                        agreement summary across sources, flagging
                                        a latest-quarter beat-vs-miss disagreement.

Source priority (most → least authoritative for our use): Yahoo (adjusted/street,
matches forwardEps) > FMP (GAAP) > Finnhub > Alpha Vantage.
"""

SOURCE_PRIORITY = ("yahoo", "fmp", "finnhub", "alphavantage")


def blend_forward_eps(candidates):
    """candidates: {source: eps}. Returns a blend dict or None when nothing usable.
      {value, sources, low, high, spread_pct, n}
    value = median of the positive candidates; spread_pct = (high-low)/median*100."""
    vals = {}
    for k, v in (candidates or {}).items():
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if f > 0:
            vals[k] = f
    if not vals:
        return None
    xs = sorted(vals.values())
    n = len(xs)
    med = xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2
    low, high = xs[0], xs[-1]
    spread = ((high - low) / med * 100) if med else None
    return {"value": round(med, 4),
            "sources": {k: round(v, 4) for k, v in vals.items()},
            "low": round(low, 4), "high": round(high, 4),
            "spread_pct": round(spread, 1) if spread is not None else None,
            "n": n}


def _primary_order(present):
    """present: iterable of source names that actually returned data. Yield in
    priority order, then any extras (stable)."""
    ordered = [s for s in SOURCE_PRIORITY if s in present]
    ordered += [s for s in present if s not in SOURCE_PRIORITY]
    return ordered


def reconcile_earnings(by_source):
    """by_source: {source: [surprise rows]}. Returns
      {list, primary, provenance, agree, total, disagree}
    `list` is the chosen primary track record (for display); `provenance` is a tag
    like 'yahoo+fmp✓+finnhub✓' or 'yahoo (fmp disagree)'; `disagree` is True when
    sources split beat-vs-miss on the latest quarter."""
    present = {s: rows for s, rows in (by_source or {}).items() if rows}
    if not present:
        return {"list": [], "primary": None, "provenance": None,
                "agree": 0, "total": 0, "disagree": False}
    order = _primary_order(present)
    primary = order[0]
    plist = present[primary]
    p_last = (plist[-1] or {}).get("grade") if plist else None

    others = order[1:]
    agree, total, disagree = 0, 0, False
    confirms, conflicts = [], []
    for s in others:
        last = (present[s][-1] or {}).get("grade") if present[s] else None
        if last is None or p_last is None:
            continue
        total += 1
        if last == p_last:
            agree += 1
            confirms.append(s)
        else:
            if {last, p_last} == {"beat", "miss"}:
                disagree = True
            conflicts.append(s)

    prov = primary
    if confirms:
        prov += "+" + "+".join(f"{s}✓" for s in confirms)
    if conflicts:
        prov += f" ({'/'.join(conflicts)} disagree)"
    return {"list": plist, "primary": primary, "provenance": prov,
            "agree": agree, "total": total, "disagree": disagree}
