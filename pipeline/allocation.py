"""Cost-basis allocation pies (by ticker + by sector), before and after what-if buys."""
import logging

from sources import fmp
import store as store_mod

log = logging.getLogger("portfolio.allocation")


def _pies(costmap, sector_of):
    bt = sorted([{"label": t, "value": round(v, 2)} for t, v in costmap.items() if v > 0],
                key=lambda x: -x["value"])
    sect = {}
    for t, v in costmap.items():
        if v > 0:
            k = sector_of(t)
            sect[k] = sect.get(k, 0) + v
    bs = sorted([{"label": k, "value": round(v, 2)} for k, v in sect.items()], key=lambda x: -x["value"])
    return {"by_ticker": bt, "by_sector": bs, "total": round(sum(v for v in costmap.values() if v > 0), 2)}


def _parse_buys(whatif):
    """(added [{ticker, amount}], skipped [raw ticker]) from what-if rows."""
    added, skipped = [], []
    for w in whatif:
        t = store_mod.clean_ticker((w or {}).get("ticker"))
        try:
            amt = float((w or {}).get("amount") or 0)
        except (TypeError, ValueError):
            skipped.append(str((w or {}).get("ticker") or "?"))
            continue
        if t and amt > 0:
            added.append({"ticker": t, "amount": amt})
    return added, skipped


def build(s, whatif=None, fmp_key=""):
    """`whatif` is a list of {ticker, amount} buys -> also returns the 'after'
    allocation. Returns (result, fmp_calls)."""
    holdings = s.get("holdings", {})
    facts = s.get("facts", {})
    sectors = {t: (facts.get(t, {}) or {}).get("sector") or "Unknown" for t in holdings}
    fmp_calls = 0

    def sector_of(t):
        """Stored sector; a what-if name outside the store costs one FMP profile."""
        nonlocal fmp_calls
        if t not in sectors:
            sec = (facts.get(t, {}) or {}).get("sector")
            if not sec and fmp_key:
                try:
                    sec = fmp.parse_profile(fmp.fetch_profile(t, fmp_key)).get("sector")
                    fmp_calls += 1
                except Exception as e:
                    log.warning("%s sector lookup failed: %s", t, e)
                    sec = None
            sectors[t] = sec or "Unknown"
        return sectors[t]

    base = {t: (h.get("shares", 0) * h.get("avg_cost", 0)) for t, h in holdings.items()
            if h.get("shares") and h.get("avg_cost")}
    result = {"before": _pies(base, sector_of)}
    if whatif:
        added, skipped = _parse_buys(whatif)
        after = dict(base)
        for a in added:
            after[a["ticker"]] = after.get(a["ticker"], 0) + a["amount"]
        result["after"] = _pies(after, sector_of)
        result["added"] = added
        if skipped:
            result["skipped"] = skipped
    return result, fmp_calls
