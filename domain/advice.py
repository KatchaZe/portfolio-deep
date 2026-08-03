"""
advice — Damodaran-style ACTION synthesis for one display row (Thai).

Combines every signal already shown on the stock card into one actionable
recommendation, following the framework's hierarchy:
  1. VALUE first  (upside vs anchor FV; pre-profit -> RevDCF expectations check)
  2. QUALITY      (ROIC-WACC spread = moat; earnings quality = trust the numbers)
  3. TIMING       (momentum composite as entry timing, S9 crash guard as veto)
Numbers + Narrative: price is what you pay, value is what you get (S13/S19);
momentum only schedules the entry, it never overrides value (S9).

Pure function on the row dict — no network, no store. Unit-testable offline.
"""


def _fnum(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def build(row):
    """row: a display-row dict (portfolio_view / analyze_row). Returns a short
    Thai action paragraph, or "" when there is not enough data to say anything."""
    try:
        return _build(row)
    except Exception:                      # advice must NEVER break a row
        return ""


def _build(row):
    km = row.get("key_metrics") or {}
    up = _fnum(row.get("upside_pct"))
    net_up = _fnum(row.get("net_upside_pct"))
    spread = _fnum(km.get("spread_pct"))
    eq = row.get("eq_verdict")
    v2 = row.get("momentum_v2") or {}
    mom = v2.get("mom_label")
    crash = v2.get("crash_guard") is True
    rev_verdict = row.get("rev_verdict")
    implied = _fnum(row.get("rev_implied_cagr"))
    actual = _fnum(row.get("rev_actual_1y"))
    comp = _fnum(row.get("composite"))

    parts = []

    # ---- 1) VALUE ----------------------------------------------------------
    if up is not None:
        basis = f" (net หลังภาษี/ค่าเทรด {net_up:+.0f}%)" if net_up is not None else ""
        if up >= 40:
            parts.append(f"มูลค่า: ราคาต่ำกว่า Fair Value มาก ({up:+.0f}%{basis}) — margin of safety กว้าง")
        elif up >= 15:
            parts.append(f"มูลค่า: ต่ำกว่า Fair Value ({up:+.0f}%{basis}) — มี margin of safety")
        elif up > -15:
            parts.append(f"มูลค่า: ใกล้ Fair Value ({up:+.0f}%{basis}) — ราคาสมเหตุสมผล")
        elif up > -40:
            parts.append(f"มูลค่า: สูงกว่า Fair Value ({up:+.0f}%{basis}) — จ่ายแพงกว่ามูลค่า")
        else:
            parts.append(f"มูลค่า: แพงกว่า Fair Value มาก ({up:+.0f}%{basis}) — เสี่ยง de-rating")
    elif implied is not None:
        cmp_txt = f" เทียบที่ทำได้จริงล่าสุด {actual:+.0f}%/ปี" if actual is not None else ""
        ease = {"Plausible": "อยู่ในวิสัยที่ทำได้", "Ambitious": "ท้าทาย",
                "Aggressive": "ตึงมาก", "Exceptional": "แทบเป็นไปไม่ได้"}.get(rev_verdict, "ประเมินยาก")
        # P-C: "pre-profit" only when the company really has no profit; a profitable
        # company lands here when FV inputs are missing, not because it loses money.
        tagv = "ตีมูลค่าเป็นตัวเลขไม่ได้" if row.get("profitable") else "pre-profit"
        parts.append(f"มูลค่า ({tagv}): ตลาด price-in การเติบโต ~{implied:.0f}%/ปี 10 ปี{cmp_txt} — {ease}")

    # ---- 2) QUALITY --------------------------------------------------------
    q = []
    if spread is not None:
        if spread >= 10:
            q.append(f"moat แข็งแรง (ROIC-WACC {spread:+.0f}pp)")
        elif spread >= 0:
            q.append(f"สร้างมูลค่าเหนือทุนเล็กน้อย (spread {spread:+.0f}pp)")
        else:
            q.append(f"ทำลายมูลค่า (ROIC ต่ำกว่า WACC {spread:.0f}pp)")
    if eq == "CLEAN":
        q.append("งบไว้ใจได้ (EQ CLEAN)")
    elif eq == "REVIEW":
        q.append("งบมีจุดต้องตาม (EQ REVIEW)")
    elif eq == "LOW":
        q.append("คุณภาพกำไรต่ำ (EQ LOW) — อย่าเชื่อ EPS ตรง ๆ")
    if q:
        parts.append("คุณภาพ: " + " · ".join(q))

    # ---- 3) TIMING (momentum never overrides value — S9) -------------------
    if crash:
        parts.append("จังหวะ: ตลาด RISK-OFF — S9 crash guard ห้ามไล่ราคาแม้สัญญาณบวก")
    elif mom in ("Strong", "Positive"):
        parts.append("จังหวะ: momentum สนับสนุน — ทยอยเข้าได้")
    elif mom in ("Weak", "Negative"):
        parts.append("จังหวะ: momentum อ่อน — ไม่ต้องรีบ ตั้งรอโซนล่างของ range")

    # ---- 4) ACTION (synthesis) ---------------------------------------------
    good_value = (up is not None and up >= 15) or (implied is not None and rev_verdict == "Plausible")
    bad_value = (up is not None and up <= -15) or (rev_verdict in ("Aggressive", "Exceptional"))
    good_quality = (spread or 0) >= 0 and eq != "LOW"
    good_mom = mom in ("Strong", "Positive") and not crash

    if good_value and good_quality and good_mom:
        act = "ซื้อ/เพิ่มน้ำหนักแบบแบ่งไม้ — ถูก×ดี×จังหวะหนุน ครบทั้งสามเงื่อนไข"
    elif good_value and good_quality:
        act = "ทยอยสะสม (DCA) — มูลค่ากับคุณภาพผ่าน แต่ใช้ momentum เลือกจังหวะเข้า"
    elif good_value and not good_quality:
        act = "ถูกแต่คุณภาพไม่ผ่าน — รอหลักฐานงบดีขึ้นก่อน (value trap risk)"
    elif bad_value and comp is not None and comp >= 3.5:
        act = "ธุรกิจดีแต่ราคาแพง — ถือของเดิม ไม่เพิ่ม รอ pullback เข้าโซน Fair Value"
    elif bad_value:
        act = "แพงและพื้นฐานไม่เด่น — พิจารณาลดน้ำหนัก/ไม่เข้าใหม่"
    elif comp is not None and comp >= 3:
        act = "ถือ — พื้นฐานผ่านเกณฑ์ รอราคาหรือข้อมูลใหม่ก่อนขยับ"
    elif parts:
        act = "ถือ/รอข้อมูลเพิ่ม — สัญญาณยังไม่ชัดพอจะขยับ"
    else:
        return ""
    parts.append("Action: " + act)
    return " | ".join(parts)
