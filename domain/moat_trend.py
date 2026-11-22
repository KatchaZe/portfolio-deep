"""
moat_trend — is the competitive position widening or narrowing? Four signals and a
label (docs/peer-panel/SPEC.md). Display only: nothing here feeds a score (PP1).

    S1 spread_slope        OLS slope of ROIC - WACC across the contiguous fiscal years
    S2 share_change        3-year change in industry revenue share, relative to the base
    S3 gm_gap_change       (target GM - peer median GM) at FY0 minus the same at FY3
    S4 incremental_margin  operating margin on the revenue added FY3 -> FY0, vs FY0's own

Values are fractions except S1 (pp per year); the bands below are in points. Each
signal votes +1 / 0 / -1 against its band. A signal that cannot be measured
is ABSENT (with a flag), never a 0 vote: "unknown" is not "stable". Fewer than two
signals gives no label. Pure: no network, no clock.
"""
import datetime as dt

from domain import peer_panel, trend

# Provisional bands: set by judgment, not yet calibrated on history. The output says so.
SPREAD_SLOPE_PP = 1.0      # ROIC - WACC slope, pp per year
SHARE_REL = 0.10           # industry share change, relative to the base-year share
GM_GAP_PP = 2.0            # change in the gross-margin gap to the peer median, pp
INC_PP = 5.0               # incremental vs average operating margin, pp
INC_MIN_GROWTH = 0.10      # S4 needs revenue FY0/FY3 - 1 >= this: a tiny delta makes the ratio noise (R21)
THRESHOLDS = "provisional"

MIN_SLOPE_YEARS = 3        # a slope through two points is a difference, not a trend
MIN_PEERS_GM = peer_panel.GM_MIN_COMPARATORS   # a peer median of fewer firms is one firm's margin
SUB_MIN_MATCHED = 5        # S2 on the same-SIC sub-group needs this many filers in both years (R28)
LABEL_AT = 2               # sum of votes for improving / eroding
INTANGIBLE_PP = (10.0, 20.0)   # GM above the peer median: weak / moderate hint
SCALE_SHARE = (0.10, 0.25)     # industry revenue share: weak / moderate hint
EFFICIENT_TOP5 = (0.80, 0.90)  # top-5 share of industry revenue: weak / moderate hint (R22)
EFFICIENT_SHARE_PP = 2.0       # ... with the target's 3-year share change within this
EFFICIENT_MAX_MEMBERS = 30     # ... in an industry of at most this many SEC filers
COST_OPM_PP = 5.0              # operating margin above the peer median, pp (R22)
COST_GM_PP = 2.0               # ... with gross margin no more than this above the median
TOP5_TREND_PP = 2.0            # top-5 share change that counts as a trend (R23)
OPM_GAP_PP = 2.0               # change in the FY operating-margin gap that counts (R23)
WATCH_N = 3

# Thai names for the watch list (moat_trend.watch); the UI shows them as written
SIGNAL_TH = {"spread_slope": "ROIC − WACC รายปี",
             "share_change": "ส่วนแบ่งรายได้ในอุตสาหกรรม",
             "gm_gap_change": "Gross margin เทียบ median คู่แข่ง",
             "incremental_margin": "Margin ส่วนเพิ่มเทียบ margin เฉลี่ย"}
# each signal's band in the signal's own unit (S2-S4 are fractions)
SIGNAL_BAND = {"spread_slope": SPREAD_SLOPE_PP, "share_change": SHARE_REL,
               "gm_gap_change": GM_GAP_PP / 100, "incremental_margin": INC_PP / 100}


def direction(value, band):
    if value > band:
        return 1
    if value < -band:
        return -1
    return 0


def _years(d0, d1):
    return (dt.date.fromisoformat(d1[:10]) - dt.date.fromisoformat(d0[:10])).days / 365.25


def _slope(xs, ys):
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx if sxx else None


