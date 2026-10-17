"""
DEEP Framework v8.2 engine — implements the DeepEngine contract.
Free-data limits handled per skill invariant 17 (skip + flag, never fabricate).
"""
import decimal as _decimal
import datetime
from typing import NamedTuple, Optional

import config
from domain import pead as pead_mod
from domain import trend as trend_mod
from domain.company_type import CompanyType, classify, normalized_margin
from . import fcff_dcf, young_dcf
from . import financial as financial_mod
from .contract import DeepEngine, Valuation

# Functions read config.ERP / config.ERP_AS_OF at call time so the dashboard's
# Assumptions override applies without a restart. These snapshots are reference only.
ERP = config.ERP             # snapshot at import — do NOT use in calculations
ERP_AS_OF = config.ERP_AS_OF
ROIC_TERMINAL = 0.15
REVERSE_HORIZON = 10
GROWTH_CAP = 0.30
RD_LIFE = 5
DEFAULT_KD_SPREAD = 0.013

# Damodaran synthetic rating -> default spread, large non-financial firms, interest
# coverage = EBIT / interest. Vintage: January 2026
# (pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/ratings.html). Refresh with the
# annual January data update; the previous table sat 30-75 bp above it.
SYNTH_SPREAD_AS_OF = "2026-01"
SYNTH_SPREAD = [
    (8.50, 0.0040), (6.50, 0.0055), (5.50, 0.0070), (4.25, 0.0078),
    (3.00, 0.0089), (2.50, 0.0111), (2.25, 0.0138), (2.00, 0.0184),
    (1.75, 0.0275), (1.50, 0.0321), (1.25, 0.0509), (0.80, 0.0885),
    (0.65, 0.1261), (0.20, 0.1600), (-1e9, 0.1900),
]

TERMINAL_MARGIN_CAP = 0.40
TERMINAL_MARGIN_FLOOR = 0.05


def terminal_margin(ticker, operating_income, revenue, industry_margin=None, industry=None):
    """Terminal operating margin for the reverse DCF: the company's own current
    operating margin clamped to [5%, 40%]. Pre-profit, where there is no margin of its
    own, it is the Damodaran industry pre-tax operating margin (a young firm converges
    on its industry's economics), then a generic 25% when the industry is unknown.
    Returns (margin, source_label, anchored_bool).

    A clamp that changes the answer says so in the label: the ceiling makes the
    reverse DCF demand more growth, the floor flatters a thin-margin business."""
    if operating_income is not None and revenue:
        cur = operating_income / revenue
        if 0 < cur < TERMINAL_MARGIN_FLOOR and industry_margin and industry_margin > cur:
            # barely profitable: today's sliver of a margin is a stage, not the
            # steady state, so it converges on its industry like a pre-profit firm.
            # Not floored: the floor would lift it above both itself and its industry.
            tm = min(TERMINAL_MARGIN_CAP, industry_margin)
            return tm, (f"terminal margin {tm*100:.0f}% (industry margin, Damodaran "
                        f"{industry or 'industry'} - today's {cur*100:.1f}% is below the "
                        f"{TERMINAL_MARGIN_FLOOR*100:.0f}% floor)"), False
        if cur > 0:
            tm = max(TERMINAL_MARGIN_FLOOR, min(TERMINAL_MARGIN_CAP, cur))
            if tm < cur - 1e-9:
                return tm, (f"terminal margin CAPPED {cur*100:.0f}% -> {tm*100:.0f}% "
                            f"(ceiling {TERMINAL_MARGIN_CAP*100:.0f}%) - RevDCF asks for more "
                            f"growth than the firm's own margin implies"), True
            if tm > cur + 1e-9:
                return tm, (f"terminal margin FLOORED {cur*100:.0f}% -> {tm*100:.0f}% "
                            f"(floor {TERMINAL_MARGIN_FLOOR*100:.0f}%) - a thin-margin business "
                            f"is credited with more than it earns"), True
            return tm, f"terminal margin {tm*100:.0f}% (from current op margin {cur*100:.0f}%)", True
    if industry_margin is not None and industry_margin > 0:
        tm = max(TERMINAL_MARGIN_FLOOR, min(TERMINAL_MARGIN_CAP, industry_margin))
        return tm, (f"terminal margin {tm*100:.0f}% (industry margin, Damodaran "
                    f"{industry or 'industry'} - pre-profit)"), False
    return 0.25, "terminal margin 25% (generic assumption - pre-profit, no table)", False


NONOP_MATERIAL = 0.10          # non-operating income above 10% of EBIT is stripped


def core_net_income(f):
    """Net income without the non-operating line, and that line. (net income, None)
    when it is immaterial or cannot be measured.

    non-operating = pretax income - (EBIT - interest expense): investment gains, equity
    -method results, one-off disposals. GOOGL's TTM net income ($244B) sat far above its
    operating income ($148B) on $136B of other income; valued, scored for accruals and
    used for its own P/E history as if it were recurring, it read as cheap with
    low-quality earnings at the same time. Cash and investments are valued on the
    balance sheet, so their income is not counted a second time in earnings."""
    ni, oi, pt = f.net_income, f.operating_income, f.income_before_tax
    if ni is None or oi is None or pt is None or not oi:
        return ni, None
    if str((getattr(f, "provenance", None) or {}).get("operating_income", "")).startswith("sec-derived"):
        return ni, None                    # EBIT derived from pretax: nothing to strip
    nonop = pt - oi + (f.interest_expense or 0.0)
    if abs(nonop) <= NONOP_MATERIAL * abs(oi):
        return ni, None
    return ni - nonop * (1 - f.tax_rate), nonop


def working_capital_change(f):
    """dWC = (AR + inventory - AP)_now - (AR + inventory - AP)_prior, the dWC leg of
    reinvestment (capex + acquisitions + dWC - D&A).

    Built from the balance sheet, not `IncreaseDecreaseInOperatingCapital`, whose sign
    convention varies between filers. Receivables are required: the legs have opposite
    signs, so a filer tagging only payables would read as working capital RELEASING
    cash and inflate FCFF. Returns (dWC, label) or (None, why-not).
    """
    def pair(now_attr, prior_attr):
        return getattr(f, now_attr, None), getattr(f, prior_attr, None)

    ar_now, ar_prior = pair("receivables", "receivables_prior")
    if ar_now is None or ar_prior is None:
        return None, "receivables not filed for both years - dWC not computable"
    legs, have = [ar_now - ar_prior], ["AR"]
    for name, (n, p) in (("inventory", pair("inventory", "inventory_prior")),
                         ("AP", pair("accounts_payable", "accounts_payable_prior"))):
        if n is None or p is None:
            continue
        legs.append((-1 if name == "AP" else 1) * (n - p))
        have.append(name)
    return sum(legs), f"dWC from {'+'.join(have)}"


def erp_months_old(as_of=None, today=None):
    """Months since the ERP assumption was set (for the staleness flag).
    Reads config.ERP_AS_OF at call time (store override aware)."""
    try:
        y, m = (int(x) for x in str(as_of or config.ERP_AS_OF).split("-")[:2])
        d = today or datetime.date.today()
        return (d.year - y) * 12 + (d.month - m)
    except Exception:
        return 99          # an as-of nobody can read is stale, not fresh


def _clamp(x, lo=0.0, hi=5.0):
    return max(lo, min(hi, x))


def _add_flag(f, note):
    """Append a flag once."""
    if note and note not in f.flags:
        f.flags.append(note)


def _band(x, bands):
    for t, s in bands:
        if x >= t:
            return s
    return bands[-1][1]


def cost_of_equity(rf, beta, crp=0.0):
    """Ke = Rf + beta x (ERP + CRP). `crp` is the firm's revenue-weighted country risk
    premium over the US (sources/country_risk.py); scaling it by beta is Damodaran's
    simplest exposure measure. config.ERP is read at call time (override aware)."""
    return rf + beta * (config.ERP + (crp or 0.0))


def _spread_from_coverage(cov):
    for lb, sp in SYNTH_SPREAD:
        if cov >= lb:
            return sp
    return SYNTH_SPREAD[-1][1]


TERMINAL_BETA_LO, TERMINAL_BETA_HI = 0.8, 1.2


def terminal_beta(beta):
    """Beta for the stable-growth phase, clamped to Damodaran's 0.8-1.2 band in both
    directions: a firm growing at ~risk-free forever cannot stay twice as risky as
    the market, nor at beta 0.2."""
    if beta is None:
        return 1.0
    return min(TERMINAL_BETA_HI, max(TERMINAL_BETA_LO, beta))


def wacc_true(rf, beta, equity_mktcap, total_debt, cash, tax, interest_expense, operating_income,
              beta_override=None, crp=0.0):
    """True WACC. Returns (wacc, ke, kd_pretax, note).
    `beta_override` recomputes Ke at a different beta while holding the capital
    structure and cost of debt fixed — used for the terminal-phase WACC."""
    ke = cost_of_equity(rf, beta_override if beta_override is not None else beta, crp)
    if not total_debt or not equity_mktcap:
        return ke, ke, None, "no debt/weights -> WACC=Ke"
    if interest_expense and interest_expense > 0 and operating_income:
        cov = operating_income / interest_expense
        spread = _spread_from_coverage(cov)
        note = f"Kd via coverage {cov:.1f}x"
    else:
        spread = DEFAULT_KD_SPREAD
        note = "Kd via default spread (no interest data)"
    kd_after = (rf + spread) * (1 - tax)
    v = equity_mktcap + total_debt
    we, wd = equity_mktcap / v, total_debt / v
    return ke * we + kd_after * wd, ke, rf + spread, note


def rd_capitalize(rnd_annuals, reported_oi, reported_ic, tax, life=RD_LIFE):
    rd = [x for x in (rnd_annuals or []) if isinstance(x, (int, float)) and x > 0]
    if not rd or reported_oi is None or not reported_ic or reported_ic <= 0:
        return None
    if len(rd) < life + 1:
        rd = rd + [rd[-1]] * (life + 1 - len(rd))
    research_asset = sum(rd[i] * (life - i) / life for i in range(0, life + 1))
    amort = sum(rd[i] / life for i in range(1, life + 1))
    adj_oi = reported_oi + rd[0] - amort
    adj_ic = reported_ic + research_asset
    if adj_ic <= 0:
        return None
    return adj_oi, adj_ic, adj_oi * (1 - tax) / adj_ic


def two_stage_pe(g_h, n, g_st, ke, roic_h, roic_st, ke_st=None):
    """Justified P/E over a high-growth stage then a perpetuity.

    `ke` prices the high-growth years, `ke_st` capitalizes the perpetuity; the
    terminal value is still discounted back across the high-growth years at `ke`.
    ke_st defaults to ke."""
    ke_st = ke if ke_st is None else ke_st
    if ke_st <= g_st:
        return None
    payout_h = max(0.0, 1 - g_h / roic_h) if roic_h else 0.0
    payout_st = max(0.0, 1 - g_st / roic_st) if roic_st else 0.0
    comp = ((1 + g_h) ** n) / ((1 + ke) ** n)
    if abs(ke - g_h) < 1e-9:
        term1 = payout_h * (1 + g_h) * n / (1 + ke)
    else:
        term1 = payout_h * (1 + g_h) * (1 - comp) / (ke - g_h)
    term2 = payout_st * ((1 + g_h) ** n) * (1 + g_st) / ((ke_st - g_st) * (1 + ke) ** n)
    return term1 + term2


# One justified-P/E band for both valuation paths, wide enough to catch garbage
# without donating value: a low P/E is the correct answer for a high-Ke, low-ROIC
# business. The ceiling is a real judgement cap, not a no-op: term2 carries
# ((1+g_h)/(1+ke))^n, which exceeds 1 when g_h > ke, so raw P/Es of 40-56 are
# reachable. Whenever it binds the row is flagged as a boundary.
PE_FLOOR, PE_CEIL = 5.0, 35.0

# Past this ratio the two point methods' [low, high] pair is a method conflict, not
# a range that narrows a decision.
FV_DISAGREE_RATIO = 2.0

# Past this ratio between the fair value on consensus growth and on realised growth,
# the Price pillar stops claiming to know whether the stock is cheap. Tighter than
# FV_DISAGREE_RATIO because it compares two growth stories, not two methods.
GROWTH_FV_DISAGREE = 1.5


def fundamental_peg_price(g_high, g_stable, ke, roic_high, roic_stable, forward_eps, years=5,
                          ke_stable=None):
    # Zero growth is a valid input (a shrinking company floored at 0%), not a missing
    # one: the two-stage formula handles it with payout_h = 1.
    if forward_eps is None or forward_eps <= 0 or g_high is None or g_high < 0:
        return None, None
    pe_trailing = two_stage_pe(g_high, years, g_stable, ke, roic_high, roic_stable, ke_st=ke_stable)
    if pe_trailing is None or pe_trailing <= 0:
        return None, None
    # two_stage_pe is Damodaran's P0/EPS0: every term carries (1 + g_high). It is
    # applied to NEXT year's EPS here, so it becomes the forward multiple
    # P0/EPS1 = P0/EPS0 / (1 + g_high); multiplying the trailing multiple by forward
    # EPS counted the first year of growth twice (+20% at g = 20%).
    pe = pe_trailing / (1 + g_high)
    pe_clamped = _clamp(pe, PE_FLOOR, PE_CEIL)
    detail = {"fair_pe": round(pe_clamped, 2), "fair_pe_raw": round(pe, 2),
              "fair_pe_trailing": round(pe_trailing, 2),
              # PEG is undefined at zero growth — report None rather than dividing by 0
              "fundamental_peg": round(pe_clamped / (g_high * 100), 3) if g_high > 0 else None,
              "pe_clamped": pe_clamped != pe}
    return pe_clamped * forward_eps, detail


def fundamental_growth(capex, dep_amort, nopat, roic_now, acquisitions=0.0):
    """Damodaran's fundamental growth: g = reinvestment rate x ROIC, with
    acquisitions counted as reinvestment.

    A cross-check, not the driver: (capex - D&A)/NOPAT sees only physical
    reinvestment, so it badly understates asset-light R&D spenders. Returns None
    when it cannot be computed honestly."""
    if capex is None or dep_amort is None or not nopat or nopat <= 0:
        return None
    if roic_now is None or roic_now <= 0:
        return None
    return _clamp((capex + (acquisitions or 0.0) - dep_amort) / nopat, 0.0, 0.8) * roic_now


