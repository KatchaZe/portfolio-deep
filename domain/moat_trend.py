"""
moat_trend — is the competitive position widening or narrowing? Four signals and a
label (docs/peer-panel/SPEC.md). Display only: nothing here feeds a score (PP1).

    S1 spread_slope        OLS slope of ROIC - WACC across the contiguous fiscal years
    S2 share_change        3-year change in industry revenue share, relative to the base
    S3 gm_gap_change       (target GM - peer median GM) at FY0 minus the same at FY3
    S4 incremental_margin  operating margin on the revenue added FY3 -> FY0, vs FY0's own

Each signal votes +1 / 0 / -1 against its band below. A signal that cannot be measured
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
THRESHOLDS = "provisional"

MIN_SLOPE_YEARS = 3        # a slope through two points is a difference, not a trend
MIN_PEERS_GM = 3           # a peer median of fewer firms is one firm's margin
LABEL_AT = 2               # sum of votes for improving / eroding
INTANGIBLE_PP = (10.0, 20.0)   # GM above the peer median: weak / moderate hint
SCALE_SHARE = (0.10, 0.25)     # industry revenue share: weak / moderate hint


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


def spread_slope(roic_map, wacc_pct):
    """(signal, reason). `roic_map` is trend.roic_series; WACC is today's, held across
    the window (the app keeps no WACC history)."""
    if wacc_pct is None:
        return None, "S1: no WACC"
    win = trend._window(roic_map)
    if len(win) < MIN_SLOPE_YEARS:
        return None, f"S1: {len(win)} contiguous fiscal years of ROIC (need {MIN_SLOPE_YEARS})"
    xs = [_years(win[0], d) for d in win]
    ys = [roic_map[d]["roic"] - wacc_pct for d in win]
    b = _slope(xs, ys)
    if b is None:
        return None, "S1: fiscal-year dates do not vary"
    return {"name": "spread_slope", "value": round(b, 2), "unit": "pp/yr",
            "direction": direction(b, SPREAD_SLOPE_PP),
            "window": f"FY{win[0][:4]}..FY{win[-1][:4]}"}, None


def share_change(share):
    """(signal, reason) from peer_panel.industry_share."""
    if not share or not share.get("years"):
        return None, "S2: no industry share"
    new = share["years"][-1]
    old = new - peer_panel.SHARE_LOOKBACK
    a, b = share["target_share"].get(str(new)), share["target_share"].get(str(old))
    if a is None or b is None:
        return None, f"S2: target share missing in CY{old} or CY{new}"
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
    v = (gap0 - gap3) * 100
    return {"name": "gm_gap_change", "value": round(v, 2), "unit": "pp",
            "direction": direction(v, GM_GAP_PP),
            "window": f"FY{target['fy3']['end'][:4]}..FY{target['fy0']['end'][:4]}"}, None


def incremental_margin(target):
    """(signal, reason): (OI FY0 - OI FY3)/(Rev FY0 - Rev FY3) minus OI/Rev at FY0, pp.
    Only when revenue grew: on shrinking revenue the ratio's sign flips its meaning."""
    f0, f3 = target.get("fy0") or {}, target.get("fy3") or {}
    r0, r3, o0, o3 = f0.get("revenue"), f3.get("revenue"), f0.get("operating_income"), f3.get("operating_income")
    if None in (r0, r3, o0, o3):
        return None, "S4: revenue or operating income missing at FY0 or FY3"
    if r0 <= 0 or r3 <= 0 or r0 <= r3:
        return None, "S4: revenue did not grow FY3 -> FY0"
    v = ((o0 - o3) / (r0 - r3) - o0 / r0) * 100
    return {"name": "incremental_margin", "value": round(v, 2), "unit": "pp",
            "direction": direction(v, INC_PP),
            "window": f"FY{f3['end'][:4]}..FY{f0['end'][:4]}"}, None


def label(signals):
    if len(signals) < LABEL_AT:
        return None
    s = sum(x["direction"] for x in signals)
    return "improving" if s >= LABEL_AT else "eroding" if s <= -LABEL_AT else "stable"


def source_hints(target, peers, share):
    """What the numbers are consistent with. Never a finding."""
    hints = []
    t0 = _gm((target or {}).get("fy0"))
    med = _median([_gm(p.get("fy0")) for p in peers])
    if t0 is not None and med is not None and sum(1 for p in peers if _gm(p.get("fy0")) is not None) >= MIN_PEERS_GM:
        gap = (t0 - med) * 100
        if gap >= INTANGIBLE_PP[0]:
            hints.append({"type": "intangible",
                          "strength": "moderate" if gap >= INTANGIBLE_PP[1] else "weak",
                          "evidence": f"gross margin {gap:.0f}pp above the peer median (FY0): "
                                      "consistent with pricing power from brand or IP"})
    if share and share.get("years"):
        s = share["target_share"].get(str(share["years"][-1]))
        if s is not None and s >= SCALE_SHARE[0]:
            hints.append({"type": "scale",
                          "strength": "moderate" if s >= SCALE_SHARE[1] else "weak",
                          "evidence": f"{s:.0%} of industry revenue among US SEC filers "
                                      f"(CY{share['years'][-1]}): consistent with a scale advantage"})
    return hints


def build(f, target, peers, share, wacc_pct):
    """results[ticker]["moat_trend"]. `f` facts (object or stored dict) for the ROIC
    series; `target` the target's panel point; `peers` the eligible peer points
    (peer_panel.peer_set); `share` peer_panel.industry_share or None."""
    rmap = trend.roic_series(trend._get(f, "operating_income_annuals_dated"),
                             trend._get(f, "ic_components_dated", {}), trend._tax_rate(f))
    tgt = target or {}
    signals, flags = [], []
    for sig, why in (spread_slope(rmap, wacc_pct), share_change(share),
                     gm_gap_change(tgt, peers or []), incremental_margin(tgt)):
        if sig:
            signals.append(sig)
        else:
            flags.append(why)
    lab = label(signals)
    if lab is None:
        flags.append(f"{len(signals)} signal(s): no label (need {LABEL_AT})")
    return {"label": lab, "n_signals": len(signals), "signals": signals,
            "source_hints": source_hints(tgt, peers or [], share),
            "thresholds": THRESHOLDS, "flags": flags}