def spread_slope(roic_map, wacc_pct, latest_fy=None):
    """(signal, reason). `roic_map` is trend.roic_series; WACC is today's, held across
    the window (the app keeps no WACC history). `latest_fy` the newest fiscal-year end
    with operating income: a window ending more than one fiscal year before it is not
    today's trend (E57, TMDX's ROIC stops at FY2022 while S3 and S4 read FY2025)."""
    if wacc_pct is None:
        return None, "S1: no WACC"
    win = trend._window(roic_map)
    if len(win) < MIN_SLOPE_YEARS:
        return None, f"S1: {len(win)} contiguous fiscal years of ROIC (need {MIN_SLOPE_YEARS})"
    if latest_fy and (dt.date.fromisoformat(latest_fy[:10]) - dt.date.fromisoformat(win[-1][:10])).days > trend.FY_STEP_DAYS[1]:
        behind = round(_years(win[-1], latest_fy))
        return None, f"S1: ROIC ends FY{win[-1][:4]}, {behind} fiscal years before the latest FY{latest_fy[:4]}"
    xs = [_years(win[0], d) for d in win]
    ys = [roic_map[d]["roic"] - wacc_pct for d in win]
    b = _slope(xs, ys)
    if b is None:
        return None, "S1: fiscal-year dates do not vary"
    return {"name": "spread_slope", "value": round(b, 2), "unit": "pp/yr",
            "direction": direction(b, SPREAD_SLOPE_PP),
            "window": f"FY{win[0][:4]}..FY{win[-1][:4]}"}, None


def share_change(share):
    """(signal, reason): S2 on the same-SIC sub-group (share["sub_group"]) when it has
    SUB_MIN_MATCHED filers with revenue in both years and a target share, else on the
    full industry (R28). The signal's `basis` says which; `basis_reason` why not the
    sub-group when the industry was used."""
    sub = (share or {}).get("sub_group") or {}
    m = sub.get("matched_n")
    if sub.get("sic") and isinstance(m, int) and m >= SUB_MIN_MATCHED:
        sig, why = _share_signal(sub)
        if sig:
            return dict(sig, basis="sub_group", sic=sub["sic"], matched_n=m,
                        n_members=sub.get("n_members")), None
    note = sub_fallback_reason(sub) if sub.get("sic") else (
        sub.get("reason") or "ไม่มีกลุ่มธุรกิจเดียวกัน จึงใช้ทั้งอุตสาหกรรม")
    sig, why = _share_signal(share)
    if not sig:
        return None, why
    return dict(sig, basis="industry", matched_n=share.get("matched_n"),
                n_members=share.get("n_members"), basis_reason=note), None


def sub_fallback_reason(sub):
    """Thai: why S2 could not use the same-SIC sub-group, naming the first failed rule
    and its counts. The cause is checked in the order industry_share applies its rules:
    a year's coverage (R20), the matched set (R13), then SUB_MIN_MATCHED (R28)."""
    head, tail = f"ธุรกิจเดียวกัน SIC {sub['sic']}", "จึงใช้ทั้งอุตสาหกรรม"
    if sub.get("reason"):
        return f"{sub['reason']} · S2 ใช้ทั้งอุตสาหกรรม"
    ys = sub.get("years") or []
    if not ys:
        return f"{head} ไม่มีปีปฏิทินที่ใช้ได้ {tail}"
    new = ys[-1]
    old = new - peer_panel.SHARE_LOOKBACK
    n = sub.get("n_members")
    m = sub.get("matched_n")
    for y in (old, new) if m is None else ():    # a matched set exists only when both passed
        k = str(y)
        cov = (sub.get("coverage") or {}).get(k)
        if cov is None:
            return f"{head}: CY{y} ไม่มีข้อมูลรายได้จาก SEC frames {tail}"
        if cov < peer_panel.COVERAGE_MIN:
            return (f"{head}: CY{y} มีข้อมูลใน SEC {(sub.get('n_covered') or {}).get(k)} จาก {n} ราย "
                    f"({cov:.0%} ต่ำกว่าเกณฑ์ {peer_panel.COVERAGE_MIN:.0%}) ไม่คิดการเปลี่ยนส่วนแบ่งในกลุ่มนี้ "
                    f"{tail}")
    then = (sub.get("n_with_revenue") or {}).get(str(old))
    if isinstance(m, int) and m >= SUB_MIN_MATCHED and sub.get("share_change_pp") is None:
        if sub.get("matched_share_then") is None:
            return f"{head}: ไม่มีรายได้ของหุ้นนี้ใน CY{old} หรือ CY{new} {tail}"
        return (f"{head}: มีรายได้ทั้ง CY{old} และ CY{new} {m} จาก {then} ราย "
                f"(ต่ำกว่าเกณฑ์ {peer_panel.COVERAGE_MIN:.0%}) {tail}")
    if isinstance(m, int) and m >= SUB_MIN_MATCHED:
        return f"{head}: ส่วนแบ่งของหุ้นนี้ใน CY{old} เป็นศูนย์ เทียบการเปลี่ยนไม่ได้ {tail}"
    return (f"{head} มี {m or 0} รายที่มีรายได้ทั้ง CY{old} และ CY{new} "
            f"(ต้อง ≥{SUB_MIN_MATCHED}) {tail}")


