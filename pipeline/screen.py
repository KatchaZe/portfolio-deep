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

    P3-3: names missing either axis used to be DROPPED here, silently. A screen whose
    job is to say "look at these first" cannot quietly shorten the universe it screened
    — the reader has no way to tell "not attractive" from "not shown". On the committed
    portfolio that hid 6 of 21 names, including the one with the HIGHEST quality score
    in the book (TSM, E_econ 5.0, no fair value because the forward EPS was missing).
    A name that scores 5/5 on quality and is invisible on the quality screen is the
    worst possible failure mode for this page.

    They are now kept, with `garp_score=None`, `candidate=False` and an explicit
    `unscreenable` reason, sorted last. Callers that plot points (the quadrant chart)
    already filter on economics/price being present, so nothing gets plotted at a
    made-up coordinate."""
    scored, unscored = [], []
    for it in items:
        econ, price = it.get("economics"), it.get("price")
        g = garp_score(econ, price)
        row = {**it, "garp_score": g, "candidate": is_candidate(econ, price),
               "unscreenable": unscreenable_reason(econ, price)}
        (scored if g is not None else unscored).append(row)
    # ties are common (both inputs are 0-5 half-point subscores, so 7.0 comes up a
    # lot); without a tie-break the order fell out of dict insertion order and the
    # table reshuffled between refreshes for no reason the reader could see.
    scored.sort(key=lambda x: (-x["garp_score"], x.get("ticker") or ""))
    unscored.sort(key=lambda x: x.get("ticker") or "")
    return scored + unscored
