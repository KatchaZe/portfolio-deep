"""
Price/returns source for the risk engine — the ONLY part of the risk feature that
touches the network. Kept separate from the math (domain/engine/risk.py) and from
the store so the isolation guarantees in ALLOCATION_RISK_UPGRADE_PLAN.md §10 hold.

Per-ticker daily-return series, sourced accuracy-first and confidence-tagged:
    Tier 1  FMP dividend-ADJUSTED history (adjClose)  -> source "fmp"   [FACT]
    Tier 2  Stooq daily closes (free, split-only)     -> source "stooq" [CALC]
    Tier 3  no history available                       -> source "proxy"[JUDG-PROXY]

QUOTA SAFETY (so the Portfolio/Watchlist tabs never starve):
  * a hard pre-check `quota_used + planned_fmp_calls < quota_cap` BEFORE every FMP
    call; if it would exceed, we silently DEGRADE to stooq instead of erroring.
  * FMP calls are COUNTED and returned; app.py commits the counter under st.LOCK
    via the proven reload->add_fmp_calls->save pattern (same as /api/allocation/whatif).
  * results are cached in their OWN data/risk_cache.json (never portfolio.json),
    keyed by the holdings set + date, so each ticker costs FMP at most once a day.

This module never imports `store` and never writes portfolio.json.
"""
import os
import json
import hashlib
import datetime as dt

import config
from sources import fmp, stooq
from domain.engine import risk

CACHE_PATH = os.path.join(config.DATA_DIR, "risk_cache.json")
DEFAULT_DAYS = 400          # ~1.5y of daily data -> stable vol/correlation


# --------------------------------------------------------------------------- #
#  Cache (separate file — cannot affect the shared store)                       #
# --------------------------------------------------------------------------- #
def _today():
    return dt.date.today().isoformat()


def holdings_key(tickers):
    """Stable cache key from the SET of held tickers + today's date, so adding or
    removing a holding (or a new trading day) auto-invalidates the cache."""
    h = hashlib.sha1(",".join(sorted(tickers)).encode()).hexdigest()[:12]
    return f"{_today()}:{h}"


def load_cache():
    try:
        with open(CACHE_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_cache(c):
    try:
        os.makedirs(config.DATA_DIR, exist_ok=True)
        tmp = f"{CACHE_PATH}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(c, fh)
        os.replace(tmp, CACHE_PATH)
    except OSError:
        pass            # cache is best-effort; never break the request


# --------------------------------------------------------------------------- #
#  Fetch + build returns                                                        #
# --------------------------------------------------------------------------- #
def _fetch_one(ticker, fmp_key, may_use_fmp, days):
    """Return (series_dict_or_None, source, used_fmp_call). Never raises."""
    # Tier 1: FMP adjusted (only if allowed by the quota pre-check)
    if may_use_fmp and fmp_key:
        try:
            j = fmp.fetch_history(ticker, fmp_key, days=days)
            ph = fmp.parse_history(j)
            if ph.get("closes"):
                return ph, "fmp", True
        except Exception:
            pass                       # fall through to free source
    # Tier 2: Stooq (free)
    try:
        sc = stooq.fetch_chart(ticker)
        if sc.get("closes"):
            return sc, "stooq", False
    except Exception:
        pass
    # Tier 3: nothing
    return None, "proxy", False


def fetch_returns(tickers, fmp_key="", quota_used=0, quota_cap=250,
                  days=DEFAULT_DAYS, prefer_fmp=True, use_cache=True):
    """Build {ticker: {returns, vol, source, as_of, n}} plus the number of FMP
    calls actually spent. Accuracy-first with a strict quota guard + degrade.

    Returns (data, fmp_calls, meta).
    """
    tickers = list(dict.fromkeys(t for t in tickers if t))      # de-dupe, keep order
    key = holdings_key(tickers)
    cache = load_cache() if use_cache else {}
    if use_cache and cache.get("key") == key and cache.get("data"):
        d = cache["data"]
        if all(t in d for t in tickers):                         # full hit, no fetch
            return {t: d[t] for t in tickers}, 0, {"cache": "hit", "key": key}

    data, calls = {}, 0
    for t in tickers:
        # quota pre-check: only spend an FMP call if it stays UNDER the cap
        may_use_fmp = bool(prefer_fmp and fmp_key and (quota_used + calls + 1 < quota_cap))
        series, source, used = _fetch_one(t, fmp_key, may_use_fmp, days)
        if used:
            calls += 1
        rets = risk.daily_returns(series["closes"]) if series else []
        data[t] = {
            "returns": rets,
            "vol": round(risk.annualized_vol(rets), 4) if rets else None,
            "source": source,
            "as_of": (series["dates"][-1] if series and series.get("dates") else None),
            "n": len(rets),
        }

    if use_cache:
        save_cache({"key": key, "saved": _today(), "data": data})
    degraded = any(v["source"] != "fmp" for v in data.values()) and bool(fmp_key)
    return data, calls, {"cache": "miss", "key": key, "quota_degraded": degraded}
