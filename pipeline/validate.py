"""
Validate — quality gate on a normalized FinancialFacts (sanity, forward-EPS
resolution, assumption flags, confidence + tier).
"""
import config

ERP = config.ERP            # same market ERP the engine uses (sanity-band check only)
MAX_NET_MARGIN = 0.65


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
        wacc = rf + ff.beta * ERP
        if not (0.03 <= wacc <= 0.25):
            ff.flags.append(f"WACC {wacc:.0%} out of band")

    if fmp_income:
        _cross_check(ff, fmp_income)

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
    """Trust analyst consensus unless it breaches the revenue-capacity ceiling."""
    ff.forward_eps_raw = ff.forward_eps
    sec_eps = None
    if ff.net_income and ff.shares_diluted:
        sec_eps = ff.net_income / ff.shares_diluted
    elif ff.eps_gaap:
        sec_eps = ff.eps_gaap
    sec_fwd = sec_eps * (1 + min(ff.growth_lt or 0.0, 0.25)) if sec_eps else None
    ceiling = (ff.revenue / ff.shares_diluted * MAX_NET_MARGIN) if (ff.revenue and ff.shares_diluted) else None
    y = ff.forward_eps

    if y and y > 0 and (ceiling is None or y <= ceiling):
        pass
    elif sec_fwd:
        ff.forward_eps = round(sec_fwd, 2)
        ff.provenance["forward_eps"] = "sec-derived (consensus rejected)"
        if y:
            ff.flags.append(f"forward_eps {round(y,2)} rejected (> ceiling {round(ceiling,2) if ceiling else 'na'}); used SEC {ff.forward_eps}")


def _assumption_flags(ff):
    """Flag inputs that, when absent, the engine fills with a GENERIC ASSUMPTION
    rather than real data — so a guessed value is never silent (DEEP invariant 17)."""
    if ff.beta is None:
        ff.flags.append("beta missing → defaults to 1.0 (assumption)")
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
    score += _earnings_confidence(ff)
    score += _consensus_confidence(ff)
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
