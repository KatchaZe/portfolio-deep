"""
Damodaran S32 (Mean Reversion) + S33 (Valuing the Market): a market-level overlay
that flags whether the broad market is cheap / fair / expensive, to inform an
asset-allocation (cash) tilt — NOT in-and-out market timing (which S34 says fails).

Inputs reuse what the app already has + one manual value (like config.ERP):
  rf           live 10Y Treasury (yahoo.fetch_treasury_10y)
  implied_erp  Damodaran implied ERP (config.ERP, refreshed monthly)
  market_pe    S&P trailing P/E (config.MARKET_PE, manual monthly)
Pure.
"""


def overlay(rf, implied_erp, market_pe, erp_low=0.040, erp_high=0.055):
    """Return {regime, tilt, earnings_yield_pct, fed_spread_pct, implied_erp_pct,
    rf_pct, note}. regime/tilt from where the implied ERP sits vs a normal band
    (mean reversion); Fed-model earnings-yield spread = E/P - Rf as context.
    regime=None when implied_erp is missing (never invents)."""
    if implied_erp is None:
        return {"regime": None}
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
            "note": note}