def _share_signal(share):
    """(signal, reason) from one peer_panel.industry_share, on the matched set of filers
    with revenue in both years (SPEC R13)."""
    if not share or not share.get("years"):
        return None, "S2: no industry share"
    new = share["years"][-1]
    old = new - peer_panel.SHARE_LOOKBACK
    a, b = share.get("matched_share_now"), share.get("matched_share_then")
    if a is None or b is None:
        return None, f"S2: no target share on the filers reporting in both CY{old} and CY{new}"
    if b <= 0:
        return None, f"S2: target share in CY{old} is zero: no relative change"
    rel = (a - b) / b
    return {"name": "share_change", "value": round(rel, 4), "unit": "relative",
            "direction": direction(rel, SHARE_REL), "window": f"CY{old}..CY{new}"}, None


def _gm(leg):
    return peer_panel._ratio((leg or {}).get("gross_profit"), (leg or {}).get("revenue"))


def _median(v):
    return peer_panel.stats(v)["median"]


def gm_gap_change(target, peers):
    """(signal, reason). The peer median at FY0 and at FY3 is taken over the same peers:
    those with a gross margin at both."""
    t0, t3 = _gm(target.get("fy0")), _gm(target.get("fy3"))
    if t0 is None or t3 is None:
        return None, "S3: target gross margin missing at FY0 or FY3"
    both = [(_gm(p.get("fy0")), _gm(p.get("fy3"))) for p in peers]
    both = [(a, b) for a, b in both if a is not None and b is not None]
    if len(both) < MIN_PEERS_GM:
        return None, f"S3: {len(both)} peers with gross margin at FY0 and FY3 (need {MIN_PEERS_GM})"
    gap0 = t0 - _median([a for a, _ in both])
    gap3 = t3 - _median([b for _, b in both])
    v = gap0 - gap3
    return {"name": "gm_gap_change", "value": round(v, 6), "unit": "fraction",
            "direction": direction(v, GM_GAP_PP / 100),
            "window": f"FY{target['fy3']['end'][:4]}..FY{target['fy0']['end'][:4]}"}, None


def incremental_margin(target):
    """(signal, reason): (OI FY0 - OI FY3)/(Rev FY0 - Rev FY3) minus OI/Rev at FY0, as a
    fraction (SPEC R8); the band INC_PP is in points.
    Only when revenue grew at least INC_MIN_GROWTH over the three years (R21): on
    shrinking revenue the ratio's sign flips its meaning, and on a near-zero delta it
    is noise (ABBV 2026-11-20: -122.6 pp from 0.3% growth)."""
    f0, f3 = target.get("fy0") or {}, target.get("fy3") or {}
    r0, r3, o0, o3 = f0.get("revenue"), f3.get("revenue"), f0.get("operating_income"), f3.get("operating_income")
    if None in (r0, r3, o0, o3):
        return None, "S4: revenue or operating income missing at FY0 or FY3"
    if r0 <= 0 or r3 <= 0:
        return None, "S4: revenue not positive at FY0 or FY3"
    grew = r0 / r3 - 1
    if grew < INC_MIN_GROWTH:
        return None, f"S4: revenue grew {grew:.1%} in 3 years (need ≥{INC_MIN_GROWTH:.0%})"
    v = (o0 - o3) / (r0 - r3) - o0 / r0
    return {"name": "incremental_margin", "value": round(v, 6), "unit": "fraction",
            "direction": direction(v, INC_PP / 100),
            "window": f"FY{f3['end'][:4]}..FY{f0['end'][:4]}"}, None


def label(signals):
    if len(signals) < LABEL_AT:
        return None
    s = sum(x["direction"] for x in signals)
    return "improving" if s >= LABEL_AT else "eroding" if s <= -LABEL_AT else "stable"


def _opm(leg):
    return peer_panel._ratio((leg or {}).get("operating_income"), (leg or {}).get("revenue"))


def _trend_of(signal):
    return None if not signal else signal["direction"]


def _trend(v, band):
    return None if v is None else direction(v, band)


