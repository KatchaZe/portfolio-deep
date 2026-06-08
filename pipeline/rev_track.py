"""
rev_track — build a revenue beat/miss history *forward* in time.

Yahoo gives revenue estimates only for the CURRENT (not-yet-reported) quarter,
not historically. So each refresh we snapshot that estimate keyed by the quarter
end; when the SEC actual for that quarter later appears, we grade it (beat/meet/
miss) and append to a rolling 4-quarter history. Pure + side-effect-only-on-`s`,
so it unit-tests without any network.

Store shape:
  s["rev_snapshots"][TICKER] = { "2026-07-31": {"est": 1.23e10, "captured": "2026-06-08"} }
  s["rev_surprises"][TICKER] = [ {quarter, rev_actual, rev_estimate, surprise_pct, grade}, ... ]  # oldest->newest, <=4
"""

THRESHOLD = 2.0   # percent; |surprise| <= 2% == "meet"


def grade(surprise_pct):
    if surprise_pct is None:
        return None
    if surprise_pct > THRESHOLD:
        return "beat"
    if surprise_pct < -THRESHOLD:
        return "miss"
    return "meet"


def update(s, ticker, rev_estimate_curq, revenue_quarters, today_str):
    """Snapshot the current-quarter estimate, then grade any snapshot whose SEC
    actual is now available. Returns the ticker's rolling history (also stored)."""
    t = ticker.upper().strip()
    snaps = s.setdefault("rev_snapshots", {}).setdefault(t, {})
    hist = s.setdefault("rev_surprises", {}).setdefault(t, [])
    graded_qs = {x["quarter"] for x in hist}

    # 1) snapshot / refresh the estimate for the quarter about to report
    ce = rev_estimate_curq or {}
    qe, est = ce.get("quarter_end"), ce.get("estimate")
    if qe and est and qe not in graded_qs:
        snaps[qe] = {"est": float(est), "captured": today_str}

    # 2) grade snapshots that now have an actual
    actuals = revenue_quarters or {}
    for q in sorted(snaps.keys()):
        if q in graded_qs:
            del snaps[q]
            continue
        act = actuals.get(q)
        est = snaps[q].get("est")
        if act is not None and est:
            sp = (act - est) / abs(est) * 100
            hist.append({"quarter": q, "rev_actual": act, "rev_estimate": est,
                         "surprise_pct": round(sp, 1), "grade": grade(sp)})
            del snaps[q]

    hist.sort(key=lambda x: x["quarter"])
    s["rev_surprises"][t] = hist[-4:]
    return s["rev_surprises"][t]
