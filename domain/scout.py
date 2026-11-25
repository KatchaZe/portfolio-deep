"""Sector scout: one Damodaran industry's members ranked on Value, Quality and Moat.

Display only. Nothing here feeds a holding's DEEP score, fair value or rating (PP1); the
tab reads the scout job's cached JSON (pipeline/scout_job.py, tools/scout_fetch.py) and this
module ports the approved mockup v2 (tools of 2026-11-24, checks_v2.log) with its rules:

  exclusions  R1 price or period unusable, R2 share count implausible (E77), R3 a US filer on
              a period older than 200 days. The first rule that matches excludes; all are kept.
  flags       F3 foreign annual filer (ranked with the flag), F5 one-off (O1 documented gain,
              O2 add-back above half of raw EBIT without recovery, O3 the 25% cap), F6 EV a lower
              bound, F7 financial / crypto SIC, F8 margin jump to check (watch only).
  tiers       Value (revenue >= $200M, EBIT > 0, EV), Quality (ROIC with a meaningful IC),
              Moat (the moat_trend vote, revenue >= $200M); a component scores only when
              COVER_MIN of the tier has it. Group 2 (price history) and filing dates are info only.

Pure: build(D, holdings, today, book) takes the job's JSON dict and returns the payload.
"""
import datetime as dt
import re
import statistics

from domain import moat_trend as MT, peer_panel as PP, trend as TR
from domain.facts import DEFAULT_TAX_RATE

QW = {"spread": 30, "cap": 15, "inc": 15, "fcfconv": 15, "sbc": 10, "dil": 15}
PEN_AR, PEN_CAP = 10, 20
# O1: one-off gains inside operating income with documented evidence. Kept until a refetch
# shows the shared reader (E79, sec_edgar.GAIN_TAGS) takes the gain out itself.
ONE_OFF_GAINS = {
    "NVST": "กำไรไม่ใช่เงินสด $428.1M จากการรับรู้ผลต่างการแปลงค่าเงินสะสม (CTA release) อยู่ใน EBIT TTM "
            "ไตรมาส 3/2026 (ข่าวผลประกอบการ 2026-11-04)",
}
COVER_MIN = 0.8
RECOVERY_PP = 2.0
TAX = 0.25
G_STABLE = 0.03
DCF_YEARS = 10
REV_MIN = 200e6
W = {"value": 0.5, "quality": 0.3, "moat": 0.2}
IFRS_TH = "รายได้ IFRS ยังไม่ถูกอ่านในชุดข้อมูลนี้ (กดรันใหม่เพื่ออ่าน)"


def ifrs_gap(m):
    return m["ttm"].get("accounting_standard") == "ifrs" and any(x.get("value") is None for x in m["revenue_cy"].values())


def v(o):
    return (o or {}).get("value")


def d(s):
    return dt.date.fromisoformat(s[:10])


def trading_lag(price_date, ref):
    """Weekdays after price_date up to and including ref (no US holiday in the window)."""
    n, x = 0, d(price_date)
    while x < ref:
        x += dt.timedelta(days=1)
        n += x.weekday() < 5
    return n


def pctile(values, higher_better=True):
    """{key: 0..100} share of the others this one beats (ties count half)."""
    keys = list(values)
    n = len(keys)
    out = {}
    for k in keys:
        a = values[k]
        if n == 1:
            out[k] = 50.0
            continue
        beat = sum((a > values[o]) if higher_better else (a < values[o]) for o in keys if o != k)
        tie = sum(a == values[o] for o in keys if o != k)
        out[k] = 100.0 * (beat + 0.5 * tie) / (n - 1)
    return out


def fy_pair(rows):
    """(fy0, fy3) rows: newest FY and the one about three years before it."""
    if not rows:
        return None, None
    f0 = rows[0]
    for r in rows[1:]:
        if 1000 <= (d(f0["end"]) - d(r["end"])).days <= 1200:
            return f0, r
    return f0, None


def roic_spread_series(rows, wacc_pp):
    """Contiguous FY window ending at FY0 of (years since start, ROIC - WACC in pp).
    ROIC = EBIT x (1 - 25%) / (equity + debt - cash) at each FY end."""
    win = []
    for r in rows:
        if win and not (PP.FY_GAP[0] <= (d(win[-1]["end"]) - d(r["end"])).days <= PP.FY_GAP[1]):
            break
        ic = None if r.get("equity") is None or r.get("cash") is None else r["equity"] + (r.get("debt") or 0) - r["cash"]
        if r.get("ebit") is None or not ic or ic <= 0:
            break
        win.append(dict(r, roic=r["ebit"] * (1 - TAX) / ic * 100))
    win.reverse()
    if not win:
        return [], None
    x0 = d(win[0]["end"])
    return [((d(r["end"]) - x0).days / 365.25, r["roic"] - wacc_pp) for r in win], win


def s1(rows, wacc_pp):
    pts, win = roic_spread_series(rows, wacc_pp)
    if len(pts) < MT.MIN_SLOPE_YEARS:
        return None, f"S1: ROIC ติดต่อกันได้ {len(pts)} ปีงบ (ต้อง ≥{MT.MIN_SLOPE_YEARS}; ข้ามปีที่ IC ≤ 0)"
    b = MT._slope([p[0] for p in pts], [p[1] for p in pts])
    return {"name": "spread_slope", "value": round(b, 2), "unit": "pp/yr",
            "direction": MT.direction(b, MT.SPREAD_SLOPE_PP),
            "window": f"FY{win[0]['end'][:4]}..FY{win[-1]['end'][:4]}",
            "basis": "Theil-Sen ของ ROIC−WACC (ROIC = EBIT×0.75 ÷ (ทุน+หนี้−เงินสด) ณ สิ้นปีงบ, WACC วันนี้คงที่)"}, None


