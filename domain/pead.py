"""
Damodaran S25 (Information Trading — Earnings Reports / PEAD): after an earnings
surprise, prices tend to DRIFT in the direction of the surprise for weeks
(markets learn slowly). Reads the latest stored EPS (+ optional revenue) surprise
and returns a forward drift BIAS. Pure; never fabricates.
"""


def signal(eps_surprises, rev_surprises=None):
    """Latest-surprise drift bias. eps_surprises / rev_surprises are oldest->newest
    lists of {grade, surprise_pct, quarter}. Returns
        {bias: 'up'|'down'|'neutral'|None, strength, eps_grade, eps_surprise_pct,
         rev_grade, quarter, note}
    bias=None when there is no surprise on record (never invents one)."""
    if not eps_surprises:
        return {"bias": None}
    last = eps_surprises[-1]                       # list is oldest -> newest
    grade = last.get("grade")
    spct = last.get("surprise_pct")
    rev_grade = rev_surprises[-1].get("grade") if rev_surprises else None

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
