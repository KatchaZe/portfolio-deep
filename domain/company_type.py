"""Which valuation model a company gets (Damodaran: the model follows the business).

    FINANCIAL  banks, insurers, brokers: excess-return model on book equity; WACC,
               invested capital and FCFF are not meaningful (deposits are raw material)
    YOUNG      no operating profit yet: forward young-company DCF with failure risk
    CYCLICAL   commodity and heavy-cycle industries: FCFF DCF whose margin reverts to
               the firm's own multi-year median instead of staying at today's
    STANDARD   everything else: FCFF DCF on today's margin

Decided once per evaluation from the industry and the income statement, so the rest
of the engine branches on one value instead of re-deriving the type in each stage.
Pure: no I/O.
"""
from enum import Enum

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


def classify(industry, operating_income, revenue, margin_history=None):
    """(CompanyType, reason). `margin_history` is the operating margins by fiscal year,
    newest first."""
    if industry in FINANCIAL_INDUSTRIES:
        return CompanyType.FINANCIAL, f"{industry}: valued on equity (excess returns), not FCFF"
    hist = [m for m in (margin_history or []) if m is not None]
    # before the loss test: a steel maker at the bottom of its cycle is a cyclical in
    # a bad year, not a start-up (Damodaran normalizes it rather than writing it off)
    if (industry in CYCLICAL_INDUSTRIES and revenue and len(hist) >= MIN_MARGIN_YEARS
            and (normalized_margin(hist) or 0) > 0):
        return CompanyType.CYCLICAL, (f"{industry} is cyclical: margin reverts to its "
                                      f"{len(hist)}-year median")
    if operating_income is None or operating_income <= 0 or not revenue:
        return CompanyType.YOUNG, "no operating profit yet: young-company DCF"
    return CompanyType.STANDARD, "operating company: FCFF DCF"


def normalized_margin(margin_history, years=10):
    """Median operating margin over up to `years` fiscal years (newest first)."""
    hist = sorted(m for m in (margin_history or [])[:years] if m is not None)
    if not hist:
        return None
    mid = len(hist) // 2
    return hist[mid] if len(hist) % 2 else (hist[mid - 1] + hist[mid]) / 2
