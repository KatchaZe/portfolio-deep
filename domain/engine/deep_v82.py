"""
DEEP Framework v8.2 engine — implements the DeepEngine contract.
Free-data limits handled per skill invariant 17 (skip + flag, never fabricate).
"""
import decimal as _decimal
import datetime

import config
from domain import pead as pead_mod
from domain import trend as trend_mod
from . import young_dcf
from .contract import DeepEngine, Valuation

# --- constants (v8) ---------------------------------------------------------
# NOTE (assumptions feature): functions below read config.ERP / config.ERP_AS_OF
# at CALL time, so a store-backed manual override (dashboard "Assumptions" form,
# applied in store.load()) takes effect without a restart. The module-level
# snapshot is kept only for back-compat/reference.
ERP = config.ERP             # snapshot at import — do NOT use in calculations
ERP_AS_OF = config.ERP_AS_OF
ROIC_TERMINAL = 0.15
REVERSE_HORIZON = 10
GROWTH_CAP = 0.30
RD_LIFE = 5
DEFAULT_KD_SPREAD = 0.013

SYNTH_SPREAD = [
    (8.50, 0.0069), (6.50, 0.0085), (5.50, 0.0103), (4.25, 0.0124),
    (3.00, 0.0150), (2.50, 0.0185), (2.25, 0.0238), (2.00, 0.0307),
    (1.75, 0.0400), (1.50, 0.0510), (1.25, 0.0620), (0.80, 0.0840),
    (0.65, 0.1080), (0.20, 0.1400), (-1e9, 0.1900),
]

# Fallback terminal operating margins for PRE-PROFIT names only. Profitable names
# now DERIVE terminal margin from their own current SEC op margin — see terminal_margin().
TERMINAL_MARGIN = {
    "NVDA": 0.35, "MSFT": 0.40, "AVGO": 0.35, "TSM": 0.40, "GOOGL": 0.35,
    "ORCL": 0.35, "MELI": 0.20, "ABBV": 0.30, "TMDX": 0.20, "LLY": 0.30,
    "TSLA": 0.15, "NVO": 0.35,
}
TERMINAL_MARGIN_CAP = 0.40
TERMINAL_MARGIN_FLOOR = 0.05


def terminal_margin(ticker, operating_income, revenue):
    """Terminal operating margin for the reverse DCF, DATA-ANCHORED where possible:
    the company's own current SEC operating margin (clamped). Falls back to the table,
    then generic 25%, ONLY when there is no positive current margin (pre-profit).
    Returns (margin, source_label, anchored_bool).

    REV-20: the [5%, 40%] band is the LAST universal constant that still bound a
    company silently. It is a two-sided clamp, but it is not symmetric in effect: the
    40% ceiling binds real businesses (a 55%-margin software firm is held to 40%,
    which makes the reverse DCF demand more growth and the verdict read harsher),
    while the 5% floor flatters a barely-profitable one. Neither is wrong as a
    guard — extrapolating today's margin to perpetuity deserves restraint — but a
    clamp that changes the answer has to say so, like every other one here."""
    if operating_income is not None and revenue:
        cur = operating_income / revenue
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
    tm = TERMINAL_MARGIN.get(ticker)
    if tm is not None:
        return tm, f"terminal margin {tm*100:.0f}% (assumed table - pre-profit)", False
    return 0.25, "terminal margin 25% (generic assumption - pre-profit, no table)", False


def working_capital_change(f):
    """dWC = (AR + inventory - AP)_now - (AR + inventory - AP)_prior  (REV-18).

    Damodaran's reinvestment is capex + acquisitions + dWC - D&A; only the first,
    and (since REV-9) the second, were being counted. A growing firm funds its
    receivables and inventory out of the same cash that funds its plant, so leaving
    dWC out understates what growth costs — most for exactly the working-capital-
    heavy businesses (retail, distribution, hardware) where it matters.

    Built from the BALANCE SHEET, not from `IncreaseDecreaseInOperatingCapital`,
    because that cash-flow tag's sign convention varies between filers and a silent
    sign flip would turn a cash drain into a cash source. (AR + inventory - AP) has
    exactly one reading. Returns (dWC, label) or (None, why-not).

    P2-3: a PARTIAL delta is worse than none. The legs have opposite signs, so a filer
    that tags only AccountsPayable produces a NEGATIVE dWC — "working capital released
    cash" — purely because the offsetting receivables and inventory were not filed.
    That understates reinvestment and inflates FCFF, and it does so in the direction
    that flatters the company. Receivables is the leg every revenue-generating filer
    reports and the largest of the three, so it is required; inventory and payables
    refine the answer when present.
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
        return 0


def _clamp(x, lo=0.0, hi=5.0):
    return max(lo, min(hi, x))


def _add_flag(f, note):
    """Append a flag once. Replaces ~14 copies of the if-not-in-list-append idiom;
    a duplicated flag is harmless but a MISSED one is not, so this keeps every
    caller on the same shape."""
    if note and note not in f.flags:
        f.flags.append(note)


def _band(x, bands):
    for t, s in bands:
        if x >= t:
            return s
    return bands[-1][1]


def cost_of_equity(rf, beta):
    return rf + beta * config.ERP        # call-time read (assumptions override aware)


def _spread_from_coverage(cov):
    for lb, sp in SYNTH_SPREAD:
        if cov >= lb:
            return sp
    return SYNTH_SPREAD[-1][1]


TERMINAL_BETA_LO, TERMINAL_BETA_HI = 0.8, 1.2


def terminal_beta(beta):
    """Beta for the STABLE-GROWTH phase (P-K).

    Damodaran's stable-growth rules apply to risk as well as to returns: a company
    that has settled into perpetual ~risk-free growth cannot still be twice as
    risky as the whole market, and a defensive name cannot stay at beta 0.2 for
    ever either. His stated stable-growth band is 0.8-1.2, applied here in BOTH
    directions (NVDA 2.21 -> 1.2, REGN 0.24 -> 0.8).

    This closes the last gap in the terminal-phase story: ROIC fades to the
    industry, growth is capped at the risk-free rate, and now the discount rate
    fades too. Leaving beta un-faded was what made a 62%-ROIC company earn a
    stable-phase P/E of 8.3 — and the old PE floor of 12 was quietly papering
    over it."""
    if beta is None:
        return 1.0
    return min(TERMINAL_BETA_HI, max(TERMINAL_BETA_LO, beta))


def wacc_true(rf, beta, equity_mktcap, total_debt, cash, tax, interest_expense, operating_income,
              beta_override=None):
    """True WACC. Returns (wacc, ke, kd_pretax, note).
    `beta_override` recomputes Ke at a different beta while holding the capital
    structure and cost of debt fixed — used for the terminal-phase WACC."""
    ke = cost_of_equity(rf, beta_override if beta_override is not None else beta)
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

    P-B2: `ke` prices the high-growth years, `ke_st` capitalizes the perpetuity.
    They used to be the same number, which meant a stable-growth perpetuity was
    discounted at the company's CURRENT (high-growth) cost of equity for ever —
    the same inconsistency as freezing ROIC. The terminal cash flow is still
    discounted BACK across the high-growth years at `ke`, because those years
    really are that risky. Defaults to ke_st = ke for callers that don't care."""
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


# P-M: one band for both valuation paths (they clamp the SAME quantity — a
# justified P/E — and used to disagree: [8,30] for PEG vs [12,25] for the FVP
# exit multiple). The band is now a garbage catcher, not a value donor:
#   * the old FLOOR bound 11 of 21 holdings in the FVP path and gifted up to
#     +103% (RKLB 5.9 -> 12). A low justified P/E is the CORRECT answer for a
#     high-Ke, low-ROIC business, not a number to be overridden.
#   * the old ceilings (30 / 25) cut genuine answers on the highest-quality names.
# REVIEW-4: an earlier note here claimed ke_st - g_st >= 3% bounds the formula near
# 35 "by construction". That is FALSE — term2 carries ((1+g_h)/(1+ke))^n, which
# exceeds 1 whenever g_h > ke, so raw P/Es of 40-56 are reachable (g 25-30% with a
# high sector ROIC). 35 is therefore a real judgement cap, not a no-op, and it does
# bind (LLY raw 42.2). It is deliberately kept: an unbounded justified P/E on
# 5 years of extrapolated growth is a bigger risk than clipping the top decile.
# Whenever it binds the row is flagged as a boundary rather than an estimate.
PE_FLOOR, PE_CEIL = 5.0, 35.0

# D4: how far apart the two point methods may sit before the [low, high] pair stops
# being readable as a range. 2x is a judgement, chosen because it is roughly where a
# band stops narrowing a decision and starts merely containing it: a fair value
# somewhere between $41 and $299 rules nothing in or out.
FV_DISAGREE_RATIO = 2.0

# L3: how far the fair value may move between the CONSENSUS growth rate and the one the
# company actually delivered before the Price pillar stops claiming to know whether the
# stock is cheap. 1.5x is tighter than FV_DISAGREE_RATIO above because that one compares
# two METHODS on the same story, while this compares two STORIES — and the cheapness
# verdict then rests entirely on whose growth you believe, which is not a measurement.
GROWTH_FV_DISAGREE = 1.5


def fundamental_peg_price(g_high, g_stable, ke, roic_high, roic_stable, forward_eps, years=5,
                          ke_stable=None):
    # REVIEW-6: ZERO growth is a valid input, not a missing one. The old guard was
    # `g_high <= 0`, so once P-E started (correctly) reporting 0% for a shrinking
    # company, that company lost its PEG valuation entirely and fell through to a
    # trailing-EPS method — PFE swung from +37% to -60% on this alone. The two-stage
    # formula handles g=0 fine: payout_h becomes 1 (nothing needs reinvesting).
    if forward_eps is None or forward_eps <= 0 or g_high is None or g_high < 0:
        return None, None
    pe = two_stage_pe(g_high, years, g_stable, ke, roic_high, roic_stable, ke_st=ke_stable)
    if pe is None or pe <= 0:
        return None, None
    pe_clamped = _clamp(pe, PE_FLOOR, PE_CEIL)
    detail = {"fair_pe": round(pe_clamped, 2), "fair_pe_raw": round(pe, 2),
              # PEG is undefined at zero growth — report None rather than dividing by 0
              "fundamental_peg": round(pe_clamped / (g_high * 100), 3) if g_high > 0 else None,
              "pe_clamped": pe_clamped != pe}
    return pe_clamped * forward_eps, detail


def fundamental_growth(capex, dep_amort, nopat, roic_now, acquisitions=0.0):
    """Damodaran's fundamental growth (P-F): g = reinvestment rate x ROIC — growth
    is not free, it has to be bought with reinvestment.

    Used as a CROSS-CHECK, not as the driver: (capex - D&A)/NOPAT sees only
    physical reinvestment, so it badly understates asset-light R&D spenders
    (NVDA prints 0.0% while growing 65%), and capex is missing for several
    filers. Returns None when it cannot be computed honestly."""
    if capex is None or dep_amort is None or not nopat or nopat <= 0:
        return None
    if roic_now is None or roic_now <= 0:
        return None
    # REV-9: acquisitions are reinvestment too (Damodaran, S5) — growth bought with
    # M&A is not free growth. Defaults to 0 so existing callers are unchanged.
    return _clamp((capex + (acquisitions or 0.0) - dep_amort) / nopat, 0.0, 0.8) * roic_now


def sustainable_growth_cap(roic_now, roic_fallback=None):
    """Growth ceiling for the high-growth stage (P-H).

    Sustainable growth = reinvestment rate x ROIC, and reinvestment rate cannot
    exceed 100% of earnings without raising outside capital. So g <= ROIC.

    Why this matters: the payout term is max(0, 1 - g/ROIC). When g > ROIC the
    required reinvestment exceeds earnings, and the max(0, .) silently floors the
    shortfall at zero instead of charging for it — MELI at g=30% with ROIC=13.7%
    needs to reinvest 219% of earnings, yet the model paid no dilution and still
    scaled the terminal earnings base by (1+g)^5 = 3.7x. Growth was free.

    REV-2: when ROIC is unknown or non-positive the old code returned GROWTH_CAP —
    the FULL 30%. That is the worst possible default: exactly the firms we cannot
    measure got the most generous growth. And it is NOT true that such names always
    fall through to the reverse DCF: a company whose invested capital is negative
    (cash > equity + debt — routine for cash-rich software and biotech) has
    positive NOPAT, positive FCFF, ROIC = None, and lands squarely on this path.
    `roic_fallback` (the terminal ROIC — already floored at WACC and capped by the
    industry) is now used instead, so an unmeasurable firm is held to the average
    company's return rather than handed the ceiling."""
    if roic_now is None or roic_now <= 0:
        if roic_fallback and roic_fallback > 0:
            return min(GROWTH_CAP, roic_fallback)
        return GROWTH_CAP
    return min(GROWTH_CAP, roic_now)