def _gaps(target, peers, fn, legs=("fy0", "fy3")):
    """(target - peer median) at each leg over the peers with `fn` at every leg, or None
    under MIN_PEERS_GM such peers or a missing target value."""
    t = [fn((target or {}).get(k)) for k in legs]
    both = [[fn(p.get(k)) for k in legs] for p in peers]
    both = [v for v in both if None not in v]
    if None in t or len(both) < MIN_PEERS_GM:
        return None
    return [t[i] - _median([v[i] for v in both]) for i in range(len(legs))]


def _fy(leg):
    return f"FY{leg['end'][:4]}" if (leg or {}).get("end") else None


def source_hints(target, peers, share, signals=(), gm_peers=None, gm_scope=None):
    """What the numbers are consistent with. Never a finding. Each hint carries the
    numbers behind it as `measures` [{metric, value, period}] (the UI words them) and a
    `trend` (+1/0/-1, None unknown) from the signal that moves with it (R23).
    `gm_peers` are the gross-margin comparators (peer_panel.gm_scope, R26); None = no
    valid comparison, so no hint that reads gross margin (intangible, cost_advantage).
    The scale hint's trend is the full industry's share change, the set its share is on."""
    hints, tgt = [], target or {}
    sig = {x["name"]: x["direction"] for x in signals}
    gm = None if gm_peers is None else _gaps(tgt, gm_peers, _gm, ("fy0",))
    comp = None if not gm_scope else {k: gm_scope.get(k) for k in ("level", "sic", "n")}
    if gm is not None:
        gap = gm[0] * 100
        if gap >= INTANGIBLE_PP[0]:
            hints.append({"type": "intangible",
                          "strength": "moderate" if gap >= INTANGIBLE_PP[1] else "weak",
                          "measures": [{"metric": "gm_gap_pp", "value": round(gap, 1),
                                        "period": _fy(tgt.get("fy0")), "comparators": comp}],
                          "trend": sig.get("gm_gap_change"),
                          "evidence": f"gross margin {gap:.0f}pp above the peer median (FY0): "
                                      "consistent with pricing power from brand or IP"})
    years = (share or {}).get("years") or []
    if years:
        cy = f"CY{years[-1]}"
        s = share["target_share"].get(str(years[-1]))
        if s is not None and s >= SCALE_SHARE[0]:
            hints.append({"type": "scale",
                          "strength": "moderate" if s >= SCALE_SHARE[1] else "weak",
                          "measures": [{"metric": "industry_share", "value": round(s, 4), "period": cy}],
                          "trend": _trend_of(_share_signal(share)[0]),
                          "evidence": f"{s:.0%} of industry revenue among US SEC filers "
                                      f"({cy}): consistent with a scale advantage"})
        t5 = (share.get("top5_share") or {}).get(str(years[-1]))
        chg, n = share.get("share_change_pp"), share.get("n_members")
        if (t5 is not None and t5 >= EFFICIENT_TOP5[0] and chg is not None
                and abs(chg) <= EFFICIENT_SHARE_PP and n is not None and n <= EFFICIENT_MAX_MEMBERS):
            window = f"CY{years[-1] - peer_panel.SHARE_LOOKBACK}..{cy}"
            hints.append({"type": "efficient_scale",
                          "strength": "moderate" if t5 >= EFFICIENT_TOP5[1] else "weak",
                          "measures": [{"metric": "top5_share", "value": round(t5, 4), "period": cy},
                                       {"metric": "share_change_pp", "value": round(chg, 2), "period": window},
                                       {"metric": "n_members", "value": n, "period": cy}],
                          "trend": _trend(share.get("top5_change_pp"), TOP5_TREND_PP),
                          "evidence": f"top 5 filers hold {t5:.0%} of industry revenue ({cy}), "
                                      f"the target's share moved {chg:+.1f}pp over {window}, "
                                      f"{n} SEC filers: consistent with efficient scale"})
    opm_t = peer_panel._ratio(tgt.get("ebit_ttm"), tgt.get("revenue_ttm"))
    opm_p = [peer_panel._ratio(p.get("ebit_ttm"), p.get("revenue_ttm")) for p in peers]
    opm_p = [v for v in opm_p if v is not None]
    if opm_t is not None and gm is not None and len(opm_p) >= MIN_PEERS_GM:
        o_gap, g_gap = (opm_t - _median(opm_p)) * 100, gm[0] * 100
        if o_gap >= COST_OPM_PP and g_gap <= COST_GM_PP:
            fy = _gaps(tgt, peers, _opm)
            hints.append({"type": "cost_advantage", "strength": "moderate",
                          "measures": [{"metric": "opm_gap_pp", "value": round(o_gap, 1), "period": "TTM"},
                                       {"metric": "gm_gap_pp", "value": round(g_gap, 1),
                                        "period": _fy(tgt.get("fy0")), "comparators": comp}],
                          "trend": None if fy is None else _trend(fy[0] - fy[1], OPM_GAP_PP / 100),
                          "evidence": f"operating margin {o_gap:.0f}pp above the peer median (TTM) with "
                                      f"gross margin {g_gap:+.0f}pp vs the median (FY0): "
                                      "consistent with a cost advantage"})
    return hints


