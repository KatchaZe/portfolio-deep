"""
Validate — Phase 2 quality gate on a normalized FinancialFacts.

Does three things:
  1. Sanity-checks derived metrics (operating margin, ROIC, WACC band) and flags
     out-of-band values.
  2. Resolves the forward EPS the engine should trust: keeps the analyst (Yahoo)
     consensus when plausible (e.g. ABBV adjusted $16), but replaces it with a
     SEC-derived forward when it breaches the revenue ceiling (e.g. AVGO's
     unsplit $19 -> ~$6) so the valuation isn't poisoned by a bad input.
  3. Re-scores confidence and assigns a green / yellow / red tier.

Optional `fmp_income` (when FMP statements are available for the symbol, e.g.
ABBV/MSFT) enables a SEC-vs-FMP cross-check on revenue + net income.
"""
ERP = 0.0475
MAX_NET_MARGIN = 0.65      # revenue-per-share ceiling factor for forward EPS


def validate(ff, fmp_income=None, rf=0.045):
    tax = ff.tax_rate
    nopat = ff.operating_income * (1 - tax) if ff.operating_income is not None else None

    # --- sanity rules ---
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

    # --- cross-check vs FMP where available ---
    if fmp_income:
        _cross_check(ff, fmp_income)

    # --- resolve forward EPS ---
    _resolve_forward_eps(ff)

    # --- re-score + tier ---
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
        sec_eps = ff.net_income / ff.shares_diluted          # operating/GAAP basis
    elif ff.eps_gaap:
        sec_eps = ff.eps_gaap
    sec_fwd = sec_eps * (1 + min(ff.growth_lt or 0.0, 0.25)) if sec_eps else None
    ceiling = (ff.revenue / ff.shares_diluted * MAX_NET_MARGIN) if (ff.revenue and ff.shares_diluted) else None
    y = ff.forward_eps

    if y and y > 0 and (ceiling is None or y <= ceiling):
        pass                                                  # consensus is plausible — keep it
    elif sec_fwd:
        ff.forward_eps = round(sec_fwd, 2)
        ff.provenance["forward_eps"] = "sec-derived (consensus rejected)"
        if y:
            ff.flags.append(f"forward_eps {round(y,2)} rejected (> ceiling {round(ceiling,2) if ceiling else 'na'}); used SEC {ff.forward_eps}")
    # else: leave whatever we have


def _rescore(ff):
    score = 100
    critical = ("revenue", "net_income", "shares_diluted", "price", "forward_eps")
    for fld in critical:
        if getattr(ff, fld) is None:
            score -= 18
    # each non-cosmetic flag costs confidence
    serious = [f for f in ff.flags if "out of band" in f or "differ" in f or "rejected" in f
               or "not converted" in f]
    score -= 8 * len(serious)
    if any(f.startswith("converted") for f in ff.flags):
        score -= 12
    ff.confidence = max(0, min(100, score))
    ff.confidence_tier = "green" if ff.confidence >= 80 else "yellow" if ff.confidence >= 50 else "red"
    return ff