def share_sets(members, years):
    """Matched set = members with revenue in both CY(old) and CY(new). Returns
    {group_key: {'cik_rev': {t: (old, new)}, 'tot': (old, new)}} for 'industry' and each SIC."""
    old, new = years
    groups = {}
    for m in members:
        a, b = v(m["revenue_cy"].get(str(old))), v(m["revenue_cy"].get(str(new)))
        if not (a and b and a > 0 and b > 0):
            continue
        for key in ("industry", "sic:" + str(v(m["sic"]))):
            g = groups.setdefault(key, {"rev": {}})
            g["rev"][m["ticker"]] = (a, b)
    for g in groups.values():
        g["tot"] = (sum(x[0] for x in g["rev"].values()), sum(x[1] for x in g["rev"].values()))
    return groups


def s2(m, groups, years):
    old, new = years
    t, sub = m["ticker"], groups.get("sic:" + str(v(m["sic"])))
    use, basis, note = groups.get("industry"), "industry", None
    if sub and len(sub["rev"]) >= MT.SUB_MIN_MATCHED and t in sub["rev"]:
        use, basis = sub, "sub_group"
    elif sub is not None:
        note = f"SIC {v(m['sic'])} มี {len(sub['rev'])} รายที่มีรายได้ทั้ง CY{old} และ CY{new} (ต้อง ≥{MT.SUB_MIN_MATCHED}) จึงใช้ทั้งอุตสาหกรรม"
    if not use or t not in use["rev"]:
        if ifrs_gap(m):
            return None, "S2: " + IFRS_TH
        return None, f"S2: ไม่มีรายได้ SEC frames ทั้ง CY{old} และ CY{new}"
    a, b = use["rev"][t]
    share = {"years": [old, new], "matched_share_then": a / use["tot"][0], "matched_share_now": b / use["tot"][1]}
    sig, why = MT._share_signal(share)
    if not sig:
        return None, why
    sig.update(basis=basis, matched_n=len(use["rev"]), share_then=share["matched_share_then"],
               share_now=share["matched_share_now"], basis_reason=note,
               sic=v(m["sic"]) if basis == "sub_group" else None)
    return sig, None


def point(m):
    f0, f3 = fy_pair(m["fy"]["rows"])
    leg = lambda r: None if r is None else {"end": r["end"], "revenue": r.get("revenue"),
                                            "gross_profit": r.get("gross_profit"), "operating_income": r.get("ebit")}
    return {"symbol": m["ticker"], "fy0": leg(f0), "fy3": leg(f3)}


def moat(m, pts, sics, groups, years, wacc_pp, as_of):
    tgt = pts[m["ticker"]]
    peers = [p for k, p in pts.items() if k != m["ticker"] and p["fy0"] and p["fy3"]
             and (as_of - d(p["fy0"]["end"])).days <= 400 + 365]
    sig3, why3, ok3 = MT.gm_signal(tgt, peers, sics) if tgt["fy0"] and tgt["fy3"] else (None, "S3: ไม่มีปีงบ FY0 หรือ FY3", True)
    if not ok3:
        sig3, why3 = None, "S3: " + PP.GM_NA_TH
    sig4, why4 = MT.incremental_margin(tgt) if tgt["fy0"] and tgt["fy3"] else (None, "S4: ไม่มีปีงบ FY0 หรือ FY3")
    out, flags = [], []
    for sig, why in (s1(m["fy"]["rows"], wacc_pp), s2(m, groups, years), (sig3, why3), (sig4, why4)):
        (out.append(sig) if sig else flags.append(why))
    lab = MT.label(out)
    possible = 4 - (0 if ok3 else 1)
    return {"label": lab, "net": sum(s["direction"] for s in out) if lab else None,
            "cast": len(out), "possible": possible, "signals": out, "absent": flags}


def implied_growth(ev, nopat, wacc):
    """g such that EV = sum_{t=1..10} NOPAT0 (1+g)^t / (1+WACC)^t + TV / (1+WACC)^10,
    TV = NOPAT10 (1+g_s) / (WACC - g_s). No reinvestment charge, so g is a floor."""
    def val(g):
        pv, n = 0.0, nopat
        for t in range(1, DCF_YEARS + 1):
            n *= 1 + g
            pv += n / (1 + wacc) ** t
        return pv + n * (1 + G_STABLE) / (wacc - G_STABLE) / (1 + wacc) ** DCF_YEARS
    lo, hi = -0.5, 1.0
    if val(lo) > ev:
        return lo, "below"
    if val(hi) < ev:
        return hi, "above"
    for _ in range(80):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if val(mid) < ev else (lo, mid)
    return (lo + hi) / 2, None