# REV-1 / P2-1: annual share-count creep charged into the forward EPS.
#
# The DEEP contract is explicit that SBC is a real expense handled through DILUTION,
# not through a score penalty. REV-1 implemented that with the only measure available
# at the time — SBC$ / market cap — and that proxy turned out to be wrong twice over:
#
#   * it measures GROSS grants, while what divides earnings is the share count NET of
#     buybacks. On the committed fixtures it disagrees in SIGN with reality for MSFT
#     (proxy +0.39%/yr vs actual -0.05%) and ABBV (+0.28% vs 0.00%) — both grant
#     heavily and retire more than they issue;
#   * it is inversely proportional to market cap, so the SAME grant is charged double
#     the dilution after the stock halves. That penalises precisely the beaten-down
#     names a value screen exists to surface.
#
# The real series was in the companyfacts JSON all along (10-18 annual points per US
# filer). It is now primary; the proxy survives only for filers that do not report a
# share count at all (IFRS, e.g. NVO).
SBC_DILUTION_CAP = 0.06
SBC_DILUTION_FLAG_AT = 0.015
# Dilution is measured over up to this many years so one buyback-heavy or
# acquisition-heavy year cannot set the whole forward assumption.
DILUTION_LOOKBACK_YEARS = 3


def share_count_growth(shares_annuals, years=DILUTION_LOOKBACK_YEARS):
    """Actual annual growth in the diluted share count (P2-1).

    Negative is a real and common answer — a company retiring more stock than it
    grants genuinely shrinks its share base, and charging it dilution would be
    fiction. Returns (rate, label) or (None, None) when the history is too short."""
    s = [x for x in (shares_annuals or []) if isinstance(x, (int, float)) and x > 0]
    if len(s) < 2:
        return None, None
    n = min(years, len(s) - 1)
    rate = (s[0] / s[n]) ** (1.0 / n) - 1
    return rate, f"actual diluted share count, {n}y CAGR"


def sbc_dilution_rate(sbc, market_cap):
    """FALLBACK proxy for filers with no share-count series (IFRS). See above for why
    this is a last resort rather than the measurement."""
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
    """Stable-phase ROIC (P-D). Damodaran's three rules for the terminal phase:
      1. competition erodes excess returns -> ROIC must FADE toward the cost of
         capital; nobody earns 40-60% forever,
      2. a firm with a DURABLE moat may keep part of its spread, but a modest part,
      3. the cost of capital is also the FLOOR — a business earning below it gets
         restructured, acquired or liquidated; it does not burn capital forever.
    So: keep half of today's spread, never above the industry ceiling, never
    above what the firm actually earns today (no free upgrade), never below the
    cost of capital.

    `sector_cap` is Damodaran's own industry ROIC (sources/damodaran.py); when it
    is unavailable we fall back to ROIC_TERMINAL = 15%, which is close to his
    market-wide lease & R&D adjusted figure (15.07%, Jan 2026) — i.e. "converge
    to the average company" when we have no industry view.

    Replaces the old hardcoded ROIC_TERMINAL=0.15 for every company, which handed
    a 5%-ROIC business a 15% perpetuity and made the model systematically rate
    LOW-quality firms as the cheapest (PFE +151%, REGN +110%, ELV +29%)."""
    cap = sector_cap if (sector_cap and sector_cap > 0) else ROIC_TERMINAL
    if roic_now is None or wacc_val is None or wacc_val <= 0:
        # REVIEW-2: with no ROIC of its own there is nothing to fade FROM, so the
        # industry number would become a gift — Tobacco 77.7%, Retail Building
        # Supply 39.3%. Fall back to the market-average default and let the
        # industry figure only pull it DOWN.
        # REV-17: rule 3 (the cost of capital is the FLOOR) was skipped on this
        # branch — it only applied when ROIC was known. A high-WACC firm we cannot
        # measure was handed a perpetuity return BELOW its own cost of capital,
        # i.e. permanent value destruction by default. That was survivable while
        # this branch only fed the perpetuity; REV-2 now routes the growth cap and
        # the high-growth ROIC through it too, so the floor has to hold everywhere.
        base = min(cap, ROIC_TERMINAL)
        return base if wacc_val is None or wacc_val <= 0 else max(wacc_val, base)
    half = wacc_val + 0.5 * max(0.0, roic_now - wacc_val)
    return max(wacc_val, min(roic_now, cap, half))


def stable_exit_pe(g_st, ke, roic_st):
    """Justified STABLE-period P/E (Damodaran, Gordon form):
    PE = payout_st * (1+g_st) / (Ke - g_st), payout_st = 1 - g_st/ROIC_st.
    Used as the exit multiple in the Future Value Projection — replaces the old
    'exit PE = growth x 100' PEG=1 heuristic (Philosophy-2026 alignment: exit
    multiples must come from stable-period fundamentals, S5/S19)."""
    if ke is None or g_st is None or ke <= g_st:
        return None
    payout = max(0.0, 1 - g_st / roic_st) if roic_st else 0.0
    return payout * (1 + g_st) / (ke - g_st)


def future_value_projection(eps0, growth, ke, exit_pe):
    if eps0 is None or eps0 <= 0 or exit_pe is None:
        return None
    pe = _clamp(exit_pe, PE_FLOOR, PE_CEIL)     # P-M: same band as the PEG path
    return (eps0 * (1 + growth) ** 5 * pe) / (1 + ke) ** 5


