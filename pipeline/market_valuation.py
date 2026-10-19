"""
Damodaran S32 (Mean Reversion) + S33 (Valuing the Market): a market-level overlay
that flags whether the broad market is cheap / fair / expensive, to inform an
asset-allocation (cash) tilt — NOT in-and-out market timing (which S34 says fails).

Inputs reuse what the app already has + one manual value (like config.ERP):
  rf           live 10Y Treasury (yahoo.fetch_treasury_10y)
  implied_erp  Damodaran implied ERP (config.ERP, read from his home page monthly)
  market_pe    S&P 500 trailing P/E (SPY from Yahoo; the saved MARKET_PE when that fails)
  history      his year-end implied ERPs since 1961 (sources/damodaran_snapshot.ERP_HISTORY)

The cheap/expensive band is where today's ERP sits in its OWN history: below the 25th
percentile = expensive, above the 75th = cheap (S32 mean reversion). It was a fixed
4.0% / 5.5%, which put 1961-2025's median (4.10%) at the edge of "expensive".
Pure.
"""

FALLBACK_BAND = (0.040, 0.055)
MIN_HISTORY_YEARS = 20


def _percentile(sorted_vals, q):
    """Linear interpolation between closest ranks (statistics.quantiles 'inclusive')."""
    k = (len(sorted_vals) - 1) * q
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def erp_bands(history):
    """(25th, 75th percentile, years used, first year, last year) of the implied-ERP
    history, or None with fewer than MIN_HISTORY_YEARS values."""
    vals = sorted(v for v in (history or {}).values() if isinstance(v, (int, float)))
    if len(vals) < MIN_HISTORY_YEARS:
        return None
    years = sorted(history)
    return (round(_percentile(vals, 0.25), 4), round(_percentile(vals, 0.75), 4),
            len(vals), years[0], years[-1])


def overlay(rf, implied_erp, market_pe, history=None):
    """Return {regime, tilt, earnings_yield_pct, fed_spread_pct, implied_erp_pct,
    rf_pct, note, erp_low_pct, erp_high_pct, band_source}. regime/tilt from where the
    implied ERP sits in its own history (mean reversion); Fed-model earnings-yield
    spread = E/P - Rf as context. regime=None when implied_erp is missing (never invents)."""
    if implied_erp is None:
        return {"regime": None}
    bands = erp_bands(history)
    if bands:
        erp_low, erp_high, n, y0, y1 = bands
        band_source = f"Damodaran implied ERP {y0}-{y1} ({n} yrs): 25th/75th percentile"
    else:
        erp_low, erp_high = FALLBACK_BAND
        band_source = "fixed 4.0%/5.5% (ERP history not available)"
    ey = (1.0 / market_pe) if market_pe else None
    fed = (ey - rf) if (ey is not None and rf is not None) else None
    if implied_erp <= erp_low:
        regime, tilt, note = "expensive", "raise cash / trim high-beta", "ERP ต่ำ = ตลาดแพง (priced for perfection)"
    elif implied_erp >= erp_high:
        regime, tilt, note = "cheap", "lean in / deploy cash", "ERP สูง = ตลาดถูกเทียบความเสี่ยง"
    else:
        regime, tilt, note = "fair", "stay invested, no timing tilt", "ERP อยู่ในกรอบปกติ"
    return {"regime": regime, "tilt": tilt,
            "earnings_yield_pct": round(ey * 100, 2) if ey is not None else None,
            "fed_spread_pct": round(fed * 100, 2) if fed is not None else None,
            "implied_erp_pct": round(implied_erp * 100, 2),
            "rf_pct": round(rf * 100, 2) if rf is not None else None,
            "note": note,
            "erp_low_pct": round(erp_low * 100, 2), "erp_high_pct": round(erp_high * 100, 2),
            "band_source": band_source}
