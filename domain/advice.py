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


# (matches upside %, headline, verdict, show margin of safety) — first match wins
_VALUE_BANDS = (
    (lambda u: u >= 40, "ราคาต่ำกว่า Fair Value มาก", "ส่วนเผื่อกว้าง", True),
    (lambda u: u >= 15, "ต่ำกว่า Fair Value", "มีส่วนเผื่อความปลอดภัย", True),
    (lambda u: u > -15, "ใกล้ Fair Value", "ราคาสมเหตุสมผล", False),
    (lambda u: u > -40, "สูงกว่า Fair Value", "จ่ายแพงกว่ามูลค่า", False),
    (lambda u: True, "แพงกว่า Fair Value มาก", "เสี่ยง de-rating", False),
)
_REV_EASE = {"Plausible": "อยู่ในวิสัยที่ทำได้", "Ambitious": "ท้าทาย",
             "Aggressive": "ตึงมาก", "Exceptional": "แทบเป็นไปไม่ได้"}
_EQ_TEXT = {"CLEAN": "งบไว้ใจได้ (EQ CLEAN)",
            "REVIEW": "งบมีจุดต้องตาม (EQ REVIEW)",
            "LOW": "คุณภาพกำไรต่ำ (EQ LOW) — อย่าเชื่อ EPS ตรง ๆ"}
_BULLISH = ("Strong", "Positive")
_BEARISH = ("Weak", "Negative")


def _value_clause(row, up, implied):
    if up is not None:
        net_up = _fnum(row.get("net_upside_pct"))
        basis = f" (net หลังภาษี/ค่าเทรด {net_up:+.0f}%)" if net_up is not None else ""
        _, head, verdict, show_mos = next(b for b in _VALUE_BANDS if b[0](up))
        # `upside_pct` is (V-P)/PRICE, the return if price converges. Damodaran's margin
        # of safety is (V-P)/VALUE, a smaller number, so it is stated separately.
        mos = (up / (100.0 + up) * 100.0) if up > -100 else None
        mos_txt = f", MoS {mos:+.0f}% ของมูลค่า" if (show_mos and mos is not None) else ""
        return f"มูลค่า: {head} ({up:+.0f}%{basis}{mos_txt}) — {verdict}"
    if implied is not None:
        actual = _fnum(row.get("rev_actual_1y"))
        cmp_txt = f" เทียบที่ทำได้จริงล่าสุด {actual:+.0f}%/ปี" if actual is not None else ""
        ease = _REV_EASE.get(row.get("rev_verdict"), "ประเมินยาก")
        # a profitable company lands here when FV inputs are missing, not because it
        # loses money, so "pre-profit" only when it really has no profit
        tagv = "ตีมูลค่าเป็นตัวเลขไม่ได้" if row.get("profitable") else "pre-profit"
        return f"มูลค่า ({tagv}): ตลาด price-in การเติบโต ~{implied:.0f}%/ปี 10 ปี{cmp_txt} — {ease}"
    return None


def _quality_clause(spread, eq):
    q = []
    if spread is not None:
        if spread >= 10:
            q.append(f"moat แข็งแรง (ROIC-WACC {spread:+.0f}pp)")
        elif spread >= 0:
            q.append(f"สร้างมูลค่าเหนือทุนเล็กน้อย (spread {spread:+.0f}pp)")
        else:
            q.append(f"ทำลายมูลค่า (ROIC ต่ำกว่า WACC {spread:.0f}pp)")
    if eq in tuple(_EQ_TEXT):              # tuple: == membership, so an unhashable eq is no error
        q.append(_EQ_TEXT[eq])
    return ("คุณภาพ: " + " · ".join(q)) if q else None


def _timing_clause(mom, crash):
    """Momentum only schedules the entry; it never overrides value (S9)."""
    if crash:
        return "จังหวะ: ตลาด RISK-OFF — S9 crash guard ห้ามไล่ราคาแม้สัญญาณบวก"
    if mom in _BULLISH:
        return "จังหวะ: momentum สนับสนุน — ทยอยเข้าได้"
    if mom in _BEARISH:
        return "จังหวะ: momentum อ่อน — ไม่ต้องรีบ ตั้งรอโซนล่างของ range"
    return None


def _action(good_value, bad_value, good_quality, good_mom, comp, has_parts):
    if good_value and good_quality and good_mom:
        return "ซื้อ/เพิ่มน้ำหนักแบบแบ่งไม้ — ถูก×ดี×จังหวะหนุน ครบทั้งสามเงื่อนไข"
    if good_value and good_quality:
        return "ทยอยสะสม (DCA) — มูลค่ากับคุณภาพผ่าน แต่ใช้ momentum เลือกจังหวะเข้า"
    if good_value:
        return "ถูกแต่คุณภาพไม่ผ่าน — รอหลักฐานงบดีขึ้นก่อน (value trap risk)"
    if bad_value and comp is not None and comp >= 3.5:
        return "ธุรกิจดีแต่ราคาแพง — ถือของเดิม ไม่เพิ่ม รอ pullback เข้าโซน Fair Value"
    if bad_value:
        return "แพงและพื้นฐานไม่เด่น — พิจารณาลดน้ำหนัก/ไม่เข้าใหม่"
    if comp is not None and comp >= 3:
        return "ถือ — พื้นฐานผ่านเกณฑ์ รอราคาหรือข้อมูลใหม่ก่อนขยับ"
    if has_parts:
        return "ถือ/รอข้อมูลเพิ่ม — สัญญาณยังไม่ชัดพอจะขยับ"
    return None


def _build(row):
    up = _fnum(row.get("upside_pct"))
    implied = _fnum(row.get("rev_implied_cagr"))
    spread = _fnum((row.get("key_metrics") or {}).get("spread_pct"))
    eq = row.get("eq_verdict")
    v2 = row.get("momentum_v2") or {}
    mom = v2.get("mom_label")
    crash = v2.get("crash_guard") is True
    rev_verdict = row.get("rev_verdict")

    parts = [c for c in (_value_clause(row, up, implied), _quality_clause(spread, eq),
                         _timing_clause(mom, crash)) if c]
    act = _action(
        good_value=(up is not None and up >= 15) or (implied is not None and rev_verdict == "Plausible"),
        bad_value=(up is not None and up <= -15) or (rev_verdict in ("Aggressive", "Exceptional")),
        good_quality=(spread or 0) >= 0 and eq != "LOW",
        good_mom=mom in _BULLISH and not crash,
        comp=_fnum(row.get("composite")),
        has_parts=bool(parts))
    if act is None:
        return ""
    return " | ".join(parts + ["Action: " + act])