def reverse_dcf(price, shares, revenue, rev_1y, total_debt, cash, wacc_val, g, tax, margin,
                wacc_term=None, roic_term=None, market_cap=None, margin_now=None,
                actual_growth=None):
    """Full-path reverse DCF (Philosophy-2026 fix): solve the revenue CAGR x such
    that PV(interim FCFF years 1..H) + PV(terminal value) = Enterprise Value.
    The old closed form assumed ALL of EV compounds into the year-H terminal value
    (no credit for interim cash flows) which OVERSTATED the implied CAGR — verdicts
    skewed toward 'Aggressive/Exceptional'. Damodaran reverse DCF discounts the
    whole FCFF path (S19/S21: what growth is the market pricing in?).

    P-L, two terminal-phase fixes — both matter a lot here because the terminal
    value is 90-97% of total PV in this model, so it is barely a 10-year DCF at all:
      * `wacc_term` capitalizes the perpetuity (Ke rebuilt at a stable-growth beta,
        same capital structure and Kd). Using the current high-growth WACC for a
        PERPETUITY shrank TV for high-beta names, so the solver had to invent extra
        growth to reach EV and the verdict came out too harsh — and too lenient for
        low-beta names. HIMS (beta 2.34) reads 22.8% implied CAGR the old way, 10.9%
        this way; LLY (beta 0.51) moves the other way, 13.7% -> 16.7%.
      * `roic_term` replaces a hardcoded ROIC_TERMINAL=15% in the reinvestment
        term — a leftover the terminal-ROIC work missed. Reinvestment is x/ROIC, so
        assuming 15% for a 23%-ROIC filer overstated what growth costs it.
    Both fall back to the old behaviour when not supplied.

    Margin is held at the terminal margin for the whole path. Tested: ramping it
    from today's margin instead moves the answer by <0.6pp (HIMS 22.8% -> 23.3%),
    because the terminal value dominates — so the simplification is harmless. It is
    NOT, as previously documented, a conservative bound in either direction."""
    wacc_term = wacc_val if wacc_term is None else wacc_term
    roic_t = roic_term if (roic_term and roic_term > 0) else ROIC_TERMINAL
    if not (price and shares and revenue) or wacc_val <= g or wacc_term <= g:
        return {"triggered": False}
    # REV-25: use the SAME market cap the WACC weights were built from. The engine
    # already prefers the REPORTED figure (T4) precisely because for a depositary
    # listing price x SEC-share-count is in the wrong unit — but this function then
    # recomputed the product anyway, so the reverse DCF could be valuing a different
    # firm from the one the discount rate was priced for. `_resolve_shares` only
    # intervenes past a 20% gap, so up to 20% of disagreement passed silently.
    mcap = market_cap if (market_cap and market_cap > 0) else price * shares
    ev = mcap + (total_debt or 0) - (cash or 0)

    def margin_at(t, m):
        """Operating margin in year t (REV-24).

        The old model held the TERMINAL margin flat from year 1. The docstring
        defended that with a test showing <0.6pp of movement — but that test was run
        on a company whose current margin was already close to its terminal one. For
        a PRE-PROFIT filer the two are nowhere near each other: the terminal margin is
        an assumed 25% while the company is losing money TODAY, so the model credited
        it with a 25% margin next year and every year after. That inflates PV, so the
        solver needs less growth to reach EV, and the verdict comes out too kind on
        exactly the names whose verdict is hardest to get right.

        Now ramps linearly from today's margin to the terminal one, which is also what
        young_dcf.going_concern does — the two models no longer disagree about the same
        company's next five years. `margin_now=None` keeps the old flat behaviour for
        callers that do not supply it (skill-parity path)."""
        if margin_now is None:
            return m
        return margin_now + (m - margin_now) * (t / REVERSE_HORIZON)

    def pv_at(x, m):
        """PV of the FCFF path at revenue CAGR x with terminal operating margin m."""
        if m <= 0:
            return None
        # REV-3 / REV-28: reinvestment is x/ROIC, UNCAPPED on the upside.
        #
        # The original cap was 0.9, which charged every CAGR from 0.9xROIC to 100% an
        # identical 90% of earnings — growth was free above the cap, the same defect
        # P-H fixed on the PEG path. REV-3 raised it to 1.0, which was still wrong and
        # in a more subtle way: at exactly x = ROIC the interim FCFF becomes ZERO and
        # stays there, so above that point the model is 100% terminal value, FCFF
        # still does not respond to growth, and a margin ramp has nothing to act on.
        # The cap did not remove the flatness, it moved it.
        #
        # There is no honest ceiling here. Growth beyond what ROIC can fund is paid for
        # with OUTSIDE capital, which is a real cash outflow: FCFF goes negative, and
        # Damodaran's young-company models carry exactly that. Letting the ratio run
        # free restores the property the solver needs — FCFF strictly decreasing in x —
        # and makes the price pay for the growth it is implying.
        # D5 — INVESTIGATED AND DELIBERATELY LEFT AS IS. `x` is the REVENUE CAGR, and
        # Damodaran writes the reinvestment rate as g/ROIC with g the growth in
        # EARNINGS, so this looked like the wrong quantity. Deriving it says otherwise.
        #
        # Reinvestment is dCapital/NOPAT. Under a constant sales-to-capital ratio —
        # Damodaran's own assumption for a growth firm, and the one young_dcf uses:
        #
        #     Capital  = Revenue / (S/C)          dCapital = Capital * x
        #     ROIC     = NOPAT / Capital
        #     reinvest = Capital*x / (ROIC*Capital) = x / ROIC
        #
        # so the revenue CAGR is EXACTLY right, not an approximation of the earnings one.
        #
        # It matters most where the two diverge — a margin ramp. Charging NOPAT growth
        # there bills the firm for growth it never had to fund: revenue +20% with the
        # margin going 10% -> 25% is 200% NOPAT growth, but the capital needed is still
        # only dRevenue/(S/C). Implemented and measured, that reading pushed HIMS from
        # 16.7% to 36.2% implied CAGR and sent ELV, RKLB and AXON out of band entirely —
        # the solver could not reach EV at any growth rate because FCFF had been charged
        # for margin recovery. Reverted.
        #
        # Margin expansion costs no capital. Revenue growth is what costs capital.
        reinvest = max(0.0, x / roic_t) if x > 0 else 0.0
        reinvest_t = min(0.8, g / roic_t)
        pv = 0.0
        rev_t = revenue
        for t in range(1, REVERSE_HORIZON + 1):
            rev_t *= (1 + x)
            m_t = margin_at(t, m)
            # a loss-making year produces negative FCFF; that is the honest number and
            # the solver needs to see it, so no floor here
            fcff = rev_t * m_t * (1 - tax if m_t > 0 else 1.0) * (1 - reinvest)
            pv += fcff / (1 + wacc_val) ** t
        fcff_next = rev_t * (1 + g) * m * (1 - tax) * (1 - reinvest_t)
        tv = fcff_next / (wacc_term - g)          # perpetuity at the STABLE rate...
        return pv + tv / (1 + wacc_val) ** REVERSE_HORIZON   # ...discounted back at the risky one

    def implied(m):
        """Lowest revenue CAGR x in [-50%, +100%] whose FCFF path is worth EV.

        REV-28: this used to assume pv_at was MONOTONIC in x and bisect the whole
        band. That held only because reinvestment was capped — once growth is properly
        paid for, pv_at is UNIMODAL: value rises with growth while the return on that
        growth exceeds its cost, peaks, then falls as the reinvestment bill outruns the
        profit. A firm earning 10% on capital against a 9% WACC creates almost nothing
        by growing, which is Damodaran's whole point about growth being worthless
        without a spread — and with the old bracket every such company came back
        "out of band" no matter what it was priced at, because pv_at(+100%) is deeply
        negative for them.

        So: locate the peak first, then bisect the RISING branch. The rising-branch
        root is the meaningful one — the LEAST growth that justifies today's price.
        If even the peak falls short of EV, the price genuinely cannot be justified by
        any growth path and that is reported rather than hidden."""
        if m <= 0 or ev <= 0:
            return None, "no margin / no enterprise value"
        lo, hi = -0.50, 1.00
        plo = pv_at(lo, m)
        if plo is None:
            return None, "path not computable"
        if plo >= ev:
            return lo, None         # even permanent decline is worth more than the price
        n = 40
        vals = [(v, x) for v, x in ((pv_at(lo + (hi - lo) * i / n, m),
                                     lo + (hi - lo) * i / n) for i in range(n + 1))
                if v is not None]
        if not vals:
            return None, "path not computable"
        peak_v, peak_x = max(vals)
        if peak_v < ev:
            # Two very different failures used to share one message. If the peak sits
            # INSIDE the band, no growth rate justifies the price — typical of a firm
            # whose ROIC barely exceeds its WACC, where growth adds almost nothing and
            # the reinvestment bill eventually outruns the profit. If the peak is at
            # the boundary, the model simply needs more than 100%/yr.
            if peak_x < hi - 1e-9:
                return None, (f"no growth rate justifies this price - value peaks at "
                              f"{peak_x*100:.0f}%/yr and the spread is too thin to add more")
            return None, "price implies more than 100%/yr revenue growth"
        a, b = lo, peak_x
        for _ in range(60):
            mid = (a + b) / 2
            if (pv_at(mid, m) or 0) < ev:
                a = mid
            else:
                b = mid
        return (a + b) / 2, None

    base, why = implied(margin)
    # P3-1: `actual_1y_pct` must be a LIKE-FOR-LIKE fiscal-year growth rate.
    #
    # It used to be computed here as `revenue / rev_1y - 1`, and those two numbers do
    # not share a time base: `revenue` is the TTM (sec_edgar builds it by summing
    # quarters) while `rev_1y` is `revenue_annuals[1]`, the fiscal year BEFORE the last
    # completed one. For any filer whose TTM has moved past its last fiscal year that
    # spans ~21 months of growth and reports it as one year:
    #
    #     MSFT  TTM $318.3B / FY2024 $245.1B = +29.8%   reported FY2025 growth: +15%
    #     AVGO  TTM $75.5B  / FY2023 $51.6B  = +46.3%   reported: +23.9%
    #     NVDA  +94.3%  vs reported +65.5%   CELH +119.0% vs reported +85.5%
    #
    # The error is one-directional (TTM >= last FY, so the "actual" is always too high)
    # and this number is the DENOMINATOR of the acceleration ratio that picks the
    # verdict — Plausible / Ambitious / Aggressive / Exceptional. An inflated actual
    # makes the market's implied growth look easier to reach than it is, so the verdict
    # came out systematically too FORGIVING, and the card printed a growth rate the
    # company never announced right next to "ทำได้จริงล่าสุด".
    #
    # The engine already computes the honest figure — `rev_growth_yoy`, FY over FY,
    # the same one the Demand pillar scores — so it is passed in rather than
    # recomputed from two different clocks. The old expression survives only as a
    # fallback for direct callers (skill-parity tests) that supply no growth.
    a1 = actual_growth if actual_growth is not None else (
        (revenue / rev_1y - 1) if rev_1y else None)
    accel = (base / a1) if (base and a1 and a1 > 0) else None
    if a1 is not None and a1 <= 0:
        verdict = "Cannot benchmark - shrinking"
    elif accel is None:
        verdict = "Unknown"
    elif accel < 1.5:
        verdict = "Plausible"
    elif accel < 3:
        verdict = "Ambitious"
    elif accel < 5:
        verdict = "Aggressive"
    else:
        verdict = "Exceptional"
    # REV-5: "Unknown" used to cover two completely different situations — no prior
    # revenue to compare against, and a price the model cannot reach at ALL inside
    # [-50%, +100%] CAGR. The second one is the more interesting verdict of the two
    # and it was being swallowed. The skill script already reports it; the app did not.
    out_of_band = base is None
    if out_of_band:
        verdict = why or "Beyond model band"
    # REV-3b: growth the business cannot pay for out of its own earnings. Reported as
    # a RATIO, not a boolean tripwire — implied CAGR slightly above terminal ROIC is
    # the norm for any growth name and flagging all of them is just noise. The number
    # that matters is how far past self-funding the price sits.
    fund_ratio = None if (base is None or not roic_t) else round(base / roic_t, 2)
    # REV-6: `implied()` runs 60 bisection steps x 10 years; the old dict
    # comprehension called it TWICE per scenario (once in the test, once in the
    # value) — six full solves where three would do.
    sens = {}
    for k, dm in (("bear", -0.05), ("base", 0.0), ("bull", 0.05)):
        x, _ = implied(margin + dm)
        sens[k] = round(x * 100, 1) if x else None
    return {"triggered": True, "enterprise_value": round(ev, 0),
            "implied_cagr_pct": round(base * 100, 1) if base else None,
            "actual_1y_pct": round(a1 * 100, 1) if a1 is not None else None,
            "acceleration": round(accel, 2) if accel else None, "verdict": verdict,
            "out_of_band": out_of_band,
            "funding_ratio": fund_ratio, "roic_terminal_pct": round(roic_t * 100, 1),
            "sensitivity": sens}


def earnings_quality(net_income, cfo, total_assets, sbc, revenue,
                     receivables=None, receivables_prior=None, revenue_prior=None,
                     revenue_growth=None):
    """REV-19: adds the AR-vs-revenue check that `receivables` was being fetched for.

    `FinancialFacts.receivables` has carried the comment "AR vs revenue
    (channel-stuffing check)" since v8.2 and the check was never written — the field
    was pulled from SEC on every refresh and read by nothing. The signal itself is
    standard: when receivables grow much faster than revenue, sales are being booked
    that have not turned into cash, either because collection is deteriorating or
    because revenue is being pulled forward. It needs the PRIOR AR, which is why it
    could not be written before (REV-19 added it to the extractor)."""
    if cfo is None or net_income is None:
        return None, [], None
    flags = []
    cc = (cfo / net_income) if net_income > 0 else None
    if cc is not None and cc < 0.8:
        flags.append("low cash conversion <0.8")
    if total_assets:
        acc = (net_income - cfo) / total_assets
        if acc > 0.05:
            flags.append("high accruals >5% of assets")
    if sbc is not None and revenue:
        if sbc / revenue > 0.10:
            flags.append("SBC >10% of revenue")
    _rev_g = revenue_growth if revenue_growth is not None else (
        (revenue / revenue_prior - 1) if (revenue and revenue_prior) else None)
    if receivables and receivables_prior and _rev_g is not None:
        ar_g = receivables / receivables_prior - 1
        # P3-1: prefer the caller's like-for-like fiscal-year growth. The fallback
        # (revenue / revenue_prior) mixes a TTM numerator with a fiscal-year
        # denominator and overstates growth, which SUPPRESSES this flag.
        rev_g = _rev_g
        # only meaningful when AR is actually growing AND outpacing sales by a wide
        # margin; a shrinking book or a small gap is ordinary business noise.
        if ar_g > 0.10 and ar_g > rev_g + 0.15:
            flags.append(f"receivables +{ar_g*100:.0f}% vs revenue +{rev_g*100:.0f}% "
                         f"(sales not turning into cash)")
    verdict = "CLEAN" if not flags else "REVIEW" if len(flags) <= 2 else "LOW"
    return verdict, flags, cc


def fade_ratio(near, far):
    """Consensus growth fade: how much of the near-term growth rate survives to the
    far year. Returns (ratio, why-not).

    A2: the ratio only carries meaning when the NEAR rate is positive. With a negative
    denominator it inverts, and both readings come out wrong in the same direction:

        near -10% -> far  +5%   ratio -0.50  ->  scored as a severe fade  (-0.5)
        near -10% -> far  -5%   ratio +0.50  ->  scored as no fade at all ( 0.0)

    The first company is turning from decline to growth — the best trajectory on the
    list — and it was the one being penalised; the second is still shrinking and got
    away clean. PFE is the live case: consensus -4.1% then +0.75%, penalised -0.5.

    A company that is already shrinking is not "fading", it is declining, and the base
    band has already scored that. So: no near-term growth, no fade reading. A positive
    near rate collapsing to a negative far one still produces a negative ratio, which
    the band correctly treats as the worst kind of fade — that case was right already."""
    # "no consensus path filed" and "consensus says the company is shrinking" are
    # different answers and must not share a message — that is the REV-5 lesson.
    if near is None or far is None:
        return None, "no consensus path"
    if near <= 0:
        return None, ("consensus near-term growth ไม่เป็นบวก — fade ไม่มีความหมาย "
                      "(การหดตัวถูกให้คะแนนที่ฐานแล้ว)")
    return far / near, None


def acquisition_intensity(acquisitions_net, revenue):
    """Cash SPENT on acquisitions as a share of revenue — the organic-growth penalty.

    A2: this used `abs(acquisitions_net)`, so a DIVESTITURE looked exactly like a
    purchase. `PaymentsToAcquireBusinessesNetOfCashAcquired` is a payment, so a
    negative value means net cash came IN — the company sold a business. Selling one is
    not buying growth, and Damodaran's organic-growth question is only about growth
    that was purchased. ELV is the live case: -$39M net, penalised as if it had spent
    $39M acquiring.

    Returns (intensity, label). Zero for a net divestiture — not None, because we DO
    know the answer: nothing was bought."""
    if acquisitions_net is None or not revenue:
        return None, None
    if acquisitions_net <= 0:
        return 0.0, "net divestiture — ไม่ได้ซื้อการเติบโต"
    return acquisitions_net / revenue, None


SUSTAINED_RATIO, SPIKE_RATIO, DECEL_RATIO = 0.80, 0.50, 1.30


