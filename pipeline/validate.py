"""
Validate — quality gate on a normalized FinancialFacts (sanity, forward-EPS
resolution, assumption flags, confidence + tier).
"""
import config
from pipeline import dataquality

ERP = config.ERP            # import-time snapshot (back-compat); checks read config.ERP live
MAX_NET_MARGIN = 0.65
# S1: shares-free sanity band on the CONSENSUS forward EPS. The revenue-capacity
# ceiling below needs shares_diluted, which IFRS/ADR filers often don't give us —
# and when it is missing that guard silently disappears (TSM sailed through with a
# forward EPS of 323.34, which is TWD per ORDINARY share against a USD ADR price of
# $398: an implied forward P/E of 1.2x). Price and EPS must at least be in the same
# currency and the same share unit; a P/E outside this band says they are not.
FWD_PE_MIN, FWD_PE_MAX = 3.0, 200.0
# R3: the capacity ceiling must be compared against FORWARD revenue, since the EPS
# being tested is a forward number. Holding it against CURRENT revenue per share is
# systematically tight for fast growers and did reject NVDA's real consensus
# (fwd EPS 8.18 vs a ceiling of 6.76 built on today's revenue). Growth is capped at
# the engine's own GROWTH_CAP so the ceiling can't be inflated by a wild estimate.
CEILING_GROWTH_CAP = 0.30


def forward_eps_rejection(forward_eps, revenue, shares, price, growth_lt):
    """Why this consensus forward EPS is not credible, or None if it is.

    R1: ONE gate, used by both normalize._score (confidence) and _resolve_forward_eps
    (substitution). They used to be separate copies — normalize kept its own
    hardcoded 0.65 ceiling and never got the P/E check — so a value could be
    rejected for valuation while still scoring as trustworthy."""
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
        if not (FWD_PE_MIN <= pe <= FWD_PE_MAX):
            return (f"implied forward P/E {pe:.1f}x outside [{FWD_PE_MIN:.0f}, {FWD_PE_MAX:.0f}] "
                    f"- currency or share-unit mismatch vs price {price}")
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

    L4 (2026-08-10). An unconverted local currency always makes EPS TOO LARGE — TWD,
    JPY, KRW and DKK all take many units to the dollar — so the tell is an implied
    trailing P/E far BELOW anything a real business trades at. TSM: EPS 309 (TWD, per
    ADS) against a USD ADR price of 421 is a P/E of 1.4x.

    Only the low side is rejected, and only on POSITIVE earnings. A very high P/E is
    ordinary in a trough year, and a loss-making company has no meaningful P/E at all —
    rejecting either would throw away good rows to catch a fault that cannot produce
    them. Same band as the forward-EPS gate, deliberately: one definition of "these two
    numbers are not comparable", not two that can drift apart."""
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
    tax = ff.tax_rate
    nopat = ff.operating_income * (1 - tax) if ff.operating_income is not None else None

    if ff.operating_income is not None and ff.revenue:
        opm = ff.operating_income / ff.revenue
        if not (-0.50 <= opm <= 0.90):
            ff.flags.append(f"op margin {opm:.0%} out of band")
    ic = (ff.total_debt or 0) + (ff.equity or 0) - (ff.cash or 0)
    roic = (nopat / ic) if (nopat is not None and ic and ic > 0) else None
    if roic is not None and not (-1.0 <= roic <= 3.0):
        ff.flags.append(f"ROIC {roic:.0%} out of band")
    if ff.beta is not None:
        wacc = rf + ff.beta * config.ERP     # call-time read (assumptions override aware)
        if not (0.03 <= wacc <= 0.25):
            ff.flags.append(f"WACC {wacc:.0%} out of band")

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

    Two independent gates, because each one can be blind on its own (S1/S2):
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
        return
    # L1 (2026-08-10): THE REPLACEMENT MUST CLEAR THE SAME BAR AS THE VALUE IT REPLACES.
    # The gate used to be run on the consensus figure only, so a rejected number was
    # swapped for an unchecked one — and on TSM the substitute was WORSE: consensus
    # 277.21 was rejected at an implied P/E of 1.5x and replaced by an SEC-derived
    # 386.69, an implied P/E of 1.09x. Nothing looked at it, fv_peg came out at $8,612
    # against a $422 price, the Price pillar scored a maximum 5.0, and the row read BUY.
    # The docstring above already said a flagged blank beats an unflagged wrong number;
    # that principle was applied to the no-fallback branch and skipped on this one.
    sec_reason = (forward_eps_rejection(sec_fwd, ff.revenue, ff.shares_diluted,
                                        ff.price, ff.growth_lt) if sec_fwd else None)
    usable = sec_fwd if (sec_fwd and sec_reason is None) else None
    if reason and usable:
        ff.forward_eps = round(usable, 2)
        ff.provenance["forward_eps"] = "sec-derived (consensus rejected)"
        ff.flags.append(f"forward_eps {round(y, 2)} rejected ({reason}); used SEC {ff.forward_eps}")
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
    elif sec_reason:                   # no consensus AND the SEC figure fails the gate
        ff.forward_eps = None
        ff.provenance["forward_eps"] = "rejected (sec-derived failed the gate)"
        ff.flags.append(f"no consensus, and SEC-derived {round(sec_fwd, 2)} rejected "
                        f"({sec_reason}) - PEG valuation skipped")


def _assumption_flags(ff):
    """Flag inputs that, when absent, the engine fills with a GENERIC ASSUMPTION
    rather than real data — so a guessed value is never silent (DEEP invariant 17)."""
    if ff.beta is None:
        ff.flags.append("beta missing → defaults to 1.0 (assumption)")
    # S3: shares_diluted going missing is not just one blank field — it silently
    # removes the forward-EPS ceiling AND collapses WACC to Ke (no market cap means
    # no equity/debt weights). IFRS/ADR filers (TSM, NVO) hit this. Never silent.
    if not ff.shares_diluted:
        ff.flags.append("shares outstanding missing → no market cap: WACC falls back to Ke "
                        "(unweighted) and the forward-EPS ceiling is disabled")
    elif str((ff.provenance or {}).get("shares_diluted", "")).startswith("derived"):
        # T2: SEC gave no share count (typical of IFRS filers), so it was derived
        # from market cap / price. That is the right UNIT for a depositary listing,
        # but it is a derived input — say so rather than presenting it as filed data.
        ff.flags.append("shares outstanding derived from market cap / price "
                        "(SEC count missing or in a different share unit)")
    ibt, txe = ff.income_before_tax, ff.tax_expense
    tax_sourced = (ibt and txe is not None and ibt != 0 and 0 <= (txe / ibt) <= 0.6)
    if not tax_sourced:
        ff.flags.append("tax rate defaults to 21% (no usable filing data)")
    if ff.growth_lt is None:
        ff.flags.append("growth missing → defaults to 8% (assumption)")


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
    # L4: the filings and the price are not in the same unit. That is not one weak
    # input among several — every per-share number on the card is unusable, so the row
    # must not read as trustworthy while it is true.
    if not getattr(ff, "per_share_unit_ok", True):
        score -= 30
    score += _earnings_confidence(ff)
    score += _consensus_confidence(ff)
    # DQ: the data's own condition costs confidence — stale financials, a gap in the
    # invested-capital series, an unconverted currency. Applied here rather than as
    # another `serious` flag so the penalty is graded by severity instead of a flat -8,
    # and so a row can be scored while still being visibly less trustworthy.
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