def sustainable_growth_cap(roic_now, roic_fallback=None):
    """Growth ceiling for the high-growth stage: g <= ROIC, because reinvestment
    above 100% of earnings needs outside capital, and the payout term
    max(0, 1 - g/ROIC) would otherwise make the excess growth free.

    When ROIC is unmeasurable (e.g. negative invested capital at a cash-rich firm)
    the cap falls back to `roic_fallback`, the terminal ROIC, rather than the full
    GROWTH_CAP: the firms we cannot measure must not get the most generous growth."""
    if roic_now is None or roic_now <= 0:
        if roic_fallback and roic_fallback > 0:
            return min(GROWTH_CAP, roic_fallback)
        return GROWTH_CAP
    return min(GROWTH_CAP, roic_now)


# SBC is a real expense charged through DILUTION of the forward EPS, not a score
# penalty. The measured diluted share-count growth is primary; SBC$ / market cap is
# only a fallback for filers with no share-count series (IFRS), because it counts
# gross grants (not net of buybacks) and doubles when the stock halves.
SBC_DILUTION_CAP = 0.06
SBC_DILUTION_FLAG_AT = 0.015
# Dilution is measured over up to this many years so one buyback-heavy or
# acquisition-heavy year cannot set the whole forward assumption.
DILUTION_LOOKBACK_YEARS = 3


def share_count_growth(shares_annuals, years=DILUTION_LOOKBACK_YEARS):
    """Actual annual growth in the diluted share count; negative when buybacks
    exceed grants. Returns (rate, label) or (None, None) when the history is too
    short."""
    s = [x for x in (shares_annuals or []) if isinstance(x, (int, float)) and x > 0]
    if len(s) < 2:
        return None, None
    n = min(years, len(s) - 1)
    rate = (s[0] / s[n]) ** (1.0 / n) - 1
    return rate, f"actual diluted share count, {n}y CAGR"


def sbc_dilution_rate(sbc, market_cap):
    """Fallback dilution proxy for filers with no share-count series (IFRS)."""
    if not sbc or sbc <= 0 or not market_cap or market_cap <= 0:
        return None
    return min(SBC_DILUTION_CAP, sbc / market_cap)


def dilution_rate(f, market_cap):
    """Annual share-count growth to charge against a forward EPS.

    Real history first, SBC proxy only when there is none. Clamped at 0 on the low
    side for VALUATION purposes: a buyback programme is a capital-allocation choice
    that can stop at any time, so crediting negative dilution would capitalise a
    discretionary policy into perpetuity. Returns (rate, label)."""
    real, label = share_count_growth(getattr(f, "shares_diluted_annuals", None))
    if real is not None:
        return max(0.0, min(SBC_DILUTION_CAP, real)), label + (
            " (floored at 0: net buybacks are discretionary, not capitalised)" if real < 0 else "")
    proxy = sbc_dilution_rate(getattr(f, "sbc", None), market_cap)
    if proxy is None:
        return None, None
    return proxy, "SBC / market cap proxy (no share-count history filed)"


def dilute(eps, rate, years=1):
    """EPS after `years` of SBC dilution. Identity when the rate is unknown."""
    if eps is None or not rate:
        return eps
    return eps / (1 + rate) ** years


def terminal_roic(roic_now, wacc_val, sector_cap=None):
    """Stable-phase ROIC under Damodaran's terminal rules: excess returns fade, a
    durable moat keeps a modest part of its spread, and the cost of capital is the
    floor. So: keep half of today's spread, never above the industry ceiling, never
    above today's ROIC, never below the cost of capital.

    `sector_cap` is Damodaran's industry ROIC (sources/damodaran.py); without it the
    ceiling is ROIC_TERMINAL = 15%, close to his market-wide adjusted figure."""
    cap = sector_cap if (sector_cap and sector_cap > 0) else ROIC_TERMINAL
    if roic_now is None or wacc_val is None or wacc_val <= 0:
        # No ROIC to fade from: start at the market-average default and let the
        # industry figure only pull it down (an industry ROIC of 77% would be a gift).
        # The cost-of-capital floor still holds, because the growth cap and the
        # high-growth ROIC fall back to this number too.
        base = min(cap, ROIC_TERMINAL)
        return base if wacc_val is None or wacc_val <= 0 else max(wacc_val, base)
    half = wacc_val + 0.5 * max(0.0, roic_now - wacc_val)
    return max(wacc_val, min(roic_now, cap, half))


def stable_exit_pe(g_st, ke, roic_st):
    """Justified STABLE-period P/E (Damodaran, Gordon form):
    PE = payout_st * (1+g_st) / (Ke - g_st), payout_st = 1 - g_st/ROIC_st.
    The exit multiple of the Future Value Projection."""
    if ke is None or g_st is None or ke <= g_st:
        return None
    payout = max(0.0, 1 - g_st / roic_st) if roic_st else 0.0
    return payout * (1 + g_st) / (ke - g_st)


def future_value_projection(eps0, growth, ke, exit_pe, payout=0.0):
    """PV of the dividends paid in years 1-5 plus the year-5 price (EPS_5 x exit P/E).
    `payout` is the high-growth payout 1 - g/ROIC; leaving the dividends out undervalued
    the projection by ~10% at g = 20% against the full DDM."""
    if eps0 is None or eps0 <= 0 or exit_pe is None:
        return None
    pe = _clamp(exit_pe, PE_FLOOR, PE_CEIL)     # same band as the PEG path
    div = sum(eps0 * (1 + growth) ** t * max(0.0, payout or 0.0) / (1 + ke) ** t
              for t in range(1, 6))
    return div + (eps0 * (1 + growth) ** 5 * pe) / (1 + ke) ** 5


# implied CAGR / actual growth -> how hard the priced-in growth is to reach
ACCELERATION_VERDICTS = [(5.0, "Exceptional"), (3.0, "Aggressive"), (1.5, "Ambitious"),
                         (-1e9, "Plausible")]


def _rdcf_verdict(base, why, a1, accel):
    """Reverse-DCF verdict from the implied CAGR `base` and the actual growth a1."""
    if base is None:
        # a price no CAGR in the band can reach is its own verdict, not "Unknown"
        return why or "Beyond model band"
    if a1 is not None and a1 <= 0:
        return "Cannot benchmark - shrinking"
    if accel is None:
        return "Unknown"
    return _band(accel, ACCELERATION_VERDICTS)


def _implied_cagr(pv, ev):
    """Lowest revenue CAGR x in [-50%, +100%] with pv(x) >= ev. Returns (x, why-not).

    pv is unimodal in x, not monotonic: value rises while growth earns more than it
    costs, then falls as reinvestment outruns profit. So locate the peak, then bisect
    the rising branch for the least growth that justifies the price. If even the peak
    falls short of EV, no growth path does, and that is reported."""
    lo, hi = -0.50, 1.00
    plo = pv(lo)
    if plo is None:
        return None, "path not computable"
    if plo >= ev:
        return lo, None         # even permanent decline is worth more than the price
    n = 40
    vals = [(v, x) for v, x in ((pv(lo + (hi - lo) * i / n),
                                 lo + (hi - lo) * i / n) for i in range(n + 1))
            if v is not None]
    if not vals:
        return None, "path not computable"
    peak_v, peak_x = max(vals)
    if peak_v < ev:
        # Peak inside the band: a thin spread means no growth rate justifies the
        # price. Peak at the boundary: the price needs more than 100%/yr.
        if peak_x < hi - 1e-9:
            return None, (f"no growth rate justifies this price - value peaks at "
                          f"{peak_x*100:.0f}%/yr and the spread is too thin to add more")
        return None, "price implies more than 100%/yr revenue growth"
    a, b = lo, peak_x
    for _ in range(60):
        mid = (a + b) / 2
        if (pv(mid) or 0) < ev:
            a = mid
        else:
            b = mid
    return (a + b) / 2, None


def _fcff_path_pv(x, m, *, revenue, tax, g, roic_t, wacc_val, wacc_term, margin_now,
                  roic_now=None):
    """PV of the reverse-DCF FCFF path at revenue CAGR x with terminal operating
    margin m. The margin ramps linearly from margin_now to m (flat when None), and the
    return on new capital from the firm's own ROIC to the terminal one (Damodaran
    fades excess returns; charging a 34%-ROIC firm a 15% ROIC's reinvestment for all
    ten years read its implied growth as harsher than the market's)."""
    if m <= 0:
        return None
    # Reinvestment is x/ROIC with x the REVENUE CAGR, uncapped. Under a constant
    # sales-to-capital ratio dCapital = Capital * x, so reinvest = x / ROIC exactly;
    # margin expansion costs no capital. Above x = ROIC the growth is funded with
    # outside capital and FCFF goes negative, which keeps FCFF strictly decreasing
    # in x (a cap would make growth above it free).
    r0 = roic_now if (roic_now and roic_now > roic_t) else roic_t
    reinvest_t = min(0.8, g / roic_t)
    pv = 0.0
    rev_t = revenue
    for t in range(1, REVERSE_HORIZON + 1):
        roic_y = r0 + (roic_t - r0) * (t / REVERSE_HORIZON)
        reinvest = max(0.0, x / roic_y) if x > 0 else 0.0
        rev_t *= (1 + x)
        m_t = m if margin_now is None else margin_now + (m - margin_now) * (t / REVERSE_HORIZON)
        # A loss year gives negative FCFF and the solver must see it. Reinvestment
        # is a dollar outlay sized at the TERMINAL margin, never NOPAT_t * reinvest:
        # with reinvest > 1, a negative NOPAT times (1 - reinvest) books a loss year
        # as positive cash.
        nopat_t = rev_t * m_t * (1 - tax if m_t > 0 else 1.0)
        reinvest_cash = rev_t * m * (1 - tax) * reinvest
        fcff = nopat_t - reinvest_cash
        pv += fcff / (1 + wacc_val) ** t
    fcff_next = rev_t * (1 + g) * m * (1 - tax) * (1 - reinvest_t)
    tv = fcff_next / (wacc_term - g)          # perpetuity at the STABLE rate...
    return pv + tv / (1 + wacc_val) ** REVERSE_HORIZON   # ...discounted back at the risky one


def reverse_dcf(price, shares, revenue, rev_1y, total_debt, cash, wacc_val, g, tax, margin,
                wacc_term=None, roic_term=None, market_cap=None, margin_now=None,
                actual_growth=None, roic_now=None):
    """Full-path reverse DCF: solve the revenue CAGR x such that PV(FCFF years 1..H)
    + PV(terminal value) = enterprise value, i.e. what growth the market prices in.

    The terminal value is 90-97% of PV here, so the terminal phase decides the
    answer: `wacc_term` capitalizes the perpetuity (Ke at a stable-growth beta) and
    `roic_term` prices the reinvestment. Both default to the current WACC / 15%.

    The operating margin ramps linearly from `margin_now` to the terminal `margin`
    over the horizon (as young_dcf does), so a loss-maker is not credited with the
    terminal margin from year 1. With margin_now=None it is held flat at `margin`.

    `market_cap` should be the one the WACC weights were built from, so EV prices
    the same firm as the discount rate (price x shares is the wrong unit for a
    depositary listing)."""
    wacc_term = wacc_val if wacc_term is None else wacc_term
    roic_t = roic_term if (roic_term and roic_term > 0) else ROIC_TERMINAL
    if not (price and shares and revenue) or wacc_val <= g or wacc_term <= g:
        return {"triggered": False}
    mcap = market_cap if (market_cap and market_cap > 0) else price * shares
    ev = mcap + (total_debt or 0) - (cash or 0)

    def implied(m):
        if m <= 0 or ev <= 0:
            return None, "no margin / no enterprise value"
        return _implied_cagr(lambda x: _fcff_path_pv(
            x, m, revenue=revenue, tax=tax, g=g, roic_t=roic_t, wacc_val=wacc_val,
            wacc_term=wacc_term, margin_now=margin_now, roic_now=roic_now), ev)

    base, why = implied(margin)
    # P3-1 clock trap: `revenue` is TTM while `rev_1y` is the fiscal year BEFORE the
    # last completed one, so revenue / rev_1y spans ~21 months and overstates growth,
    # making the verdict too forgiving. The engine passes the FY-over-FY rate the
    # Demand pillar scores; the ratio is only a fallback for direct callers.
    a1 = actual_growth if actual_growth is not None else (
        (revenue / rev_1y - 1) if rev_1y else None)
    accel = (base / a1) if (base and a1 and a1 > 0) else None
    out_of_band = base is None
    # How far past self-funding the price sits, as a ratio: implied CAGR slightly
    # above terminal ROIC is normal for any growth name.
    fund_ratio = None if (base is None or not roic_t) else round(base / roic_t, 2)
    sens = {}
    for k, dm in (("bear", -0.05), ("base", 0.0), ("bull", 0.05)):
        x, _ = implied(margin + dm)
        sens[k] = round(x * 100, 1) if x else None
    return {"triggered": True, "enterprise_value": round(ev, 0),
            "implied_cagr_pct": round(base * 100, 1) if base else None,
            "actual_1y_pct": round(a1 * 100, 1) if a1 is not None else None,
            "acceleration": round(accel, 2) if accel else None,
            "verdict": _rdcf_verdict(base, why, a1, accel),
            "out_of_band": out_of_band,
            "funding_ratio": fund_ratio, "roic_terminal_pct": round(roic_t * 100, 1),
            "sensitivity": sens}


def earnings_quality(net_income, cfo, total_assets, sbc, revenue,
                     receivables=None, receivables_prior=None, revenue_prior=None,
                     revenue_growth=None):
    """Accrual-quality verdict: cash conversion, accruals, and receivables growing
    much faster than revenue (sales not turning into cash).
    Returns (verdict, flags + disclosures, cash_conversion)."""
    if cfo is None or net_income is None:
        return None, [], None
    flags, tested = [], 0
    cc = (cfo / net_income) if net_income > 0 else None
    if cc is not None:
        tested += 1
        if cc < 0.8:
            flags.append("low cash conversion <0.8")
    if total_assets:
        tested += 1
        acc = (net_income - cfo) / total_assets
        if acc > 0.05:
            flags.append("high accruals >5% of assets")
    # SBC is charged once, through dilution of the forward EPS, so it is a disclosure
    # here and stays out of the verdict that scores (no double counting).
    disclosures = []
    if sbc is not None and revenue:
        if sbc / revenue > 0.10:
            disclosures.append(f"SBC {sbc / revenue * 100:.0f}% of revenue "
                               f"(คิดผ่าน dilution ใน forward EPS แล้ว — ไม่หักคะแนนซ้ำ)")
    # Prefer the caller's fiscal-year growth: the fallback revenue / revenue_prior is
    # TTM over FY, overstates growth, and so suppresses the receivables flag.
    rev_g = revenue_growth if revenue_growth is not None else (
        (revenue / revenue_prior - 1) if (revenue and revenue_prior) else None)
    if receivables and receivables_prior and rev_g is not None:
        tested += 1
        ar_g = receivables / receivables_prior - 1
        if ar_g > 0.10 and ar_g > rev_g + 0.15:
            flags.append(f"receivables +{ar_g*100:.0f}% vs revenue +{rev_g*100:.0f}% "
                         f"(sales not turning into cash)")
    # CLEAN is a finding: a firm none of the tests could run on (a loss-maker with no
    # total assets) is UNTESTED, which Execution scores as missing data (3.0, not 4.0)
    if not tested:
        return None, disclosures + ["earnings quality untested (no test applicable)"], cc
    verdict = "CLEAN" if not flags else "REVIEW" if len(flags) <= 2 else "LOW"
    return verdict, flags + disclosures, cc


