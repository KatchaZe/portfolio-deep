"""Which valuation model a company gets (Damodaran: the model follows the business).

    FINANCIAL  banks, insurers, brokers: excess-return model on book equity; WACC,
               invested capital and FCFF are not meaningful (deposits are raw material)
    YOUNG      no operating profit yet: forward young-company DCF with failure risk
    CYCLICAL   commodity and heavy-cycle industries, or a firm whose own last five
               years show a revenue fall with a margin swing (firm_cycle_test): FCFF
               DCF on today's revenue, starting at today's margin held to the peak line
               (peak_line), whose margin reverts to its own multi-year median
               (Damodaran's scaled normalization)
    STANDARD   everything else: FCFF DCF on today's margin

Decided once per evaluation from the industry and the income statement, so the rest
of the engine branches on one value instead of re-deriving the type in each stage.
Pure: no I/O.
"""
from enum import Enum
from typing import NamedTuple

from sources.industries import FINANCIAL_INDUSTRIES


class CompanyType(str, Enum):
    FINANCIAL = "financial"
    YOUNG = "young"
    CYCLICAL = "cyclical"
    STANDARD = "standard"


# Industries whose margins swing with a commodity price or a capacity cycle. A
# structural grower that happens to sit in a cyclical-looking industry (semiconductor
# designers) is not in the list: reverting NVDA to its 10-year median margin would be
# a forecast, not a normalization.
CYCLICAL_INDUSTRIES = frozenset({
    "Oil/Gas (Integrated)", "Oil/Gas (Production and Exploration)", "Oil/Gas Distribution",
    "Oilfield Svcs/Equip.", "Coal & Related Energy", "Metals & Mining", "Precious Metals",
    "Steel", "Chemical (Basic)", "Chemical (Diversified)", "Paper/Forest Products",
    "Auto & Truck", "Auto Parts", "Rubber& Tires", "Homebuilding", "Building Materials",
    "Air Transport", "Shipbuilding & Marine", "Trucking", "Farming/Agriculture",
})
MIN_MARGIN_YEARS = 5           # a "normal" margin needs at least one cycle of history

# Firm-level cyclical test (user decision 2026-11-26, design "ก. revised"; rule (c)
# replaced 2026-11-28, E107). A firm outside CYCLICAL_INDUSTRIES is cyclical when all
# three hold on its own FISCAL years (today's TTM margin plays no part):
#   (a) revenue fell >= CYCLE_REVENUE_DROP year on year, with the margin falling the
#       same year, at least once in the last CYCLE_LOOKBACK_YEARS fiscal years: demand
#       that turns, not a grower's pause;
#   (b) an operating loss year (below CYCLE_LOSS_MARGIN), or an operating-margin range
#       above CYCLE_MARGIN_RANGE, in the same years: the swing reaches the margin;
#   (c) the margin recovered after that fall: a later fiscal year reached the normal
#       margin. A fall that never turned is a decline or a turnaround (INTC, REGN,
#       STAA: 0.2-0.8x their normal today), and normalizing it would value the recovery
#       nobody has seen; without (c) the 614-filer scan made 8 of them cyclical (E107).
# Today's margin no longer decides the class (E107): the old rule (c) "above 1.5x normal" made
# MU standard at 41.0% (FV $1,532, BUY) and cyclical at 44.8% (FV $444, HOLD), so a
# falling peak tripled the value. Above the peak line (CYCLE_PEAK_MARGIN_X x normal,
# Cycle.at_peak) the line only caps the margin the DCF starts at and the returns
# Economics scores (E105-E106), which is continuous through the line.
# MU passes both (FY2023 revenue -49%, FY2023 margin -37%). NVDA fails (a): its revenue
# has not fallen in five years.
CYCLE_LOOKBACK_YEARS = 5
CYCLE_REVENUE_DROP = 0.15
CYCLE_MARGIN_RANGE = 0.30
CYCLE_PEAK_MARGIN_X = 1.5
# Tuned on the 614 cached filers (scan 2026-11-26), each with its reason:
# - the fall in (a) must take the margin down with it the same year: demand that turns
#   does (operating leverage); a fall with a rising margin is a divestiture or a
#   restatement (APP FY2023 -35% while its margin went -1.7% -> 41.9%: the apps
#   business restated out of the later year only);
# - the loss in (b) must be a real one, below CYCLE_LOSS_MARGIN: a breakeven year is
#   not the margin turning (VSEC FY2021 -0.6% beside 7-9% in every other year);
# - the normal margin must reach CYCLE_MIN_NORMAL_MARGIN (the 5% below which the DCF
#   already treats a margin as a stage, not a steady state): a ~0% "normal" is a
#   turnaround's loss history, and reverting to it values a recovered firm near zero
#   (BKD 0.3%, CGNT 0.4%, INTC on a six-year window 1.9%).
CYCLE_LOSS_MARGIN = -0.02
CYCLE_MIN_NORMAL_MARGIN = 0.05
# The cyclical normal margin is the median over up to NORMAL_YEARS fiscal years. Seven
# years span one memory or commodity cycle; with five or six the median is used and
# flagged (short_window), below five a firm is not normalized at all.
NORMAL_YEARS, NORMAL_MIN_YEARS = 10, 7
# E92 (2026-11-28): the base is the normal margin on CURRENT revenue (Damodaran,
# normearn.htm: average margin x current revenue). The log-linear revenue trend that
# used to replace current revenue at a "revenue peak" is retired from the fair value:
# its slope came mostly from the peak year it tested (MU FY17-25 2.4%/yr, FY17-26
# 11.4%/yr) and moved FV between $145 and $411 on the window. revenue_trend() is kept
# for the Bear cross-check only (deep_v82.cycle_checks).
# Bear / Bull margins for a cyclical: the 25th / 75th percentile of the same window.
# A trough year held forever values the firm as if every year were its worst; the
# lower quartile is the low part of the cycle persisting. Floored at BEAR_CYCLE_FLOOR
# so the Bear FCFF stays a going concern, not a liquidation.
BEAR_PERCENTILE, BULL_PERCENTILE = 0.25, 0.75
BEAR_CYCLE_FLOOR = 0.02


