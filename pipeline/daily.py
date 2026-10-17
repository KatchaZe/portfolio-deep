"""
Daily momentum run (no FMP quota unless Yahoo is blocked).

  fetch_daily(tickers, fmp_key)   NETWORK, run OUTSIDE store.LOCK, in parallel
  commit_daily(s, fetched)        STORE, caller holds store.LOCK

Per ticker: short-window indicators (RSI/MACD/DBBMV, secondary) plus the
composite momentum (primary) under "v2". The market regime (SPY) travels under
the reserved MARKET_KEY and is committed to s["market"].
"""
import logging

import config
from sources import yahoo
from domain import indicators, momentum
from pipeline import prices, market_valuation, batch

log = logging.getLogger("portfolio.daily")

MARKET_KEY = "^MARKET"


def spy_series(fmp_key):
    """SPY adjusted closes; ~2y is enough for SMA200 and the regression beta."""
    return prices.fetch_daily_adjusted("SPY", fmp_key, rng="2y")


def short_indicators(t):
    """RSI/MACD/DBBMV over the short series; {} when the indicators refuse it.
    Raises when no price source answers."""
    c = prices.fetch_daily_series(t)
    m = indicators.compute(t, c["closes"], c["volumes"], c["dates"])
    return {} if "error" in m else m


def composite_momentum(t, fmp_key, **window):
    """(composite dict or None when it cannot be computed, the long series used).
    Raises when no price source answers."""
    lc = prices.fetch_daily_adjusted(t, fmp_key, **window)
    mv = momentum.compute(t, lc["closes"], lc["dates"], lc["dividend_adjusted"])
    if "error" in mv:
        return None, lc
    mv["src"] = lc.get("source")
    mv["quality"] = lc.get("quality")
    return mv, lc


def _daily_one(t, fmp_key, sp):
    """Per-ticker daily payload. Never raises (each part is contained)."""
    m = {}
    try:
        m = short_indicators(t)
    except Exception as e:
        log.warning("%s daily momentum failed: %s", t, e)
    try:
        # the S9 reversal needs 756 bars, so demand them
        mv, lc = composite_momentum(t, fmp_key, rng="5y", full_bars=780)
        if mv is not None:
            if sp:                     # regression beta vs SPY: cross-check of the vendor beta
                try:
                    b = momentum.regression_beta(lc["closes"], lc["dates"],
                                                 sp["closes"], sp["dates"])
                    if b is not None:
                        mv["beta_calc"] = b
                except Exception as e:
                    log.warning("%s beta_calc failed: %s", t, e)
            m["v2"] = mv
    except Exception as e:
        log.warning("%s daily momentum_v2 failed: %s", t, e)
    return m


def _market_overview(sp):
    """S9 regime + S16/S35 12m benchmark + S32/33 valuation overlay from SPY."""
    mkt = momentum.market_state(sp["closes"])
    mkt["ret_12m"] = momentum.roc(sp["closes"], 252)
    mkt["as_of"] = sp["dates"][-1] if sp.get("dates") else None
    try:
        rf, _ = yahoo.fetch_treasury_10y()
        from pipeline.fundamentals import refresh_implied_erp
        refresh_implied_erp()
        mkt["valuation"] = market_valuation.overlay(rf, config.ERP, config.MARKET_PE)
        # assumption freshness, so the UI can flag a stale ERP/PE
        mkt["valuation"]["erp_as_of"] = config.ERP_AS_OF
        mkt["valuation"]["market_pe_as_of"] = config.MARKET_PE_AS_OF
        mkt["valuation"]["erp_stale_months"] = config.ERP_STALE_MONTHS
    except Exception as e:
        log.warning("market valuation overlay failed: %s", e)
    return mkt


def fetch_daily(tickers, fmp_key=""):
    """NETWORK PHASE. `fmp_key` is spent only if Yahoo is blocked (the long
    ladder's FMP tier); pass "" to keep the run quota-free. Run OUTSIDE store.LOCK."""
    # SPY first, so every ticker can compute its regression beta
    sp = None
    try:
        sp = spy_series(fmp_key)
    except Exception as e:
        log.warning("SPY fetch failed (beta_calc + market_state skipped): %s", e)

    out = {}
    futs = batch.run_parallel(lambda t: _daily_one(t, fmp_key, sp), tickers)
    for t in tickers:
        m = futs[t].result()
        if m:
            out[t] = m
    try:
        out[MARKET_KEY] = _market_overview(sp if sp is not None else spy_series(fmp_key))
    except Exception as e:
        log.warning("market_state SPY failed: %s", e)
    return out


def commit_daily(s, fetched):
    """STORE PHASE, merge momentum. Caller must hold store.LOCK.
    Returns the holdings tickers that were updated."""
    for t, m in fetched.items():
        if t == MARKET_KEY:
            s["market"] = m
        else:
            s["momentum"][t] = m
    return [t for t in fetched if t != MARKET_KEY]