def oneoff_status(t, rev, ebit, raw, add, f0, rows):
    """v1 and v2 one-off reasons. v2 flags only evidence of a one-time item: O1 a
    documented non-operating or non-cash gain inside operating income, O2 an add-back
    larger than half of raw EBIT while raw EBIT itself did not recover, O3 the 25% cap.
    A margin jump matched by revenue or gross-margin recovery is not a one-off; an
    unmatched one is a watch note (F8), never an exclusion."""
    v1, v2 = [], []
    half = bool(raw and add and abs(add) > 0.5 * abs(raw))
    cap = bool(rev and add and abs(add - 0.25 * rev) < 1)
    m0 = f0["ebit"] / f0["revenue"] if f0 and f0.get("revenue") and f0.get("ebit") is not None else None
    raw_m = raw / rev if raw is not None and rev else None
    jump = (ebit / rev - m0) * 100 if ebit is not None and ebit > 0 and rev and m0 is not None else None
    if half:
        v1.append("add-back > ครึ่งของ EBIT ดิบ")
    if cap:
        v1.append("add-back ชนเพดาน 25%")
    if jump is not None and jump > 10:
        v1.append("margin กระโดด > 10 จุด")
    recovered = raw_m is not None and m0 is not None and (raw_m - m0) * 100 > RECOVERY_PP
    if t in ONE_OFF_GAINS:
        v2.append("O1 " + ONE_OFF_GAINS[t])
    if half and not recovered:
        v2.append(f"O2 add-back ${add / 1e6:,.0f}M มากกว่าครึ่งของ EBIT ดิบ ${raw / 1e6:,.0f}M และ margin ดิบ TTM "
                  f"{'—' if raw_m is None else format(raw_m, '.1%')} ไม่ฟื้นเกิน FY{f0['end'][:4] if f0 else '—'} "
                  f"{'—' if m0 is None else format(m0, '.1%')} + {RECOVERY_PP:.0f} จุด")
    if cap:
        v2.append(f"O3 add-back ${add / 1e6:,.0f}M ชนเพดาน 25% ของรายได้")
    watch = None
    if not v2 and jump is not None and jump > 10:
        r1 = rows[1] if len(rows) > 1 else None
        gm = lambda r: r["gross_profit"] / r["revenue"] if r and r.get("gross_profit") and r.get("revenue") else None
        rev_up = f0 and f0.get("revenue") and rev / f0["revenue"] - 1 >= 0.05
        gm_up = gm(f0) is not None and gm(r1) is not None and (gm(f0) - gm(r1)) * 100 >= 2
        if not (rev_up or gm_up or recovered):
            watch = (f"margin TTM {ebit / rev:.1%} สูงกว่า FY{f0['end'][:4]} {m0:.1%} อยู่ {jump:.1f} จุด โดยรายได้และ GM ไม่ได้ฟื้นตาม "
                     "ยังจัดอันดับ แต่ควรเปิดงบดูว่ามีกำไรครั้งเดียวหรือไม่")
    return {"v1": v1, "v2": v2, "watch": watch, "recovered": recovered}


PENDING = "รอข้อมูล (รันสคริปต์ใหม่)"


def _fy_roic_map(rows):
    """Fallback for members whose SEC part predates group 1: the app's trend.roic_series on
    the FY rows already fetched (same EBIT and IC legs), at the app's DEFAULT_TAX_RATE."""
    oi = [[r["end"], r["ebit"]] for r in rows if r.get("ebit") is not None]
    ic = {r["end"]: {"equity": r.get("equity"), "cash": r.get("cash"), "debt": r.get("debt")} for r in rows}
    return TR.roic_series(oi, ic, DEFAULT_TAX_RATE)


def quality_parts(m, wacc):
    """{part: {v, basis, why, src}} for the Quality components beyond ROIC - WACC."""
    g = (m.get("group1") or {}).get("derived")
    out = {}
    if g:
        cap = g["cap_years"]
        out["cap"] = dict(v=cap["value"], of=cap.get("of"), basis="engine", why=th_why(cap["reason"]), src=cap["source"])
        inc = g["incremental_roic"]
        out["inc"] = dict(v=None if inc["value"] is None else inc["value"] / 100 - wacc, raw=inc["value"], basis="engine",
                          why=th_why(inc["reason"]), src=inc["source"])
        for k, key in (("fcfconv", "fcf_conversion"), ("sbc", "sbc_pct_revenue"), ("dil", "dilution_cagr_pct")):
            x = g[key]
            v = x["value"]
            if v is not None and k in ("sbc", "dil"):
                v = v / 100
            out[k] = dict(v=v, basis="engine", why=th_why(x["reason"]), src=x["source"])
        eq = g["eq"]
        out["ar"] = dict(v=g["receivables_flag"]["value"], basis="engine", why=th_why(g["receivables_flag"]["reason"]),
                         src=g["receivables_flag"]["source"], eq=eq["value"], flags=eq.get("flags") or [])
        tt = g["ttm"]
        out["nde"] = dict(v=tt["net_debt_ebitda"]["value"], basis="engine", why=th_why(tt["net_debt_ebitda"]["reason"]),
                          src=tt["net_debt_ebitda"]["source"], nd=tt["net_debt"]["value"], ebitda=tt["ebitda"]["value"])
        out["cov"] = dict(v=tt["interest_coverage"]["value"], basis="engine", why=th_why(tt["interest_coverage"]["reason"]),
                          src=tt["interest_coverage"]["source"])
        fl = g["fcf_latest_fy"]
        out["fcf"] = dict(v=fl["value"], end=fl.get("end"), why=th_why(fl["reason"]), src=fl["source"])
        out["mt"] = dict(v=g["margin_trend"]["value"], src=g["margin_trend"]["source"], why=g["margin_trend"]["reason"])
        out["dur"] = dict(v=g["fcf_durability"]["value"], note=g["fcf_durability"].get("note"), why=g["fcf_durability"]["reason"])
        return out
    why = (m.get("group1") or {}).get("reason") or PENDING
    rmap = _fy_roic_map(m["fy"]["rows"])
    win = TR._window(rmap, years=5)
    src = f"ประมาณจาก fy rows เดิม (trend.roic_series ภาษี {DEFAULT_TAX_RATE:.0%} คงที่) รอค่าจากเอนจินเมื่อรันใหม่"
    if len(win) >= 4:
        out["cap"] = dict(v=sum(1 for d in win if rmap[d]["roic"] > wacc * 100), of=len(win), basis="fy", why=None, src=src)
    else:
        out["cap"] = dict(v=None, basis="fy", why=f"ROIC ติดต่อกัน {len(win)} ปีงบ (ต้อง ≥4)", src=src)
    iw = TR.incremental_window(rmap, 5)
    pct, w2 = TR.incremental_roic_pct(rmap, iw) if iw else (None, f"มีปีที่ NOPAT เป็นบวกไม่ถึง {TR.INC_MIN_YEARS} ปีติดกัน")
    out["inc"] = dict(v=None if pct is None else pct / 100 - wacc, raw=pct, basis="fy", why=w2, src=src)
    for k in ("fcfconv", "sbc", "dil", "ar", "nde", "cov", "fcf", "mt", "dur"):
        out[k] = dict(v=None, basis=None, why=PENDING, src=None, pending=True, g1why=why)
    return out