class Cycle(NamedTuple):
    """What normalizing a cyclical firm runs on, decided once from its own history."""
    basis: str                      # "industry" (CYCLICAL_INDUSTRIES) or "firm" (rules a-c)
    reason: str                     # Thai, shown on the card and the flag
    normal_margin: float            # median of up to NORMAL_YEARS fiscal years
    margin_years: int               # years in that median
    short_window: bool              # fewer than NORMAL_MIN_YEARS (5 or 6)
    low_margin: float               # Bear: BEAR_PERCENTILE of the window, floored
    high_margin: float              # Bull: BULL_PERCENTILE of the window
    trough_margin: float            # the window's lowest year, floored (Monte Carlo at a peak)
    at_peak: bool                   # today's margin above peak_line: the cap binds


def _quantile(xs, q):
    xs = sorted(xs)
    pos = q * (len(xs) - 1)
    i = int(pos)
    return xs[i] if i + 1 >= len(xs) else xs[i] + (xs[i + 1] - xs[i]) * (pos - i)


def _years_between(a, b):
    from datetime import date
    return (date.fromisoformat(str(a)[:10]) - date.fromisoformat(str(b)[:10])).days / 365.25


def revenue_trend(revenue_dated):
    """(trend revenue at the newest date, annual growth) from a log-linear least-squares
    fit through [(date, revenue)] (any order, positive revenues only); (None, None)
    with fewer than three points. A Bear cross-check only (E92): its slope is set
    largely by the newest point, so it never drives the fair value."""
    import math
    pts = [(d, r) for d, r in revenue_dated if r and r > 0]
    if len(pts) < 3:
        return None, None
    newest = max(d for d, _ in pts)
    xs = [_years_between(d, newest) for d, _ in pts]
    ys = [math.log(r) for _, r in pts]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return None, None
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    return math.exp(my - b * mx), math.exp(b) - 1