def fade_ratio(near, far):
    """Consensus growth fade: how much of the near-term growth rate survives to the
    far year. Returns (ratio, why-not).

    Only defined for a positive near rate: a negative denominator inverts the ratio,
    scoring a company turning from decline to growth as a severe fade. Shrinking is
    already scored by the base band. Positive-to-negative still gives a negative
    ratio, correctly the worst fade."""
    if near is None or far is None:
        return None, "no consensus path"
    if near <= 0:
        return None, ("consensus near-term growth ไม่เป็นบวก — fade ไม่มีความหมาย "
                      "(การหดตัวถูกให้คะแนนที่ฐานแล้ว)")
    return far / near, None


def acquisition_intensity(acquisitions_net, revenue):
    """Cash SPENT on acquisitions as a share of revenue — the organic-growth penalty.

    `acquisitions_net` is a payment: negative means a net divestiture, which is not
    bought growth, so it reads as 0.0 (known: nothing was bought), not None.
    Returns (intensity, label)."""
    if acquisitions_net is None or not revenue:
        return None, None
    if acquisitions_net <= 0:
        return 0.0, "net divestiture — ไม่ได้ซื้อการเติบโต"
    return acquisitions_net / revenue, None


SUSTAINED_RATIO, SPIKE_RATIO, DECEL_RATIO = 0.80, 0.50, 1.30


def growth_consistency(g_1y, g_3y):
    """Is this year's growth the pace the company actually runs at?

    Compares the latest fiscal year against the 3-year CAGR ending the same year:
      3y between 80% and 130% of 1y -> the pace is genuinely sustained     +0.25
      3y <  50% of 1y               -> latest year is ~2x the run-rate     -0.5
      3y > 130% of 1y               -> DECELERATING                         0.0
    Asymmetric on purpose: a one-year spike is what misleads a band built to reward
    35%+, while deceleration is already scored by the base band on the slower year.

    Only speaks when 1-year growth is positive (a negative denominator inverts the
    comparison). Returns (adjustment, note)."""
    if g_1y is None or g_3y is None or g_1y <= 0:
        return 0.0, None
    ratio = g_3y / g_1y
    if ratio < SPIKE_RATIO:
        return -0.5, (f"3y CAGR {g_3y*100:.0f}% vs 1y {g_1y*100:.0f}% — "
                      f"ปีล่าสุดเร็วกว่าค่าเฉลี่ย {1/ratio:.1f} เท่า")
    if ratio > DECEL_RATIO:
        return 0.0, (f"3y CAGR {g_3y*100:.0f}% vs 1y {g_1y*100:.0f}% — "
                     f"ชะลอตัว (ฐานให้คะแนนปีล่าสุดไปแล้ว)")
    if ratio >= SUSTAINED_RATIO:
        return 0.25, f"3y CAGR {g_3y*100:.0f}% ~ 1y {g_1y*100:.0f}% — โตต่อเนื่องจริง"
    return 0.0, f"3y CAGR {g_3y*100:.0f}% vs 1y {g_1y*100:.0f}%"


def _r_demand(g, peer_median, acq_intensity, fade_ratio, notes, acq_label=None,
              fade_why=None, consistency_adj=0.0, consistency_note=None):
    if g is None:
        return None
    base = _band(g, [(0.35, 5.0), (0.25, 4.0), (0.15, 3.0), (0.08, 2.0), (0.0, 1.0), (-1e9, 0.0)])
    notes.append(f"D base(growth {g*100:.0f}%)={base}")
    score = base
    if acq_intensity is not None:
        pen = -1.0 if acq_intensity > 0.10 else (-0.5 if acq_intensity > 0.05 else 0.0)
        score += pen
        notes.append(f"D organic({acq_label or f'acq {acq_intensity*100:.1f}% of rev'})={pen}")
    else:
        notes.append("D organic adj=skipped(no acquisitions data)")
    if peer_median is not None:
        adj = 0.5 if g > peer_median else (-0.5 if g < peer_median * 0.8 else 0.0)
        score += adj; notes.append(f"D peer(vs {peer_median*100:.0f}%)={adj}")
    else:
        notes.append("D peer adj=skipped(no peer data)")
    if fade_ratio is not None:
        adj = -0.5 if fade_ratio < 0.4 else (0.25 if fade_ratio > 0.7 else 0.0)
        score += adj; notes.append(f"D durability(fade {fade_ratio:.2f})={adj}")
    else:
        notes.append(f"D durability adj=skipped({fade_why or 'no consensus path'})")
    if consistency_note:
        score += consistency_adj
        notes.append(f"D 3y-consistency({consistency_note})={consistency_adj}")
    else:
        notes.append("D 3y-consistency adj=skipped(<4 ปีงบ หรือ growth ไม่เป็นบวก)")
    return _clamp(_apply_budget(base, score, notes, "D"))


FCF_DURABILITY_CAGR = 2.0      # %/yr — below this in either direction reads as flat


def fcf_durability(trend_rows):
    """Does free cash flow compound across years, or was last year a good year?

    +/-0.5, needs 3+ consecutive filed years. Reads the trend strip the card draws, so
    the card and the score cannot disagree about what the cash flow did.
    Returns (adjustment, note) or (0.0, None) when there is not enough history."""
    row = next((r for r in (trend_rows or []) if r.get("key") == "fcf"), None)
    if not row or row.get("n", 0) < 3:
        return 0.0, None
    pts = [p.get("v") for p in row.get("points") or []]
    cagr = row.get("summary")
    latest = pts[-1] if pts else None
    if latest is not None and latest <= 0:
        # burning cash in the latest filed year is a fact, not a trend reading
        return -0.5, f"FCF ล่าสุดติดลบ ({row['n']}y)"
    if cagr is None:
        return 0.0, None
    if cagr > FCF_DURABILITY_CAGR:
        return 0.5, f"FCF โต {cagr:+.1f}%/ปี ({row['n']}y)"
    if cagr < -FCF_DURABILITY_CAGR:
        return -0.5, f"FCF หด {cagr:+.1f}%/ปี ({row['n']}y)"
    return 0.0, f"FCF ทรงตัว {cagr:+.1f}%/ปี ({row['n']}y)"


BEAT_MIN_QUARTERS = 3


def beat_consistency(eps_surprises):
    """Does management deliver what it told the market it would?

    An adjustment, not the Execution base (whether earnings are real comes before
    whether they beat). Asymmetric: beating is partly a guidance game (+0.25), while
    repeatedly missing is direct evidence management cannot forecast its own
    business (-0.5). 'meet' is excluded from the denominator. Needs 3+ graded
    quarters; order-independent (see pead.chronological)."""
    graded = [r.get("grade") for r in pead_mod.chronological(eps_surprises)
              if r.get("grade") in ("beat", "miss")]
    if len(graded) < BEAT_MIN_QUARTERS:
        return 0.0, None
    beats = graded.count("beat")
    rate = beats / len(graded)
    label = f"{beats}/{len(graded)} beat"
    if rate >= 0.75:
        return 0.25, f"{label} — ทำได้ตามที่บอกตลาดสม่ำเสมอ (guidance game ได้ จึงให้น้ำหนักน้อย)"
    if rate <= 0.25:
        return -0.5, f"{label} — พลาดเป้าตัวเองซ้ำ ๆ คือหลักฐานตรงว่าคาดการณ์ธุรกิจตัวเองไม่ได้"
    return 0.0, label


def _trend_roic_map(f, tax):
    """Per-year RAW ROIC map (no R&D or lease capitalization) from the trend series,
    or {} when the inputs are absent."""
    try:
        return trend_mod.roic_series(getattr(f, "operating_income_annuals_dated", None),
                                     getattr(f, "ic_components_dated", None) or {}, tax)
    except Exception:
        return {}


def _r_execution(eq_verdict, margin_trend, inc_roic, wacc_val, notes, fcf_adj=0.0, fcf_note=None,
                 inc_src=None, skip_inc_note=False, beat_adj=0.0, beat_note=None):
    base = {"CLEAN": 4.0, "REVIEW": 3.0, "LOW": 1.5}.get(eq_verdict)
    if base is None:
        base = 3.0; notes.append("E_exec base(no earnings-quality data)=3.0")
    else:
        notes.append(f"E_exec base(EQ {eq_verdict})={base}")
    score = base
    if margin_trend:
        adj = {"up": 0.5, "flat": 0.0, "down": -0.5}.get(margin_trend, 0.0)
        score += adj; notes.append(f"E_exec margin-trend({margin_trend})={adj}")
    if inc_roic is not None and wacc_val is not None:
        inc = inc_roic - wacc_val
        adj = 1.0 if inc >= 0.10 else 0.5 if inc >= 0 else -0.5 if inc >= -0.05 else -1.0
        score += adj
        notes.append(f"E_exec incremental-ROIC(inc-spread {inc*100:.0f}%"
                     f"{', ' + inc_src if inc_src else ''})={adj}")
    elif not skip_inc_note:
        notes.append("E_exec incremental-ROIC adj=skipped(no prior op income)")
    if fcf_note:
        score += fcf_adj
        notes.append(f"E_exec FCF-durability({fcf_note})={fcf_adj}")
    else:
        notes.append("E_exec FCF-durability adj=skipped(<3 consecutive filed years)")
    if beat_note:
        score += beat_adj
        notes.append(f"E_exec beat-consistency({beat_note})={beat_adj}")
    else:
        notes.append(f"E_exec beat-consistency adj=skipped(<{BEAT_MIN_QUARTERS} graded quarters)")
    return _clamp(_apply_budget(base, score, notes, "E_exec"))


# upper bound is 1.0 on purpose: the correction may only ever lower a score
NORMALIZE_FACTOR_MIN, NORMALIZE_FACTOR_MAX = 0.6, 1.0
NORMALIZE_MIN_YEARS = 4


