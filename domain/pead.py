"""
Damodaran S25 (Information Trading — Earnings Reports / PEAD): after an earnings
surprise, prices tend to DRIFT in the direction of the surprise for weeks
(markets learn slowly). Reads the latest stored EPS (+ optional revenue) surprise
and returns a forward drift BIAS. Pure; never fabricates.
"""


def chronological(rows):
    """Surprise rows sorted OLDEST -> NEWEST by their own `quarter` date.

    B6: every consumer of these lists took `[-1]` as "the latest quarter", trusting
    list ORDER to encode chronology. Most sources deliver oldest-first, but the Yahoo
    path does not, and on the committed portfolio TSLA and REGN — both Yahoo-primary —
    carried [2026-03-31, 2025-12-31, 2025-09-30, 2025-06-30]. `[-1]` therefore read a
    quarter NINE MONTHS stale as the newest one, so the PEAD drift signal, the
    source-reconciliation tie-break and the earnings circles were all describing the
    wrong quarter. The rows carry the date; nothing has to be inferred from position.
    Same lesson as P3-1: when a value has a date on it, join on the date.

    Rows with no usable quarter keep their relative order and sort last, since the only
    honest guess about an undated row is that it is not the newest."""
    rows = [r for r in (rows or []) if isinstance(r, dict)]
    dated = [r for r in rows if isinstance(r.get("quarter"), str) and r["quarter"]]
    undated = [r for r in rows if r not in dated]
    return sorted(dated, key=lambda r: r["quarter"]) + undated


def latest(rows):
    """The most recent surprise row by date, or None."""
    ordered = chronological(rows)
    return ordered[-1] if ordered else None


def signal(eps_surprises, rev_surprises=None):
    """Latest-surprise drift bias. eps_surprises / rev_surprises are lists of
    {grade, surprise_pct, quarter} in ANY order — the newest is picked by date, not by
    position (see `chronological`). Returns
        {bias: 'up'|'down'|'neutral'|None, strength, eps_grade, eps_surprise_pct,
         rev_grade, quarter, note}
    bias=None when there is no surprise on record (never invents one)."""
    if not eps_surprises:
        return {"bias": None}
    last = latest(eps_surprises) or {}
    grade = last.get("grade")
    spct = last.get("surprise_pct")
    rev_grade = (latest(rev_surprises) or {}).get("grade") if rev_surprises else None

    bias = {"beat": "up", "miss": "down"}.get(grade, "neutral")
    strong = isinstance(spct, (int, float)) and abs(spct) >= 10
    mixed = (grade == "beat" and rev_grade == "miss") or (grade == "miss" and rev_grade == "beat")
    if mixed:
        note = "EPS/revenue ขัดกัน — สัญญาณ drift อ่อน"
    elif bias == "up":
        note = "EPS beat → มักมี drift ขึ้นต่อหลังประกาศ (PEAD)"
    elif bias == "down":
        note = "EPS miss → มักมี drift ลงต่อหลังประกาศ (PEAD)"
    else:
        note = "ตรงคาด — ไม่มี drift เด่น"
    return {"bias": bias, "strength": "mild" if (mixed or not strong) else "strong",
            "eps_grade": grade, "eps_surprise_pct": spct, "rev_grade": rev_grade,
            "quarter": last.get("quarter"), "note": note}