TH_WHY = {
    "no FY FCF, or NOPAT not positive": "ไม่มี FCF ปีงบ หรือ NOPAT ไม่เป็นบวก",
    "SBC not tagged": "ไม่ได้แท็ก SBC",
    "fewer than 2 years of diluted share counts": "จำนวนหุ้นไม่ถึง 2 ปี",
    "earnings quality untested": "ทดสอบคุณภาพกำไรไม่ได้",
    "EBITDA not positive or not filed": "EBITDA ไม่เป็นบวกหรือไม่ได้ยื่น",
    "no interest expense filed (no debt cost to cover)": "ไม่มีดอกเบี้ยจ่าย",
    "not measurable": "วัดไม่ได้",
}


def th_why(w):
    """The user-facing reason: Thai, without the engine's internal note prefix."""
    w = str(w or "")
    m = re.search(r"adj=skipped\((.*)\)$", w)
    if m:
        w = m.group(1)
    if w.startswith("fewer than") and "contiguous FY with ROIC" in w:
        return "ROIC ติดต่อกันไม่ถึง 4 ปีงบ"
    return TH_WHY.get(w, w)


def penalties(qp):
    """(points, [reasons]) taken off Quality: the receivables flag and leverage risk."""
    pts, why = 0, []
    if qp["ar"].get("v"):
        pts += PEN_AR
        why.append(f"EQ ลูกหนี้โตเร็วกว่ารายได้ −{PEN_AR}")
    nde, nd, eb = qp["nde"].get("v"), qp["nde"].get("nd"), qp["nde"].get("ebitda")
    if nde is not None and nde > 5 or (nd is not None and nd > 0 and eb is not None and eb <= 0):
        pts += 10
        why.append("หนี้สุทธิ/EBITDA > 5 (หรือ EBITDA ≤ 0 แต่มีหนี้สุทธิ) −10")
    elif nde is not None and nde > 3:
        pts += 5
        why.append("หนี้สุทธิ/EBITDA > 3 −5")
    cov = qp["cov"].get("v")
    if cov is not None and cov < 1.5:
        pts += 10
        why.append("interest coverage < 1.5 เท่า −10")
    elif cov is not None and cov < 3:
        pts += 5
        why.append("interest coverage < 3 เท่า −5")
    return min(pts, PEN_CAP), why


def info_cols(m, today, book=None):
    """Group 2 and filing columns. Shown, never scored."""
    g = m.get("group2") or {}
    have = bool(g.get("bars"))
    pend = g.get("reason") if not have else None
    f = m.get("filing") or {}
    nxt = f.get("next_expected_by")
    own = (book or {}).get(m["ticker"])
    return {
        "have": have, "pending": None if have else PENDING + (f" · {pend}" if pend and "offline" not in pend else ""),
        "vol": g["vol_ann"]["value"] if have else None,
        "beta": own[0] if own else g["beta_spy"]["value"] if have else None,
        "beta_end": own[1] if own else g["beta_spy"].get("window_end") if have else None,
        "beta_src": "store (Run Daily)" if own else "scout job" if have else None,
        "top": g["top_corr"]["value"] if have else None,
        "mom": (g["momentum"]["value"] or {}).get("mom_label") if have and g["momentum"]["value"] else None,
        "ind": g["indicators"]["value"] if have else None,
        "hist": f"{g.get('first')}..{g.get('last')} ({g.get('bars')} วัน) {g.get('source')}" if have else None,
        "filed": f.get("latest_filed"), "period_end": f.get("period_end"), "next_by": nxt,
        "new_since": bool(nxt and nxt <= today.isoformat()), "rule": f.get("rule"), "fetched": f.get("fetched"),
    }


def challengers(rows):
    """Tag a member whose S2 share rose while a LARGER member of the same S2 basis group
    (by share now) lost share. Reads the S2 signal the moat tier already computed."""
    by_group = {}
    for r in rows:
        r["challenger"] = None
    for r in rows:
        s2 = next((x for x in r["moat"]["signals"] if x["name"] == "share_change"), None)
        if s2:
            key = (s2["basis"], s2.get("sic"))
            by_group.setdefault(key, []).append((r, s2))
    for key, items in by_group.items():
        for r, s2 in items:
            if s2["share_now"] <= s2["share_then"]:
                continue
            losers = [(o["t"], x) for o, x in items if o is not r and x["share_now"] > s2["share_now"]
                      and x["share_now"] < x["share_then"]]
            if losers:
                big = max(losers, key=lambda lx: lx[1]["share_now"])
                r["challenger"] = {"vs": big[0], "me": [s2["share_then"], s2["share_now"]],
                                   "them": [big[1]["share_then"], big[1]["share_now"]],
                                   "group": "SIC " + str(key[1]) if key[0] == "sub_group" else "ทั้งอุตสาหกรรม",
                                   "others": [t for t, _ in losers if t != big[0]]}


def metric(val, src, per=None, why=None):
    return {"v": val, "src": src, "per": per, "why": None if val is not None else (why or "ไม่มีข้อมูล")}


