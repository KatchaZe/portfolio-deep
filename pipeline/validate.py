"""
Validate — quality gate on a normalized FinancialFacts (sanity, forward-EPS
resolution, assumption flags, confidence + tier).
"""
import config
from pipeline import dataquality

ERP = config.ERP            # import-time snapshot (back-compat); checks read config.ERP live
MAX_NET_MARGIN = 0.65
# Shares-free sanity band on the CONSENSUS forward EPS. The revenue-capacity ceiling
# needs shares_diluted, which IFRS/ADR filers often lack (TSM: 323.34 TWD per ORDINARY
# share against a USD ADR price of $398, a 1.2x P/E, sailed through). The two sides
# test different faults:
#   LOW side is the currency test. An unconverted local currency always makes EPS TOO
#   LARGE (TWD, JPY, KRW, DKK), so the tell is a P/E far below any real business.
#   HIGH side cannot be a currency artefact (no FX rate makes EPS smaller). It catches
#   a NEAR-ZERO EPS where PEG/FVP divide by noise (RKLB 9,608x). Real, very expensive
#   equities trade near 200x (TSLA, AXON), so reject only past 600x and between 200x
#   and 600x keep the value with an explicit flag.
FWD_PE_MIN, FWD_PE_MAX = 3.0, 600.0
FWD_PE_EXTREME = 200.0      # keep the value above this, but never silently
# The capacity ceiling is built on FORWARD revenue, since the EPS tested is forward;
# current revenue rejected NVDA's real consensus. Growth is capped so a wild estimate
# cannot inflate the ceiling.
CEILING_GROWTH_CAP = 0.30


def forward_eps_rejection(forward_eps, revenue, shares, price, growth_lt):
    """Why this consensus forward EPS is not credible, or None if it is.

    ONE gate, used by both normalize._score (confidence) and _resolve_forward_eps
    (substitution), so a value can never be rejected for valuation while still
    scoring as trustworthy."""
    if not forward_eps or forward_eps <= 0:
        return None
    if revenue and shares:
        g = min(max(growth_lt or 0.0, 0.0), CEILING_GROWTH_CAP)
        ceiling = revenue / shares * (1 + g) * MAX_NET_MARGIN
        if forward_eps > ceiling:
            return f"> revenue-capacity ceiling {round(ceiling, 2)} (fwd revenue x {MAX_NET_MARGIN:.0%} margin)"
    # shares-free gate: works for the IFRS/ADR filers that have no share count, where
    # the ceiling above silently disappears (TSM: TWD EPS against a USD ADR price)
    if price:
        pe = price / forward_eps
        if pe < FWD_PE_MIN:
            return (f"implied forward P/E {pe:.1f}x below {FWD_PE_MIN:.0f}x "
                    f"- currency or share-unit mismatch vs price {price}")
        if pe > FWD_PE_MAX:
            return (f"implied forward P/E {pe:.1f}x above {FWD_PE_MAX:.0f}x "
                    f"- forward EPS is near zero against price {price}, so PEG/FVP "
                    f"would divide by noise")
    return None


def forward_eps_extreme(forward_eps, price):
    """Why this forward EPS is EXTREME but still usable, or None.

    A forward P/E between FWD_PE_EXTREME and FWD_PE_MAX is a real reading on a very
    expensive equity and must reach the valuation, but a PEG built on a 200x+
    multiple is fragile enough that the row has to say so."""
    if not forward_eps or forward_eps <= 0 or not price or price <= 0:
        return None
    pe = price / forward_eps
    if FWD_PE_EXTREME < pe <= FWD_PE_MAX:
        return (f"forward P/E {pe:.0f}x (สูงมาก แต่เป็นตัวเลขจริง ไม่ใช่หน่วยเพี้ยน) "
                f"— PEG/FVP อ่อนไหวต่อสมมติฐานการเติบโตมาก ให้ถ่วงน้ำหนักกับ reverse DCF")
    return None


