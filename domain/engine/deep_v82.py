"""
DEEP Framework v8.2 engine — implements the DeepEngine contract.
Free-data limits handled per skill invariant 17 (skip + flag, never fabricate).
"""
import datetime

import config
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
                wacc_term=None, roic_term=None, market_cap=None, margin_now=None):
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
    a1 = (revenue / rev_1y - 1) if rev_1y else None
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
                     receivables=None, receivables_prior=None, revenue_prior=None):
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
    if receivables and receivables_prior and revenue and revenue_prior:
        ar_g = receivables / receivables_prior - 1
        rev_g = revenue / revenue_prior - 1
        # only meaningful when AR is actually growing AND outpacing sales by a wide
        # margin; a shrinking book or a small gap is ordinary business noise.
        if ar_g > 0.10 and ar_g > rev_g + 0.15:
            flags.append(f"receivables +{ar_g*100:.0f}% vs revenue +{rev_g*100:.0f}% "
                         f"(sales not turning into cash)")
    verdict = "CLEAN" if not flags else "REVIEW" if len(flags) <= 2 else "LOW"
    return verdict, flags, cc


def _r_demand(g, peer_median, acq_intensity, fade_ratio, notes):
    if g is None:
        return None
    base = _band(g, [(0.35, 5.0), (0.25, 4.0), (0.15, 3.0), (0.08, 2.0), (0.0, 1.0), (-1e9, 0.0)])
    notes.append(f"D base(growth {g*100:.0f}%)={base}")
    score = base
    if acq_intensity is not None:
        pen = -1.0 if acq_intensity > 0.10 else (-0.5 if acq_intensity > 0.05 else 0.0)
        score += pen; notes.append(f"D organic(acq {acq_intensity*100:.1f}% of rev)={pen}")
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
        notes.append("D durability adj=skipped(no consensus path)")
    return _clamp(score)


def _r_execution(eq_verdict, margin_trend, inc_roic, wacc_val, notes):
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
        score += adj; notes.append(f"E_exec incremental-ROIC(inc-spread {inc*100:.0f}%)={adj}")
    else:
        notes.append("E_exec incremental-ROIC adj=skipped(no prior op income)")
    return _clamp(score)


def _r_economics(spread, spread_prior, notes):
    if spread is None:
        return None
    base = _band(spread, [(0.20, 5.0), (0.10, 4.0), (0.05, 3.0), (0.0, 2.0), (-0.05, 1.0), (-1e9, 0.0)])
    notes.append(f"E_econ base(spread {spread*100:.1f}%)={base}")
    score = base
    if spread_prior is not None:
        delta = spread - spread_prior
        adj = 0.5 if delta > 0.02 else (-1.0 if (delta < -0.02 and spread < 0.05)
                                        else -0.5 if delta < -0.02 else 0.0)
        # REV-14: labelled "5y" but the input is operating_income_annuals[1] and the
        # prior-year balance sheet — a ONE-year change. Renamed rather than re-sourced;
        # the 5y series exists but the prior-IC components are only fetched one year back.
        score += adj; notes.append(f"E_econ 1y-spread-trend(d {delta*100:+.1f}pp)={adj}")
    else:
        notes.append("E_econ spread-trend adj=skipped(no prior ROIC)")
    return _clamp(score)


def _r_price(fv_anchor, price, own_pe_pctile, notes):
    if fv_anchor is None or not price or fv_anchor <= 0:
        notes.append("P base=skipped(no point fair value)")
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
    return _clamp(score)


WEIGHTS = {"D": 0.20, "E_exec": 0.20, "E_econ": 0.30, "P": 0.30}


