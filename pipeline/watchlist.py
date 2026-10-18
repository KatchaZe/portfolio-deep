"""
Watchlist: analyse tickers on demand into display rows. Nothing is stored; the
caller commits only the FMP quota counter afterwards.
"""
import logging

from sources import yahoo
from domain import momentum, advice
from pipeline import fundamentals, daily, rows, batch

log = logging.getLogger("portfolio.watchlist")


def analyze_row(ticker, rf, fmp_key="", rf_live=True, regime=None, spy=None):
    """Full analysis for ONE ticker incl. momentum, returned as a display row.
    Ephemeral, NETWORK ONLY. Returns (row, fmp_calls). `regime` is the SPY market
    state; it must be known before the advice is built, as on the holdings card.
    `spy` is the SPY series the relative momentum component compares against."""
    t = ticker.upper().strip()
    # the shared ROC table here too, or a single-ticker add would use the bundled
    # snapshot while the same ticker in a batch refresh uses the live table
    ff, val, calls = fundamentals.analyze(t, rf, fmp_key, rf_live=rf_live,
                                          roc_table=fundamentals.fetch_roc_table())
    mom = {}
    mom_v2 = None
    try:
        mom = daily.short_indicators(t)
    except Exception as e:
        log.warning("%s momentum failed: %s", t, e)
    try:
        mom_v2, _ = daily.composite_momentum(t, fmp_key, market=spy)
    except Exception as e:
        log.warning("%s momentum_v2 failed: %s", t, e)
    if mom_v2:                         # split-only warning matters only for dividend payers
        mom_v2["div_warn"] = momentum.div_warn(mom_v2.get("dividend_adjusted"),
                                               getattr(ff, "dividend_ps", None))
        if regime is not None:
            mom_v2["crash_guard"] = momentum.crash_guard(mom_v2.get("mom_label"), regime)
    f = ff.to_dict()
    row = rows.valuation_row(t, f, val.to_dict(), mom, mom_v2,
                             rev_surprises=f.get("rev_surprises_fmp"),
                             has_new_schema=True, fmp_on=bool(fmp_key))
    row["advice"] = advice.build(row)
    return row, calls


def _market(fmp_key):
    """(SPY series or None, regime risk_on / risk_off or None). Without SPY the rows
    carry no crash_guard and no relative momentum component."""
    try:
        sp = daily.spy_series(fmp_key)
        return sp, momentum.market_state(sp["closes"]).get("regime")
    except Exception as e:
        log.warning("watchlist market_state failed: %s", e)
        return None, None


def fetch_watchlist(tickers, fmp_key="", quota_used=0, quota_cap=250):
    """NETWORK PHASE, run OUTSIDE store.LOCK."""
    rf, rf_live = yahoo.fetch_treasury_10y()
    spy, regime = _market(fmp_key)
    out_rows, calls = [], 0
    todo, errors = batch.partition_by_quota(tickers, batch.guaranteed_cost(fmp_key),
                                            quota_used, quota_cap)
    futs = batch.run_parallel(lambda t: analyze_row(t, rf, fmp_key, rf_live, regime, spy), todo)
    for t in todo:                     # keep input order for the display rows
        try:
            r, c = futs[t].result()
            calls += c
            out_rows.append(r)
        except Exception as e:
            errors.append(f"{t}: {str(e)[:50]}")
    # rank needs the whole batch, so it stays a post-pass (advice does not read it)
    momentum.cross_sectional_rank([r["momentum_v2"] for r in out_rows if r.get("momentum_v2")])
    return {"rows": out_rows, "errors": errors, "fmp_calls": calls,
            "rf_pct": round(rf * 100, 2), "rf_live": rf_live}
