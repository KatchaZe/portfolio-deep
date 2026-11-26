"""Which valuation model a company gets (Damodaran: the model follows the business).

    FINANCIAL  banks, insurers, brokers: excess-return model on book equity; WACC,
               invested capital and FCFF are not meaningful (deposits are raw material)
    YOUNG      no operating profit yet: forward young-company DCF with failure risk
    CYCLICAL   commodity and heavy-cycle industries, or a firm whose own last five
               years show a revenue fall, a margin swing and a peak margin today
               (firm_cycle_test): FCFF DCF whose margin reverts to its own multi-year
               median and whose revenue converges to its trend from a peak
    STANDARD   everything else: FCFF DCF on today's margin

Decided once per evaluation from the industry and the income statement, so the rest
of the engine branches on one value instead of re-deriving the type in each stage.
Pure: no I/O.
"""
from enum import Enum
from typing import NamedTuple, Optional

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

# Firm-level cyclical test (user decision 2026-11-26, design "ก. revised"). A firm
# outside CYCLICAL_INDUSTRIES is cyclical when ALL three hold on its own fiscal years:
#   (a) revenue fell >= CYCLE_REVENUE_DROP year on year at least once in the last
#       CYCLE_LOOKBACK_YEARS fiscal years: demand that turns, not a grower's pause;
#   (b) an operating loss year, or an operating-margin range above CYCLE_MARGIN_RANGE,
#       in the same years: the swing reaches the margin;
#   (c) today's margin is above CYCLE_PEAK_MARGIN_X x its cyclical normal margin:
#       the valuation would otherwise capitalize a peak.
# MU FY2026 passes all three (FY2023 revenue -49%, FY2023 margin -37%, 74.6% today vs
# a 27.5% ten-year median). NVDA fails (a): its revenue has not fallen in five years.
CYCLE_LOOKBACK_YEARS = 5
CYCLE_REVENUE_DROP = 0.15
CYCLE_MARGIN_RANGE = 0.30
CYCLE_PEAK_MARGIN_X = 1.5
# The cyclical normal margin is the median over up to NORMAL_YEARS fiscal years. Seven
# years span one memory or commodity cycle; with five or six the median is used and
# flagged (short_window), below five a firm is not normalized at all.
NORMAL_YEARS, NORMAL_MIN_YEARS = 10, 7
# Latest revenue above PEAK_REVENUE_RATIO x the log-linear trend through the window is
# a cyclical peak: the DCF's revenue converges to the trend line (Damodaran,
# normalizing a cyclical's base year) instead of compounding from the peak.
PEAK_REVENUE_RATIO = 1.25
# Bear / Bull margins for a cyclical: the 25th / 75th percentile of the same window.
# A trough year held forever values the firm as if every year were its worst; the
# lower quartile is the low part of the cycle persisting. Floored at BEAR_CYCLE_FLOOR
# so the Bear FCFF stays a going concern, not a liquidation.
BEAR_PERCENTILE, BULL_PERCENTILE = 0.25, 0.75
BEAR_CYCLE_FLOOR = 0.02


class Cycle(NamedTuple):
    """What normalizing a cyclical firm runs on, decided once from its own history."""
    basis: str                      # "industry" (CYCLICAL_INDUSTRIES) or "firm" (rule a-c)
    reason: str                     # Thai, shown on the card and the flag
    normal_margin: float            # median of up to NORMAL_YEARS fiscal years
    margin_years: int               # years in that median
    short_window: bool              # fewer than NORMAL_MIN_YEARS (5 or 6)
    low_margin: float               # Bear: BEAR_PERCENTILE of the window, floored
    high_margin: float              # Bull: BULL_PERCENTILE of the window
    trend_revenue: Optional[float]  # log-linear trend at the latest fiscal year
    trend_growth: Optional[float]   # its slope: the through-cycle growth rate
    peak_ratio: Optional[float]     # latest fiscal-year revenue / trend_revenue
    at_peak: bool                   # peak_ratio > PEAK_REVENUE_RATIO


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
    with fewer than three points. One fit gives both: the trend line through the cycle
    and its slope, so the base and the growth cannot come from different stories."""
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
    """(passes, Thai reason): rule (a)-(c) above on the newest CYCLE_LOOKBACK_YEARS."""
    rev = sorted(revenue_dated or [], reverse=True)[:CYCLE_LOOKBACK_YEARS + 1]
    drops = [(d0, r0 / r1 - 1) for (d0, r0), (_, r1) in zip(rev, rev[1:]) if r1 and r1 > 0]
    drop = min(drops, key=lambda x: x[1]) if drops else None
    recent = [m for m in (margin_history or [])[:CYCLE_LOOKBACK_YEARS] if m is not None]
    if not (drop and drop[1] <= -CYCLE_REVENUE_DROP and recent and normal and normal > 0
            and current_margin is not None):
        return False, None
    loss, swing = min(recent) < 0, max(recent) - min(recent)
    if not (loss or swing > CYCLE_MARGIN_RANGE):
        return False, None
    if current_margin <= CYCLE_PEAK_MARGIN_X * normal:
        return False, None
    why = ("ปีขาดทุนจากการดำเนินงาน" if loss else f"margin แกว่ง {swing*100:.0f} จุด")
    return True, (f"รายได้ลด {-drop[1]*100:.0f}% ในปีงบ {str(drop[0])[:4]}, {why} ใน "
                  f"{CYCLE_LOOKBACK_YEARS} ปี และ margin วันนี้ {current_margin*100:.1f}% เกิน "
                  f"{CYCLE_PEAK_MARGIN_X}x ของ margin ปกติ {normal*100:.1f}%")


def cycle_profile(industry, margin_history, revenue_dated=None, current_margin=None):
    """Cycle when the firm is cyclical (its industry, or rule a-c on its own years),
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
    rev = sorted(revenue_dated or [], reverse=True)[:NORMAL_YEARS]
    trend, slope = revenue_trend(rev)
    ratio = (rev[0][1] / trend) if (trend and rev) else None
    return Cycle(basis=basis, reason=reason, normal_margin=normal, margin_years=len(window),
                 short_window=len(window) < NORMAL_MIN_YEARS,
                 low_margin=max(_quantile(window, BEAR_PERCENTILE), BEAR_CYCLE_FLOOR),
                 high_margin=_quantile(window, BULL_PERCENTILE),
                 trend_revenue=trend, trend_growth=slope, peak_ratio=ratio,
                 at_peak=bool(ratio and ratio > PEAK_REVENUE_RATIO))


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
        return CompanyType.CYCLICAL, (f"{cyc.reason}: margin reverts to its "
                                      f"{cyc.margin_years}-year median"), cyc
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
