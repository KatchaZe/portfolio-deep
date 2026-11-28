"""Primary fair value far from the other methods: a display flag, never a number (E104, E108).

The primary FV is compared with the median of the other methods that can speak to it:
PEG and FV/P (not for a young-company primary, whose near-zero earnings make an
earnings multiple meaningless: HIMS $36.90 vs a $4.29 PEG), the peer relative value
(not when its regression is the code's own weak fit or its leave-one-out error is above
FV_OUTLIER_MAX_LOO: AVGO r2 0.007, loo 0.56) and, for a cyclical, the cycle cross-checks.
With at least FV_OUTLIER_MIN_METHODS left, the row is flagged when the FV sits more than
FV_OUTLIER_RATIO from their median in log terms, either way (the old +/-40% flagged a
high FV at 1.40x but a low one only at 1/1.67x), AND at least FV_OUTLIER_MIN_METHODS of
them lie beyond the same ratio on the same side: one stray method is not a consensus.
Pure: reads and annotates a Valuation, changes no score or value.
"""
import math
import statistics

FV_OUTLIER_RATIO = 1.5
FV_OUTLIER_MIN_METHODS = 2          # one other method is a pair, not a consensus of methods
FV_OUTLIER_MAX_LOO = 0.40           # a relative fit missing its own peers by more is not a method
FV_OUTLIER_FLAG = "ค่าหลักต่างจากวิธีเทียบอื่นมาก ตรวจสมมติฐาน"


def _relative(rel):
    """The relative value the flag may read, or None when its fit is weak."""
    model = rel.get("model") or {}
    if model.get("weak") or (model.get("loo_median_err") or 0) > FV_OUTLIER_MAX_LOO:
        return None
    return rel.get("value_regression") or rel.get("value_median")


def comparators(v):
    """[(label, value)] of the other methods with a positive value that can speak to
    this primary."""
    young = str(getattr(v, "anchor_method", "") or "").startswith("Young-Company")
    rel = getattr(v, "relative", None) or {}
    out = [] if young else [("PEG", getattr(v, "fv_peg", None)), ("FV/P", getattr(v, "fv_fvp", None))]
    out.append(("relative", _relative(rel)))
    out += [(c.get("name"), c.get("value"))
            for c in ((getattr(v, "scenarios", None) or {}).get("cycle_checks") or [])]
    return [(k, x) for k, x in out if isinstance(x, (int, float)) and x > 0]


def assess(v):
    """{flag, gap_pct, median, methods, beyond} or None when fewer than
    FV_OUTLIER_MIN_METHODS can be compared."""
    fv = getattr(v, "anchor_value", None)
    others = comparators(v)
    if not fv or fv <= 0 or len(others) < FV_OUTLIER_MIN_METHODS:
        return None
    med = statistics.median(x for _, x in others)
    d, edge = math.log(fv / med), math.log(FV_OUTLIER_RATIO)
    beyond = [k for k, x in others if math.log(fv / x) * (1 if d >= 0 else -1) > edge]
    return {"flag": abs(d) > edge and len(beyond) >= FV_OUTLIER_MIN_METHODS,
            "gap_pct": round((fv / med - 1) * 100, 1), "median": round(med, 2),
            "methods": [k for k, _ in others], "beyond": beyond}


def mark(v):
    """Set key_metrics['fv_outlier'] and the flag; idempotent (re-run after the peer
    relative block arrives)."""
    v.flags = [f for f in (v.flags or []) if not f.startswith(FV_OUTLIER_FLAG)]
    out = assess(v)
    if v.key_metrics is None:
        v.key_metrics = {}
    v.key_metrics["fv_outlier"] = out
    if out and out["flag"]:
        v.flags.append(f"{FV_OUTLIER_FLAG}: FV ${v.anchor_value:,.2f} vs median ${out['median']:,.2f} "
                       f"of {', '.join(out['methods'])} ({out['gap_pct']:+.1f}%; {', '.join(out['beyond'])} "
                       f"ต่างเกิน {FV_OUTLIER_RATIO}x ด้านเดียวกัน)")
    return v
