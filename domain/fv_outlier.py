"""Primary fair value far from the other methods: a display flag, never a number (E104).

The primary FV is compared with the median of every other method the row carries: PEG,
FV/P, the peer relative value (regression, else peer median) and, for a cyclical, the
cycle cross-checks. More than FV_OUTLIER_GAP away either way flags the row and badges the
Fair Value cell. Pure: reads and annotates a Valuation, changes no score or value.
"""
import statistics

FV_OUTLIER_GAP = 0.40
FV_OUTLIER_MIN_METHODS = 2          # one other method is a pair, not a consensus of methods
FV_OUTLIER_FLAG = "ค่าหลักต่างจากวิธีเทียบอื่นมาก ตรวจสมมติฐาน"


def comparators(v):
    """[(label, value)] of the other methods with a positive value."""
    rel = getattr(v, "relative", None) or {}
    out = [("PEG", getattr(v, "fv_peg", None)), ("FV/P", getattr(v, "fv_fvp", None)),
           ("relative", rel.get("value_regression") or rel.get("value_median"))]
    out += [(c.get("name"), c.get("value"))
            for c in ((getattr(v, "scenarios", None) or {}).get("cycle_checks") or [])]
    return [(k, x) for k, x in out if isinstance(x, (int, float)) and x > 0]


def assess(v):
    """{flag, gap_pct, median, methods} or None when there is nothing to compare."""
    fv = getattr(v, "anchor_value", None)
    others = comparators(v)
    if not fv or fv <= 0 or len(others) < FV_OUTLIER_MIN_METHODS:
        return None
    med = statistics.median(x for _, x in others)
    gap = fv / med - 1
    return {"flag": abs(gap) > FV_OUTLIER_GAP, "gap_pct": round(gap * 100, 1),
            "median": round(med, 2), "methods": [k for k, _ in others]}


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
                       f"of {', '.join(out['methods'])} ({out['gap_pct']:+.1f}%, เกิน "
                       f"±{FV_OUTLIER_GAP*100:.0f}%)")
    return v