def build(D, holdings, today=None, book=None):
    """The payload for one industry. `holdings` the store's tickers; `book` {ticker: (beta,
    date)} the holdings' own regression beta from Run Daily (E82), shown on their rows."""
    key = slug(D["industry"])
    holdings = sorted(holdings)
    today = today or dt.date.today()
    as_of = d(D["as_of"])
    ref = as_of - dt.timedelta(days=1)
    while ref.weekday() >= 5:
        ref -= dt.timedelta(days=1)
    wacc = v(D["damodaran"]["wacc"])
    wsrc = D["damodaran"]["wacc"]["source"]
    members = D["members"]
    years = (max(D["panel_years"]) - PP.SHARE_LOOKBACK, max(D["panel_years"]))
    groups = share_sets(members, years)
    pts = {m["ticker"]: point(m) for m in members}
    sics = {m["ticker"]: v(m["sic"]) for m in members}
    rows = []
    for m in members:
        t, tt, b, mk, ck = m["ticker"], m["ttm"], m["balance"], m["market"], m["checks"]
        price, pdate = v(mk["price"]), mk["price"].get("date")
        rev, ebit, raw, add = v(tt["revenue"]), v(tt["ebit"]), v(tt["ebit_raw"]), tt.get("addback") or 0
        eq, debt, cash, ls = v(b["equity"]), v(b["debt"]), v(b["cash"]), v(b["operating_leases"])
        shares, mcap, ev = v(mk["shares"]), v(mk["market_cap"]), v(mk["ev"])
        per_ttm = f"TTM ถึง {tt['period_end']}" if tt.get("period_end") else None
        fyrows = m["fy"]["rows"]
        f0, f3 = fy_pair(fyrows)
        sec_sic = (m.get("sec_sic") or [None, None])
        # ---------- exclusion rules (first match wins; all hits kept) ----------
        hits = []
        age = ck.get("latest_period_age_days")
        if price is None:
            hits.append(("R1", f"ไม่มีราคา: {mk['price'].get('reason') or 'ไม่มีราคาปิด T-1'}"))
        elif price <= 0.01:
            hits.append(("R1", f"ราคา ${price:.4f} ≤ $0.01 (น่าจะเป็นราคาค้างของหุ้นที่เลิกซื้อขาย)"))
        elif pdate and trading_lag(pdate, ref) > 3:
            hits.append(("R1", f"ราคาเก่า: ปิดวันที่ {pdate} ช้ากว่า {ref} ไป {trading_lag(pdate, ref)} วันทำการ (เกณฑ์ ≤3)"))
        if age is None or age > 400:
            hits.append(("R1", f"งวดล่าสุดเก่า: {tt.get('period_end') or 'ไม่มีงวด'} ({age if age is not None else '—'} วัน, เกณฑ์ ≤400)"))
        mreason = mk["market_cap"].get("reason") or ""
        if mcap is None and mreason.startswith("share count implausible"):
            hits.append(("R2", "จำนวนหุ้น SEC ใช้ไม่ได้: " + mreason))
        elif rev is not None and mcap is not None and rev > 50e6 and mcap / rev < 0.05:
            hits.append(("R2", f"market cap ÷ รายได้ = {mcap / rev:.4f} (< 0.05) จำนวนหุ้น SEC {shares:,.0f} น่าจะผิดหน่วย ~1000 เท่า"))
        elif shares is not None and rev is not None and rev > 5e6 and shares < 1e6:
            hits.append(("R2", f"จำนวนหุ้น {shares:,.0f} (< 1M) กับรายได้ ${rev / 1e6:,.0f}M น่าจะผิดหน่วย"))
        if ck.get("stale_base") and m["domicile"] == "us":
            hits.append(("R3", f"บริษัท US แต่งวดล่าสุด {tt.get('period_end')} เก่ากว่า 200 วัน (ยังไม่ได้อ่าน 10-Q ล่าสุด)"))
        if rev is None:
            hits.append(("R1", "ไม่มีรายได้ TTM"))
        excl = hits[0] if hits else None
        # ---------- flags ----------
        flags = []
        if ck.get("stale_base") and m["domicile"] != "us":
            flags.append(("F3", f"ผู้ยื่นงบรายปีต่างประเทศ งวดล่าสุด {tt.get('period_end')} (ข้อมูลเก่าราว {age} วัน)"))
        oo = oneoff_status(t, rev, ebit, raw, add, f0, fyrows)
        if oo["v2"]:
            flags.append(("F5", "one-off: " + "; ".join(oo["v2"])))
        elif oo["watch"]:
            flags.append(("F8", oo["watch"]))
        lower = False
        if mk["ev"].get("lower_bound") and ev is not None:
            lower = True
            flags.append(("F6", "EV เป็นขั้นต่ำ: ไม่ทราบหนี้ปัจจุบัน แต่ข้อมูลแสดงว่ามีหนี้ (" + (b["debt"].get("reason") or "") [:160] + ")"))
        elif "lower_bound" not in mk["ev"] and debt is None and ev is not None:
            untagged = "ไม่ทราบ" in (b["debt"].get("reason") or "")
            fy_debt = f0.get("debt") if f0 else None
            if untagged or (fy_debt is not None and fy_debt > 0):
                lower = True
                flags.append(("F6", "EV เป็นขั้นต่ำ: ไม่ทราบหนี้ปัจจุบัน แต่ " + ("มีหนี้ที่ระบบอ่านไม่ได้" if untagged else f"ปีงบ FY{f0['end'][:4]} มีหนี้ ${fy_debt / 1e6:,.0f}M")))
        fin = PP.is_financial(sec_sic[0])
        if fin:
            flags.append(("F7", f"SIC ที่ยื่น SEC {sec_sic[0]} ({sec_sic[1]}): ธุรกิจการเงิน/คริปโต/ปล่อยกู้ EBIT ไม่ใช่กำไรจากการดำเนินงาน ไม่จัดอันดับ EBIT/EV"))
        if ifrs_gap(m):
            flags.append(("DQ", IFRS_TH + ": ส่วนแบ่งรายได้ S2 จึงไม่มีค่า และไม่อยู่ในชุดที่ใช้คิดส่วนแบ่งของทั้งกลุ่ม"))
        fcodes = {f[0] for f in flags}
        # ---------- tier 2 quality ----------
        ic = None if eq is None else eq + (debt or 0) + (ls or 0) - (cash or 0)
        roic = None
        q_why = None
        if excl:
            q_why = "ตัดออก: " + excl[1]
        elif ebit is None:
            q_why = "ไม่มี EBIT TTM"
        elif eq is None or eq <= 0:
            q_why = f"n/m: ส่วนของผู้ถือหุ้น {'—' if eq is None else f'${eq / 1e6:,.0f}M'} ไม่เป็นบวก"
        elif rev is None or ic <= max(0.1 * rev, 50e6):
            q_why = f"n/m: ทุนลงทุน ${ic / 1e6:,.0f}M ≤ max(10% ของรายได้, $50M) ROIC ไม่มีความหมาย"
        elif "F5" in fcodes:
            q_why = "ไม่จัดอันดับ: EBIT มีรายการครั้งเดียว (ดูธง one-off)"
        elif fin:
            q_why = "n/m: ธุรกิจการเงิน/คริปโต (SIC SEC 6000–6799) ROIC แบบ EBIT ไม่มีความหมาย"
        else:
            roic = ebit * (1 - TAX) / ic
        # ---------- tier 3 value ----------
        v_why = []
        if excl:
            v_why.append("ตัดออก: " + excl[1])
        else:
            if rev is None or rev < REV_MIN:
                v_why.append(f"รายได้ TTM {'—' if rev is None else f'${rev / 1e6:,.0f}M'} < $200M")
            if ebit is None or ebit <= 0:
                v_why.append("EBIT TTM ไม่เป็นบวก")
            if ev is None or ev <= 0:
                v_why.append("ไม่มี EV: " + (mk["ev"].get("reason") or "EV ไม่เป็นบวก"))
            if "F5" in fcodes:
                v_why.append("one-off ใน EBIT ยังไม่มีตัวเลขปรับ")
            if lower:
                v_why.append("EV เป็นขั้นต่ำ (หนี้ไม่ครบ)")
            if fin:
                v_why.append("ธุรกิจการเงิน/คริปโต (SIC SEC 6000–6799)")
        g3, g3_per = None, None
        if f0 and f3 and f0.get("revenue") and f3.get("revenue") and f0["revenue"] > 0 and f3["revenue"] > 0:
            g3 = (f0["revenue"] / f3["revenue"]) ** (1 / 3) - 1
            g3_per = f"FY{f3['end'][:4]}→FY{f0['end'][:4]}"
        qp = quality_parts(m, wacc)
        pen, pen_why = penalties(qp)
        hsic = D.get("holding_sic") or {}
        mine = str(sec_sic[0] or v(m["sic"]) or "")
        sim = sorted(h for h, c in hsic.items() if h != t and c and str(c) == mine)
        rows.append(dict(
            oo=oo, qp=qp, pen=pen, pen_why=pen_why, info=info_cols(m, today, book), sim=sim,
            t=t, name=m["name"].split(" (")[0], sic=str(v(m["sic"])), sec_sic=sec_sic, dom=m["domicile"],
            hold=t in holdings, excl=excl, hits=hits, flags=flags, roic=roic, q_why=q_why,
            v_ok=not v_why, v_why=v_why, ebit=ebit, ev=ev, rev=rev, g3=g3, ic=ic,
            moat=moat(m, pts, sics, groups, years, wacc * 100, as_of),
            m={
                "price": metric(price, mk["price"].get("source"), pdate, mk["price"].get("reason")),
                "mcap": metric(mcap, mk["market_cap"].get("source"), pdate, mk["market_cap"].get("reason")),
                "ev": metric(ev, mk["ev"].get("source"), f"ราคา {pdate} · งบดุล {tt.get('period_end')}", mk["ev"].get("reason")),
                "rev": metric(rev, tt["revenue"].get("source"), per_ttm, tt["revenue"].get("reason")),
                "ebit": metric(ebit, tt["ebit"].get("source"), per_ttm, tt["ebit"].get("reason")),
                "ebit_raw": metric(raw, tt["ebit_raw"].get("source"), per_ttm, tt["ebit_raw"].get("reason")),
                "addback": metric(add, "peers.normalized one-time add-back", per_ttm),
                "equity": metric(eq, b["equity"].get("source"), tt.get("period_end"), b["equity"].get("reason")),
                "debt": metric(debt, b["debt"].get("source"), tt.get("period_end"), b["debt"].get("reason")),
                "cash": metric(cash, b["cash"].get("source"), tt.get("period_end"), b["cash"].get("reason")),
                "leases": metric(ls, b["operating_leases"].get("source"), tt.get("period_end"), b["operating_leases"].get("reason")),
                "ic": metric(ic, "ทุน + หนี้ + สัญญาเช่า − เงินสด", tt.get("period_end"), "ไม่มีส่วนของผู้ถือหุ้น"),
                "g3": metric(g3, "fy.rows รายได้ปีงบ (SEC companyfacts)", g3_per, "ไม่มีรายได้ปีงบ FY0 และ FY3"),
            }))
    challengers(rows)
    # ---------- value metrics need the same-SIC medians ----------
    vrows = [r for r in rows if r["v_ok"]]
    for r in vrows:
        r["ey"] = r["ebit"] / r["ev"]
        r["evebit"] = r["ev"] / r["ebit"]
    for r in vrows:
        same = None
        for lv, label in ((4, f"SIC {r['sic']}"), (3, f"SIC {r['sic'][:3]}x")):
            peers = [p for p in vrows if p is not r and p["sic"][:lv] == r["sic"][:lv]]
            if len(peers) >= PP.GM_MIN_COMPARATORS:
                same = (peers, label)
                break
        if same is None:
            same = ([p for p in vrows if p is not r], "ทั้งอุตสาหกรรม")
        peers, label = same
        med_m = statistics.median(p["evebit"] for p in peers)
        gs = [p["g3"] for p in peers if p["g3"] is not None]
        med_g = statistics.median(gs) if gs else None
        if r["g3"] is not None and med_g is not None:
            r["rel"] = r["evebit"] / med_m * (1 + med_g) / (1 + r["g3"])
        else:
            r["rel"] = r["evebit"] / med_m
        r["rel_basis"] = dict(group=label, n=len(peers), med_m=med_m, med_g=med_g, growth_adj=r["g3"] is not None and med_g is not None)
        g, clip = implied_growth(r["ev"], r["ebit"] * (1 - TAX), wacc)
        r["gimp"], r["gimp_clip"] = g, clip
        r["ggap"] = None if r["g3"] is None else r["g3"] - g
    # ---------- tier scores (industry-wide, fixed per name) ----------
    for r in rows:
        f = r["qp"]["fcf"].get("v")
        r["fcfy"] = f / r["ev"] if r["v_ok"] and f is not None else None
    pv1 = pctile({r["t"]: r["ey"] - wacc for r in vrows})
    pv2 = pctile({r["t"]: r["rel"] for r in vrows}, higher_better=False)
    pv3 = pctile({r["t"]: r["ggap"] for r in vrows if r["ggap"] is not None})
    qrows = [r for r in rows if r["roic"] is not None]
    cover = lambda rs, f: sum(1 for r in rs if f(r)) / len(rs) if rs else 0.0
    scored = {"fcfy": cover(vrows, lambda r: r["fcfy"] is not None) >= COVER_MIN}
    pv4 = pctile({r["t"]: r["fcfy"] for r in vrows if r["fcfy"] is not None}) if scored["fcfy"] else {}
    qp = {"spread": pctile({r["t"]: r["roic"] - wacc for r in qrows})}
    for k, hb in (("cap", True), ("inc", True), ("fcfconv", True), ("sbc", False), ("dil", False)):
        scored[k] = cover(qrows, lambda r: r["qp"][k].get("v") is not None or r["qp"][k].get("basis")) >= COVER_MIN
        qp[k] = pctile({r["t"]: (r["qp"][k]["v"] / r["qp"][k]["of"] if k == "cap" else r["qp"][k]["v"])
                        for r in qrows if r["qp"][k].get("v") is not None}, higher_better=hb) if scored[k] else {}
    scored["pen"] = cover(qrows, lambda r: r["qp"]["nde"].get("basis")) >= COVER_MIN
    for r in qrows:
        have = {k: qp[k][r["t"]] for k in QW if r["t"] in qp[k]}
        base = sum(QW[k] * x for k, x in have.items()) / sum(QW[k] for k in have)
        r["q_parts"] = {k: round(x, 1) for k, x in have.items()}
        r["q_base"] = base
        if not scored["pen"]:
            r["pen_shown"], r["pen"] = r["pen"], 0
        r["s_quality"] = max(0.0, base - r["pen"])
    pq = {r["t"]: r["s_quality"] for r in qrows}
    mset = {r["t"]: r["moat"]["net"] for r in rows if not r["excl"] and r["moat"]["net"] is not None
            and (r["rev"] or 0) >= REV_MIN}
    pm = pctile(mset)
    for r in rows:
        if not r["excl"] and r["moat"]["net"] is not None and (r["rev"] or 0) < REV_MIN:
            rv = "—" if r["rev"] is None else f"${r['rev'] / 1e6:,.0f}M"
            r["moat_why"] = f"รายได้ TTM {rv} < $200M (ชั้น Moat ต้องรายได้ ≥ $200M เหมือน Value)"
        parts = [pv1.get(r["t"]), pv2.get(r["t"]), pv3.get(r["t"]), pv4.get(r["t"])]
        parts = [p for p in parts if p is not None]
        r["s_value"] = sum(parts) / len(parts) if r["v_ok"] else None
        r["s_value_n"] = len(parts) if r["v_ok"] else 0
        r["s_quality"] = pq.get(r["t"])
        r["s_moat"] = pm.get(r["t"])
        if r["s_value"] is None:
            r["s_comb"], r["comb_n"] = None, 0
        else:
            have = [(W[k], s) for k, s in (("value", r["s_value"]), ("quality", r["s_quality"]), ("moat", r["s_moat"])) if s is not None]
            r["s_comb"] = sum(w * s for w, s in have) / sum(w for w, _ in have)
            r["comb_n"] = len(have)
    # ---------- reference rows for holdings not in the Damodaran US list ----------
    present = {r["t"] for r in rows}
    refs = [{"t": h, "missing": "ไม่อยู่ในรายชื่อ Damodaran อุตสาหกรรมนี้ (US) ของชุดข้อมูล scout จึงไม่มีตัวเลขให้เทียบ"} for h in holdings if h not in present]
    sic_groups = {}
    for r in rows:
        sic_groups.setdefault(r["sic"], 0)
        sic_groups[r["sic"]] += 1
    cov_txt = {k: round(100 * (cover(vrows, lambda r: r["fcfy"] is not None) if k == "fcfy" else
                               cover(qrows, (lambda r: r["qp"]["nde"].get("basis")) if k == "pen" else
                                     (lambda r, k=k: r["qp"][k].get("v") is not None or r["qp"][k].get("basis"))))) for k in scored}
    has_g2 = any(r["info"]["have"] for r in rows)
    ind = dict(has_g2=has_g2, key=key, industry=D["industry"], scored=scored, cover=cov_txt,
                sector_headroom=D.get("sector_headroom"), holdings=holdings, as_of=D["as_of"], ref_date=str(ref), wacc=wacc, wacc_src=wsrc,
                years=years, n_members=len(members), rows=rows, refs=refs, sic_groups=sic_groups, groups=groups,
                coverage=D["coverage"])
    del ind["groups"]
    for r in ind["rows"]:
        r["q_short"] = None if r["roic"] is not None else short_q(r["q_why"])
        r["v_short"] = None if r["v_ok"] else short_v(r["v_why"])
    ind["ranks"] = views(ind)
    return ind


