"""
Fair-value uncertainty rating -> the margin of safety the Price pillar asks for.

Morningstar's method: the wider the range of plausible values around the fair value,
the deeper the discount needed before a stock counts as cheap (and the higher the
premium before it counts as dear). Its published 5-star / 1-star thresholds, as
price / fair value:

    Low        5-star <= 0.80   1-star > 1.25
    Medium     5-star <= 0.70   1-star > 1.35
    High       5-star <= 0.60   1-star > 1.55
    Very High  5-star <= 0.50   1-star > 1.75
    Extreme    5-star <= 0.25   1-star > 4.00

The inner 4-star / 2-star edges are Morningstar's own for Low (0.95 / 1.05) and Medium
(0.90 / 1.10); for High and up they are interpolated here [JUDG].

The width of the range is measured on the anchor's own story Monte Carlo,
(p90 - p10) / p50; failing that, the young-company band, then Bear / Base / Bull.
The width cut-offs are [JUDG], set on the five fixture names (MSFT 0.31, NVO 0.39,
AVGO 0.53, ABBV 0.56, ORCL 1.07). A name with no range keeps the app's
original bands ("Unrated").

Pure: no I/O.
"""

# rating -> (MoS for score 5, 4, 3, 2), MoS = (FV - P) / FV; below the last -> 1
BANDS = {
    "Low":       (0.20, 0.05, -0.05, -0.25),
    "Medium":    (0.30, 0.10, -0.10, -0.35),
    "High":      (0.40, 0.15, -0.15, -0.55),
    "Very High": (0.50, 0.20, -0.20, -0.75),
    "Extreme":   (0.75, 0.40, -0.40, -3.00),
    "Unrated":   (0.40, 0.15, -0.15, -0.40),
}
# (upper width, rating), widths are (p90 - p10) / p50
WIDTH_CUTS = ((0.40, "Low"), (0.70, "Medium"), (1.10, "High"), (1.60, "Very High"))


def _width(lo, mid, hi):
    if lo is None or mid is None or hi is None or mid <= 0:
        return None
    return max(0.0, (hi - lo) / mid)


def measure(scenarios, young_dcf=None):
    """(width, basis) of the anchor's value range, or (None, None)."""
    mc = (scenarios or {}).get("story_mc") or {}
    w = _width(mc.get("p10"), mc.get("p50"), mc.get("p90"))
    if w is not None:
        return w, "story Monte Carlo (p90 - p10) / p50"
    ymc = (young_dcf or {}).get("monte_carlo") or {}
    w = _width(ymc.get("p10"), ymc.get("p50"), ymc.get("p90"))
    if w is not None:
        return w, "young-company Monte Carlo (p90 - p10) / p50"
    cases = [c.get("value") for c in (scenarios or {}).get("cases") or []]
    if len(cases) == 3:
        w = _width(min(cases), cases[1], max(cases))
        if w is not None:
            return w, "Bear / Base / Bull (Bull - Bear) / Base"
    return None, None


def rating(width):
    if width is None:
        return "Unrated"
    return next((r for cut, r in WIDTH_CUTS if width < cut), "Extreme")


def assess(scenarios, young_dcf=None):
    """The uncertainty block a Valuation carries."""
    width, basis = measure(scenarios, young_dcf)
    r = rating(width)
    b = BANDS[r]
    return {"rating": r,
            "width": round(width, 3) if width is not None else None,
            "basis": basis,
            "bands_pct": [round(x * 100, 1) for x in b],
            "add_min_mos": b[1],
            "tag": "[JUDG] Morningstar uncertainty bands; width cut-offs set on fixtures"}


def bands(block):
    """MoS bands (decimals) for an uncertainty block, the original bands without one."""
    return BANDS.get((block or {}).get("rating") or "Unrated", BANDS["Unrated"])
