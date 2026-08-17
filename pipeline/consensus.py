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


# A forward EPS is only comparable to another forward EPS in the SAME currency, and
# nothing upstream guarantees that. Yahoo's `forwardEps` is quoted for the LISTED
# security (so USD for an ADR); FMP's analyst estimates come in the filer's own
# reporting currency. For TSM that is TWD against USD — about 32x apart — and the blend
# had no way to know, so it reported `fwdEPS 2src spread 184.3%` and docked 6 points of
# confidence for "the analysts disagree" when the truth was "one of these is in another
# currency". Same class as the L2/TSM defect (TWD EPS beside a USD price -> $8,612 fair
# value), one layer up: there it corrupted a valuation, here it corrupts the DIAGNOSIS.
#
# We cannot label the currency at this layer — the free tiers do not return one. But we
# can test each candidate against the PRICE, which is always in the listed security's
# currency: a forward P/E outside this band is not an opinion about the business, it is
# a unit artefact. TSM's TWD candidate implies 1.6x; its USD siblings imply 20-36x.
PE_MIN, PE_MAX = 2.0, 250.0


def blend_forward_eps(candidates, price=None):
    """candidates: {source: eps}. Returns a blend dict or None when nothing usable.
      {value, sources, low, high, spread_pct, n, rejected}
    value = median of the positive candidates; spread_pct = (high-low)/median*100.

    When `price` is supplied, a candidate whose implied forward P/E falls outside
    [PE_MIN, PE_MAX] is dropped as a UNIT mismatch and reported in `rejected` with the
    reason — it never reaches `spread_pct`, so dispersion measures disagreement only.
    Candidates are never all dropped: if none survives the band they are all kept and
    the downstream revenue-capacity ceiling has the final say, because a blend built on
    a suspicious number is still better than no blend at all."""
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

    rejected = {}
    if price and price > 0 and len(vals) > 1:
        keep = {}
        for k, v in vals.items():
            pe = price / v
            if PE_MIN <= pe <= PE_MAX:
                keep[k] = v
            else:
                rejected[k] = (f"forward EPS {v:,.2f} implies a {pe:,.1f}x forward P/E "
                               f"at price {price:,.2f} — outside {PE_MIN:g}-{PE_MAX:g}x, "
                               f"so it reads as a different currency/unit, not as a "
                               f"disagreement")
        if keep:                       # never reject everything
            vals = keep
        else:
            rejected = {}

    xs = sorted(vals.values())
    n = len(xs)
    med = xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2
    low, high = xs[0], xs[-1]
    spread = ((high - low) / med * 100) if med else None
    return {"value": round(med, 4),
            "sources": {k: round(v, 4) for k, v in vals.items()},
            "low": round(low, 4), "high": round(high, 4),
            "spread_pct": round(spread, 1) if spread is not None else None,
            "n": n,
            "rejected": rejected}


from domain import pead

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
    # B6: order the chosen track record by DATE before storing it, and pick the latest
    # quarter by date too. The Yahoo parser delivers newest-first while every other
    # source delivers oldest-first, so `[-1]` was comparing a 9-month-old quarter
    # against other sources' current one when Yahoo won the primary slot.
    plist = pead.chronological(present[primary])
    p_last = (pead.latest(plist) or {}).get("grade") if plist else None

    others = order[1:]
    agree, total, disagree = 0, 0, False
    confirms, conflicts = [], []
    for s in others:
        last = (pead.latest(present[s]) or {}).get("grade") if present[s] else None
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
