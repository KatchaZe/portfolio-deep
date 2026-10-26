"""
Per-PORTFOLIO diversification philosophy (Damodaran S2–4 · S35–41) — the narrative
'comment' layer for the Correlation tab.

Pure & deterministic: it consumes numbers already computed by pipeline.risk_report
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


def _fragility(enb, enb_crisis, measured=False):
    """(pct, text) for the ENB lost from normal to crisis. Under the ASSUMED floor ENB
    cannot rise, so a rise is worded as a calculation warning. A MEASURED crisis can
    genuinely show this book moving together less in that episode; that is reported as
    a finding about the episode, not a bug."""
    if not (enb and enb_crisis):
        return None, None
    pct = round((1 - enb_crisis / enb) * 100)
    if pct >= 0:
        return pct, f"การกระจายหาย ~{pct}% ตอนวิกฤต"
    if measured:
        return pct, (f"ENB สูงขึ้น {abs(pct)}% ในช่วงวิกฤตที่วัด — พอร์ตนี้วิ่งไปด้วยกันน้อยกว่าปกติ"
                     f"ในช่วงนั้น (ช่วงเดียว ไม่ใช่กฎ)")
    return pct, (f"⚠ ENB เพิ่มขึ้น {abs(pct)}% ตอนวิกฤต — ผิดปกติ "
                 f"(ENB ต้องลดลงเสมอเมื่อ correlation สูงขึ้น) ตรวจสอบการคำนวณ")


def _sector_text(top_sector, top_sector_wt, top_sector_corr):
    if not top_sector:
        return ""
    txt = f"{top_sector} {round(top_sector_wt)}%" if top_sector_wt is not None else top_sector
    if top_sector_corr is not None:
        txt += f" · intra {top_sector_corr}"
    return txt


def _index_like(r2):
    return (r2 or 0) >= 0.7


BALLAST_MAX_CORR = 0.30        # a ballast stays below this crisis correlation with the book


def _ballast_text(ballast, refs_crisis):
    """The low-crisis-correlation names, from the numbers: the held name least tied to
    the rest in the crisis, and the yardsticks (Gold, Bitcoin) as measured."""
    bits = []
    if ballast and ballast[1] is not None:
        t, c = ballast
        bits.append(f"{t} crisis corr {c:.2f}" + (" (ถ่วงได้)" if c < BALLAST_MAX_CORR else " (ยังสูง)"))
    for sym, name in (("GLD", "Gold"), ("IBIT", "BTC")):
        v = (refs_crisis or {}).get(sym)
        if v is not None:
            bits.append(f"{name} {v:.2f}")
    return " · ".join(bits) or "—"


def _pillars(n_holdings, enb, enb_crisis, bets, sec_txt, top_risk_driver, frag_txt, r2,
             ballast=None, refs_crisis=None, crisis_txt=None):
    return [
        {"name": "โครงสร้าง", "s": "S4",
         "value": (f"ถือ {n_holdings} · ENB {enb} → ~{bets} เดิมพัน" if enb else f"ถือ {n_holdings}"),
         "note": "จำนวนตัว ≠ กระจาย"},
        {"name": "ตัวขับความเสี่ยง", "s": "S2",
         "value": sec_txt or "—",
         "note": ("marginal risk หลัก" + (f" · {top_risk_driver}" if top_risk_driver else ""))},
        {"name": "ความเปราะ", "s": "S2 crisis",
         "value": (f"ENB {enb}→{enb_crisis}" if (enb and enb_crisis) else "—"),
         "note": (frag_txt or "—") + (f" · {crisis_txt}" if crisis_txt else "")},
        {"name": "ตัวถ่วง", "s": "S38–41",
         "value": _ballast_text(ballast, refs_crisis),
         "note": f"ถ่วงจริง = crisis corr < {BALLAST_MAX_CORR:.2f} (วัดในช่วงวิกฤต ไม่ใช่ค่าเฉลี่ยปกติ)"},
        {"name": "Beta honesty", "s": "S35–36",
         "value": (f"R² vs Nasdaq {r2}" if r2 is not None else "—"),
         "note": ("แทบเป็น index" if _index_like(r2) else "ยังมี active จริง")},
        {"name": "Action", "s": "S6",
         "value": "trim คู่ corr สูง · เติม low-corr", "note": "สุทธิหลังต้นทุน/ภาษี"},
    ]


def _story_normal(n_holdings, bets, sec_txt, enb, enb_crisis, frag_pct, frag_txt,
                  bench_nasdaq_corr, r2, refs_crisis=None):
    driver = f" ก้อน {sec_txt}" if sec_txt else ""
    crisis = ""
    if frag_pct is not None and frag_pct >= 0:
        crisis = f"กด Crisis แล้ว ENB ร่วง {enb}→{enb_crisis} ({frag_txt}). "
    elif frag_pct is not None:
        crisis = f"กด Crisis แล้ว ENB {enb}→{enb_crisis} — {frag_txt}. "
    beta = ""
    if r2 is not None:
        beta = ("พอร์ต corr กับ Nasdaq " + f"{bench_nasdaq_corr} (R² {r2}) = "
                + ("แทบถือ index (S35–36). " if _index_like(r2) else "ยังมี active จริง (S35–36). "))
    low = [f"{name} {v:.2f}" for sym, name in (("GLD", "Gold"), ("IBIT", "BTC"))
           for v in [(refs_crisis or {}).get(sym)] if v is not None and v < BALLAST_MAX_CORR]
    add = (f"เติมสินทรัพย์ที่ crisis corr ต่ำ (วัดได้: {', '.join(low)})" if low
           else "เติมสินทรัพย์ที่ crisis corr ต่ำ (ยังไม่มีตัวที่วัดได้ต่ำกว่า "
                f"{BALLAST_MAX_CORR:.2f} ในชุด benchmark)")
    return (f"พอร์ตถือ {n_holdings} ตัวแต่มีเดิมพันอิสระจริง ~{bets} ก้อน — "
            f"เพราะ{driver or 'การกระจุกตัว'} คือตัวขับความเสี่ยงหลัก (S2/S4 marginal). "
            + crisis + beta
            + f"Action: trim คู่ corr สูง + {add} เพื่อดัน ENB ขึ้น — สุทธิหลังหักภาษี/ค่าธรรมเนียม (S6).")


def _story_crisis(enb_crisis, top_sector, avg_pairwise, avg_pairwise_crisis,
                  crisis_txt=None, ballast=None, refs_crisis=None):
    avg = (f"avg corr {avg_pairwise}→{avg_pairwise_crisis}. "
           if (avg_pairwise is not None and avg_pairwise_crisis is not None) else "")
    src = f" ({crisis_txt})" if crisis_txt else ""
    return (f"โหมด Crisis{src}: พอร์ตเหลือ ~{round(enb_crisis) if enb_crisis else '—'} เดิมพันอิสระ "
            f"({top_sector or 'ก้อนใหญ่'} เป็นก้อนหลักที่วิ่งไปด้วยกัน). " + avg
            + "ตัวถ่วงตามที่วัดได้: " + _ballast_text(ballast, refs_crisis)
            + f" — ถ่วงได้จริงเมื่อ crisis corr < {BALLAST_MAX_CORR:.2f} (S38–41).")


def diversification_philosophy(*, n_holdings, enb, enb_crisis, eff_n,
                               top_sector=None, top_sector_wt=None,
                               top_sector_corr=None, avg_pairwise=None,
                               avg_pairwise_crisis=None, bench_nasdaq_corr=None,
                               downside_corr=None, top_risk_driver=None,
                               crisis_info=None, ballast=None, refs_crisis=None):
    """Build the per-portfolio Damodaran philosophy block from numbers computed
    upstream: a verdict gauge, six pillars, and the normal / crisis narratives."""
    ratio = (enb / eff_n) if (enb and eff_n) else None
    r_cls, r_lbl = _band(ratio, 0.7, 0.5)
    frag_pct, frag_txt = _fragility(enb, enb_crisis,
                                    measured=(crisis_info or {}).get("method") in ("measured", "mixed"))
    r2 = round(bench_nasdaq_corr ** 2, 2) if bench_nasdaq_corr is not None else None
    bets = round(enb) if enb else n_holdings
    sec_txt = _sector_text(top_sector, top_sector_wt, top_sector_corr)
    ci = crisis_info or {}
    crisis_txt = (f"วัดจริง {ci.get('first_day')}→{ci.get('last_day')}, S&P {ci.get('market_drawdown_pct')}%, "
                  f"{ci.get('window_days')} วัน" if ci.get("method") in ("measured", "mixed")
                  else ("สมมติ corr ≥ 0.9 (ไม่มีราคาย้อนหลังช่วงวิกฤต)" if ci else None))
    gauge = {
        "ratio": round(ratio, 2) if ratio else None,
        "ratio_class": r_cls, "ratio_label": r_lbl,
        "enb": enb, "enb_crisis": enb_crisis, "eff_n": eff_n,
        "fragility_pct": frag_pct, "fragility_text": frag_txt,
        "avg_pairwise": avg_pairwise,
        "avg_pairwise_crisis": avg_pairwise_crisis, "downside_corr": downside_corr,
        "r2_nasdaq": r2,
    }
    return {"gauge": gauge,
            "pillars": _pillars(n_holdings, enb, enb_crisis, bets, sec_txt,
                                top_risk_driver, frag_txt, r2, ballast, refs_crisis, crisis_txt),
            "story_normal": _story_normal(n_holdings, bets, sec_txt, enb, enb_crisis,
                                          frag_pct, frag_txt, bench_nasdaq_corr, r2, refs_crisis),
            "story_crisis": _story_crisis(enb_crisis, top_sector, avg_pairwise,
                                          avg_pairwise_crisis, crisis_txt, ballast, refs_crisis)}