def normalized_roic(roic_used, roic_map):
    """Damp a one-year ROIC toward its own multi-year median before scoring it.

    Basis trap: the map is RAW ROIC while `roic_used` is R&D- and lease-adjusted, so
    the map supplies only the SHAPE (median/latest), applied to the adjusted level.

    One-sided: applied only when it lowers the score. Median normalization suits
    cyclical noise, not a monotone decline, which the ROIC-trend leg already scores.
    Factor clamped to [0.6, 1.0]; needs 4+ contiguous years.
    Returns (normalized_roic, note) or (roic_used, None)."""
    if roic_used is None or roic_used <= 0 or len(roic_map or {}) < NORMALIZE_MIN_YEARS:
        return roic_used, None
    # Contiguous years only: a median reaching back across a filing gap normalises
    # against a different business.
    _win = trend_mod._window(roic_map, years=5)
    vals = [roic_map[d]["roic"] for d in _win]
    if len(vals) < NORMALIZE_MIN_YEARS:
        return roic_used, None
    latest = vals[-1]
    if latest <= 0:
        return roic_used, None
    med = sorted(vals)[len(vals) // 2] if len(vals) % 2 else sum(sorted(vals)[len(vals) // 2 - 1:
                                                                            len(vals) // 2 + 1]) / 2
    raw = med / latest
    if raw >= 1.0 - 0.02:
        # latest year at or below its own median: nothing to damp
        return roic_used, None
    factor = max(NORMALIZE_FACTOR_MIN, min(NORMALIZE_FACTOR_MAX, raw))
    out = roic_used * factor
    return out, (f"ROIC normalized {roic_used*100:.1f}% -> {out*100:.1f}% "
                 f"(ปีล่าสุด {latest:.1f}% vs median {len(vals)}y {med:.1f}% = {factor:.2f}x)")


CAP_MIN_YEARS = 4
SECTOR_BEAT, SECTOR_LAG = 1.5, 0.7
# Cap on the NET adjustment per pillar, so stacked adjustments cannot outweigh the
# base band. 2.0 because D could already stack organic (-1.0) + peer (-0.5) + fade
# (-0.5) = -2.0; a tighter budget would silently re-rate existing answers.
ADJ_BUDGET = 2.0


def _apply_budget(base, score, notes, pillar):
    """Clamp the NET adjustment to +/-ADJ_BUDGET and say so when it binds."""
    if base is None:
        return score
    net = score - base
    if abs(net) <= ADJ_BUDGET + 1e-9:
        return score
    capped = base + (ADJ_BUDGET if net > 0 else -ADJ_BUDGET)
    notes.append(f"{pillar} adjustment budget: net {net:+.2f} capped to "
                 f"{ADJ_BUDGET if net > 0 else -ADJ_BUDGET:+.2f} (base ต้องเป็นคำตอบหลัก)")
    return capped


def competitive_advantage_period(roic_map, wacc_val, years=5):
    """How many of the last N fiscal years did the business out-earn its capital?
    The duration of the moat, next to its size.

    Uses TODAY's WACC as the hurdle for every past year, a consistent question
    rather than what each year faced. Contiguous years only.
    Returns (adjustment, note), +/-0.5."""
    if not roic_map or not wacc_val or wacc_val <= 0:
        return 0.0, None
    win = trend_mod._window(roic_map, years=years)
    if len(win) < CAP_MIN_YEARS:
        return 0.0, None
    hurdle = wacc_val * 100
    above = sum(1 for d in win if roic_map[d]["roic"] > hurdle)
    n = len(win)
    if above == n:
        return 0.5, f"{above}/{n} ปี ROIC > WACC — excess return ต่อเนื่องทุกปี"
    if above <= 1:
        return -0.5, f"{above}/{n} ปี ROIC > WACC — แทบไม่เคยชนะต้นทุนทุน"
    if above <= n // 2:
        return -0.25, f"{above}/{n} ปี ROIC > WACC — ชนะไม่ถึงครึ่ง"
    return 0.0, f"{above}/{n} ปี ROIC > WACC"


def sector_relative(roic_used, sector_roc):
    """Is this return good FOR ITS INDUSTRY (Damodaran's industry ROC)?

    Basis caveat: the industry figure is not lease- or R&D-adjusted, unlike our ROIC,
    so this leans harsh; hence the wide 1.5x / 0.7x thresholds.
    Returns (adjustment, note)."""
    if roic_used is None or roic_used <= 0 or not sector_roc or sector_roc <= 0:
        return 0.0, None
    ratio = roic_used / sector_roc
    if ratio >= SECTOR_BEAT:
        return 0.5, f"ROIC {roic_used*100:.0f}% = {ratio:.1f}x ของอุตสาหกรรม ({sector_roc*100:.0f}%)"
    if ratio <= SECTOR_LAG:
        return -0.5, f"ROIC {roic_used*100:.0f}% = {ratio:.1f}x ของอุตสาหกรรม ({sector_roc*100:.0f}%) — ต่ำกว่าเพื่อน"
    return 0.0, f"ROIC {roic_used*100:.0f}% ~ อุตสาหกรรม ({sector_roc*100:.0f}%)"


def _r_economics(spread, roic_delta, notes, cap_adj=0.0, cap_note=None,
                 sector_adj=0.0, sector_note=None):
    """Economics pillar: the LEVEL of the ROIC-WACC spread, adjusted for its TREND.

    `roic_delta` is the caller's like-for-like FY0-vs-FY1 ROIC change (WACC cancels,
    so it equals the spread change); the headline spread is TTM and may be
    R&D-adjusted, so it must not be differenced against a prior-year spread here."""
    if spread is None:
        return None
    base = _band(spread, [(0.20, 5.0), (0.10, 4.0), (0.05, 3.0), (0.0, 2.0), (-0.05, 1.0), (-1e9, 0.0)])
    notes.append(f"E_econ base(spread {spread*100:.1f}%)={base}")
    score = base
    if roic_delta is not None:
        adj = 0.5 if roic_delta > 0.02 else (-1.0 if (roic_delta < -0.02 and spread < 0.05)
                                             else -0.5 if roic_delta < -0.02 else 0.0)
        score += adj
        notes.append(f"E_econ 1y-ROIC-trend(FY0 vs FY1, d {roic_delta*100:+.1f}pp)={adj}")
    else:
        notes.append("E_econ spread-trend adj=skipped(no like-for-like prior ROIC)")
    if cap_note:
        score += cap_adj
        notes.append(f"E_econ CAP({cap_note})={cap_adj}")
    else:
        notes.append(f"E_econ CAP adj=skipped(<{CAP_MIN_YEARS} ปีงบต่อเนื่อง)")
    if sector_note:
        score += sector_adj
        notes.append(f"E_econ sector-relative({sector_note})={sector_adj}")
    else:
        notes.append("E_econ sector-relative adj=skipped(no Damodaran industry ROC)")
    return _clamp(_apply_budget(base, score, notes, "E_econ"))


def fcf_yield_adj(trend_rows, enterprise_value, wacc_val):
    """A second, independent read on cheapness for the Price pillar: FCF / EV against
    WACC, a firm-level cash flow against a firm-level required return.

    Reads FCF off the trend strip the card draws. Deliberately harsh on a heavy
    investor; Demand and Economics credit that spending. Bounded at +/-0.5.
    Returns (adjustment, note) or (0.0, None) when it cannot be measured."""
    row = next((r for r in (trend_rows or []) if r.get("key") == "fcf"), None)
    if not row or not row.get("points") or not enterprise_value or enterprise_value <= 0:
        return 0.0, None
    if not wacc_val or wacc_val <= 0:
        return 0.0, None
    fcf = row["points"][-1]["v"]
    if fcf is None:
        return 0.0, None
    y = fcf / enterprise_value
    if y <= 0:
        return -0.5, f"FCF yield ติดลบ (FCF {fcf/1e9:.1f}B) — ราคาไม่มีกระแสเงินสดรองรับ"
    ratio = y / wacc_val
    if ratio >= 1.0:
        adj = 0.5
    elif ratio >= 0.6:
        adj = 0.25
    elif ratio >= 0.3:
        adj = 0.0
    else:
        adj = -0.25
    return adj, f"FCF yield {y*100:.1f}% vs WACC {wacc_val*100:.1f}% ({ratio:.2f}x)"


def _r_price(fv_anchor, price, own_pe_pctile, notes, fcf_adj=0.0, fcf_note=None):
    if fv_anchor is None or not price or fv_anchor <= 0:
        # No point fair value: an FCF yield is still a cheapness opinion, scored
        # around neutral because the margin-of-safety half is missing.
        if fcf_note:
            score = _clamp(NEUTRAL_SCORE + fcf_adj * 2)      # +/-1.0 around neutral
            notes.append(f"P base=no point fair value; scored on cash alone "
                         f"({fcf_note})={score}")
            return score
        notes.append("P base=skipped(no point fair value, no FCF either)")
        return None
    # Margin of safety divides by VALUE, not price (dividing by price is upside and
    # skews the bands asymmetrically).
    mos = (fv_anchor - price) / fv_anchor
    base = _band(mos, [(0.40, 5.0), (0.15, 4.0), (-0.15, 3.0), (-0.40, 2.0), (-1e9, 1.0)])
    notes.append(f"P base(margin-of-safety {mos*100:+.0f}% of value)={base}")
    score = base
    if own_pe_pctile is not None:
        adj = 0.5 if own_pe_pctile <= 0.33 else (-0.5 if own_pe_pctile >= 0.67 else 0.0)
        score += adj; notes.append(f"P own-5y-multiple(pctile {own_pe_pctile*100:.0f}%)={adj}")
    else:
        notes.append("P own-5y-multiple adj=skipped(no P/E history)")
    if fcf_note:
        score += fcf_adj
        notes.append(f"P FCF-yield({fcf_note})={fcf_adj}")
    else:
        notes.append("P FCF-yield adj=skipped(no FCF series or no enterprise value)")
    return _clamp(_apply_budget(base, score, notes, "P"))


WEIGHTS = {"D": 0.20, "E_exec": 0.20, "E_econ": 0.30, "P": 0.30}


NEUTRAL_SCORE = 2.5     # midpoint of the 0-5 band: "we did not find out", not "it is bad"


_D = _decimal.Decimal


def _q2(d):
    """Half-up to two places, as a float. `round()` cannot be used: it resolves a tie by
    the binary representation, which is why 1.325 and 2.575 rounded differently on two
    machines running identical code."""
    return float(d.quantize(_D("0.01"), rounding=_decimal.ROUND_HALF_UP))


def composite(scores):
    """Weighted DEEP composite.

    A missing pillar must not inherit the weighted average of the others (failing to
    measure would then beat measuring and finding it poor), so the renormalized score
    is bounded by the score with the missing pillar at NEUTRAL. The min makes it
    one-sided: a data gap can only lower a row; a fully measured row is untouched."""
    if not any(scores.get(k) is not None for k in WEIGHTS):
        return None
    # Decimal on the pillars AS REPORTED (rounded to 2dp), rounded half-up: the
    # weighted sum lands on a ...5 tie systematically, where float rounding depends on
    # the last bits and the machine. The composite must reproduce by hand from the
    # card (invariant I13).
    dw = {k: _D(str(WEIGHTS[k])) for k in WEIGHTS}
    avail = {k: _D(str(round(scores[k], 2))) for k in WEIGHTS if scores.get(k) is not None}
    wsum = sum(dw[k] for k in avail)
    renorm = sum(avail[k] * dw[k] for k in avail) / wsum
    if wsum >= 1:                                # nothing missing -> nothing to bound
        return _q2(renorm)
    neutral = sum(avail.get(k, _D(str(NEUTRAL_SCORE))) * dw[k] for k in WEIGHTS)
    return _q2(min(renorm, neutral))


def cap_reco_without_price(reco, price_score):
    """A BUY requires a price opinion: with the Price pillar missing, the composite
    renormalizes over the rest and a data outage would upgrade the row."""
    if price_score is None and reco == "BUY":
        return "HOLD / Accumulate", ("recommendation capped at HOLD: no fair value, so the "
                                     "Price pillar (30%) was not tested")
    return reco, None


def cap_reco_without_quality(reco, econ_score):
    """A BUY requires a measured Economics pillar: an unmeasured ROIC-WACC spread
    means the test of whether growth creates value was never run. ROIC goes missing
    for ordinary reasons (no operating-income tag, invested capital <= 0)."""
    if econ_score is None and reco == "BUY":
        return "HOLD / Accumulate", ("recommendation capped at HOLD: ROIC unmeasurable, so the "
                                     "Economics pillar (30%) was not tested")
    return reco, None


def stars(c):
    # Half-up via Decimal, not round(): composites move in quarter points, and
    # banker's rounding would tip 2.25 down to two stars.
    half = float((_D(str(c)) * 2).quantize(_D("1"), rounding=_decimal.ROUND_HALF_UP)) / 2
    full = int(half)
    h = (half - full) == 0.5
    return "★" * full + ("½" if h else "") + "☆" * (5 - full - (1 if h else 0))


def recommendation(c):
    return "BUY" if c >= 4 else "HOLD / Accumulate" if c >= 3 else "HOLD" if c >= 2 else "SELL / AVOID"


def _signal(reco):
    if reco is None:
        return None
    return "BUY" if reco == "BUY" else "HOLD" if reco.startswith("HOLD") else "SELL"


class DeepV82Engine(DeepEngine):
    version = "8.2"

    def evaluate(self, facts, rf=0.045):
        """Run the valuation stages in order. Each stage appends to f.flags and to
        `notes` (the breakdown drawer) as it goes, so the call order is the order
        those lines appear on the card."""
        f, notes = facts, []
        ctype = _company_type(f)
        cap = _capital(f, rf, notes, ctype)
        acq, fcff = _reinvestment(f, cap.nopat, ctype)
        gr = _growth(f, cap, acq, rf, ctype)
        dil = _dilution(f, cap.mcap)
        pv = _point_values(f, cap, gr, dil.fwd_eps)
        pi = _priced_in(f, cap, gr, rf, ctype)
        anc = _anchor(f, cap, fcff, gr, dil.rate, pv, pi, rf, ctype)
        range_low, range_high, fv_disagreement = _fv_range(f, anc)
        ex = _execution_inputs(f, cap, acq, gr, notes, ctype)
        scores = _pillars(f, cap, gr, pv, anc.value, ex, notes, ctype)
        comp, reco = _overall(f, scores, notes)

        # EVA on ONE basis: the measured return on the capital it was measured on
        # (R&D-capitalized ROIC x R&D-capitalized capital). The median-damped scoring
        # spread x raw capital mixed two bases and read ~43% low for R&D-heavy names.
        ic_basis = cap.rd[1] if cap.rd else cap.ic
        eva = ((cap.roic_used - cap.wacc) * ic_basis
               if (cap.roic_used is not None and ic_basis and ic_basis > 0) else None)
        v = Valuation(
            version=self.version, **scores,
            composite=round(comp, 2) if comp is not None else None,
            stars=stars(comp) if comp is not None else "",
            recommendation=reco, signal=_signal(reco),
            anchor_method=anc.method, anchor_value=_round_nonzero(anc.value),
            range_low=_round_nonzero(range_low), range_high=_round_nonzero(range_high),
            fv_peg=_round_nonzero(anc.fv_peg), fv_fvp=_round_nonzero(anc.fv_fvp),
            fv_dcf=_round_nonzero((anc.dcf or {}).get("per_share")),
            fv_excess_return=_round_nonzero((anc.exret or {}).get("per_share")),
            company_type=ctype.value, dcf=anc.dcf or {}, excess_return=anc.exret or {},
            reverse_dcf=pi.rdcf, young_dcf=anc.ydcf or {},
            cost_of_equity=round(cap.ke, 4),
            eva=round(eva, 0) if eva is not None else None,
            eq_verdict=ex.eq_verdict,
            subscores={"breakdown": notes},
            key_metrics=_key_metrics(f, cap, gr, dil, pv, pi, ex, fv_disagreement, ctype, anc),
            flags=list(f.flags))
        try:
            v.verdict = _verdict(f, v)
        except Exception as e:                       # guard: never let a verdict typo/bad field crash a ticker
            v.verdict = (f"{v.recommendation or 'N/A'} {v.stars or ''} - verdict unavailable ({type(e).__name__})").strip()
        return v


# --- evaluate() stages, in call order -----------------------------------------
# Every stage that does arithmetic on a fact series reads it off `f` itself
# (e.g. `ann = f.revenue_annuals or []`) rather than taking it as a parameter, so
# domain/contracts.py can see which clock each operand is on. A value handed in as
# an argument or read off a stage result is invisible to that scanner.

def _margin_history(f):
    """Operating margin by fiscal year, newest first (None where a year is missing)."""
    oia = f.operating_income_annuals or []
    ann = f.revenue_annuals or []
    return [(o / r) if (o is not None and r) else None for o, r in zip(oia, ann)]


def _company_type(f):
    """Which model values this firm (domain/company_type.py), decided once."""
    ctype, why = classify(getattr(f, "damodaran_industry", None), f.operating_income,
                          f.revenue, _margin_history(f))
    if ctype is not CompanyType.STANDARD:
        _add_flag(f, f"model: {ctype.value} - {why}")
    return ctype


class _Capital(NamedTuple):
    beta: float
    tax: float
    nopat: Optional[float]
    lease: float                    # operating lease liability, counted as debt
    debt_eff: float                 # total debt + leases
    ic: float                       # invested capital: debt_eff + equity - cash
    roic: Optional[float]           # raw NOPAT / IC
    rd: Optional[tuple]             # rd_capitalize() result when R&D is capitalized
    roic_used: Optional[float]      # measured ROIC: what the valuation uses
    roic_scored: Optional[float]    # median-damped ROIC: what Economics scores and the card shows
    spread: Optional[float]         # roic_scored - wacc
    roic_map: dict                  # raw per-year ROIC from the trend series
    mcap: Optional[float]
    wacc: float
    ke: float
    kd_pre: Optional[float]
    beta_t: float                   # stable-phase beta
    wacc_term: float
    ke_term: float


def relevered_beta(beta_unlevered, debt, equity_mktcap, marginal_tax=None):
    """Damodaran's bottom-up beta: industry unlevered beta x (1 + (1 - t) x D/E), with
    D/E at market value and t the marginal rate the industry beta was un-levered at."""
    from sources.damodaran import MARGINAL_TAX_US
    t = MARGINAL_TAX_US if marginal_tax is None else marginal_tax
    de = (debt or 0.0) / equity_mktcap if equity_mktcap and equity_mktcap > 0 else 0.0
    return beta_unlevered * (1 + (1 - t) * max(de, 0.0))


# Below these a firm is still in its early, riskier stage, not the mature industry
# its unlevered beta describes: an operating margin under 5% (the reverse DCF's
# terminal-margin floor), or a small company growing revenue above 30% a year.
EARLY_MARGIN, EARLY_GROWTH, EARLY_MAX_MCAP = 0.05, 0.30, 50e9


def _early_stage(f, mcap):
    oi, rev = f.operating_income, f.revenue
    if oi is None or oi <= 0:
        return "pre-profit"
    if rev and oi / rev < EARLY_MARGIN:
        return f"operating margin {oi / rev:.1%} (barely profitable)"
    ann = f.revenue_annuals or []
    if len(ann) > 3 and ann[3] and ann[3] > 0 and mcap and mcap < EARLY_MAX_MCAP:
        cagr = (ann[0] / ann[3]) ** (1 / 3) - 1
        if cagr > EARLY_GROWTH:
            return f"small high-growth firm (3y revenue CAGR {cagr:.0%})"
    return None


FIN_BETA_LO, FIN_BETA_HI = 0.6, 1.6


def _financial_beta(f):
    """A bank's or insurer's equity beta. The industry unlevered beta cannot be
    re-levered here: deposits and policy reserves would count as debt, the very thing
    that makes D/E meaningless for a financial firm. The regression beta, bounded to a
    plausible range, else the market's 1.0."""
    reg = f.beta
    if reg:
        b = max(FIN_BETA_LO, min(FIN_BETA_HI, reg))
        _add_flag(f, (f"financial firm: regression beta {reg:.2f}"
                      + (f" bounded to {b:.2f}" if abs(b - reg) > 1e-9 else "")
                      + " (industry beta not re-levered: deposits are not capital)"))
        return b
    _add_flag(f, "financial firm: no regression beta - market beta 1.0 used")
    return 1.0


def _bottom_up_beta(f, notes):
    """The beta Ke runs on. Industry unlevered beta re-levered at the firm's market D/E
    when the industry is known (Damodaran: a single-stock regression beta is too noisy,
    NVO read 0.35 against a pharma industry at 0.98); the regression beta otherwise;
    1.0 when neither exists. Debt includes capitalized operating leases, the same D
    the WACC weights use."""
    reg = f.beta
    bu = f.beta_unlevered_industry
    mcap = f.market_cap or ((f.price or 0) * (f.shares_diluted or 0)) or None
    if bu and mcap:
        debt = (f.total_debt or 0.0) + (f.operating_leases or 0.0)
        b = relevered_beta(bu, debt, mcap)
        why = (f"beta bottom-up {b:.2f} = {f.beta_industry or 'industry'} unlevered "
               f"{bu:.2f} re-levered at D/E {debt / mcap:.0%}")
        # A pre-profit firm is riskier than the mature industry it will grow into
        # (Damodaran starts young firms at a high-growth beta that fades). The mature
        # industry beta alone would discount RKLB at ~8.6%, so it keeps the higher one.
        stage = _early_stage(f, mcap)
        if stage and reg and reg > b:
            _add_flag(f, why + f" - {stage}, so the higher regression beta {reg:.2f} is used")
            return reg
        _add_flag(f, why + (f" (regression beta {reg:.2f} kept as a cross-check)" if reg else ""))
        return b
    return reg or 1.0


def _capital(f, rf, notes, ctype=CompanyType.STANDARD):
    """Returns on capital (leases, and R&D where possible, capitalized) and the cost
    of capital today and in the stable phase."""
    tax = f.tax_rate
    fin = ctype is CompanyType.FINANCIAL
    beta = _financial_beta(f) if fin else _bottom_up_beta(f, notes)
    erp_age = erp_months_old()
    if erp_age > config.ERP_STALE_MONTHS:
        _add_flag(f, (f"ERP {config.ERP*100:.2f}% as-of {config.ERP_AS_OF} is {erp_age}mo old "
                      f"- refresh (Assumptions form / config.ERP)"))
    crp = getattr(f, "crp", None) or 0.0
    if crp > 0:
        _add_flag(f, (f"country risk premium +{crp*100:.2f}% ({getattr(f, 'crp_basis', None) or 'country'}) "
                      f"- Ke = Rf + beta x (ERP + CRP)"))
    if fin:
        return _financial_capital(f, rf, beta, tax, crp)

    nopat = f.operating_income * (1 - tax) if f.operating_income is not None else None
    # operating lease liability is debt (Damodaran): inside WACC weights, IC and EV
    lease = getattr(f, "operating_leases", None) or 0
    debt_eff = (f.total_debt or 0) + lease
    ic = debt_eff + (f.equity or 0) - (f.cash or 0)
    if lease:
        _add_flag(f, f"operating leases ${lease/1e9:.1f}B capitalized into debt+IC (S5)")
    roic = (nopat / ic) if (nopat is not None and ic and ic > 0) else None
    rd = rd_capitalize(f.rnd_annuals, f.operating_income, ic, tax)
    if rd:
        notes.append(f"R&D-capitalized ROIC {rd[2]*100:.1f}%")
    roic_used = rd[2] if rd else roic

    # Prefer the REPORTED market cap: for a depositary listing price x SEC share
    # count is in the wrong unit.
    mcap = getattr(f, "market_cap", None) or (
        (f.price * f.shares_diluted) if (f.price and f.shares_diluted) else None)
    w, ke, kd_pre, wacc_note = wacc_true(rf, beta, mcap, debt_eff or None, f.cash, tax,
                                         f.interest_expense, f.operating_income, crp=crp)
    if ("default spread" in wacc_note and debt_eff) or "no debt/weights" in wacc_note:
        _add_flag(f, "WACC: " + wacc_note)              # every degraded-WACC path
    # terminal WACC: same capital structure and Kd, Ke at a stable-growth beta
    beta_t = terminal_beta(beta)
    w_term, ke_term, _, _ = wacc_true(rf, beta, mcap, debt_eff or None, f.cash, tax,
                                      f.interest_expense, f.operating_income,
                                      beta_override=beta_t, crp=crp)
    if abs(beta_t - beta) > 0.4:
        _add_flag(f, (f"terminal beta {beta:.2f} -> {beta_t:.2f} (stable-phase risk fade) - "
                      f"FV leans on this assumption"))

    # `roic_scored` (median-damped) is what Economics scores AND what the card
    # shows, so spread == displayed ROIC - WACC (invariant I2). The valuation keeps
    # the measured `roic_used`: damping is prudence in scoring a moat, not a claim
    # about the returns the business earns.
    roic_map = _trend_roic_map(f, tax)
    roic_scored, norm_note = normalized_roic(roic_used, roic_map)
    if norm_note:
        _add_flag(f, norm_note + " — คะแนน Economics และ ROIC บนการ์ดใช้ค่าที่ normalize แล้ว "
                                 "(การประเมินมูลค่ายังใช้ค่าที่วัดได้จริง)")
    spread = (roic_scored - w) if roic_scored is not None else None
    return _Capital(beta=beta, tax=tax, nopat=nopat, lease=lease, debt_eff=debt_eff, ic=ic,
                    roic=roic, rd=rd, roic_used=roic_used, roic_scored=roic_scored,
                    spread=spread, roic_map=roic_map, mcap=mcap, wacc=w, ke=ke,
                    kd_pre=kd_pre, beta_t=beta_t, wacc_term=w_term, ke_term=ke_term)


def _financial_capital(f, rf, beta, tax, crp):
    """A financial firm's "capital" is its book equity and its cost of capital is Ke:
    debt is raw material (deposits, float), so WACC, invested capital and leases do not
    apply. The return measured is ROE on beginning-of-year equity."""
    eq = f.equity
    base = f.equity_prior if (f.equity_prior and f.equity_prior > 0) else eq
    roe = (f.net_income / base) if (f.net_income is not None and base and base > 0) else None
    ke = cost_of_equity(rf, beta, crp)
    beta_t = terminal_beta(beta)
    ke_term = cost_of_equity(rf, beta_t, crp)
    mcap = getattr(f, "market_cap", None) or (
        (f.price * f.shares_diluted) if (f.price and f.shares_diluted) else None)
    _add_flag(f, "financial firm: ROE vs cost of equity replaces ROIC vs WACC "
                 "(debt is raw material, not capital)")
    return _Capital(beta=beta, tax=tax, nopat=f.net_income, lease=0.0, debt_eff=0.0,
                    ic=eq or 0.0, roic=roe, rd=None, roic_used=roe, roic_scored=roe,
                    spread=(roe - ke) if roe is not None else None, roic_map={}, mcap=mcap,
                    wacc=ke, ke=ke, kd_pre=None, beta_t=beta_t, wacc_term=ke_term,
                    ke_term=ke_term)


def _reinvestment(f, nopat, ctype=CompanyType.STANDARD):
    """Reinvestment = capex + acquisitions + dWC - D&A, and the FCFF it leaves.
    Returns (acquisitions counted, fcff). A financial firm has no FCFF."""
    if ctype is CompanyType.FINANCIAL:
        return 0.0, None
    # acquisitions_net is a PAYMENT: negative is a divestiture, floored at 0 rather
    # than credited as negative reinvestment (which would flatter FCFF).
    acq = max(0.0, f.acquisitions_net) if f.acquisitions_net is not None else 0.0
    dwc, dwc_label = working_capital_change(f)
    # Floored at 0 but no ceiling: reinvesting more than NOPAT is a real state and
    # must show as negative FCFF.
    reinvest = 0.0
    if nopat and nopat > 0 and f.capex is not None and f.dep_amort is not None:
        reinvest = max(0.0, (f.capex + acq + (dwc or 0.0) - f.dep_amort) / nopat)
    fcff = nopat * (1 - reinvest) if nopat is not None else None
    if reinvest > 1.0:
        _add_flag(f, (f"reinvestment {reinvest*100:.0f}% of NOPAT (capex+M&A+dWC-D&A) - "
                      f"consuming more cash than it earns; FCFF negative"))
    if dwc and f.revenue and abs(dwc) / f.revenue > 0.02:
        _add_flag(f, (f"working capital {'absorbed' if dwc > 0 else 'released'} "
                      f"${abs(dwc)/1e9:.1f}B ({abs(dwc)/f.revenue*100:.1f}% of revenue) - "
                      f"counted as reinvestment ({dwc_label})"))
    return acq, fcff


class _Growth(NamedTuple):
    roic_term: float                # stable-phase ROIC
    roic_high: float                # high-growth ROIC: the firm's own, else roic_term
    growth: float                   # consensus growth, capped at g <= ROIC
    rev_growth_yoy: Optional[float]  # FY0 over FY1 revenue growth
    actual_3y: Optional[float]      # 3-year revenue CAGR ending FY0
    eps0: Optional[float]           # trailing GAAP EPS
    ke_stable: float
    g_stable: float


def _growth(f, cap, acq, rf, ctype=CompanyType.STANDARD):
    """Terminal ROIC, the growth the valuation may assume, realised revenue growth,
    trailing EPS, and the stable-phase Ke and growth."""
    # Terminal ROIC comes first: it is also the fallback for the growth cap and the
    # high-growth ROIC when the firm's own ROIC is unmeasurable. Floored against
    # the higher of today's and the terminal cost of capital.
    own_roic = cap.roic_used if (cap.roic_used and cap.roic_used > 0) else None
    # an industry return on capital is no ceiling for a bank's return on equity
    sector_roic = (None if ctype is CompanyType.FINANCIAL
                   else getattr(f, "terminal_roic_sector", None))
    roic_term = terminal_roic(own_roic, max(cap.wacc, cap.wacc_term), sector_roic)
    if sector_roic and abs(roic_term - sector_roic) < 1e-9 and roic_term > cap.wacc_term:
        _add_flag(f, f"terminal ROIC {roic_term*100:.1f}% set by industry ceiling (Damodaran ROC)")

    g_cap = sustainable_growth_cap(cap.roic_used, roic_fallback=roic_term)
    if own_roic is None:
        _add_flag(f, (f"ROIC unmeasurable (negative or zero invested capital) - high-growth "
                      f"ROIC and growth cap both fall back to the terminal {roic_term*100:.1f}%, "
                      f"not the old 15%/30% defaults"))
    # a measured 0% growth is not missing (`is not None`, never `or 0.08`)
    g_lt = f.growth_lt if f.growth_lt is not None else 0.08
    growth_raw = _clamp(g_lt, 0.0, GROWTH_CAP)
    growth = min(growth_raw, g_cap)
    if growth_raw > growth + 1e-9:
        _add_flag(f, (f"growth capped {growth_raw*100:.0f}% -> {growth*100:.0f}% "
                      f"(g <= ROIC: faster needs outside capital)"))
    ann = f.revenue_annuals or []
    rev_1y = ann[1] if len(ann) > 1 else None
    rev_3y = ann[3] if len(ann) > 3 else None
    rev_growth_yoy = (ann[0] / rev_1y - 1) if (ann and rev_1y) else None
    actual_3y = ((ann[0] / rev_3y) ** (1 / 3) - 1) if (ann and rev_3y) else None

    nopat = cap.nopat
    eps0 = None
    ni = f.net_income
    if ctype is not CompanyType.FINANCIAL:
        ni, nonop = core_net_income(f)
        if nonop is not None:
            _add_flag(f, (f"non-operating income ${nonop/1e9:+.1f}B "
                          f"({nonop / abs(f.operating_income) * 100:+.0f}% of EBIT) stripped "
                          f"from net income for EPS and earnings quality "
                          f"(core ${ni/1e9:.1f}B vs reported ${f.net_income/1e9:.1f}B)"))
    # break-even net income (0.0) is a measurement, not a gap
    if ni is not None and f.shares_diluted:
        earn = min(ni, nopat) if (nopat and ni > nopat) else ni
        eps0 = earn / f.shares_diluted
    elif f.eps_gaap is not None:
        eps0 = f.eps_gaap

    # The high-growth years are discounted at today's Ke, unfloored (risk fades only
    # in the perpetuity, via terminal_beta). The stable-phase floor is a numerical
    # guard on (Ke - g), since g_stable is derived from it, not a risk view.
    ke_stable = max(cap.ke_term, rf + 0.035)
    g_stable = min(rf, ke_stable - 0.03)
    g_fund = fundamental_growth(f.capex, f.dep_amort, nopat, cap.roic_used, acquisitions=acq)
    if (ctype is not CompanyType.FINANCIAL and g_fund is not None and growth > 0.02
            and growth > 2 * max(g_fund, 0.0)):
        _add_flag(f, (f"growth {growth*100:.0f}% vs fundamental growth {g_fund*100:.1f}% "
                      f"(reinvest x ROIC) - unfunded by physical reinvestment"
                      + ("; asset-light? R&D not counted" if g_fund < 0.01 else "")))
    return _Growth(roic_term=roic_term,
                   roic_high=own_roic if own_roic is not None else roic_term,
                   growth=growth, rev_growth_yoy=rev_growth_yoy, actual_3y=actual_3y,
                   eps0=eps0, ke_stable=ke_stable, g_stable=g_stable)


class _Dilution(NamedTuple):
    rate: Optional[float]           # annual share-count growth charged
    label: Optional[str]
    fwd_eps: Optional[float]        # forward EPS after dilution


def _dilution(f, mcap):
    """Dilution is charged only to an EPS that does not already carry SBC: the
    consensus forward EPS is non-GAAP (SBC added back), while eps0 (FVP) and a
    SEC-derived forward EPS are GAAP, already expensed. No double counting."""
    rate, label = dilution_rate(f, mcap)
    fwd_is_gaap = str((getattr(f, "provenance", None) or {}).get("forward_eps", "")).startswith("sec-derived")
    fwd_eps = f.forward_eps if fwd_is_gaap else dilute(f.forward_eps, rate, years=1)
    if rate and rate >= SBC_DILUTION_FLAG_AT and not fwd_is_gaap:
        if f.forward_eps is None or fwd_eps is None:
            _add_flag(f, (f"share-count dilution {rate*100:.1f}%/yr NOT charged — "
                          f"ไม่มี forward EPS ให้หัก (source: {label})"))
        else:
            _add_flag(f, (f"share-count dilution {rate*100:.1f}%/yr charged to the "
                          f"non-GAAP forward EPS ({f.forward_eps} -> "
                          f"{round(fwd_eps, 2)}); source: {label}"))
    elif fwd_is_gaap and rate:
        _add_flag(f, "forward EPS is SEC/GAAP-derived - SBC already expensed, "
                     "no dilution charged (would double-count)")
    return _Dilution(rate=rate, label=label, fwd_eps=fwd_eps)


class _PointValues(NamedTuple):
    fv_peg: Optional[float]         # Fundamental PEG on consensus growth
    peg_detail: Optional[dict]
    g_realised: Optional[float]
    fv_peg_realised: Optional[float]  # Fundamental PEG on realised growth
    growth_fv_x: Optional[float]    # fv_peg / fv_peg_realised
    growth_unreconciled: bool
    fv_fvp: Optional[float]         # Future Value Projection


def _point_values(f, cap, gr, fwd_eps):
    """The two point fair values (Fundamental PEG, Future Value Projection), plus the
    PEG on realised growth to show how much the value rests on consensus."""
    fv_peg, peg_d = fundamental_peg_price(gr.growth, gr.g_stable, cap.ke, gr.roic_high, gr.roic_term,
                                          fwd_eps, ke_stable=gr.ke_stable)
    if peg_d and peg_d.get("pe_clamped"):
        _add_flag(f, (f"fundamental PE clamped to [{PE_FLOOR:.0f}, {PE_CEIL:.0f}] "
                      f"(raw {peg_d.get('fair_pe_raw')}) - FV is a boundary, not an estimate"))

    # The fair value runs on consensus growth while Demand scores realised growth,
    # so value the PEG on both. Realised = the more conservative of the 1y and 3y
    # rates, floored at 0 (no valuing on negative compounding).
    real = [g for g in (gr.rev_growth_yoy, gr.actual_3y) if g is not None]
    g_realised = max(0.0, min(real)) if real else None
    fv_peg_realised, growth_fv_x = None, None
    if g_realised is not None:
        fv_peg_realised, _ = fundamental_peg_price(g_realised, gr.g_stable, cap.ke,
                                                   gr.roic_high, gr.roic_term, fwd_eps,
                                                   ke_stable=gr.ke_stable)
    # One-sided: fires only when consensus FLATTERS the fair value. Consensus below
    # realised growth means the row is cheap even on the conservative story.
    if fv_peg and fv_peg_realised and fv_peg_realised > 0:
        growth_fv_x = round(fv_peg / fv_peg_realised, 2)
    growth_unreconciled = bool(growth_fv_x and growth_fv_x >= GROWTH_FV_DISAGREE)
    if growth_unreconciled:
        _add_flag(f, (
            f"valuation ใช้ growth {gr.growth*100:.1f}% (consensus) แต่บริษัททำได้จริง "
            f"{g_realised*100:.1f}% → FV ${round(fv_peg, 2)} vs ${round(fv_peg_realised, 2)} "
            f"({growth_fv_x}x) — Price pillar ถูกจำกัดไว้ที่กลาง ๆ เพราะ 'ถูกหรือแพง' "
            f"ขึ้นกับว่าเชื่อ growth ของใคร ไม่ใช่สิ่งที่วัดได้"))

    # The exit multiple is stable-phase, so built at the stable-phase Ke; the five
    # high-growth years are discounted at today's Ke. eps0 is GAAP: not diluted.
    exit_pe = stable_exit_pe(gr.g_stable, gr.ke_stable, gr.roic_term)
    payout_h = max(0.0, 1 - gr.growth / gr.roic_high) if gr.roic_high else 0.0
    fv_fvp = future_value_projection(gr.eps0, gr.growth, cap.ke, exit_pe, payout=payout_h)
    return _PointValues(fv_peg=fv_peg, peg_detail=peg_d, g_realised=g_realised,
                        fv_peg_realised=fv_peg_realised, growth_fv_x=growth_fv_x,
                        growth_unreconciled=growth_unreconciled, fv_fvp=fv_fvp)


class _PricedIn(NamedTuple):
    tmargin: Optional[float]        # terminal operating margin (None: financial firm)
    tm_label: str
    tm_anchored: bool               # derived from the firm's own margin
    rdcf: dict                      # reverse_dcf() result


def _priced_in(f, cap, gr, rf, ctype=CompanyType.STANDARD):
    """Terminal margin and the reverse DCF: what growth the market prices in. Not run
    for a financial firm, which has no operating margin or FCFF to reverse."""
    if ctype is CompanyType.FINANCIAL:
        return _PricedIn(tmargin=None, tm_label="n/a (financial firm)", tm_anchored=False, rdcf={})
    tmargin, tm_label, tm_anchored = terminal_margin(
        f.ticker, f.operating_income, f.revenue,
        industry_margin=getattr(f, "industry_margin", None),
        industry=getattr(f, "damodaran_industry", None))
    # "anchored" does not mean the firm's own margin survived the clamp
    if "CAPPED" in tm_label or "FLOORED" in tm_label:
        _add_flag(f, tm_label)
    if str((getattr(f, "provenance", None) or {}).get("operating_income", "")).startswith("sec-derived"):
        _add_flag(f, "EBIT derived (pretax + interest) - filer tags no operating income")

    ann = f.revenue_annuals or []
    rev_1y = ann[1] if len(ann) > 1 else None
    # debt_eff (leases included) and the resolved market cap, so the reverse DCF
    # values the same firm the WACC priced; today's margin so the path ramps.
    m_now = (f.operating_income / f.revenue) if (f.operating_income is not None and f.revenue) else None
    rdcf = reverse_dcf(price=f.price, shares=f.shares_diluted, revenue=f.revenue, rev_1y=rev_1y,
                       total_debt=cap.debt_eff or None, cash=f.cash, wacc_val=cap.wacc, g=rf,
                       tax=cap.tax, margin=tmargin, wacc_term=cap.wacc_term,
                       roic_term=gr.roic_term, market_cap=cap.mcap, margin_now=m_now,
                       # FY over FY, the rate Demand scores (not TTM over FY-2)
                       actual_growth=gr.rev_growth_yoy, roic_now=gr.roic_high)
    if rdcf.get("triggered") and m_now is not None and abs(m_now - tmargin) > 0.10:
        _add_flag(f, (f"RevDCF margin ramps {m_now*100:.0f}% -> {tmargin*100:.0f}% over "
                      f"{REVERSE_HORIZON}y (was held flat at the terminal margin from year 1)"))
    if rdcf.get("triggered") and not tm_anchored:
        _add_flag(f, tm_label + " - RevDCF verdict approximate")
    if (rdcf.get("funding_ratio") or 0) >= 2.0:
        _add_flag(f, (f"market prices {rdcf.get('implied_cagr_pct')}%/yr growth against a terminal "
                      f"ROIC of {rdcf.get('roic_terminal_pct')}% ({rdcf.get('funding_ratio')}x) - "
                      f"far beyond self-funding; the gap has to come from outside capital"))
    if rdcf.get("out_of_band"):
        _add_flag(f, "reverse DCF: " + (rdcf.get("verdict") or "no implied growth"))
    return _PricedIn(tmargin=tmargin, tm_label=tm_label, tm_anchored=tm_anchored, rdcf=rdcf)


class _Anchor(NamedTuple):
    method: str
    value: Optional[float]          # the fair value the Price pillar tests
    fv_peg: Optional[float]         # point values after the FVP sanity check and unit gate
    fv_fvp: Optional[float]
    ydcf: Optional[dict]            # young_dcf.evaluate() result, pre-profit only
    dcf: Optional[dict] = None      # fcff_dcf.value() result, operating companies
    exret: Optional[dict] = None    # financial.value() result, banks and insurers


# Damodaran's marginal tax rate for the stable phase (the US federal + state rate his
# industry betas are un-levered at).
from sources.damodaran import MARGINAL_TAX_US as _MARGINAL_TAX  # noqa: E402
CYCLICAL_MARGIN_YEARS = 3       # years for a cyclical's margin to revert to its median
DCF_TERMINAL_HEAVY = 85.0       # % of firm value in the terminal value worth a flag
S2C_DEFAULT = 1.5               # sales-to-capital when neither the firm nor industry has one
S2C_BAND = (0.4, 6.0)           # plausible range for a firm's own sales-to-capital
QUOTE_SHARES_TOLERANCE = 0.15   # filing vs quote share count before we trust the quote


def _quote_shares(f, cap):
    """The share count per-share values are divided by, in the QUOTE's unit. A
    depositary listing (one ADR = several ordinary shares) files ordinary shares while
    the price is per ADR; market cap / price recovers the ADR count."""
    sh = f.shares_diluted
    if cap.mcap and f.price:
        implied = cap.mcap / f.price
        if not sh or abs(sh / implied - 1) > QUOTE_SHARES_TOLERANCE:
            _add_flag(f, (f"intrinsic value per share uses market cap / price "
                          f"({implied/1e6:,.0f}M shares) - the filed count "
                          f"{(sh or 0)/1e6:,.0f}M is in a different unit"))
            return implied
    return sh


def _dcf_sales_to_capital(f, cap):
    """Marginal sales-to-capital: the firm's own revenue / invested capital, held
    inside [0.5x, 2x] of its Damodaran industry's (else a plausible band). Returns
    (ratio, label)."""
    ind = getattr(f, "industry_sales_to_capital", None)
    own = (f.revenue / cap.ic) if (f.revenue and cap.ic and cap.ic > 0) else None
    if own is not None:
        lo, hi = (0.5 * ind, 2.0 * ind) if ind else S2C_BAND
        s2c = _clamp(own, lo, hi)
        if abs(s2c - own) > 1e-9:
            return s2c, (f"own {own:.2f} held to {s2c:.2f} "
                         f"({'industry ' + format(ind, '.2f') + ' x0.5-2' if ind else 'band'})")
        return s2c, f"own {own:.2f}"
    if ind:
        return ind, f"industry {ind:.2f}"
    return S2C_DEFAULT, f"default {S2C_DEFAULT}"


def _dcf_revenue_growth(f, gr):
    """Revenue growth for the five high-growth years: consensus revenue growth (the
    average of the near and far years), else the more conservative of realised 1y/3y
    growth, else the capped EPS growth. Returns (g, source)."""
    cons = [g for g in (f.fwd_growth_near, f.fwd_growth_far) if g is not None]
    if cons:
        g, src = sum(cons) / len(cons), "consensus revenue growth"
    else:
        real = [g for g in (gr.rev_growth_yoy, gr.actual_3y) if g is not None]
        if real:
            g, src = max(0.0, min(real)), "realised revenue growth"
        else:
            g, src = gr.growth, "EPS growth (no revenue history)"
    return _clamp(g, -0.05, GROWTH_CAP), src


def _lease_ebit_addback(f, cap, rf):
    """Interest on operating leases to add back to EBIT. The lease liability is debt
    in the DCF (cap.debt_eff) and capital in sales-to-capital, so EBIT must stop
    charging its financing cost (Damodaran). Under US GAAP the whole lease cost sits
    in EBIT; under IFRS 16 the interest is already below EBIT, so nothing to add."""
    if not cap.lease or getattr(f, "accounting_standard", None) == "ifrs":
        return 0.0
    return cap.lease * (cap.kd_pre if cap.kd_pre else rf + DEFAULT_KD_SPREAD)


def _dcf_target_margin(f, m_now):
    """Where today's margin goes in the FCFF DCF. A barely profitable firm (under 5%)
    converges on its industry's margin; any other keeps its own, limited only at twice
    its industry's (a 64% NVDA is not forced down to the reverse DCF's 40% ceiling,
    which is a forecast of competition, not a normalization). Returns (margin, label)."""
    ind = getattr(f, "industry_margin", None)
    if m_now < TERMINAL_MARGIN_FLOOR and ind and ind > m_now:
        return ind, f"industry margin {ind*100:.1f}% (today's {m_now*100:.1f}% is a stage)"
    hi = max(TERMINAL_MARGIN_CAP, 2 * ind) if ind and ind > 0 else None
    if hi is not None and m_now > hi:
        return hi, f"own {m_now*100:.1f}% held to 2x industry {hi*100:.1f}%"
    return m_now, f"own margin {m_now*100:.1f}%"


def _fcff_dcf(f, cap, gr, pi, ctype, rf):
    """Damodaran's FCFF DCF for an operating company, on lease-adjusted EBIT. A
    cyclical's margin reverts to its own multi-year median; any other firm keeps its
    own (_dcf_target_margin). Returns the fcff_dcf result with its sources, or None."""
    if not f.revenue or f.operating_income is None:
        return None
    addback = _lease_ebit_addback(f, cap, rf)
    m_adj = addback / f.revenue
    m_now = f.operating_income / f.revenue + m_adj
    years = 5
    if ctype is CompanyType.CYCLICAL:
        norm = normalized_margin(_margin_history(f))
        if norm is None or norm <= 0:
            return None
        target, t_label = norm + m_adj, f"normalized median {norm*100:.1f}%"
        years = CYCLICAL_MARGIN_YEARS
        _add_flag(f, (f"cyclical: operating margin {m_now*100:.1f}% reverts to its "
                      f"normalized {target*100:.1f}% over {years}y"))
    else:
        if m_now <= 0:
            return None
        target, t_label = _dcf_target_margin(f, m_now)
    g_high, g_src = _dcf_revenue_growth(f, gr)
    s2c, s2c_src = _dcf_sales_to_capital(f, cap)
    shares = _quote_shares(f, cap)
    out = fcff_dcf.value(
        revenue=f.revenue, margin_now=m_now, margin_target=target, g_high=g_high,
        g_stable=gr.g_stable, sales_to_capital=s2c, tax_now=cap.tax,
        tax_marginal=_MARGINAL_TAX, wacc=cap.wacc,
        wacc_stable=max(cap.wacc_term, gr.g_stable + 0.01), roic_stable=gr.roic_term,
        cash=f.cash, debt=cap.debt_eff, shares=shares, margin_years=years)
    if not out:
        return None
    out["sources"] = {"growth": g_src, "sales_to_capital": s2c_src, "margin_target": t_label,
                      "lease_interest_added": round(addback, 0) if addback else None}
    if (out.get("terminal_share_pct") or 0) > DCF_TERMINAL_HEAVY:
        _add_flag(f, (f"FCFF DCF: {out['terminal_share_pct']}% of value is terminal - "
                      f"the fair value rests on the stable-phase assumptions"))
    return out


def _excess_return(f, cap, gr):
    """Excess-return value of a bank or insurer (domain/engine/financial.py)."""
    out = financial_mod.value(book_equity=f.equity, roe=cap.roic_used, ke=cap.ke,
                              g_high=gr.growth, g_stable=gr.g_stable,
                              shares=_quote_shares(f, cap))
    if out:
        _add_flag(f, (f"excess-return model: ROE {out['roe']*100:.1f}% fades to "
                      f"{out['roe_stable']*100:.1f}% vs Ke {out['ke']*100:.1f}% on book "
                      f"equity ${out['book_equity']/1e9:,.1f}B"))
    return out


def _anchor(f, cap, fcff, gr, dil_rate, pv, pi, rf, ctype=CompanyType.STANDARD):
    """Choose the fair-value method the Price pillar tests, with its fallbacks:
    FINANCIAL -> excess-return model; STANDARD / CYCLICAL -> FCFF DCF; YOUNG, or when
    the primary model cannot run -> the earnings paths (FVP / Fundamental PEG), and
    pre-profit -> the young-company model."""
    eps_pos = gr.eps0 is not None and gr.eps0 > 0
    fcf_pos = fcff is not None and fcff > 0
    fv_peg, fv_fvp = pv.fv_peg, pv.fv_fvp
    # an FVP below 10% of price is a broken input: discard the method, not the row
    if fv_fvp and f.price and fv_fvp < 0.1 * f.price:
        _add_flag(f, "FVP discarded (below 10% of price - unreliable inputs)")
        fv_fvp = None
    # Route to the young-company model (domain/engine/young_dcf.py) on EARNINGS,
    # not free cash flow: a profitable firm out-investing its NOPAT this year is an
    # ordinary DCF case with a loud flag, not a young company.
    if not fcf_pos and eps_pos and ctype is not CompanyType.FINANCIAL:
        _add_flag(f, "FCFF negative on heavy reinvestment but earnings are positive - "
                     "valued on the normal earnings paths, not the young-company model")
    ydcf = dcf = exret = None
    method = value = None
    try:
        if ctype is CompanyType.FINANCIAL:
            exret = _excess_return(f, cap, gr)
            if exret and exret.get("per_share"):
                method, value = "Excess Return Model", exret["per_share"]
        elif ctype in (CompanyType.STANDARD, CompanyType.CYCLICAL):
            dcf = _fcff_dcf(f, cap, gr, pi, ctype, rf)
            if dcf and dcf.get("per_share"):
                method, value = "FCFF DCF", dcf["per_share"]
    except Exception as e:          # a primary model must never kill a row
        _add_flag(f, f"{ctype.value} model unavailable ({type(e).__name__}) - earnings paths used")
    # the same broken-input guard the FVP gets: a value under 10% of the price says
    # more about one distorted input (a charge-laden EBIT) than about the business
    if value is not None and f.price and value < 0.1 * f.price:
        _add_flag(f, (f"{method} ${value} is below 10% of the price - treated as a broken "
                      f"input, not a verdict; anchored on the earnings paths and left out "
                      f"of the fair-value range"))
        for d in (dcf, exret):
            if d:
                d["discarded"] = "below 10% of the price"
        method = value = None
    elif value is None and ctype is not CompanyType.YOUNG:
        primary = (exret if ctype is CompanyType.FINANCIAL else dcf)
        model = "excess-return model" if ctype is CompanyType.FINANCIAL else "FCFF DCF"
        why = ("equity value <= 0 (reinvestment for the assumed growth exceeds what the "
               "margin earns)" if primary else "inputs missing")
        _add_flag(f, f"{model}: {why} - anchored on the earnings paths")
    if value is None:
        if ctype is CompanyType.FINANCIAL and not eps_pos:
            # a loss-making bank has no FCFF and no young-company story to tell
            method = "No point valuation (loss-making financial)"
        elif not eps_pos:
            method, value, ydcf = _young_company_anchor(f, cap, fcff, gr, dil_rate, pi, rf)
        elif cap.spread is not None and cap.spread > 0.10 and (gr.rev_growth_yoy or 0) > 0.15:
            method, value = "Future Value Projection", fv_fvp
        else:
            method, value = "Fundamental PEG", fv_peg
    if value is None and method not in ("Terminal-Anchored Reverse DCF",
                                        "No point valuation (loss-making financial)"):
        if method == "Fundamental PEG" and fv_fvp:
            method, value = "Future Value Projection", fv_fvp
        elif method == "Future Value Projection" and fv_peg:
            method, value = "Fundamental PEG", fv_peg
        if value is None:
            method = ("No point valuation (financial)" if ctype is CompanyType.FINANCIAL
                      else "Terminal-Anchored Reverse DCF")

    # Per-share unit gate, placed after every valuation method has run so no
    # method can bypass it. When filings and quote disagree on units we cannot
    # tell which is wrong, so every per-share value is suppressed.
    if not getattr(f, "per_share_unit_ok", True):
        fv_peg = fv_fvp = None
        ydcf = dcf = exret = None
        method, value = "No per-share valuation (unit mismatch)", None
    return _Anchor(method=method, value=value, fv_peg=fv_peg, fv_fvp=fv_fvp, ydcf=ydcf,
                   dcf=dcf, exret=exret)


def _young_company_anchor(f, cap, fcff, gr, dil_rate, pi, rf):
    """Pre-profit: the young-company DCF anchors only when its band qualifies;
    otherwise the reverse DCF stands alone. Returns (method, value, ydcf)."""
    ydcf = None
    try:
        ydcf = young_dcf.evaluate(
            f, rf=rf, wacc=cap.wacc, roic_term=gr.roic_term, invested_capital=cap.ic,
            debt_eff=cap.debt_eff, tax=cap.tax, target_margin=pi.tmargin,
            target_margin_label=pi.tm_label, annual_dilution=dil_rate, fcff=fcff,
            growth_fallback=f.growth_lt, target_margin_anchored=pi.tm_anchored)
    except Exception as e:          # a young-company model must never kill a row
        _add_flag(f, f"young-company DCF unavailable ({type(e).__name__})")
    if ydcf and ydcf.get("promote"):
        mc = ydcf["monte_carlo"]
        value = mc["p50"]
        _add_flag(f, (
            f"pre-profit valued forward (S20): p50 ${value} "
            f"[p10 ${mc['p10']} - p90 ${mc['p90']}], "
            f"survival {ydcf['p_survival']*100:.0f}%, distress ${ydcf['distress_per_share']}"))
        return "Young-Company DCF (failure-adjusted)", value, ydcf
    if ydcf:
        _add_flag(f, "young-company DCF is informational only: "
                     + (ydcf.get("blocked_reason") or "did not qualify"))
    return "Terminal-Anchored Reverse DCF", None, ydcf


def _fv_range(f, anc):
    """[low, high] of the point methods, or a young company's simulated band.
    Returns (low, high, disagreement ratio or None)."""
    def kept(d):
        return None if (d or {}).get("discarded") else (d or {}).get("per_share")
    methods = {"FCFF DCF": kept(anc.dcf), "Excess Return Model": kept(anc.exret),
               "Fundamental PEG": anc.fv_peg, "Future Value Projection": anc.fv_fvp}
    avail = {k: v for k, v in methods.items() if v and v > 0}
    range_low = min(avail.values()) if avail else None
    range_high = max(avail.values()) if avail else None
    # [low, high] is two point methods, not a confidence interval. When they are
    # far apart the gap is labelled as a method conflict, not narrowed.
    fv_disagreement = None
    if range_low and range_high and range_low > 0:
        ratio = range_high / range_low
        if ratio >= FV_DISAGREE_RATIO:
            fv_disagreement = round(ratio, 2)
            lo_m = min(avail, key=avail.get)
            hi_m = max(avail, key=avail.get)
            _add_flag(f, (f"fair-value methods disagree {ratio:.1f}x ({hi_m} ${range_high:.0f} "
                          f"vs {lo_m} ${range_low:.0f}) - the band is a method conflict, "
                          f"not a confidence interval; the anchor is {anc.method}"))
    if anc.method.startswith("Young-Company") and (anc.ydcf or {}).get("monte_carlo"):
        range_low = anc.ydcf["monte_carlo"]["p10"]
        range_high = anc.ydcf["monte_carlo"]["p90"]
    return range_low, range_high, fv_disagreement


class _ExecutionInputs(NamedTuple):
    eq_verdict: Optional[str]
    inc_roic: Optional[float]
    inc_src: Optional[str]
    inc_declined: bool              # the multi-year measure refused; no 1-year proxy
    margin_trend: Optional[str]
    roic_delta: Optional[float]     # like-for-like FY0 - FY1 ROIC


def _execution_inputs(f, cap, acq, gr, notes, ctype=CompanyType.STANDARD):
    """Earnings quality, incremental ROIC, margin trend and the ROIC trend. A financial
    firm's accruals, invested capital and operating margin are not comparable to an
    operating company's, so those signals are skipped rather than mis-scored."""
    if ctype is CompanyType.FINANCIAL:
        notes.append("E_exec EQ/incremental-ROIC adj=skipped(financial firm)")
        return _ExecutionInputs(eq_verdict=None, inc_roic=None, inc_src=None,
                                inc_declined=True, margin_trend=None, roic_delta=None)
    eq_verdict = _earnings_signals(f, gr.rev_growth_yoy)
    inc_roic, inc_src, inc_declined = _incremental_roic(f, cap.roic_map, acq, cap.tax, notes)
    return _ExecutionInputs(eq_verdict=eq_verdict, inc_roic=inc_roic, inc_src=inc_src,
                            inc_declined=inc_declined, margin_trend=_margin_trend(f),
                            roic_delta=_roic_delta(f, cap))


def _earnings_signals(f, rev_growth_yoy):
    """Earnings-quality verdict and the billings signal; flags both. Tested on core
    net income: a one-off gain is not an accrual problem of the operating business."""
    ann = f.revenue_annuals or []
    rev_1y = ann[1] if len(ann) > 1 else None
    core_ni, _ = core_net_income(f)
    eq_verdict, eq_flags, _ = earnings_quality(
        core_ni, f.cfo, f.total_assets, f.sbc, f.revenue,
        receivables=f.receivables, receivables_prior=getattr(f, "receivables_prior", None),
        revenue_prior=rev_1y,
        # FY over FY: a TTM-inflated growth rate would hide the AR signal
        revenue_growth=rev_growth_yoy)
    for fl in eq_flags:
        _add_flag(f, "EQ: " + fl)
    if f.deferred_revenue and f.deferred_revenue_prior and rev_growth_yoy is not None:
        dr_g = f.deferred_revenue / f.deferred_revenue_prior - 1
        # deferred revenue is a fiscal-year-end balance: compare FY revenue growth
        sig = "positive" if dr_g > rev_growth_yoy else "lagging"
        _add_flag(f, f"billings {sig} (deferred rev {dr_g*100:+.0f}% vs rev {rev_growth_yoy*100:+.0f}%)")
    return eq_verdict


def _incremental_roic(f, roic_map, acq, tax, notes):
    """Incremental ROIC: dNOPAT / dIC over up to 5 contiguous years, whose
    denominator is large and stable. The 1-year capex + M&A - D&A fallback has a
    near-zero denominator whenever capex ~ D&A, hence the [-2, 2] clamp. Both are
    raw (no R&D or lease capitalization). Returns (roic, source, declined)."""
    oia = f.operating_income_annuals or []
    inc_roic, inc_src, declined = None, None, False
    if roic_map:
        win = trend_mod._window(roic_map, years=5)
        pct, why = (trend_mod.incremental_roic_pct(roic_map, win) if len(win) >= 2
                    else (None, "ปีงบไม่ต่อเนื่องพอ"))
        if pct is not None:
            inc_roic = _clamp(pct / 100.0, -2.0, 2.0)
            inc_src = f"ΔNOPAT/ΔIC {len(win)}y"
        else:
            # The multi-year measure refused (normally: no new capital to measure).
            # The noisy 1-year proxy must not answer that question either.
            declined = True
            notes.append(f"E_exec incremental-ROIC adj=skipped({why})")
    if (inc_roic is None and not declined
            and len(oia) > 1 and oia[1] and f.capex is not None and f.dep_amort is not None):
        d_nopat = (oia[0] - oia[1]) * (1 - tax)
        reinvest_1y = (f.capex + acq - f.dep_amort)
        if reinvest_1y and reinvest_1y > 0:
            inc_roic = _clamp(d_nopat / reinvest_1y, -2.0, 2.0)
            inc_src = "1y capex+M&A−D&A (ประมาณ)"
    return inc_roic, inc_src, declined


def _margin_trend(f):
    """Operating margin FY0 vs FY1: "up", "down", "flat" or None. Not TTM vs FY-2
    (spans two years), nor TTM vs FY0 (identical until the TTM passes the FY end)."""
    oia = f.operating_income_annuals or []
    ann = f.revenue_annuals or []
    rev_1y = ann[1] if len(ann) > 1 else None
    if len(oia) > 1 and oia[1] and rev_1y and ann and ann[0] and oia[0] is not None:
        m_now, m_prior = oia[0] / ann[0], oia[1] / rev_1y
        if m_prior:
            return "up" if m_now > m_prior * 1.02 else "down" if m_now < m_prior * 0.98 else "flat"
    return None


def _roic_delta(f, cap):
    """Like-for-like FY0 - FY1 ROIC change, or None."""
    oia = f.operating_income_annuals or []
    if not (f.equity_prior is not None and f.cash_prior is not None and len(oia) > 1 and oia[1]):
        return None
    # Leases on both sides of the trend. With no filed prior-year lease, reuse
    # today's (flagged): zero is the one answer known to be wrong.
    lease_prior = getattr(f, "operating_leases_prior", None)
    if lease_prior is None and cap.lease:
        lease_prior = cap.lease
        _add_flag(f, "prior-year lease liability not filed - current lease reused "
                     "for the like-for-like ROIC trend")
    ic_prior = (f.total_debt_prior or 0) + (lease_prior or 0) + f.equity_prior - f.cash_prior
    if not (ic_prior and ic_prior > 0):
        return None
    roic_fy0 = _roic_fy(op_income=oia[0], invested=cap.ic, rnd_series=f.rnd_annuals,
                        tax=cap.tax, rd_basis=cap.rd)
    roic_fy1 = _roic_fy(op_income=oia[1], invested=ic_prior, rnd_series=(f.rnd_annuals or [])[1:],
                        tax=cap.tax, rd_basis=cap.rd)
    if roic_fy0 is not None and roic_fy1 is not None:
        return roic_fy0 - roic_fy1
    if cap.rd:
        _add_flag(f, "ROIC trend skipped: the headline ROIC is R&D-capitalized but "
                     "the prior year cannot be measured the same way (short R&D "
                     "history) - a raw-vs-adjusted delta reads as a collapse that "
                     "never happened")
    return None


def _roic_fy(op_income, invested, rnd_series, tax, rd_basis):
    """ROIC for ONE fiscal year on the headline's basis: R&D-capitalized only when
    the headline is (`rd_basis`). None when the basis cannot be matched, because a
    raw-vs-adjusted delta reads as a fake collapse."""
    if op_income is None or not invested or invested <= 0:
        return None
    if not rd_basis:                                # headline is raw -> stay raw
        return op_income * (1 - tax) / invested
    out = rd_capitalize(rnd_series, op_income, invested, tax)
    return out[2] if out else None


def _pillars(f, cap, gr, pv, anchor_value, ex, notes, ctype=CompanyType.STANDARD):
    """The four DEEP pillar scores, each from its rubric in _r_*."""
    acq_int, acq_label = acquisition_intensity(f.acquisitions_net, f.revenue)
    fade, fade_why = fade_ratio(f.fwd_growth_near, f.fwd_growth_far)
    cons_adj, cons_note = growth_consistency(gr.rev_growth_yoy, gr.actual_3y)
    D = _r_demand(gr.rev_growth_yoy, getattr(f, "peer_median_growth", None), acq_int, fade,
                  notes, acq_label=acq_label, fade_why=fade_why,
                  consistency_adj=cons_adj, consistency_note=cons_note)
    try:
        t5 = trend_mod.build(f)
    except Exception:                       # a display feature must never kill a row
        t5 = {}
    trend_rows = (t5 or {}).get("rows")
    # a bank's "free cash flow" swings with its loan book, not its execution
    fcf_adj, fcf_note = ((0.0, None) if ctype is CompanyType.FINANCIAL
                         else fcf_durability(trend_rows))
    beat_adj, beat_note = beat_consistency(
        f.earnings_surprises or getattr(f, "eps_surprises_backfill", None))
    E_exec = _r_execution(ex.eq_verdict, ex.margin_trend, ex.inc_roic, cap.wacc, notes,
                          fcf_adj=fcf_adj, fcf_note=fcf_note, inc_src=ex.inc_src,
                          skip_inc_note=ex.inc_declined,
                          beat_adj=beat_adj, beat_note=beat_note)
    cap_adj, cap_note = competitive_advantage_period(cap.roic_map, cap.wacc)
    # the same ROIC the spread was built from (invariant I2)
    fin = ctype is CompanyType.FINANCIAL
    # a bank's ROE is not comparable to its industry's return on capital
    sec_adj, sec_note = sector_relative(cap.roic_scored,
                                        None if fin else getattr(f, "terminal_roic_sector", None))
    E_econ = _r_economics(cap.spread, ex.roic_delta, notes, cap_adj=cap_adj, cap_note=cap_note,
                          sector_adj=sec_adj, sector_note=sec_note)
    # the same EV the WACC weights and the reverse DCF were built from
    ev = (cap.mcap + (cap.debt_eff or 0) - (f.cash or 0)) if cap.mcap else None
    fy_adj, fy_note = (0.0, None) if fin else fcf_yield_adj(trend_rows, ev, cap.wacc)
    P = _r_price(anchor_value, f.price, getattr(f, "own_pe_pctile", None), notes,
                 fcf_adj=fy_adj, fcf_note=fy_note)
    # When the two growth stories disagree, "cheap" is a belief about consensus:
    # cap (never lift) P at NEUTRAL.
    if pv.growth_unreconciled and P is not None and P > NEUTRAL_SCORE:
        notes.append(f"P growth-unreconciled(consensus vs realised FV {pv.growth_fv_x}x)"
                     f"={NEUTRAL_SCORE - P:+.2f}")
        P = NEUTRAL_SCORE
    return {"D": D, "E_exec": E_exec, "E_econ": E_econ, "P": P}


def _overall(f, scores, notes):
    """Composite and recommendation, with the caps for unmeasured pillars.
    Returns (composite, recommendation)."""
    comp = composite(scores)
    # Say so when composite()'s neutral bound moved the score. This float renorm
    # mirrors composite()'s Decimal one; it only prints the flag, so it stays float.
    missing = [k for k in WEIGHTS if scores.get(k) is None]
    if missing and comp is not None:
        avail = {k: v for k, v in scores.items() if v is not None}
        wsum = sum(WEIGHTS[k] for k in avail)
        renorm = sum(avail[k] * WEIGHTS[k] for k in avail) / wsum if wsum else None
        if renorm is not None and renorm > comp + 1e-9:
            _add_flag(f, (f"composite {renorm:.2f} -> {comp:.2f}: {', '.join(missing)} not "
                          f"measurable, scored NEUTRAL ({NEUTRAL_SCORE}) instead of inheriting "
                          f"the average of the pillars that were"))
        # Also in the breakdown drawer, where scores are read. Must NOT end in
        # "=<number>": the drawer colours a trailing number as an adjustment.
        for k in missing:
            notes.append(f"{k} วัดไม่ได้=ใช้ค่ากลาง {NEUTRAL_SCORE} "
                         f"(น้ำหนัก {WEIGHTS[k]:.0%} · ไม่มีข้อมูลพอ ไม่ได้แปลว่าแย่)")
    reco = recommendation(comp) if comp is not None else None
    for cap_reco, pillar in ((cap_reco_without_price, "P"), (cap_reco_without_quality, "E_econ")):
        reco, why = cap_reco(reco, scores[pillar])
        if why:
            _add_flag(f, why)
    return comp, reco


def _pct(x, nd):
    """x as a rounded percentage, None-safe (0.0 stays 0.0)."""
    return round(x * 100, nd) if x is not None else None


def _round_nonzero(x, nd=2):
    """round(x), or None when x is missing or zero (zero means not computed)."""
    return round(x, nd) if x else None


def _key_metrics(f, cap, gr, dil, pv, pi, ex, fv_disagreement, ctype=CompanyType.STANDARD,
                 anc=None):
    dcf = (anc.dcf if anc else None) or {}
    di = dcf.get("inputs") or {}
    crp = getattr(f, "crp", None)
    return {"wacc_pct": round(cap.wacc * 100, 2),
            "company_type": ctype.value,
            "damodaran_industry": getattr(f, "damodaran_industry", None),
            "industry_basis": getattr(f, "industry_basis", None),
            "crp_pct": _pct(crp, 2) if crp else None,
            "crp_basis": getattr(f, "crp_basis", None) if crp else None,
            "dcf_growth_pct": _pct(di.get("g_high"), 1),
            "dcf_margin_target_pct": _pct(di.get("margin_target"), 1),
            "dcf_sales_to_capital": di.get("sales_to_capital"),
            "dcf_terminal_share_pct": dcf.get("terminal_share_pct"),
            "ke_pct": round(cap.ke * 100, 2),
            "kd_pct": _pct(cap.kd_pre, 2),                       # pre-tax
            "kd_after_pct": _pct(cap.kd_pre * (1 - cap.tax), 2) if cap.kd_pre else None,
            "roic_pct": _pct(cap.roic, 2),
            # what the card prints beside WACC and the spread was built from: I2
            # requires the same number
            "roic_adj_pct": _pct(cap.roic_scored, 2) if cap.rd else None,
            # before median damping, so the normalisation is inspectable
            "roic_measured_pct": _pct(cap.roic_used, 2) if cap.rd else None,
            "spread_pct": _pct(cap.spread, 2),
            # anything comparing two ROICs must first agree on this basis
            "roic_basis": ("roe_book_equity" if ctype is CompanyType.FINANCIAL
                           else "rd_capitalized+leases" if cap.rd else "raw+leases"),
            "incremental_roic_pct": _pct(ex.inc_roic, 1),
            "terminal_roic_pct": round(gr.roic_term * 100, 1),
            "terminal_beta": round(cap.beta_t, 2),
            "terminal_ke_pct": round(gr.ke_stable * 100, 2),
            "terminal_wacc_pct": round(cap.wacc_term * 100, 2),
            "growth_pct": round(gr.growth * 100, 1), "beta": round(cap.beta, 2),
            "beta_regression": round(f.beta, 2) if f.beta else None,
            "beta_source": ("bottom-up: " + (f.beta_industry or "industry"))
                           if f.beta_unlevered_industry else ("regression" if f.beta else "default 1.0"),
            # consensus growth drives the FV, realised growth drives Demand: both
            # published so a reader sees which story the price needs
            "growth_realised_pct": _pct(pv.g_realised, 1),
            "fv_peg_realised": _round_nonzero(pv.fv_peg_realised),
            "growth_fv_x": pv.growth_fv_x,
            "sbc_pct_of_rev": round(f.sbc / f.revenue * 100, 1) if (getattr(f, "sbc", None) and f.revenue) else None,
            "sbc_dilution_pct": round(dil.rate * 100, 2) if dil.rate else None,
            "dilution_source": dil.label,
            "forward_eps_diluted": _round_nonzero(dil.fwd_eps),
            "justified_pe": (pv.peg_detail or {}).get("fair_pe"),
            "erp_pct": round(config.ERP * 100, 2), "erp_as_of": config.ERP_AS_OF,
            "operating_leases": _round_nonzero(cap.lease, 0),
            "terminal_margin_pct": _pct(pi.tmargin, 1),
            "terminal_margin_anchored": pi.tm_anchored,
            # set when [low, high] must not be read as a range
            "fv_disagreement_x": fv_disagreement}


def _verdict(f, v):
    km = v.key_metrics
    # A valued pre-profit name states its worth, survival assumption and distress
    # floor, not just what the market has priced in.
    if v.anchor_method and v.anchor_method.startswith("Young-Company"):
        y = v.young_dcf or {}
        rd = v.reverse_dcf or {}
        up = (f"{((v.anchor_value - f.price) / f.price * 100):+.0f}% upside" if f.price else "")
        return (f"{v.recommendation} {v.stars} - Pre-profit forward DCF ${v.anchor_value} ({up}); "
                f"band ${v.range_low}-${v.range_high}, survival {y.get('p_survival')}, "
                f"distress ${y.get('distress_per_share')}. Market prices "
                f"~{rd.get('implied_cagr_pct')}% 10y CAGR. conf {f.confidence}")
    if v.anchor_method == "Terminal-Anchored Reverse DCF":
        rd = v.reverse_dcf or {}
        if rd.get("triggered"):
            # this branch also catches profitable companies with missing FV inputs
            lbl = "Pre-profit" if not (f.net_income and f.net_income > 0) else "No point FV (insufficient data)"
            # implied_cagr_pct is None when no growth rate justifies the price
            _icagr = rd.get("implied_cagr_pct")
            _act = rd.get("actual_1y_pct")
            _lead = (f"market prices ~{_icagr}% 10y CAGR"
                     if _icagr is not None
                     else "ไม่มีอัตราการเติบโตใดที่ทำให้ราคานี้สมเหตุผล (มูลค่าอิ่มตัวก่อนถึงราคา)")
            _cmp = f" vs actual {_act}%" if _act is not None else ""
            return (f"{v.recommendation} {v.stars} - {lbl}: {_lead}{_cmp} "
                    f"({rd.get('verdict')}). conf {f.confidence}")
        return (f"{v.recommendation or 'N/A'} {v.stars} - insufficient data for a point fair value. conf {f.confidence}").strip()
    fv = v.anchor_value
    if fv is None:
        return (f"{v.recommendation or 'N/A'} {v.stars} - no fair value computed. conf {f.confidence}").strip()
    up = (f"{((fv - f.price) / f.price * 100):+.0f}% upside" if f.price else "")
    band = f"range ${v.range_low}-${v.range_high}" if v.range_low else ""
    eq = f", EQ {v.eq_verdict}" if v.eq_verdict else ""
    return (f"{v.recommendation} {v.stars} - {v.anchor_method} ${fv} ({up}); {band}. "
            + (f"ROE {km.get('roic_pct')}% vs Ke {km.get('ke_pct')}% "
               if km.get("roic_basis") == "roe_book_equity" else
               f"ROIC {km.get('roic_adj_pct') or km.get('roic_pct')}% vs WACC {km.get('wacc_pct')}% ")
            + 
            f"(Ke {km.get('ke_pct')}%), growth {km.get('growth_pct')}%{eq}. conf {f.confidence}").strip()