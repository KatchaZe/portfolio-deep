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


def unscreenable_reason(economics, price):
    """Why a name cannot be placed on the cheap x quality map. None when it can."""
    miss = []
    if economics is None:
        miss.append("quality (ROIC-WACC unmeasurable)")
    if price is None:
        miss.append("cheap (no point fair value)")
    return " + ".join(miss) if miss else None


def rank(items):
    """Sort items by garp_score desc, adding garp_score + candidate. Each item is a
    dict with 'economics' and 'price'. Pure.

    Names missing either axis are KEPT, with garp_score=None, candidate=False and an
    `unscreenable` reason, sorted last: a screen that silently drops names cannot be
    told apart from one that found them unattractive (it once hid the book's
    highest-quality name). Plotting callers already filter on both axes."""
    scored, unscored = [], []
    for it in items:
        econ, price = it.get("economics"), it.get("price")
        g = garp_score(econ, price)
        row = {**it, "garp_score": g, "candidate": is_candidate(econ, price),
               "unscreenable": unscreenable_reason(econ, price)}
        (scored if g is not None else unscored).append(row)
    # ties are common (both inputs are half-point subscores), so break them by
    # ticker or the table reshuffles between refreshes
    scored.sort(key=lambda x: (-x["garp_score"], x.get("ticker") or ""))
    unscored.sort(key=lambda x: x.get("ticker") or "")
    return scored + unscored