def slug(s):
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def rank(rows, key, higher=True):
    """Competition rank (1,2,2,4) among rows with a value."""
    have = [r for r in rows if r.get(key) is not None]
    out = {}
    for r in have:
        out[r["t"]] = 1 + sum(((o[key] > r[key]) if higher else (o[key] < r[key])) for o in have)
    return out


TIERS = (("comb", "s_comb"), ("value", "s_value"), ("quality", "s_quality"), ("moat", "s_moat"))


def views(ind):
    rows = ind["rows"]
    vs = {"all": rows}
    for sic, n in ind["sic_groups"].items():
        if n >= MT.SUB_MIN_MATCHED or any(r["hold"] and r["sic"] == sic for r in rows):
            vs["sic:" + sic] = [r for r in rows if r["sic"] == sic]
    return {k: {tier: rank(rs, key) for tier, key in TIERS} for k, rs in vs.items()}


def short_q(why):
    w = why or ""
    if w.startswith("ตัดออก"):
        return "ตัดออก"
    if "การเงิน" in w:
        return "n/m การเงิน"
    if "ผู้ถือหุ้น" in w:
        return "n/m ทุนติดลบ"
    if "ทุนลงทุน" in w:
        return "n/m IC เล็ก"
    if "one-off" in w or "ครั้งเดียว" in w:
        return "one-off"
    return "ไม่มี EBIT"