def trailing_eps(net_income, shares_diluted, eps_gaap):
    """The per-share earnings a valuation builds on, constructed the way the engine
    constructs it (net income over diluted shares, else the filed EPS).

    Kept deliberately close to `deep_v82`'s `eps0`, and invariant I12 asserts the two
    stay in step on real fixtures — a checker that tests a LOOKALIKE of the number in
    production is the same failure as no checker at all."""
    if net_income is not None and shares_diluted:
        return net_income / shares_diluted
    return eps_gaap


def per_share_unit_mismatch(price, eps):
    """Why this EPS cannot be in the same currency and share unit as this price.

    An unconverted local currency makes EPS TOO LARGE, so the tell is an implied
    trailing P/E far below any real business (TSM: TWD EPS 309 vs a USD ADR price of
    421 is 1.4x). Only the low side, and only on POSITIVE earnings: a high P/E is
    ordinary in a trough year and a loss has no P/E. The low bound is the forward
    gate's, so "not the same currency" has one definition."""
    if not price or price <= 0 or eps is None or eps <= 0:
        return None
    pe = price / eps
    if pe < FWD_PE_MIN:
        return (f"trailing P/E {pe:.2f}x implies EPS {eps:,.2f} and price {price:,.2f} "
                f"are not in the same currency x share unit (below {FWD_PE_MIN:.0f}x)")
    return None


def _per_share_unit_gate(ff):
    """Set the flag ONCE, at the source, so every per-share method sees the same verdict.

    The alternative — checking inside each valuation — is what was tried first, and the
    number simply surfaced through the next method that had not been told yet."""
    eps = trailing_eps(ff.net_income, ff.shares_diluted, ff.eps_gaap)
    why = per_share_unit_mismatch(ff.price, eps)
    if why:
        ff.per_share_unit_ok = False
        ff.flags.append(f"หน่วยต่อหุ้นไม่ตรงกับราคา: {why} — "
                        "ข้ามการประเมินมูลค่าแบบต่อหุ้นทั้งหมด (PEG และ Future Value) "
                        "จนกว่าจะแปลงสกุลเงิน/หน่วยหุ้นได้ถูก")


def validate(ff, fmp_income=None, rf=0.045):
    _sanity_bands(ff, rf)
    if fmp_income:
        _cross_check(ff, fmp_income)
    # BEFORE the forward-EPS resolution: if the filed per-share figures are not in the
    # price's unit, the SEC-derived substitute is built from those same figures, so
    # there is no point offering it one.
    _per_share_unit_gate(ff)
    _resolve_forward_eps(ff)
    _assumption_flags(ff)
    _rescore(ff)
    return ff


def _sanity_bands(ff, rf):
    """Flag an operating margin, ROIC or WACC outside any plausible band."""
    tax = ff.tax_rate
    nopat = ff.operating_income * (1 - tax) if ff.operating_income is not None else None
    if ff.operating_income is not None and ff.revenue:
        opm = ff.operating_income / ff.revenue
        if not (-0.50 <= opm <= 0.90):
            ff.flags.append(f"op margin {opm:.0%} out of band")
    # a missing debt figure is read as zero below; say so when the firm pays interest
    if ff.total_debt is None and ((ff.interest_expense or 0) > 0
                                  or getattr(ff, "accounting_standard", None) == "ifrs"):
        ff.flags.append(f"debt not found in the filing although interest expense is "
                        f"${(ff.interest_expense or 0)/1e6:,.0f}M (or an IFRS filer) - treated as 0 (check the debt tags)")
    ic = (ff.total_debt or 0) + (ff.equity or 0) - (ff.cash or 0)
    roic = (nopat / ic) if (nopat is not None and ic and ic > 0) else None
    if roic is not None and not (-1.0 <= roic <= 3.0):
        ff.flags.append(f"ROIC {roic:.0%} out of band")
    # the beta the engine prices on: industry unlevered (re-levered roughly at 1 + D/E)
    # when known, the regression beta only as a fallback
    beta = ff.beta
    if getattr(ff, "beta_unlevered_industry", None):
        mcap = ff.market_cap or ((ff.price or 0) * (ff.shares_diluted or 0))
        de = (ff.total_debt or 0) / mcap if mcap else 0.0
        beta = ff.beta_unlevered_industry * (1 + 0.75 * max(de, 0.0))
    if beta is not None:
        wacc = rf + beta * config.ERP     # call-time read (assumptions override aware)
        if not (0.03 <= wacc <= 0.25):
            ff.flags.append(f"WACC {wacc:.0%} out of band")