def watch(signals):
    """Up to WATCH_N Thai names of the signals nearest a band edge (in band units), the
    ones whose vote the next quarter is most likely to flip."""
    def dist(x):
        b = SIGNAL_BAND[x["name"]]
        return abs(abs(x["value"]) - b) / b
    return [SIGNAL_TH[x["name"]] for x in sorted(signals, key=dist)[:WATCH_N]]


NO_EXCESS_FLAG = "hints: no excess return (ROIC − WACC ≤ 0)"


def excess_return(spread_pct):
    """True only for a real number above zero (R24); None, NaN, bool and 0 are not."""
    return (isinstance(spread_pct, (int, float)) and not isinstance(spread_pct, bool)
            and spread_pct == spread_pct and spread_pct > 0)


def gm_signal(target, peers, sics):
    """(S3, reason, applicable). S3 on the gm_scope comparators with gross margin at FY0
    and FY3 (R26); not applicable to a financial target (R25)."""
    scope = peer_panel.gm_scope(target, peers, sics, ("fy0", "fy3"))
    if not scope["applies"]:
        return None, f"S3: {scope['reason']}", False
    t0, t3 = _gm((target or {}).get("fy0")), _gm((target or {}).get("fy3"))
    if t0 is None or t3 is None:
        return None, "S3: target gross margin missing at FY0 or FY3", True
    if scope["level"] is None:
        return None, f"S3: {scope['reason']}", True
    sig, why = gm_gap_change(target, scope["peers"])
    if sig:
        sig["comparators"] = peer_panel.record(scope)
    return sig, why, True


def build(f, target, peers, share, wacc_pct, spread_pct=None, sics=None):
    """results[ticker]["moat_trend"]. `f` facts (object or stored dict) for the ROIC
    series; `target` the target's panel point; `peers` the eligible peer points
    (peer_panel.peer_set); `share` peer_panel.industry_share or None; `spread_pct` the
    current ROIC − WACC in points, the value the card's Moat chip shows: a source hint
    says where excess returns could come from, so none is emitted without them (R24).
    `sics` {symbol: raw SIC} (peer_panel.SIC_SOURCE): gross margin is compared within
    the same SIC only, and not at all for a financial target (R25-R26). `votes` says how
    many signals could vote, how many did, and which do not apply to this business."""
    rmap = trend.roic_series(trend._get(f, "operating_income_annuals_dated"),
                             trend._get(f, "ic_components_dated", {}), trend._tax_rate(f))
    tgt = target or {}
    signals, flags, na = [], [], []
    s3, s3_why, s3_ok = gm_signal(tgt, peers or [], sics)
    if not s3_ok:
        na.append("gm_gap_change")
    latest = max(trend._pairs(trend._get(f, "operating_income_annuals_dated")), default=None)
    for sig, why in (spread_slope(rmap, wacc_pct, latest), share_change(share),
                     (s3, s3_why), incremental_margin(tgt)):
        if sig:
            signals.append(sig)
        else:
            flags.append(why)
    lab = label(signals)
    if lab is None:
        flags.append(f"{len(signals)} signal(s): no label (need {LABEL_AT})")
    hints = []
    if excess_return(spread_pct):
        scope = peer_panel.gm_scope(tgt, peers or [], sics)
        hints = source_hints(tgt, peers or [], share, signals, scope["peers"], scope)
        if scope["peers"] is None:
            flags.append(f"hints: gross margin hints skipped ({scope['reason']})")
    else:
        flags.append(NO_EXCESS_FLAG)
    votes = {"possible": len(SIGNAL_BAND) - len(na), "cast": len(signals), "not_applicable": na}
    return {"label": lab, "n_signals": len(signals), "signals": signals, "votes": votes,
            "source_hints": hints,
            "watch": watch(signals), "thresholds": THRESHOLDS, "flags": flags}