def firm_cycle_test(revenue_dated, margin_history, current_margin, normal):
    """(passes, Thai reason): rules (a)-(c) above on the newest CYCLE_LOOKBACK_YEARS.
    `current_margin` is kept for the reason text only: it never decides the class."""
    rev = sorted(revenue_dated or [], reverse=True)[:CYCLE_LOOKBACK_YEARS + 1]
    mh = list(margin_history or [])
    # margin_history is aligned with the fiscal years newest first, like `rev`
    drops = [(d0, r0 / r1 - 1, i) for i, ((d0, r0), (_, r1)) in enumerate(zip(rev, rev[1:]))
             if r1 and r1 > 0 and i + 1 < len(mh) and mh[i] is not None and mh[i + 1] is not None
             and mh[i] < mh[i + 1] and r0 / r1 - 1 <= -CYCLE_REVENUE_DROP]
    # (c) the margin came back: a later fiscal year at or above the normal margin
    turned = [x for x in drops if normal and any(m is not None and m >= normal for m in mh[:x[2]])]
    drop = min(turned, key=lambda x: x[1]) if turned else None
    recent = [m for m in mh[:CYCLE_LOOKBACK_YEARS] if m is not None]
    if not (drop and recent and normal
            and normal >= CYCLE_MIN_NORMAL_MARGIN and current_margin is not None):
        return False, None
    loss, swing = min(recent) < CYCLE_LOSS_MARGIN, max(recent) - min(recent)
    if not (loss or swing > CYCLE_MARGIN_RANGE):
        return False, None
    why = ("ปีขาดทุนจากการดำเนินงาน" if loss else f"margin แกว่ง {swing*100:.0f} จุด")
    return True, (f"รายได้ลด {-drop[1]*100:.0f}% ในปีงบ {str(drop[0])[:4]}, {why} ใน "
                  f"{CYCLE_LOOKBACK_YEARS} ปี แล้ว margin กลับถึงระดับปกติ (margin วันนี้ {current_margin*100:.1f}% เทียบ "
                  f"margin ปกติ {normal*100:.1f}%)")


def cycle_profile(industry, margin_history, revenue_dated=None, current_margin=None):
    """Cycle when the firm is cyclical (its industry, or rules a-c on its own years),
    else None. `margin_history` is the operating margins by fiscal year, newest first
    (one-time add-backs applied); `revenue_dated` the fiscal-year revenue [(date, rev)]
    the margins were built on."""
    if industry in FINANCIAL_INDUSTRIES:
        return None
    window = [m for m in (margin_history or [])[:NORMAL_YEARS] if m is not None]
    if len(window) < MIN_MARGIN_YEARS:
        return None
    normal = normalized_margin(window, NORMAL_YEARS)
    if normal is None or normal <= 0:
        return None
    if industry in CYCLICAL_INDUSTRIES:
        basis, reason = "industry", f"{industry} เป็นอุตสาหกรรมวัฏจักร"
    else:
        ok, reason = firm_cycle_test(revenue_dated, margin_history, current_margin, normal)
        if not ok:
            return None
        basis = "firm"
    return Cycle(basis=basis, reason=reason, normal_margin=normal, margin_years=len(window),
                 short_window=len(window) < NORMAL_MIN_YEARS,
                 low_margin=max(_quantile(window, BEAR_PERCENTILE), BEAR_CYCLE_FLOOR),
                 high_margin=_quantile(window, BULL_PERCENTILE),
                 trough_margin=max(min(window), BEAR_CYCLE_FLOOR),
                 at_peak=bool(current_margin is not None and current_margin > CYCLE_PEAK_MARGIN_X * normal))


def peak_line(cyc):
    """The highest operating margin a cyclical is valued and scored on (E105-E106):
    CYCLE_PEAK_MARGIN_X x its normal margin. Below it today's margin is used as is."""
    return CYCLE_PEAK_MARGIN_X * cyc.normal_margin


def classify(industry, operating_income, revenue, margin_history=None, revenue_dated=None):
    """(CompanyType, reason, Cycle or None). `margin_history` is the operating margins
    by fiscal year, newest first; `revenue_dated` the fiscal-year revenue they were
    built on (the firm-level cyclical test reads both)."""
    if industry in FINANCIAL_INDUSTRIES:
        return CompanyType.FINANCIAL, f"{industry}: valued on equity (excess returns), not FCFF", None
    m_now = (operating_income / revenue) if (operating_income is not None and revenue) else None
    # before the loss test: a steel maker at the bottom of its cycle is a cyclical in
    # a bad year, not a start-up (Damodaran normalizes it rather than writing it off)
    cyc = cycle_profile(industry, margin_history, revenue_dated, m_now) if revenue else None
    if cyc is not None:
        return CompanyType.CYCLICAL, (f"{cyc.reason}: ใช้ margin มัธยฐาน "
                                      f"{cyc.margin_years} ปีงบเป็น margin ปกติ"), cyc
    if operating_income is None or operating_income <= 0 or not revenue:
        return CompanyType.YOUNG, "no operating profit yet: young-company DCF", None
    return CompanyType.STANDARD, "operating company: FCFF DCF", None


def normalized_margin(margin_history, years=NORMAL_YEARS):
    """Median operating margin over up to `years` fiscal years (newest first)."""
    hist = sorted(m for m in (margin_history or [])[:years] if m is not None)
    if not hist:
        return None
    mid = len(hist) // 2
    return hist[mid] if len(hist) % 2 else (hist[mid - 1] + hist[mid]) / 2