def growth_consistency(g_1y, g_3y):
    """B5: is this year's growth the pace the company actually runs at?

    The Demand band was read off ONE year, so a single 40% year scored 5.0/5 whether
    the company had compounded at 38% for a decade or at 8%. Damodaran's objection to
    a one-year growth rate is the same as his objection to a one-year margin: it is a
    draw from a distribution, not the distribution.

    Compares the latest fiscal year against the 3-year CAGR ending the same year:
      3y between 80% and 130% of 1y -> the pace is genuinely sustained     +0.25
      3y <  50% of 1y               -> latest year is ~2x the run-rate     -0.5
      3y > 130% of 1y               -> DECELERATING                         0.0
    Asymmetric on purpose. Sustained growth is the expected case and barely deserves a
    bonus; a year at twice the multi-year pace is the one that misleads a band built to
    reward 35%+, so that is where the weight goes.

    The upper bound exists because the first version did not have one, and a single
    threshold of "3y >= 80% of 1y" quietly swept in the opposite situation: NVO's 3-year
    CAGR is 20% against 6% last year — growth collapsing, not compounding — and it
    scored +0.25 labelled "โตต่อเนื่องจริง". Deceleration earns nothing here; the base
    band has already scored the slower year, and paying a consistency bonus on top of it
    would reward the slowdown twice.

    Only speaks when 1-year growth is positive — a decline is already scored at the
    base, and dividing by a negative denominator inverts the comparison (A2's lesson).
    Returns (adjustment, note)."""
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
    """T5-E: does the free cash flow actually compound, or was last year a good year?

    Every other E_exec input is a one- or two-period snapshot: the earnings-quality
    verdict reads THIS year's cash conversion, the margin trend compares two fiscal
    years, incremental ROIC one. So a company whose FCF has fallen four years running
    scores identically to one whose FCF is compounding, provided the latest ratios
    match. Damodaran's execution question is whether management turns the business into
    owner cash REPEATEDLY, which needs more than one observation.

    Deliberately narrow — +/-0.5, the same weight as the margin trend, and it needs
    at least three consecutive filed years before it says anything. It reads the
    already-built trend strip rather than recomputing FCF, so the card and the score
    can never disagree about what the cash flow did.

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
    """B6: does management deliver what it told the market it would?

    The v7.1 framework names "earnings beats consistency" as the BASE of the Execution
    pillar. The data has been fetched, stored and drawn as circles on the card since
    v8.2 — and scored nowhere. This adds it as an adjustment rather than a base,
    because the earnings-quality verdict is the better base: it asks whether the
    earnings are real, which has to be settled before whether they beat a forecast.

    ASYMMETRIC, and the asymmetry is the point. Beating consensus is partly a guidance
    game — a management team that guides conservatively will beat every quarter without
    the business doing anything remarkable — so a beat streak is weak evidence and gets
    +0.25. Missing your own guided number repeatedly is not a game anyone plays on
    purpose: it is direct evidence that management cannot forecast its own business,
    which is exactly what the Execution pillar exists to measure. That gets -0.5.

    'meet' is excluded from the denominator: it is the intended outcome, not evidence
    either way. Needs 3+ graded quarters. Order-independent (see pead.chronological)."""
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
    """Per-year raw ROIC map from the T5 series, or {} when the inputs are absent.
    Isolated so both the incremental-ROIC leg (A4) and the level check (A3) read the
    same map and can never end up on different bases."""
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
    """A3: damp a one-year ROIC toward its own multi-year median before scoring it.

    The Economics pillar carries 30% and its base band was read off a SINGLE year's
    ROIC. One year is not a moat. ABBV's raw return went 13.4 -> 15.5 -> 13.7 -> 8.6
    -> 15.7% — a 7.1pp range on the same business — and the band it lands in depends
    entirely on which year you ask about (latest 15.7% scores 3.0, median 13.7% scores
    2.0). Damodaran normalizes earnings for exactly this reason.

    The trap here is basis. The T5 series is RAW ROIC: no R&D capitalization, no
    capitalized leases. `roic_used` has both. Swapping one for the other would be D3
    all over again, on the same pillar, three weeks later. So the series is used only
    for its SHAPE — the ratio median/latest — which is applied to the adjusted level.
    Whatever the R&D and lease adjustments do to the level, they do to both terms and
    largely cancel in the ratio.

    ONE-SIDED, and this was learned the hard way. Median normalization is the right
    treatment for CYCLICAL noise and the wrong one for a monotone TREND, and the ratio
    cannot tell them apart. NVO's raw series runs 53.5 -> 61.2 -> 68.0 -> 44.0 -> 33.8:
    the median sits far above the latest year not because 2025 was an unlucky trough
    but because returns have been falling for three straight years. Normalizing "up"
    toward that median would hand a company a better score for a decline that is real —
    and the ROIC-trend adjustment is already penalising it, so the two would fight.

    So the factor is applied only when it makes the score HARSHER (< 1). A latest year
    sitting above its own history is a peak that should not be extrapolated; a latest
    year below its history is ambiguous, and the trend leg already covers the case
    where it is genuine. Same principle as D1: a correction that can only ever lower.

    The factor is clamped to [0.6, 1.0] and needs 4+ consecutive years before it says
    anything. Returns (normalized_roic, note) or (roic_used, None)."""
    if roic_used is None or roic_used <= 0 or len(roic_map or {}) < NORMALIZE_MIN_YEARS:
        return roic_used, None
    # CONTIGUOUS years only. `sorted(...)[-5:]` reaches back across a filing gap:
    # AVGO has no long-term debt tag for FY2022-24, so the map holds 2018-2021 and 2025,
    # and the median of that set is a pre-VMware business being used to normalise a
    # post-VMware year. Same rule the trend strip already enforces for the same reason.
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
        # latest year is at or below its own median -> nothing to damp. Either it sits
        # on the median, or it is a decline the trend leg is already scoring.
        return roic_used, None
    factor = max(NORMALIZE_FACTOR_MIN, min(NORMALIZE_FACTOR_MAX, raw))
    out = roic_used * factor
    return out, (f"ROIC normalized {roic_used*100:.1f}% -> {out*100:.1f}% "
                 f"(ปีล่าสุด {latest:.1f}% vs median {len(vals)}y {med:.1f}% = {factor:.2f}x)")


CAP_MIN_YEARS = 4
SECTOR_BEAT, SECTOR_LAG = 1.5, 0.7
# B: every pillar now carries several adjustments, each sized on its own. Left unchecked
# they sum to more than the base band that is supposed to be the answer, and — worse —
# ADDING a factor silently re-weights the ones already there. The budget caps the NET
# adjustment per pillar.
#
# 2.0, not a rounder-looking 1.5, and the number was chosen from the existing rubric
# rather than picked: D could already stack organic (-1.0) + peer (-0.5) + fade (-0.5)
# = -2.0 before this round added anything. A tighter budget would have quietly changed
# answers the rubric already gave, which is precisely the kind of silent re-rating this
# guard exists to prevent. So it binds only on stacks that are NEW.
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
    """B8: how many of the last N fiscal years did the business out-earn its capital?

    Damodaran's moat has two dimensions — the SIZE of the excess return and how long it
    lasts — and only the size was scored. A firm clearing its cost of capital in five
    years out of five is a different proposition from one that cleared it once, even
    when this year's spread is identical, and it is precisely in the marginal cases
    (spread of a few points) that the two are indistinguishable on a single year.

    Today's WACC is applied to past ROICs rather than each year's own cost of capital,
    which is not what those years actually faced. Read it as "would this business have
    cleared TODAY's hurdle in each of the last five years" — a consistent question,
    which is what a count needs. Contiguous years only.

    Returns (adjustment, note). Deliberately small: +/-0.5 on a 30% pillar."""
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
    """B7: is this return good FOR ITS INDUSTRY?

    The Economics band is absolute — a 12% spread scores 4.0 whether the company is a
    software firm where peers earn 25% or a utility where they earn 6%. Damodaran's
    excess return is a relative idea: the question is whether this business earns more
    than the capital deployed in this industry typically does.

    `terminal_roic_sector` is his published normalized industry ROC, already fetched for
    the terminal-value ceiling and read by nothing else. Note the basis caveat carried
    in sources/damodaran.py: the industry figure is not lease- or R&D-adjusted, so it
    reads slightly generous against our own adjusted ROIC. That biases this adjustment
    toward being HARSH on us, which is the safe direction, and is why the thresholds are
    wide (1.5x and 0.7x) rather than tight. Returns (adjustment, note)."""
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

    D3: the second argument used to be `spread_prior`, and the delta was taken here
    as `spread - spread_prior`. That only works if both spreads were measured the same
    way, and they were not — the current side was a TTM and (when R&D capitalization
    applied) an ADJUSTED return, while the prior side was a fiscal year two periods
    back and always a RAW one. The caller now computes the like-for-like FY0-vs-FY1
    change in ROIC and passes it directly, so this function can no longer construct a
    delta out of two incompatible numbers. WACC cancels in the difference anyway, so a
    ROIC delta and a spread delta are the same quantity."""
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
    """A1: a SECOND, independent read on cheapness for the Price pillar.

    Price carries 30% of the composite and rested entirely on one number — the margin
    of safety against a single point fair value — plus a P/E percentile. If the fair
    value is wrong, 30% of the score is wrong with nothing to contradict it. The v7.1
    framework always specified an FCF-yield leg (20% of the Price score); it was never
    built, and the reverse DCF, which was, can only reach P when it is the anchor —
    and when it is the anchor, `anchor_value` is None and P is skipped entirely.

    The test is Damodaran's, not a multiple: does the business throw off cash at a rate
    that beats what the capital costs? FCF/EV against WACC compares like with like —
    a firm-level cash flow against a firm-level required return, on the same enterprise
    value the WACC weights were built from.

    Reads the free cash flow off the SAME trend strip the card draws, so the picture
    and the score cannot disagree. Bounded at +/-0.5, matching the other Price
    adjustment. Returns (adjustment, note) or (0.0, None) when it cannot be measured.

    Note this is deliberately harsh on a heavy investor: ORCL's FY2025 FCF is negative
    on the datacentre build-out, and on a cash basis the share price genuinely is not
    supported today. The Demand and Economics pillars are where that spending earns
    its credit back."""
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
        # A1: with no point fair value the pillar used to vanish entirely, which is
        # what `cap_reco_without_price` had to paper over. An FCF yield is a complete
        # cheapness opinion on its own — cash generated against what capital costs —
        # so a row that has one is no longer priceless. Scored from the neutral 3.0
        # because the margin-of-safety half of the test really is missing.
        if fcf_note:
            score = _clamp(NEUTRAL_SCORE + fcf_adj * 2)      # +/-1.0 around neutral
            notes.append(f"P base=no point fair value; scored on cash alone "
                         f"({fcf_note})={score}")
            return score
        notes.append("P base=skipped(no point fair value, no FCF either)")
        return None
    # REV-7: the note called this "margin-of-safety" but divided by PRICE, which is
    # upside (the return if price converges), not margin of safety. Damodaran defines
    # MoS = (Value - Price) / VALUE (appendix ก, S4/S12). Dividing by price distorts
    # the bands ASYMMETRICALLY: the +40% cut was really a 28.6% margin (too easy to
    # reach) while the -40% cut was a -66.7% one (almost unreachable), so the P pillar
    # — 30% of the composite — leaned generous at both ends.
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

    D1: renormalizing over the pillars that HAPPEN to be measurable makes a missing
    pillar inherit the weighted average of the others. On a strong row that average
    is near-maximal, so failing to measure something scores better than measuring it
    and finding it poor:

        D4.0 E_exec4.0 E_econ1.0 P4.0  -> 3.10   HOLD / Accumulate
        D4.0 E_exec4.0 E_econ None P4.0 -> 4.00  BUY

    `cap_reco_without_price` / `cap_reco_without_quality` stop the BUY, but not the
    notch below it (2.90 HOLD -> 4.14 capped -> HOLD / Accumulate).

    The fix is to bound the renormalized score by what it would be if the missing
    pillar scored NEUTRAL — the honest reading of "not measured" is the middle of the
    band, neither a reward nor a punishment. Taking the MIN of the two makes this
    strictly one-sided: it can only ever lower a row, never raise one, so a data gap
    can never help. A row with everything measured is untouched, because with no
    missing pillar the two expressions are identical.

    Measured on the committed portfolio: LLY 3.21->3.00, PFE 3.29->3.05,
    TSM 4.50->3.90, NVO 2.79->2.70; AXON and RKLB unchanged; no recommendation moved."""
    # 2026-08-11: weight the pillars AS REPORTED, not as they happen to sit in binary.
    # AXON's four pillars weight to 1.3250000000000002 and REGN's to 2.575 — both exactly
    # on the .005 rounding edge, where the last bits decide whether the card reads 1.32
    # or 1.33. Those bits move with any upstream change that alters no reported value at
    # all, so the replay showed a composite drifting with all four pillars identical:
    # a contradiction on its face, and an hour spent looking for a cause that was in the
    # float, not in the logic.
    #
    # Rounding first makes the composite a function of the numbers actually on the card
    # — the same property I2 demands of spread and ROIC — and removes a whole class of
    # replay noise. It cannot change a verdict: the shift is at most half a hundredth,
    # while the recommendation bands are a quarter point apart.
    if not any(scores.get(k) is not None for k in WEIGHTS):
        return None
    # DECIMAL, not binary. Pillars move in quarter points and the weights are 0.2/0.3,
    # so the weighted sum is always a multiple of 0.025 — it lands on a three-decimal
    # value ending in 5 systematically, which is exactly where float rounding is decided
    # by the last bits. AXON weighted to 1.3250000000000002 and REGN to 2.575, and the
    # SAME literals rounded to 1.33 under one interpreter and 1.32 under another. A score
    # on the card must not depend on which machine printed it.
    #
    # Exact decimal arithmetic on the pillars AS REPORTED, rounded half-up, makes the
    # composite something a reader can reproduce by hand from the four numbers beside it
    # — the property I2 already demands of ROIC and spread, and I13 pins here.
    dw = {k: _D(str(WEIGHTS[k])) for k in WEIGHTS}
    avail = {k: _D(str(round(scores[k], 2))) for k in WEIGHTS if scores.get(k) is not None}
    wsum = sum(dw[k] for k in avail)
    renorm = sum(avail[k] * dw[k] for k in avail) / wsum
    if wsum >= 1:                                # nothing missing -> nothing to bound
        return _q2(renorm)
    neutral = sum(avail.get(k, _D(str(NEUTRAL_SCORE))) * dw[k] for k in WEIGHTS)
    return _q2(min(renorm, neutral))


def cap_reco_without_price(reco, price_score):
    """REV-8: renormalizing over the AVAILABLE pillars quietly deletes the price
    test when there is no fair value. D4/E4/E4 with P=1 scores 3.10 (HOLD); the same
    three pillars with P missing score 4.00 — BUY. So a Yahoo outage on forward EPS
    UPGRADES the recommendation, and the more expensive the stock the bigger the
    upgrade. 'Price is what you pay, value is what you get' cannot survive the price
    half going missing, so a BUY now requires a price opinion."""
    if price_score is None and reco == "BUY":
        return "HOLD / Accumulate", ("recommendation capped at HOLD: no fair value, so the "
                                     "Price pillar (30%) was not tested")
    return reco, None


def cap_reco_without_quality(reco, econ_score):
    """P3-2: the same hole REV-8 closed on the Price pillar was still open on the
    QUALITY one — and E_econ carries the same 30% weight.

    `composite()` renormalizes over whichever pillars exist, so a company whose
    ROIC cannot be measured does not get a poor quality score, it gets NO quality
    score, and the remaining pillars are simply scaled up to fill the gap. The
    arithmetic is unforgiving about which way that cuts:

        D 4.0 / E_exec 4.0 / E_econ 1.0 / P 4.0  -> 3.10  HOLD / Accumulate
        D 4.0 / E_exec 4.0 / E_econ None / P 4.0 -> 4.00  BUY

    Identical company, identical evidence, and the ONLY difference is that we
    failed to measure the pillar — so the failure to measure is worth +0.90 and a
    ratings upgrade. ROIC goes missing for ordinary reasons (a filer that tags no
    operating-income subtotal, or invested capital <= 0 because the cash pile
    exceeds debt + equity — routine for cash-rich software and biotech), so this
    is not an exotic path.

    Damodaran's position is the reason this matters more here than anywhere else:
    growth only creates value when ROIC exceeds the cost of capital. An unmeasured
    spread means the one test that decides whether growth is worth anything was
    never run, and that cannot come out as the strongest possible verdict."""
    if econ_score is None and reco == "BUY":
        return "HOLD / Accumulate", ("recommendation capped at HOLD: ROIC unmeasurable, so the "
                                     "Economics pillar (30%) was not tested")
    return reco, None


def stars(c):
    half = round(c * 2) / 2
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
        f = facts
        notes = []
        beta = f.beta or 1.0
        tax = f.tax_rate
        if erp_months_old() > config.ERP_STALE_MONTHS:
            _erp_note = (f"ERP {config.ERP*100:.2f}% as-of {config.ERP_AS_OF} is {erp_months_old()}mo old "
                         f"- refresh (Assumptions form / config.ERP)")
            _add_flag(f, _erp_note)

        nopat = f.operating_income * (1 - tax) if f.operating_income is not None else None
        # P1-5 (S5): capitalize operating leases — lease liability is debt (Damodaran)
        lease = getattr(f, "operating_leases", None) or 0
        debt_eff = (f.total_debt or 0) + lease
        ic = debt_eff + (f.equity or 0) - (f.cash or 0)
        if lease:
            _ln = f"operating leases ${lease/1e9:.1f}B capitalized into debt+IC (S5)"
            _add_flag(f, _ln)
        roic = (nopat / ic) if (nopat is not None and ic and ic > 0) else None
        rd = rd_capitalize(f.rnd_annuals, f.operating_income, ic, tax)
        if rd:
            _, _, roic_adj = rd
            notes.append(f"R&D-capitalized ROIC {roic_adj*100:.1f}%")
        roic_used = rd[2] if rd else roic

        # T4: prefer the REPORTED market cap — one measurement instead of a product
        # of two, and for a depositary listing it is the only one in the right unit.
        # normalize._resolve_shares keeps shares consistent with it, so for ordinary
        # US listings the two agree to ~0.000% and this changes nothing.
        mcap = getattr(f, "market_cap", None) or (
            (f.price * f.shares_diluted) if (f.price and f.shares_diluted) else None)
        w, ke, kd_pre, wacc_note = wacc_true(rf, beta, mcap, debt_eff or None, f.cash, tax,
                                             f.interest_expense, f.operating_income)
        # S3: surface EVERY degraded-WACC path, not just the missing-interest one.
        # "no debt/weights -> WACC=Ke" used to pass silently, so TSM/NVO carried an
        # unweighted WACC with nothing on the card to say so.
        if ("default spread" in wacc_note and debt_eff) or "no debt/weights" in wacc_note:
            _n = "WACC: " + wacc_note
            _add_flag(f, _n)
        # P-K: same capital structure and Kd, Ke rebuilt at a stable-growth beta.
        beta_t = terminal_beta(beta)
        w_term, ke_term, _, _ = wacc_true(rf, beta, mcap, debt_eff or None, f.cash, tax,
                                          f.interest_expense, f.operating_income,
                                          beta_override=beta_t)
        if abs(beta_t - beta) > 0.4:
            _bn = (f"terminal beta {beta:.2f} -> {beta_t:.2f} (stable-phase risk fade) - "
                   f"FV leans on this assumption")
            _add_flag(f, _bn)
        spread = (roic_used - w) if roic_used is not None else None
        # A3: the Economics band is read off ONE year's ROIC. Damp it toward the
        # company's own multi-year median before scoring it.
        #
        # 2026-08-11: this used to leave `roic_used` untouched "for the card", which
        # meant the card printed one ROIC and scored a different one. Invariant I2 says
        # spread == the DISPLAYED ROIC - WACC, and it went red the moment a live refresh
        # finally gave A3 the 4+ years it needs: ABBV showed 16.09% against a WACC of
        # 5.71% and a moat of +8.32pp, three numbers that cannot all be true. A flag
        # explaining the discrepancy is not a substitute for the card adding up.
        #
        # So `roic_scored` is what the Economics pillar uses AND what the card reports.
        # The VALUATION deliberately keeps the measured figure below (terminal ROIC,
        # the g<=ROIC cap, the PEG's high-growth ROIC): normalisation is a prudence
        # device for scoring a moat, not a claim about the returns the business earns.
        _roic_norm, _norm_note = normalized_roic(roic_used, _trend_roic_map(f, tax))
        roic_scored = roic_used
        if _norm_note:
            roic_scored = _roic_norm
            spread = roic_scored - w
            _add_flag(f, _norm_note + " — คะแนน Economics และ ROIC บนการ์ดใช้ค่าที่ normalize แล้ว "
                                      "(การประเมินมูลค่ายังใช้ค่าที่วัดได้จริง)")

        # REV-9: Damodaran's reinvestment is capex + ACQUISITIONS + dWC - D&A. The
        # acquisition leg was already fetched (f.acquisitions_net, used only for the
        # organic-growth penalty) but was missing here, so a serial acquirer looked
        # as capital-light as an organic grower and its FCFF read too high.
        acq = abs(f.acquisitions_net) if f.acquisitions_net is not None else 0.0
        # REV-18: the dWC leg completes capex + acquisitions + dWC - D&A.
        dwc, dwc_label = working_capital_change(f)
        # REV-23: the ceiling here used to be 0.8, which made the FCFF gate below
        # INCAPABLE of firing: fcff = nopat x (1 - reinvest) was therefore always at
        # least 20% of NOPAT, so `fcf_pos` was true for every profitable company no
        # matter how much cash it was actually consuming. The gate exists precisely to
        # catch a firm reinvesting more than it earns; the clamp guaranteed it never
        # would. Floor at 0 (a divesting firm does not generate reinvestment income)
        # but no ceiling — over-100% reinvestment is a real state and must be visible.
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

        # P-D / P-J: terminal ROIC. MOVED UP (REV-2): the growth cap and the
        # high-growth ROIC both need a fallback for firms whose own ROIC is
        # unmeasurable, and the only defensible one is this already-faded,
        # WACC-floored, industry-capped number.
        _sec_cap = getattr(f, "terminal_roic_sector", None)
        # REVIEW-3: floor against the TERMINAL cost of capital, not today's.
        roic_term = terminal_roic(roic_used if (roic_used and roic_used > 0) else None,
                                  max(w, w_term), _sec_cap)
        if _sec_cap and abs(roic_term - _sec_cap) < 1e-9 and roic_term > w_term:
            _sn = f"terminal ROIC {roic_term*100:.1f}% set by industry ceiling (Damodaran ROC)"
            _add_flag(f, _sn)

        # P-H: g <= ROIC — you cannot grow faster than your return on capital
        # without raising outside capital, which this model does not charge for.
        g_cap = sustainable_growth_cap(roic_used, roic_fallback=roic_term)
        if not (roic_used and roic_used > 0):
            _rn = (f"ROIC unmeasurable (negative or zero invested capital) - high-growth "
                   f"ROIC and growth cap both fall back to the terminal {roic_term*100:.1f}%, "
                   f"not the old 15%/30% defaults")
            _add_flag(f, _rn)
        # REVIEW-1: `growth_lt or 0.08` treated a measured ZERO as missing, so every
        # shrinking company silently got the 8% default. P-E now floors the CAGR at 0
        # precisely so decline reads as no-growth — that has to survive to here.
        _g_lt = f.growth_lt if f.growth_lt is not None else 0.08
        growth_raw = _clamp(_g_lt, 0.0, GROWTH_CAP)
        growth = min(growth_raw, g_cap)
        if growth_raw > growth + 1e-9:
            _gn = (f"growth capped {growth_raw*100:.0f}% -> {growth*100:.0f}% "
                   f"(g <= ROIC: faster needs outside capital)")
            _add_flag(f, _gn)
        ann = f.revenue_annuals or []
        rev_1y = ann[1] if len(ann) > 1 else None
        rev_3y = ann[3] if len(ann) > 3 else None
        rev_growth_yoy = (ann[0] / rev_1y - 1) if (ann and rev_1y) else None
        actual_3y = ((ann[0] / rev_3y) ** (1 / 3) - 1) if (ann and rev_3y) else None

        eps0 = None
        # REV-10: `if f.net_income and ...` reads a genuine BREAK-EVEN year (0.0) as
        # missing data and silently falls through to eps_gaap — the same falsy-vs-None
        # confusion REVIEW-1 fixed for growth. Zero is a measurement, not a gap.
        if f.net_income is not None and f.shares_diluted:
            earn = min(f.net_income, nopat) if (nopat and f.net_income > nopat) else f.net_income
            eps0 = earn / f.shares_diluted
        elif f.eps_gaap is not None:
            eps0 = f.eps_gaap

        # REV-11: `ke_eff = max(ke, rf + 3.5%)` was a FLOOR with no matching ceiling —
        # it raised the discount rate for every beta below ~0.79 (REGN 5.57% -> 8.00%,
        # a 2.4pp haircut on fair value) and never restrained a beta of 2.2. It also
        # CONTRADICTS its own docstring: P-B2 says the high-growth years keep TODAY's
        # risk and only the perpetuity fades. P-K now fades risk properly, in both
        # directions, via terminal_beta — so this floor is a second, one-way fade
        # applied to the wrong phase. Removed. There is no numerical need for it
        # either: two_stage_pe handles ke <= g_h, and every division by (Ke - g) uses
        # the stable-phase rate, which keeps its floor below.
        ke_eff = ke
        # P-B2/P-K: the perpetuity is discounted at the STABLE-phase Ke, and the
        # stable growth cap keys off that rate (it is a stable-phase quantity).
        # The floor stays HERE because g_stable is derived from it — a numerical
        # guard on (Ke - g), not a risk view. With terminal_beta >= 0.8 it is almost
        # never the binding constraint anyway.
        ke_st_eff = max(ke_term, rf + 0.035)
        g_stable = min(rf, ke_st_eff - 0.03)
        # REV-2: an unmeasurable ROIC no longer collects a flat 15%.
        roic_high = roic_used if (roic_used and roic_used > 0) else roic_term
        # P-F: growth has to be bought with reinvestment — warn when the growth we
        # are paying for is far above what the firm's reinvestment can fund.
        g_fund = fundamental_growth(f.capex, f.dep_amort, nopat, roic_used, acquisitions=acq)
        if g_fund is not None and growth > 0.02 and growth > 2 * max(g_fund, 0.0):
            note = (f"growth {growth*100:.0f}% vs fundamental growth {g_fund*100:.1f}% "
                    f"(reinvest x ROIC) - unfunded by physical reinvestment"
                    + ("; asset-light? R&D not counted" if g_fund < 0.01 else ""))
            _add_flag(f, note)
        # REV-1 / P2-1 / P2-2: charge share-count dilution — but only where the EPS
        # being multiplied does NOT already carry the cost.
        #
        # The framework's rule is "SBC is a real expense (dilution factored, NO DOUBLE
        # COUNTING)". REV-1 honoured the first half and broke the second: it diluted
        # BOTH fair-value paths, and the two run on different kinds of EPS.
        #
        #   fv_peg -> forward_eps, the NTM *adjusted* consensus. Non-GAAP: SBC is added
        #             back, so the cost is NOT in the number. Dilution belongs here.
        #   fv_fvp -> eps0, built from f.net_income. GAAP: SBC has already been
        #             deducted as an expense. Charging dilution on top bills the same
        #             compensation twice — it was costing the FVP ~12% of value.
        #
        # When validate() substitutes a SEC-derived forward EPS (consensus rejected or
        # absent) that number is GAAP too, so it must not be diluted either.
        dil_rate, dil_label = dilution_rate(f, mcap)
        _fwd_is_gaap = str((getattr(f, "provenance", None) or {}).get("forward_eps", "")).startswith("sec-derived")
        fwd_eps_dil = f.forward_eps if _fwd_is_gaap else dilute(f.forward_eps, dil_rate, years=1)
        if dil_rate and dil_rate >= SBC_DILUTION_FLAG_AT and not _fwd_is_gaap:
            _add_flag(f, (f"share-count dilution {dil_rate*100:.1f}%/yr charged to the "
                          f"non-GAAP forward EPS ({f.forward_eps} -> "
                          f"{round(fwd_eps_dil, 2) if fwd_eps_dil else None}); source: {dil_label}"))
        elif _fwd_is_gaap and dil_rate:
            _add_flag(f, "forward EPS is SEC/GAAP-derived - SBC already expensed, "
                         "no dilution charged (would double-count)")
        fv_peg, peg_d = fundamental_peg_price(growth, g_stable, ke_eff, roic_high, roic_term,
                                              fwd_eps_dil, ke_stable=ke_st_eff)
        if peg_d and peg_d.get("pe_clamped"):
            note = (f"fundamental PE clamped to [{PE_FLOOR:.0f}, {PE_CEIL:.0f}] "
                    f"(raw {peg_d.get('fair_pe_raw')}) - FV is a boundary, not an estimate")
            _add_flag(f, note)

        # L3 (2026-08-10): the card was quoting TWO growth rates and reconciling neither.
        # The Demand pillar scored NVO on the 6.4% it actually delivered — D = 1.0, with
        # an explicit "decelerating" note — while the fair value was built on the 19.5%
        # consensus: justified P/E 41.5, FV $116 against a $47 price, Price pillar 5.0,
        # BUY. One quantity, two sources, nothing reconciling them. Same defect family as
        # P3-1, except here nothing is miscoded: it is a policy nobody decided.
        #
        # Both are now computed and both are shown. The realised rate is the MORE
        # CONSERVATIVE of the last year and the 3-year CAGR, floored at zero — a
        # shrinking business cannot be valued on negative compounding, and note that a
        # ratio test cannot even see that case (TSLA: consensus +34.6% against revenue
        # -2.9%; the first threshold written for this missed it for exactly that reason).
        _real = [g for g in (rev_growth_yoy, actual_3y) if g is not None]
        g_realised = max(0.0, min(_real)) if _real else None
        fv_peg_realised, growth_fv_x = None, None
        if g_realised is not None:
            fv_peg_realised, _ = fundamental_peg_price(g_realised, g_stable, ke_eff,
                                                       roic_high, roic_term, fwd_eps_dil,
                                                       ke_stable=ke_st_eff)
        # ONE-SIDED, and the reason is the whole point: the cap exists to remove
        # confidence that consensus lent to the price, so it fires only when consensus
        # FLATTERS the fair value. When consensus sits BELOW what the company delivered
        # (AXON 5% vs 33%, MELI 14% vs 39%) the row already looks cheap on the
        # conservative story, which is stronger evidence, not weaker — a symmetric
        # ratio punished exactly those names on the first attempt. Same mistake A3 made
        # when median-normalising ROIC, caught the same way: by measuring, not reading.
        if fv_peg and fv_peg_realised and fv_peg_realised > 0:
            growth_fv_x = round(fv_peg / fv_peg_realised, 2)
        growth_unreconciled = bool(growth_fv_x and growth_fv_x >= GROWTH_FV_DISAGREE)
        if growth_unreconciled:
            _add_flag(f, (
                f"valuation ใช้ growth {growth*100:.1f}% (consensus) แต่บริษัททำได้จริง "
                f"{g_realised*100:.1f}% → FV ${round(fv_peg, 2)} vs ${round(fv_peg_realised, 2)} "
                f"({growth_fv_x}x) — Price pillar ถูกจำกัดไว้ที่กลาง ๆ เพราะ 'ถูกหรือแพง' "
                f"ขึ้นกับว่าเชื่อ growth ของใคร ไม่ใช่สิ่งที่วัดได้"))

        # exit multiple from STABLE-period fundamentals (payout/(Ke-g)), not the
        # old PEG=1 heuristic (growth x 100) — Philosophy-2026 alignment (S5/S19)
        # P-B2: the exit multiple is a STABLE-phase multiple, so it is built at the
        # stable-phase Ke; the 5 high-growth years are still discounted at ke_eff.
        exit_pe = stable_exit_pe(g_stable, ke_st_eff, roic_term)
        # P2-2: eps0 comes from GAAP net income, which has ALREADY expensed SBC.
        # Diluting it again charged the same compensation twice (~12% of the FVP).
        fv_fvp = future_value_projection(eps0, growth, ke_eff, exit_pe)

        tmargin, tm_label, tm_anchored = terminal_margin(f.ticker, f.operating_income, f.revenue)
        # REV-20: a clamp that changed the answer must say so, even when it is
        # "anchored" — anchored only means the number came from the firm's own margin,
        # not that the firm's own margin survived the band.
        if "CAPPED" in tm_label or "FLOORED" in tm_label:
            _add_flag(f, tm_label)
        # P-A: surface when EBIT was approximated because the filer tags no
        # operating-income subtotal (LLY/PFE) — ROIC/margins carry that caveat.
        if str((getattr(f, "provenance", None) or {}).get("operating_income", "")).startswith("sec-derived"):
            note = "EBIT derived (pretax + interest) - filer tags no operating income"
            _add_flag(f, note)

        # P-L: perpetuity capitalized at the stable-phase WACC, reinvestment costed
        # at the firm's own terminal ROIC (was a hardcoded 15%).
        # REV-12: pass debt_eff, not f.total_debt. Enterprise value is market cap +
        # DEBT - cash, and this model has already decided (P1-5/S5) that a lease
        # liability IS debt — it is inside the WACC weights and inside invested
        # capital. Leaving it out of EV alone made the reverse DCF value a different
        # firm from the one the WACC priced: a lease-heavy retailer's EV was understated
        # by the whole lease book, so the implied CAGR came out too low and the verdict
        # too forgiving.
        # REV-24: pass today's margin so the path RAMPS to the terminal one instead of
        # starting there. REV-25: pass the resolved market cap so EV matches the WACC.
        _m_now = (f.operating_income / f.revenue) if (f.operating_income is not None and f.revenue) else None
        rdcf = reverse_dcf(f.price, f.shares_diluted, f.revenue, rev_1y, debt_eff or None, f.cash,
                           w, rf, tax, tmargin, wacc_term=w_term, roic_term=roic_term,
                           market_cap=mcap, margin_now=_m_now,
                           # P3-1: the FY-over-FY rate the Demand pillar already scores,
                           # not TTM-over-FY-2 recomputed inside the solver
                           actual_growth=rev_growth_yoy)
        if rdcf.get("triggered") and _m_now is not None and abs(_m_now - tmargin) > 0.10:
            _add_flag(f, (f"RevDCF margin ramps {_m_now*100:.0f}% -> {tmargin*100:.0f}% over "
                          f"{REVERSE_HORIZON}y (was held flat at the terminal margin from year 1)"))
        if rdcf.get("triggered") and not tm_anchored:
            note = tm_label + " - RevDCF verdict approximate"
            _add_flag(f, note)
        if (rdcf.get("funding_ratio") or 0) >= 2.0:
            _un = (f"market prices {rdcf.get('implied_cagr_pct')}%/yr growth against a terminal "
                   f"ROIC of {rdcf.get('roic_terminal_pct')}% ({rdcf.get('funding_ratio')}x) - "
                   f"far beyond self-funding; the gap has to come from outside capital")
            _add_flag(f, _un)
        if rdcf.get("out_of_band"):
            # REV-28: say WHICH failure. "no growth rate justifies this price" (a
            # thin-spread business priced for growth that cannot create value) and
            # "needs >100%/yr" are opposite diagnoses and used to share one sentence.
            _add_flag(f, "reverse DCF: " + (rdcf.get("verdict") or "no implied growth"))

        eps_pos = eps0 is not None and eps0 > 0
        fcf_pos = fcff is not None and fcff > 0
        # P-G: an FVP below 10% of price is a broken input, not a valuation. It used
        # to nuke the whole anchor — throwing away a perfectly good Fundamental PEG
        # with it (ABBV: FVP $23 killed a $210 PEG). Discard the METHOD, not the row.
        if fv_fvp and f.price and fv_fvp < 0.1 * f.price:
            note = "FVP discarded (below 10% of price - unreliable inputs)"
            _add_flag(f, note)
            fv_fvp = None
        # REV-16 (S20): a pre-profit name now gets a real FORWARD INTRINSIC value,
        # not just the reverse-DCF pricing check. Failure risk is modelled as its own
        # probability rather than smuggled into the discount rate, and growth is paid
        # for through the sales-to-capital ratio. See domain/engine/young_dcf.py.
        # P2-5: route on EARNINGS, not on free cash flow.
        #
        # This gate used to read `(not eps_pos) or (not fcf_pos)`, and the fcf_pos leg
        # was vacuous: the 0.8 reinvestment clamp guaranteed fcff >= 20% of NOPAT for
        # every profitable company. REV-23 removed that clamp — correctly, the gate was
        # incapable of firing — but in doing so it silently changed the ROUTING for any
        # profitable company that out-invests its earnings in a given year.
        #
        # ORCL is the case that caught it: $48.2B of capex against $16.4B of NOPAT
        # (the datacenter build-out) gives a 268% reinvestment rate and FCFF of
        # -$27.4B. Perfectly real, and nothing to do with being a young company. The
        # row was being sent to the S20 young-company model — which ramps margins up
        # from a loss and derives a survival probability from cash runway — and came
        # back with a fair value of $25 against a $213 price.
        #
        # "No earnings to capitalise" and "heavy investment this year" are different
        # situations. The young-company path models the first. The second is an
        # ordinary DCF case that deserves a LOUD FLAG (it gets one above: "reinvestment
        # 268% of NOPAT ... FCFF negative") and not a different valuation method.
        pre_profit = not eps_pos
        if not fcf_pos and eps_pos:
            _add_flag(f, "FCFF negative on heavy reinvestment but earnings are positive - "
                         "valued on the normal earnings paths, not the young-company model")
        ydcf = None
        if pre_profit:
            try:
                ydcf = young_dcf.evaluate(
                    f, rf=rf, wacc=w, roic_term=roic_term, invested_capital=ic,
                    debt_eff=debt_eff, tax=tax, target_margin=tmargin,
                    target_margin_label=tm_label, annual_dilution=dil_rate, fcff=fcff,
                    growth_fallback=f.growth_lt, target_margin_anchored=tm_anchored)
            except Exception as e:          # a young-company model must never kill a row
                _add_flag(f, f"young-company DCF unavailable ({type(e).__name__})")
            if ydcf and ydcf.get("promote"):
                # the band is tight enough to mean something -> it becomes the anchor
                anchor_method = "Young-Company DCF (failure-adjusted)"
                anchor_value = ydcf["monte_carlo"]["p50"]
                _add_flag(f, (
                    f"pre-profit valued forward (S20): p50 ${anchor_value} "
                    f"[p10 ${ydcf['monte_carlo']['p10']} - p90 ${ydcf['monte_carlo']['p90']}], "
                    f"survival {ydcf['p_survival']*100:.0f}%, distress ${ydcf['distress_per_share']}"))
            else:
                anchor_method, anchor_value = "Terminal-Anchored Reverse DCF", None
                if ydcf:
                    _add_flag(f, "young-company DCF is informational only: "
                                 + (ydcf.get("blocked_reason") or "did not qualify"))
        elif spread is not None and spread > 0.10 and (rev_growth_yoy or 0) > 0.15:
            anchor_method, anchor_value = "Future Value Projection", fv_fvp
        else:
            anchor_method, anchor_value = "Fundamental PEG", fv_peg
        if anchor_value is None and anchor_method != "Terminal-Anchored Reverse DCF":
            if anchor_method == "Fundamental PEG" and fv_fvp:
                anchor_method, anchor_value = "Future Value Projection", fv_fvp
            elif anchor_method == "Future Value Projection" and fv_peg:
                anchor_method, anchor_value = "Fundamental PEG", fv_peg
            if anchor_value is None:
                anchor_method = "Terminal-Anchored Reverse DCF"

        # L4 — THE single place per-share value leaves this engine, and the only place
        # the unit verdict is read. Three methods reached for that value in turn while
        # the fix chased them one at a time: the PEG path (closed by the forward-EPS
        # gate), the Future Value Projection (which builds its own EPS from
        # net_income/shares), and then the young-company DCF — which divides FIRM value
        # by the share count and never touches EPS at all, and still printed $8,504
        # against a $421 price. A guard placed next to any one input is a guard the
        # next method does not know about; this one sits after every branch has run.
        #
        # Everything is suppressed, including a forward EPS that would pass on its own:
        # when the filings and the quote disagree about units we do not know WHICH is
        # wrong, and a flagged blank is worth more than a plausible-looking wrong price.
        if not getattr(f, "per_share_unit_ok", True):
            fv_peg = fv_fvp = None
            ydcf = None
            anchor_method, anchor_value = "No per-share valuation (unit mismatch)", None

        methods = {"Fundamental PEG": fv_peg, "Future Value Projection": fv_fvp}
        avail = {k: v for k, v in methods.items() if v and v > 0}
        range_low = min(avail.values()) if avail else None
        range_high = max(avail.values()) if avail else None
        # D4: the card presents [low, high] as a valuation RANGE, which invites the
        # reader to treat it like a confidence interval. It is not — it is two point
        # estimates from two different methods, and when they are far apart the gap is
        # not uncertainty about the price, it is the two models disagreeing about the
        # company. ABBV: Fundamental PEG $299 against FVP $41, a 7.3x spread quoted as
        # a range around a $254 price. There is already a guard that discards an FVP
        # below 10% of price (P-G), but ABBV's $41 is 16% of price, so it survived and
        # became `range_low`.
        #
        # Not silently narrowed or dropped — a real disagreement is information, and
        # picking a winner here would be inventing precision. It is labelled instead, so
        # the range reads as "the methods do not agree" rather than "we are confident
        # within this band".
        fv_disagreement = None
        if range_low and range_high and range_low > 0:
            _ratio = range_high / range_low
            if _ratio >= FV_DISAGREE_RATIO:
                fv_disagreement = round(_ratio, 2)
                _lo_m = min(avail, key=avail.get)
                _hi_m = max(avail, key=avail.get)
                _add_flag(f, (f"fair-value methods disagree {_ratio:.1f}x ({_hi_m} ${range_high:.0f} "
                              f"vs {_lo_m} ${range_low:.0f}) - the band is a method conflict, "
                              f"not a confidence interval; the anchor is {anchor_method}"))
        # REV-16: for a young company the honest range is the simulated band, not a
        # spread between two point methods that do not apply to it.
        if anchor_method.startswith("Young-Company") and (ydcf or {}).get("monte_carlo"):
            range_low = ydcf["monte_carlo"]["p10"]
            range_high = ydcf["monte_carlo"]["p90"]

        eq_verdict, eq_flags, cc = earnings_quality(
            f.net_income, f.cfo, f.total_assets, f.sbc, f.revenue,
            receivables=f.receivables, receivables_prior=getattr(f, "receivables_prior", None),
            revenue_prior=rev_1y,          # REV-19: AR-vs-revenue
            # P3-1: the AR test compares receivables growth against REVENUE growth, so
            # it inherited the same TTM-over-FY-2 inflation — and in the direction that
            # HIDES the signal: `ar_g > rev_g + 0.15` gets harder to satisfy the more
            # revenue growth is overstated, so channel-stuffing was under-detected.
            revenue_growth=rev_growth_yoy)
        for fl in eq_flags:
            _add_flag(f, "EQ: " + fl)
        if f.deferred_revenue and f.deferred_revenue_prior and rev_growth_yoy is not None:
            dr_g = f.deferred_revenue / f.deferred_revenue_prior - 1
            # P3-1: deferred revenue is a fiscal-year-end balance, so the revenue it is
            # compared against has to be fiscal-year growth too. MSFT read
            # "billings lagging (deferred rev -1% vs rev +30%)" against a reported +15%.
            rev_g = rev_growth_yoy
            sig = "positive" if dr_g > rev_g else "lagging"
            note = f"billings {sig} (deferred rev {dr_g*100:+.0f}% vs rev {rev_g*100:+.0f}%)"
            _add_flag(f, note)

        oia = f.operating_income_annuals or []
        # A4: incremental ROIC preferred from the 5-year change in NOPAT over the
        # 5-year change in INVESTED CAPITAL — the direct measure, and a denominator
        # that is large and stable.
        #
        # The fallback below (one year of capex + M&A - D&A) is the same quantity
        # approximated from the cash-flow statement, and REV-13 already had to clamp it
        # to [-2, +2] because a firm whose capex happened to land near its depreciation
        # produced a denominator near zero and a four-digit "return". Clamping stops the
        # nonsense from reaching the band but does not make the number informative: for
        # any mature company capex ~ D&A most years, so the ratio is dominated by
        # whichever way the small residual fell. Delta invested capital over five years
        # has no such cliff. Both sides are raw (no R&D capitalization, no leases) in
        # BOTH formulations, so the change does not switch measurement basis — the D3
        # mistake.
        inc_roic, inc_src, _inc_declined = None, None, False
        _rmap = _trend_roic_map(f, tax)
        if _rmap:
            # contiguous run only — measuring "the return on capital added over five
            # years" across a three-year hole measures something else entirely
            _win = trend_mod._window(_rmap, years=5)
            _pct, _why = (trend_mod.incremental_roic_pct(_rmap, _win) if len(_win) >= 2
                          else (None, "ปีงบไม่ต่อเนื่องพอ"))
            if _pct is not None:
                inc_roic = _clamp(_pct / 100.0, -2.0, 2.0)
                inc_src = f"ΔNOPAT/ΔIC {len(_win)}y"
            else:
                # The 5-year measure REFUSED for a stated reason — normally "the capital
                # base barely moved", i.e. there is no new capital whose return could be
                # measured. Falling through to the one-year proxy would answer a question
                # we just established is unanswerable, with the noisiest estimator we
                # have: ABBV's capital base is flat, and the proxy returns +195%.
                _inc_declined = True
                notes.append(f"E_exec incremental-ROIC adj=skipped({_why})")
        if (inc_roic is None and not _inc_declined
                and len(oia) > 1 and oia[1] and f.capex is not None and f.dep_amort is not None):
            d_nopat = (oia[0] - oia[1]) * (1 - tax)
            reinvest_1y = (f.capex + acq - f.dep_amort)
            if reinvest_1y and reinvest_1y > 0:
                inc_roic = _clamp(d_nopat / reinvest_1y, -2.0, 2.0)
                inc_src = "1y capex+M&A−D&A (ประมาณ)"
        # D2: margin trend on a LIKE-FOR-LIKE fiscal-year basis.
        #
        # This used to read `f.operating_income / f.revenue` (both TTM) against
        # `oia[1] / ann[1]` (the fiscal year BEFORE the last completed one) — the same
        # skipped period as P3-1, so a "1-year margin trend" spanned about two.
        #
        # The obvious alternative, TTM against the LAST fiscal year, is worse: for any
        # filer whose TTM has not yet moved past its fiscal year end (ORCL, TSM, NVO,
        # ASML here) the two are the SAME NUMBER and the trend degenerates to "flat"
        # for ever. FY0 vs FY1 is defined for every filer, never degenerates, and is
        # the comparison the company itself reports.
        #
        # It does change two answers, and both changes are the honest ones:
        #   NVDA  "up" -> "down"  (op margin FY 60.4% vs 62.4% — it did compress; the
        #                          TTM 64.0% that used to win is a later, shorter window)
        #   HIMS  "down" -> "up"  (FY 4.5% vs 4.2%; the TTM is -1.3% on a loss quarter)
        margin_trend = None
        if len(oia) > 1 and oia[1] and rev_1y and ann and ann[0] and oia[0] is not None:
            m_now, m_prior = oia[0] / ann[0], oia[1] / rev_1y
            if m_prior:
                margin_trend = "up" if m_now > m_prior * 1.02 else "down" if m_now < m_prior * 0.98 else "flat"

        roic_delta = None
        if f.equity_prior is not None and f.cash_prior is not None and len(oia) > 1 and oia[1]:
            # REV-4: capitalize leases on BOTH sides. Current IC includes the lease
            # liability (P1-5) while prior IC did not, so a lease-heavy filer was
            # measured against a smaller past capital base, its prior ROIC read too
            # high, and the trend showed a deterioration that never happened —
            # worth up to -1.0 on E_econ, the 30%-weighted pillar. Prefer the filed
            # prior-year lease; fall back to today's (flagged) rather than to zero,
            # because zero is the one answer we know is wrong.
            lease_prior = getattr(f, "operating_leases_prior", None)
            if lease_prior is None and lease:
                lease_prior = lease
                _add_flag(f, "prior-year lease liability not filed - current lease reused "
                             "for the like-for-like ROIC trend")
            ic_prior = (f.total_debt_prior or 0) + (lease_prior or 0) + f.equity_prior - f.cash_prior
            if ic_prior and ic_prior > 0:
                # D3: the trend has to compare two ROICs measured the SAME WAY. Two
                # things were different between the sides, and both pushed the same way.
                #
                # (a) TIME BASE. `spread` is built from f.operating_income, a TTM; this
                #     side is oia[1], the fiscal year before the last completed one. The
                #     delta therefore spanned ~2 years and was labelled 1y (REV-14 fixed
                #     the LABEL from 5y to 1y but not the periods). Now uses oia[0].
                #
                # (b) R&D CAPITALIZATION. When rd_capitalize() succeeds, `roic_used` —
                #     and so `spread` — is the R&D-adjusted return, while this side was
                #     always the RAW one. The gap is not small: ASML -37.6pp, TSM -8.3pp,
                #     NVDA -7.5pp, ABBV -6.0pp between raw and adjusted. That difference
                #     landed in the delta as a fake collapse in returns:
                #         ASML "1y-spread-trend(d -25.4pp) = -0.5"
                #         NVO  "1y-spread-trend(d -15.5pp) = -0.5"
                #     on the 30%-weighted pillar, for R&D-heavy firms specifically —
                #     i.e. it penalised exactly the companies the adjustment exists for.
                #     The prior year is now capitalized too, off the R&D series shifted
                #     by one year, and only when the current side was.
                def _roic_fy(op_income, invested, rnd_series):
                    """ROIC for ONE fiscal year, on whichever basis the headline uses.
                    Returns None when the basis cannot be matched, because a raw-vs-
                    adjusted delta is worse than no delta at all."""
                    if op_income is None or not invested or invested <= 0:
                        return None
                    if not rd:                                  # headline is raw -> stay raw
                        return op_income * (1 - tax) / invested
                    out = rd_capitalize(rnd_series, op_income, invested, tax)
                    return out[2] if out else None

                roic_fy0 = _roic_fy(oia[0], ic, f.rnd_annuals)
                roic_fy1 = _roic_fy(oia[1], ic_prior, (f.rnd_annuals or [])[1:])
                if roic_fy0 is not None and roic_fy1 is not None:
                    roic_delta = roic_fy0 - roic_fy1
                elif rd:
                    _add_flag(f, "ROIC trend skipped: the headline ROIC is R&D-capitalized but "
                                 "the prior year cannot be measured the same way (short R&D "
                                 "history) - a raw-vs-adjusted delta reads as a collapse that "
                                 "never happened")

        # A2: both of these used to lose the SIGN of their input, and both lost it in
        # the direction that punished the wrong company. See the two helpers.
        acq_int, acq_label = acquisition_intensity(f.acquisitions_net, f.revenue)
        fade, fade_why = fade_ratio(f.fwd_growth_near, f.fwd_growth_far)
        # B5: `actual_3y` is already computed above from the same revenue_annuals the
        # base band uses, so both sides of the comparison sit on one clock.
        _cons_adj, _cons_note = growth_consistency(rev_growth_yoy, actual_3y)
        D = _r_demand(rev_growth_yoy, getattr(f, "peer_median_growth", None), acq_int, fade,
                      notes, acq_label=acq_label, fade_why=fade_why,
                      consistency_adj=_cons_adj, consistency_note=_cons_note)
        # T5-E: the multi-year cash-generation leg. Reads the SAME strip the card
        # draws, so the score and the picture can never tell different stories.
        try:
            _t5 = trend_mod.build(f)
        except Exception:                       # a display feature must never kill a row
            _t5 = {}
        _fcf_adj, _fcf_note = fcf_durability((_t5 or {}).get("rows"))
        # B6: the beat/miss track record the card has drawn since v8.2, finally scored.
        _beat_adj, _beat_note = beat_consistency(
            f.earnings_surprises or getattr(f, "eps_surprises_backfill", None))
        E_exec = _r_execution(eq_verdict, margin_trend, inc_roic, w, notes,
                              fcf_adj=_fcf_adj, fcf_note=_fcf_note, inc_src=inc_src,
                              skip_inc_note=_inc_declined,
                              beat_adj=_beat_adj, beat_note=_beat_note)
        # B8 + B7: moat has a duration and a peer group, not just a size.
        _rmap_econ = _trend_roic_map(f, tax)
        _cap_adj, _cap_note = competitive_advantage_period(_rmap_econ, w)
        # the SAME ROIC the spread was built from — B7 and the base band are two legs of
        # one pillar, and feeding them different returns is the I2 fault one level down
        _sec_adj, _sec_note = sector_relative(roic_scored, getattr(f, "terminal_roic_sector", None))
        E_econ = _r_economics(spread, roic_delta, notes, cap_adj=_cap_adj, cap_note=_cap_note,
                              sector_adj=_sec_adj, sector_note=_sec_note)
        # A1: the same enterprise value the WACC weights and the reverse DCF were built
        # from (REV-12/REV-25), so all three price the same firm.
        _ev = (mcap + (debt_eff or 0) - (f.cash or 0)) if mcap else None
        _fy_adj, _fy_note = fcf_yield_adj((_t5 or {}).get("rows"), _ev, w)
        P = _r_price(anchor_value, f.price, getattr(f, "own_pe_pctile", None), notes,
                     fcf_adj=_fy_adj, fcf_note=_fy_note)
        # L3: when the two growth stories give fair values far apart, "cheap" is a
        # belief about consensus, not a measurement. Cap — never lift — at NEUTRAL, the
        # same bound D1 puts on a pillar that could not be measured at all. A cap can
        # only remove unearned confidence; it can never manufacture it, which is why it
        # is safe to apply to a judgement this soft.
        if growth_unreconciled and P is not None and P > NEUTRAL_SCORE:
            notes.append(f"P growth-unreconciled(consensus vs realised FV {growth_fv_x}x)"
                         f"={NEUTRAL_SCORE - P:+.2f}")
            P = NEUTRAL_SCORE
        _sc = {"D": D, "E_exec": E_exec, "E_econ": E_econ, "P": P}
        comp = composite(_sc)
        # D1: say so when the neutral bound actually moved the score, so a reader can
        # tell a genuinely middling row from one that was held back by a data gap.
        _miss = [k for k in WEIGHTS if _sc.get(k) is None]
        if _miss and comp is not None:
            _avail = {k: v for k, v in _sc.items() if v is not None}
            _wsum = sum(WEIGHTS[k] for k in _avail)
            _renorm = sum(_avail[k] * WEIGHTS[k] for k in _avail) / _wsum if _wsum else None
            if _renorm is not None and _renorm > comp + 1e-9:
                _add_flag(f, (f"composite {_renorm:.2f} -> {comp:.2f}: {', '.join(_miss)} not "
                              f"measurable, scored NEUTRAL ({NEUTRAL_SCORE}) instead of inheriting "
                              f"the average of the pillars that were"))
        reco = recommendation(comp) if comp is not None else None
        # REV-8: a missing Price pillar must not read as a BUY (see cap_reco_without_price).
        reco, _cap_note = cap_reco_without_price(reco, P)
        if _cap_note:
            _add_flag(f, _cap_note)
        # P3-2: nor may a missing Economics pillar — same 30% weight, same hole.
        reco, _cap_note2 = cap_reco_without_quality(reco, E_econ)
        if _cap_note2:
            _add_flag(f, _cap_note2)
        st = stars(comp) if comp is not None else ""
        sig = _signal(reco)

        eva = (spread * ic) if (spread is not None and ic and ic > 0) else None

        v = Valuation(
            version=self.version, D=D, E_exec=E_exec, E_econ=E_econ, P=P,
            composite=round(comp, 2) if comp is not None else None, stars=st,
            recommendation=reco, signal=sig,
            anchor_method=anchor_method,
            anchor_value=round(anchor_value, 2) if anchor_value else None,
            range_low=round(range_low, 2) if range_low else None,
            range_high=round(range_high, 2) if range_high else None,
            fv_peg=round(fv_peg, 2) if fv_peg else None,
            fv_fvp=round(fv_fvp, 2) if fv_fvp else None,
            reverse_dcf=rdcf,
            young_dcf=ydcf or {},
            cost_of_equity=round(ke, 4),
            eva=round(eva, 0) if eva is not None else None,
            eq_verdict=eq_verdict,
            subscores={"breakdown": notes},
            key_metrics={"wacc_pct": round(w * 100, 2),
                         "ke_pct": round(ke * 100, 2),
                         "kd_pct": round(kd_pre * 100, 2) if kd_pre is not None else None,
                         "roic_pct": round(roic * 100, 2) if roic is not None else None,
                         # what the card prints beside WACC, and what the spread was
                         # built from — I2 requires those to be the same number
                         "roic_adj_pct": round(roic_scored * 100, 2) if (rd and roic_scored is not None) else None,
                         # the measured figure before A3's median damping, kept so the
                         # normalisation is inspectable rather than merely asserted
                         "roic_measured_pct": (round(roic_used * 100, 2)
                                               if (rd and roic_used is not None) else None),
                         "spread_pct": round(spread * 100, 2) if spread is not None else None,
                         # G4: the BASIS the headline ROIC was measured on. D3 was two
                         # ROICs subtracted across different bases (R&D-capitalized minus
                         # raw) and reported as a 25.4pp collapse in returns. A basis that
                         # is not written down cannot be checked, so it is written down:
                         # anything comparing two ROICs must first agree on this string.
                         "roic_basis": ("rd_capitalized+leases" if rd else "raw+leases"),
                         "incremental_roic_pct": round(inc_roic * 100, 1) if inc_roic is not None else None,
                         "terminal_roic_pct": round(roic_term * 100, 1),
                         "terminal_beta": round(beta_t, 2),
                         "terminal_ke_pct": round(ke_st_eff * 100, 2),
                         "terminal_wacc_pct": round(w_term * 100, 2),
                         "growth_pct": round(growth * 100, 1), "beta": round(beta, 2),
                         # L3: the same card used to quote consensus growth in the fair
                         # value and realised growth in the Demand pillar, and reconcile
                         # neither. Both are published so a reader can see which story
                         # the price depends on.
                         "growth_realised_pct": (round(g_realised * 100, 1)
                                                 if g_realised is not None else None),
                         "fv_peg_realised": (round(fv_peg_realised, 2)
                                             if fv_peg_realised else None),
                         "growth_fv_x": growth_fv_x,
                         # REV-1: make the dilution charge visible on the card
                         "sbc_pct_of_rev": round(f.sbc / f.revenue * 100, 1) if (getattr(f, "sbc", None) and f.revenue) else None,
                         "sbc_dilution_pct": round(dil_rate * 100, 2) if dil_rate else None,
                         "dilution_source": dil_label,
                         "forward_eps_diluted": round(fwd_eps_dil, 2) if fwd_eps_dil else None,
                         "justified_pe": (peg_d or {}).get("fair_pe"),
                         "erp_pct": round(config.ERP * 100, 2), "erp_as_of": config.ERP_AS_OF,
                         "operating_leases": round(lease, 0) if lease else None,
                         "terminal_margin_pct": round(tmargin * 100, 1),
                         "terminal_margin_anchored": tm_anchored,
                         # D4: ratio between the two point methods when they disagree
                         # enough that [low, high] must not be read as a range
                         "fv_disagreement_x": fv_disagreement},
            flags=list(f.flags))
        try:
            v.verdict = _verdict(f, v)
        except Exception as e:                       # guard: never let a verdict typo/bad field crash a ticker
            v.verdict = (f"{v.recommendation or 'N/A'} {v.stars or ''} - verdict unavailable ({type(e).__name__})").strip()
        return v


def _verdict(f, v):
    km = v.key_metrics
    # REV-16 (S20): a pre-profit name with a usable forward valuation says what it is
    # WORTH and what the survival assumption behind that is — not just what the
    # market has priced in. The distress leg is stated because it is the floor the
    # position size has to survive.
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
            # P-C: this branch also catches PROFITABLE companies whose FV inputs are
            # missing (e.g. no operating-income tag). Calling those "Pre-profit" is a lie.
            lbl = "Pre-profit" if not (f.net_income and f.net_income > 0) else "No point FV (insufficient data)"
            return (f"{v.recommendation} {v.stars} - {lbl}: market prices ~{rd.get('implied_cagr_pct')}% 10y CAGR "
                    f"vs actual {rd.get('actual_1y_pct')}% ({rd.get('verdict')}). conf {f.confidence}")
        return (f"{v.recommendation or 'N/A'} {v.stars} - insufficient data for a point fair value. conf {f.confidence}").strip()
    fv = v.anchor_value
    if fv is None:
        return (f"{v.recommendation or 'N/A'} {v.stars} - no fair value computed. conf {f.confidence}").strip()
    up = (f"{((fv - f.price) / f.price * 100):+.0f}% upside" if f.price else "")
    band = f"range ${v.range_low}-${v.range_high}" if v.range_low else ""
    eq = f", EQ {v.eq_verdict}" if v.eq_verdict else ""
    return (f"{v.recommendation} {v.stars} - {v.anchor_method} ${fv} ({up}); {band}. "
            f"ROIC {km.get('roic_adj_pct') or km.get('roic_pct')}% vs WACC {km.get('wacc_pct')}% "
            f"(Ke {km.get('ke_pct')}%), growth {km.get('growth_pct')}%{eq}. conf {f.confidence}").strip()