"""
Per-PORTFOLIO diversification philosophy (Damodaran S2–4 · S35–41) — the narrative
'comment' layer for the Correlation tab.

Pure & deterministic: it consumes numbers already computed by app._build_risk
(ENB, Effective N, crisis ENB, intra-sector corr, benchmark corr, downside corr)
and returns a 3-tier payload: `gauge` + 6 `pillars` + Thai `story_normal` /
`story_crisis`. The frontend only presents it (no recomputation).

Sibling of domain/philosophy.py (S1/S42 philosophy-FIT). This does NOT replace the
per-STOCK VALUE→QUALITY→TIMING advice in domain/advice.py shown on the Allocation
tab — the two layers are complementary (per-stock vs per-portfolio).
"""


def _band(x, good, warn):
    """Map a ratio to a (css_class, thai_label) verdict."""
    if x is None:
        return ("muted", "—")
    if x >= good:
        return ("good", "ดี")
    if x >= warn:
        return ("warn", "เฝ้าระวัง")
    return ("bad", "กระจุก")


def diversification_philosophy(*, n_holdings, enb, enb_crisis, eff_n,
                               top_sector=None, top_sector_wt=None,
                               top_sector_corr=None, avg_pairwise=None,
                               avg_pairwise_crisis=None, bench_nasdaq_corr=None,
                               downside_corr=None, top_risk_driver=None):
    """Build the per-portfolio Damodaran philosophy block. All inputs are already
    computed upstream; this only synthesises the verdict + narrative. Pure."""
    ratio = (enb / eff_n) if (enb and eff_n) else None
    r_cls, r_lbl = _band(ratio, 0.7, 0.5)
    # ΔENB from normal → crisis. Positive = diversification LOST (the normal case:
    # correlations rise, ENB falls). Negative would mean ENB rose in the crisis —
    # mathematically impossible for a correct ENB, so we word it honestly instead of
    # printing "หาย ~-35%" (guard added 2026-08-16 after that exact bug shipped).
    frag_pct = round((1 - enb_crisis / enb) * 100) if (enb and enb_crisis) else None
    frag_txt = None
    if frag_pct is not None:
        if frag_pct >= 0:
            frag_txt = f"การกระจายหาย ~{frag_pct}% ตอนวิกฤต"
        else:
            frag_txt = (f"⚠ ENB เพิ่มขึ้น {abs(frag_pct)}% ตอนวิกฤต — ผิดปกติ "
                        f"(ENB ต้องลดลงเสมอเมื่อ correlation สูงขึ้น) ตรวจสอบการคำนวณ")
    r2 = round(bench_nasdaq_corr ** 2, 2) if bench_nasdaq_corr is not None else None
    bets = round(enb) if enb else n_holdings

    gauge = {
        "ratio": round(ratio, 2) if ratio else None,
        "ratio_class": r_cls, "ratio_label": r_lbl,
        "enb": enb, "enb_crisis": enb_crisis, "eff_n": eff_n,
        "fragility_pct": frag_pct, "fragility_text": frag_txt,
        "avg_pairwise": avg_pairwise,
        "avg_pairwise_crisis": avg_pairwise_crisis, "downside_corr": downside_corr,
        "r2_nasdaq": r2,
    }

    sec_txt = ""
    if top_sector:
        sec_txt = f"{top_sector} {round(top_sector_wt)}%" if top_sector_wt is not None else top_sector
        if top_sector_corr is not None:
            sec_txt += f" · intra {top_sector_corr}"

    pillars = [
        {"name": "โครงสร้าง", "s": "S4",
         "value": (f"ถือ {n_holdings} · ENB {enb} → ~{bets} เดิมพัน" if enb else f"ถือ {n_holdings}"),
         "note": "จำนวนตัว ≠ กระจาย"},
        {"name": "ตัวขับความเสี่ยง", "s": "S2",
         "value": sec_txt or "—",
         "note": ("marginal risk หลัก" + (f" · {top_risk_driver}" if top_risk_driver else ""))},
        {"name": "ความเปราะ", "s": "S2 crisis",
         "value": (f"ENB {enb}→{enb_crisis}" if (enb and enb_crisis) else "—"),
         "note": (frag_txt or "corr วิ่งเข้า 1")},
        {"name": "ตัวถ่วง", "s": "S38–41",
         "value": "Gold/BTC = pricing asset", "note": "ถ่วงจริงต้องดูตอน Crisis"},
        {"name": "Beta honesty", "s": "S35–36",
         "value": (f"R² vs Nasdaq {r2}" if r2 is not None else "—"),
         "note": ("แทบเป็น index" if (r2 or 0) >= 0.7 else "ยังมี active จริง")},
        {"name": "Action", "s": "S6",
         "value": "trim คู่ corr สูง · เติม low-corr", "note": "สุทธิหลังต้นทุน/ภาษี"},
    ]

    driver = f" ก้อน {sec_txt}" if sec_txt else ""
    story_normal = (
        f"พอร์ตถือ {n_holdings} ตัวแต่มีเดิมพันอิสระจริง ~{bets} ก้อน — "
        f"เพราะ{driver or 'การกระจุกตัว'} คือตัวขับความเสี่ยงหลัก (S2/S4 marginal). "
        + (f"กด Crisis แล้ว ENB ร่วง {enb}→{enb_crisis} ({frag_txt}). "
           if (frag_pct is not None and frag_pct >= 0) else
           (f"กด Crisis แล้ว ENB {enb}→{enb_crisis} — {frag_txt}. "
            if frag_pct is not None else ""))
        + (("พอร์ต corr กับ Nasdaq " + f"{bench_nasdaq_corr} (R² {r2}) = "
            + ("แทบถือ index (S35–36). " if (r2 or 0) >= 0.7 else "ยังมี active จริง (S35–36). "))
           if r2 is not None else "")
        + "Action: trim คู่ corr สูง + เติม sleeve corr ต่ำ (Healthcare/Energy/Gold) "
          "เพื่อดัน ENB ขึ้น — สุทธิหลังหักภาษี/ค่าธรรมเนียม (S6)."
    )
    story_crisis = (
        f"โหมด Crisis: การกระจายเกือบหาย พอร์ตยุบเหลือ ~{round(enb_crisis) if enb_crisis else '—'} "
        f"เดิมพันอิสระ ({top_sector or 'ก้อนใหญ่'} กลืนทุกอย่าง เพราะ correlation วิ่งเข้า ~1). "
        + (f"avg corr {avg_pairwise}→{avg_pairwise_crisis}. " if (avg_pairwise is not None
           and avg_pairwise_crisis is not None) else "")
        + "เฉพาะ Gold ที่ยังต่ำเท่านั้นที่ถ่วงได้จริง — BTC เป็น equity-beta ตอนวิกฤต (S38–41)."
    )
    return {"gauge": gauge, "pillars": pillars,
            "story_normal": story_normal, "story_crisis": story_crisis}