def _cross_check(ff, fmp_income):
    inc = fmp_income[0] if isinstance(fmp_income, list) and fmp_income else fmp_income
    for sec_field, fmp_key in (("revenue", "revenue"), ("net_income", "netIncome")):
        a, b = getattr(ff, sec_field), (inc or {}).get(fmp_key)
        if a and b and b != 0:
            diff = abs(a - b) / abs(b)
            if diff > 0.05:
                ff.flags.append(f"{sec_field}: SEC vs FMP differ {diff:.0%}")
            else:
                ff.provenance[sec_field] = ff.provenance.get(sec_field, "sec") + "+fmp✓"


def _resolve_forward_eps(ff):
    """Trust analyst consensus unless it fails a sanity gate.

    Two independent gates, because each one can be blind on its own:
      * revenue capacity — needs shares_diluted, absent for several IFRS/ADR filers;
      * implied forward P/E — needs only price, so it still works when shares don't.
    When a value is rejected and there is no SEC fallback, forward_eps is CLEARED
    rather than left in place: a known-wrong EPS silently produces a fair value
    (TSM's rejected 323.34 was still driving fv_peg = $5,856), and no number with a
    flag is far safer than a wrong number without one."""
    ff.forward_eps_raw = ff.forward_eps
    sec_eps = None
    if ff.net_income and ff.shares_diluted:
        sec_eps = ff.net_income / ff.shares_diluted
    elif ff.eps_gaap:
        sec_eps = ff.eps_gaap
    sec_fwd = sec_eps * (1 + min(ff.growth_lt or 0.0, 0.25)) if sec_eps else None
    y = ff.forward_eps
    reason = forward_eps_rejection(y, ff.revenue, ff.shares_diluted, ff.price, ff.growth_lt)
    if y and y > 0 and reason is None:
        _flag_extreme(ff, y)
        return
    # The replacement must clear the same bar as the value it replaces: on TSM the
    # unchecked SEC substitute was worse than the rejected consensus (1.09x P/E) and
    # produced an $8,612 fair value against a $422 price.
    sec_reason = (forward_eps_rejection(sec_fwd, ff.revenue, ff.shares_diluted,
                                        ff.price, ff.growth_lt) if sec_fwd else None)
    usable = sec_fwd if (sec_fwd and sec_reason is None) else None
    if reason and usable:
        ff.forward_eps = round(usable, 2)
        ff.provenance["forward_eps"] = "sec-derived (consensus rejected)"
        ff.flags.append(f"forward_eps {round(y, 2)} rejected ({reason}); used SEC {ff.forward_eps}")
        _flag_extreme(ff, ff.forward_eps)
    elif reason:
        ff.forward_eps = None
        ff.provenance["forward_eps"] = "rejected (no usable fallback)"
        why = (f"SEC fallback {round(sec_fwd, 2)} rejected too ({sec_reason})"
               if sec_reason else "no SEC fallback")
        ff.flags.append(f"forward_eps {round(y, 2)} rejected ({reason}); {why}"
                        " - PEG valuation skipped")
    elif usable:                       # nothing from consensus at all
        ff.forward_eps = round(usable, 2)
        ff.provenance["forward_eps"] = "sec-derived (no consensus)"
        _flag_extreme(ff, ff.forward_eps)
    elif sec_reason:                   # no consensus AND the SEC figure fails the gate
        ff.forward_eps = None
        ff.provenance["forward_eps"] = "rejected (sec-derived failed the gate)"
        ff.flags.append(f"no consensus, and SEC-derived {round(sec_fwd, 2)} rejected "
                        f"({sec_reason}) - PEG valuation skipped")


