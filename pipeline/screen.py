"""
Damodaran S13 (Passive Screeners) + S19 (GARP): find CHEAP x QUALITY mismatches —
names the market underprices despite good fundamentals. Reuses the DEEP subscores
already computed per name (no new data): Economics (E_econ = ROIC-WACC spread =
QUALITY) and Price (P = margin-of-safety = CHEAP). Pure helpers.
"""


def garp_score(economics, price):
    """Combined cheap x quality score (0-10). None if either subscore missing."""
    if economics is None or price is None:
        return None
    return round(economics + price, 2)


def is_candidate(economics, price, quality_min=3.0, cheap_min=3.0):
    """A GARP candidate is BOTH good quality AND cheap — the mismatch value seeks."""
    return bool(economics is not None and price is not None
                and economics >= quality_min and price >= cheap_min)


def rank(items):
    """Sort items by garp_score desc, adding garp_score + candidate. Each item is a
    dict with 'economics' and 'price'. Items missing either input are dropped. Pure."""
    out = []
    for it in items:
        g = garp_score(it.get("economics"), it.get("price"))
        if g is None:
            continue
        out.append({**it, "garp_score": g,
                    "candidate": is_candidate(it.get("economics"), it.get("price"))})
    out.sort(key=lambda x: -x["garp_score"])
    return out