def composite(scores):
    avail = {k: scores[k] for k in WEIGHTS if scores.get(k) is not None}
    if not avail:
        return None
    wsum = sum(WEIGHTS[k] for k in avail)
    return sum(avail[k] * WEIGHTS[k] for k in avail) / wsum


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
                           market_cap=mcap, margin_now=_m_now)
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

        methods = {"Fundamental PEG": fv_peg, "Future Value Projection": fv_fvp}
        avail = {k: v for k, v in methods.items() if v and v > 0}
        range_low = min(avail.values()) if avail else None
        range_high = max(avail.values()) if avail else None
        # REV-16: for a young company the honest range is the simulated band, not a
        # spread between two point methods that do not apply to it.
        if anchor_method.startswith("Young-Company") and (ydcf or {}).get("monte_carlo"):
            range_low = ydcf["monte_carlo"]["p10"]
            range_high = ydcf["monte_carlo"]["p90"]

        eq_verdict, eq_flags, cc = earnings_quality(
            f.net_income, f.cfo, f.total_assets, f.sbc, f.revenue,
            receivables=f.receivables, receivables_prior=getattr(f, "receivables_prior", None),
            revenue_prior=rev_1y)          # REV-19: AR-vs-revenue
        for fl in eq_flags:
            _add_flag(f, "EQ: " + fl)
        if f.deferred_revenue and f.deferred_revenue_prior and rev_1y and f.revenue:
            dr_g = f.deferred_revenue / f.deferred_revenue_prior - 1
            rev_g = f.revenue / rev_1y - 1
            sig = "positive" if dr_g > rev_g else "lagging"
            note = f"billings {sig} (deferred rev {dr_g*100:+.0f}% vs rev {rev_g*100:+.0f}%)"
            _add_flag(f, note)

        oia = f.operating_income_annuals or []
        inc_roic = None
        if len(oia) > 1 and oia[1] and f.capex is not None and f.dep_amort is not None:
            d_nopat = (oia[0] - oia[1]) * (1 - tax)
            # REV-9/REV-13: acquisitions count as reinvestment here too, and the ratio
            # is UNBOUNDED — a firm whose capex happened to land near D&A produced a
            # denominator of ~0 and an "incremental ROIC" in the thousands of percent,
            # which fed straight into the E_exec band. Clamp to a reportable range.
            reinvest_1y = (f.capex + acq - f.dep_amort)
            if reinvest_1y and reinvest_1y > 0:
                inc_roic = _clamp(d_nopat / reinvest_1y, -2.0, 2.0)
        margin_trend = None
        if len(oia) > 1 and oia[1] and rev_1y and f.operating_income and f.revenue:
            m_now, m_prior = f.operating_income / f.revenue, oia[1] / rev_1y
            if m_prior:
                margin_trend = "up" if m_now > m_prior * 1.02 else "down" if m_now < m_prior * 0.98 else "flat"

        spread_prior = None
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
                roic_prior = oia[1] * (1 - tax) / ic_prior
                spread_prior = roic_prior - w

        acq_int = (abs(f.acquisitions_net) / f.revenue) if (f.acquisitions_net is not None and f.revenue) else None
        fade = None
        if f.fwd_growth_near and f.fwd_growth_far is not None and f.fwd_growth_near != 0:
            fade = f.fwd_growth_far / f.fwd_growth_near
        D = _r_demand(rev_growth_yoy, getattr(f, "peer_median_growth", None), acq_int, fade, notes)
        E_exec = _r_execution(eq_verdict, margin_trend, inc_roic, w, notes)
        E_econ = _r_economics(spread, spread_prior, notes)
        P = _r_price(anchor_value, f.price, getattr(f, "own_pe_pctile", None), notes)
        comp = composite({"D": D, "E_exec": E_exec, "E_econ": E_econ, "P": P})
        reco = recommendation(comp) if comp is not None else None
        # REV-8: a missing Price pillar must not read as a BUY (see cap_reco_without_price).
        reco, _cap_note = cap_reco_without_price(reco, P)
        if _cap_note:
            _add_flag(f, _cap_note)
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
                         "roic_adj_pct": round(roic_used * 100, 2) if (rd and roic_used is not None) else None,
                         "spread_pct": round(spread * 100, 2) if spread is not None else None,
                         "incremental_roic_pct": round(inc_roic * 100, 1) if inc_roic is not None else None,
                         "terminal_roic_pct": round(roic_term * 100, 1),
                         "terminal_beta": round(beta_t, 2),
                         "terminal_ke_pct": round(ke_st_eff * 100, 2),
                         "terminal_wacc_pct": round(w_term * 100, 2),
                         "growth_pct": round(growth * 100, 1), "beta": round(beta, 2),
                         # REV-1: make the dilution charge visible on the card
                         "sbc_pct_of_rev": round(f.sbc / f.revenue * 100, 1) if (getattr(f, "sbc", None) and f.revenue) else None,
                         "sbc_dilution_pct": round(dil_rate * 100, 2) if dil_rate else None,
                         "dilution_source": dil_label,
                         "forward_eps_diluted": round(fwd_eps_dil, 2) if fwd_eps_dil else None,
                         "justified_pe": (peg_d or {}).get("fair_pe"),
                         "erp_pct": round(config.ERP * 100, 2), "erp_as_of": config.ERP_AS_OF,
                         "operating_leases": round(lease, 0) if lease else None,
                         "terminal_margin_pct": round(tmargin * 100, 1),
                         "terminal_margin_anchored": tm_anchored},
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