def _flag_extreme(ff, eps):
    """Every path that KEEPS a forward EPS runs through here, so an extreme multiple
    can never reach a valuation unannounced."""
    note = forward_eps_extreme(eps, ff.price)
    if note and note not in ff.flags:
        ff.flags.append(note)


def _assumption_flags(ff):
    """Flag inputs that, when absent, the engine fills with a GENERIC ASSUMPTION
    rather than real data — so a guessed value is never silent (DEEP invariant 17)."""
    if ff.beta is None:
        ff.flags.append("beta missing → defaults to 1.0 (assumption)")
    # A missing share count silently removes the forward-EPS ceiling AND collapses
    # WACC to Ke (no market cap, no weights). IFRS/ADR filers (TSM, NVO) hit this.
    if not ff.shares_diluted:
        ff.flags.append("shares outstanding missing → no market cap: WACC falls back to Ke "
                        "(unweighted) and the forward-EPS ceiling is disabled")
    elif str((ff.provenance or {}).get("shares_diluted", "")).startswith("derived"):
        # the right UNIT for a depositary listing, but derived, not filed
        ff.flags.append("shares outstanding derived from market cap / price "
                        "(SEC count missing or in a different share unit)")
    if ff.tax_rate_defaulted:
        ff.flags.append("tax rate defaults to 21% (no usable filing data)")
    if ff.growth_lt is None:
        ff.flags.append("growth missing → defaults to 8% (assumption)")


NO_PATH_PENALTY = 8


def _rescore(ff):
    score = 100
    critical = ("revenue", "net_income", "shares_diluted", "price", "forward_eps")
    for fld in critical:
        if getattr(ff, fld) is None:
            score -= 18
    serious = [f for f in ff.flags if "out of band" in f or "differ" in f or "rejected" in f
               or "not converted" in f]
    score -= 8 * len(serious)
    if any(f.startswith("converted") for f in ff.flags):
        score -= 12
    # filings and price in different units: every per-share number on the card is
    # unusable, so the row must not read as trustworthy
    if not getattr(ff, "per_share_unit_ok", True):
        score -= 30
    score += _earnings_confidence(ff)
    score += _consensus_confidence(ff)
    # no analyst revenue path: every forward number rests on the firm's own history
    if not (ff.rev_growth_path or ff.fwd_growth_y1 is not None):
        ff.flags.append(f"no consensus revenue path (FMP and Yahoo) - growth is historical "
                        f"(-{NO_PATH_PENALTY} conf)")
        score -= NO_PATH_PENALTY
    # the data's own condition (stale financials, an invested-capital gap, an
    # unconverted currency), graded by severity rather than a flat -8 `serious` flag
    score -= dataquality.total_penalty(dataquality.apply(ff))
    ff.confidence = max(0, min(100, score))
    ff.confidence_tier = "green" if ff.confidence >= 80 else "yellow" if ff.confidence >= 50 else "red"
    return ff


def _earnings_confidence(ff):
    es = getattr(ff, "earnings_surprises", None) or []
    graded = [e.get("grade") for e in es if e.get("grade")]
    total = len(graded)
    if total < 2:
        return 0
    beats = graded.count("beat")
    misses = graded.count("miss")
    delta = max(-10, min(10, round((beats - misses) / total * 10)))
    ff.flags.append(f"earnings {beats}B/{graded.count('meet')}E/{misses}M "
                    f"({'+' if delta >= 0 else ''}{delta} conf)")
    return delta


def _consensus_confidence(ff):
    n = getattr(ff, "forward_eps_n", 0) or 0
    sp = getattr(ff, "forward_eps_spread_pct", None)
    if n < 2 or sp is None:
        return 0
    if sp <= 10:
        d = 4
    elif sp <= 25:
        d = 0
    else:
        d = -6
    ff.flags.append(f"fwdEPS {n}src spread {sp}% ({'+' if d >= 0 else ''}{d} conf)")
    return d