def short_v(why):
    w = " ".join(why or [])
    for k, lab in (("ตัดออก", "ตัดออก"), ("การเงิน", "การเงิน"), ("ขั้นต่ำ", "EV ขั้นต่ำ"), ("one-off", "one-off"),
                   ("< $200M", "รายได้ < $200M"), ("EBIT TTM ไม่เป็นบวก", "EBIT ≤ 0"), ("EV", "ไม่มี EV")):
        if k in w:
            return lab
    return "ไม่ผ่าน"




# what one row carries to the page (the rest stays server-side)
KEEP = ("oo", "qp", "pen", "pen_why", "pen_shown", "q_parts", "q_base", "info", "sim", "challenger", "fcfy", "moat_why",
        "t", "name", "sic", "sec_sic", "dom", "hold", "excl", "hits", "flags", "roic", "q_why", "q_short", "v_ok", "v_why",
        "v_short", "ev", "rev", "g3", "ic", "moat", "ey", "evebit", "rel", "rel_basis", "gimp", "gimp_clip", "ggap",
        "s_value", "s_value_n", "s_quality", "s_moat", "s_comb", "comb_n")


def caveats(ind):
    """Notes that depend on what the data holds, so they stay true after a rerun."""
    rows, out = ind["rows"], []
    g1 = sum(1 for r in rows if (r["qp"]["cap"] or {}).get("basis") == "engine")
    if g1 < len(rows):
        off = sorted(k for k, ok in ind["scored"].items() if not ok)
        out.append(f"กลุ่ม 1 จากเอนจินมี {g1} จาก {len(rows)} ราย ที่เหลือ CAP และ incremental ROIC ประมาณจาก fy rows "
                   f"(ภาษี {DEFAULT_TAX_RATE:.0%} คงที่)"
                   + (f"; ส่วนที่ยังไม่นับคะแนนจนกว่าข้อมูลครบ ≥{COVER_MIN:.0%} ของชั้น: {', '.join(off)}" if off else ""))
    g2 = sum(1 for r in rows if r["info"]["have"])
    if g2 < len(rows):
        out.append(f"กลุ่ม 2 (ราคาย้อนหลัง) มี {g2} จาก {len(rows)} ราย แถวที่ไม่มีแสดงเหตุผล")
    ifrs = sorted(r["t"] for r in rows if any(IFRS_TH in f[1] for f in r["flags"]))
    if ifrs:
        out.append(IFRS_TH + ": " + ", ".join(ifrs))
    o1 = sorted(t for t in ONE_OFF_GAINS if any(r["t"] == t for r in rows))
    if o1:
        out.append("one-off O1 มาจากหลักฐานในข่าวผลประกอบการ (" + ", ".join(o1) + ") ไม่ได้มาจากตัวอ่าน SEC")
    return out


def slim(ind):
    """The page payload: rows cut to KEEP, every metric's source string sent once."""
    srcs, idx, rows = [], {}, []
    for r in ind["rows"]:
        m = {}
        for k, x in r["m"].items():
            s = x["src"]
            if s not in idx:
                idx[s] = len(srcs)
                srcs.append(s)
            m[k] = {"v": x["v"], "s": idx[s], "p": x["per"], "why": x["why"]}
        o = {k: r.get(k) for k in KEEP}
        o["m"] = m
        rows.append(o)
    return dict({k: v for k, v in ind.items() if k != "rows"}, rows=rows, sources=srcs, caveats=caveats(ind))
