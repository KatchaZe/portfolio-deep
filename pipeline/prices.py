"""
pipeline/prices.py — the ONE price-series module (deepening refactor 2026-07-19).

Unifies the three fetch ladders that used to be duplicated across the codebase
(refresh.get_prices, refresh.get_prices_long, risk_prices.fetch_returns).
Source ORDER of every ladder is preserved exactly as before:

    fetch_daily_series    (momentum, short)  yahoo chart -> stooq            (>=30 bars)
    fetch_daily_adjusted  (momentum, long)   yahoo adj -> fmp adj -> stooq   (+pricecache)
    fetch_returns         (risk desk)        pricecache -> yahoo -> fmp -> stooq -> stale-cache -> proxy (+daily risk cache)

Error contract: per-tier failures are caught and logged; the two momentum
ladders raise RuntimeError only when EVERY tier fails (caller leaves the cell
blank); fetch_returns never raises. Risk-cache policy: only GOOD series (n>0)
are persisted/served, so a failed fetch is retried on the very next run
(fix for the "proxy 0.60 all day" cache poison).
"""
import os
import json
import hashlib
import logging
import datetime as dt

import time

import config
from sources import yahoo, stooq, fmp, gdrive_store
from pipeline import pricecache
from domain import momentum
from domain.engine import risk

log = logging.getLogger("portfolio.prices")

DEFAULT_DAYS = 400          # ~1.5y of daily data -> stable vol/correlation
RISK_CACHE_PATH = os.path.join(config.DATA_DIR, "risk_cache.json")
REMOTE_CACHE_NAME = "risk_cache.json"   # Drive-shared copy (2026-07-19b): one machine
_last_remote_pull = 0.0                 # pays the FMP quota, every instance reuses it
REMOTE_PULL_MIN_SECS = 60


# --------------------------------------------------------------------------- #
#  Shared cleaning (R1)                                                        #
# --------------------------------------------------------------------------- #
def _clean_prices(d):
    """R1 for the SHORT series too: drop clearly-corrupt bars (non-positive /
    lone reverting spikes) before RSI/MACD/DBBMV read them. Pure."""
    cc, vv, dd, _q = momentum.clean_series(d.get("closes"), d.get("volumes"), d.get("dates"))
    return {**d, "closes": cc, "volumes": vv, "dates": dd}


# --------------------------------------------------------------------------- #
#  Ladder 1 — momentum SHORT series: yahoo chart -> stooq (>=30 bars)          #
# --------------------------------------------------------------------------- #
def fetch_daily_series(ticker, rng="3mo", interval="1d"):
    # (named *_series to avoid clashing with refresh.fetch_daily, the multi-ticker
    #  daily-momentum orchestrator — different concept, same-sounding name)
    """Daily closes/volumes/dates for momentum — Yahoo first, then Stooq fallback.
    Yahoo is often blocked/emptied from datacenter IPs (cloud hosts), so we fall
    back to Stooq (no key, answers from datacenter IPs) to keep momentum alive.
    Returns the series dict; raises only if BOTH sources fail."""
    yahoo_short = None
    try:
        d = _clean_prices(yahoo.fetch_chart(ticker, rng=rng, interval=interval))
        if d and len(d.get("closes", [])) >= 30:
            return d
        yahoo_short = d                         # got data, just not enough — keep as last resort
    except Exception as e:
        log.warning("%s yahoo chart failed, trying stooq: %s", ticker, e)
    try:
        d = _clean_prices(stooq.fetch_chart(ticker))
        if d and len(d.get("closes", [])) >= 30:
            tail = 180                          # Stooq returns full history; momentum needs only the tail
            return {"closes": d["closes"][-tail:], "volumes": d["volumes"][-tail:],
                    "dates": d["dates"][-tail:]}
    except Exception as e:
        log.warning("%s stooq chart failed: %s", ticker, e)
    if yahoo_short:
        return yahoo_short
    raise RuntimeError(f"no price data for {ticker} (yahoo+stooq)")


# --------------------------------------------------------------------------- #
#  Ladder 2 — momentum LONG adjusted series: yahoo -> fmp -> stooq (+cache)    #
# --------------------------------------------------------------------------- #
def fetch_daily_adjusted(ticker, fmp_key="", rng="2y", full_bars=250, min_bars=60):
    """Adjusted daily closes (oldest->newest) for the momentum composite.

    3-tier, all dividend+split adjusted EXCEPT Stooq (split-only -> flagged):
        1) Yahoo adjclose   (free, full history; IP-block risk on cloud)
        2) FMP adjClose     (API key -> not IP-blocked; spent only if Yahoo is short)
        3) Stooq            (split-only -> dividend_adjusted=False; no key)

    Returns the FIRST source with >= full_bars (so 12-1 / SMA200 are valid). If none
    reaches that (e.g. a recent listing), returns the source that provided the MOST
    bars, as long as it has >= min_bars, so a *partial* momentum still shows.
    Uses ONLY fetched data — never fabricates or back-fills. Raises only when every
    source is empty/too short, so the caller leaves the cell blank.

    R4/H3: a same-day cached series short-circuits the network only when it already
    has >= full_bars (a partial cache no longer hides a longer series, but it does
    stop same-day FMP re-spend); every successful fetch is cached; if every live
    source fails a stale cache is served (flagged) before giving up — so momentum
    survives a transient outage."""
    fresh = pricecache.read_fresh(ticker)
    n_fresh = len((fresh or {}).get("closes") or [])
    if fresh and n_fresh >= full_bars:            # H3: cache must satisfy the FULL request
        out = dict(fresh); out["from_cache"] = True
        return out
    # H3: a same-day PARTIAL cache no longer blocks a longer fetch (the old
    # >=min_bars short-circuit let a 2y series suppress a 5y request all day),
    # but FMP quota is NOT re-spent to improve a partial we already paid for
    # today — only the free tiers (Yahoo/Stooq) may try to beat the cache.
    spend_fmp = bool(fmp_key) and not (fresh and n_fresh >= min_bars)
    best = None                                   # (n_bars, payload) seen so far

    def finish(pay):
        pricecache.write(ticker, pay)             # cache the good series (errors swallowed)
        return pay

    def consider(payload):
        nonlocal best
        # R1 data-quality guard: drop corrupt bars (non-positive / lone spikes)
        cc, vv, dd, q = momentum.clean_series(payload.get("closes"),
                                              payload.get("volumes"), payload.get("dates"))
        payload["closes"], payload["volumes"], payload["dates"], payload["quality"] = cc, vv, dd, q
        n = len(cc)
        if n and (best is None or n > best[0]):
            best = (n, payload)
        return n

    try:
        d = yahoo.fetch_chart(ticker, rng=rng, interval="1d")
        pay = {"closes": d.get("adj_closes") or d.get("closes") or [],
               "volumes": d.get("volumes", []), "dates": d.get("dates", []),
               "dividend_adjusted": "adj_closes" in d, "source": "yahoo"}
        if consider(pay) >= full_bars:
            return finish(pay)
    except Exception as e:
        log.warning("%s yahoo long chart failed: %s", ticker, e)

    if spend_fmp:
        try:
            h = fmp.parse_history(fmp.fetch_history(ticker, fmp_key, days=1300))  # ~5y for reversal
            pay = {"closes": h["closes"], "volumes": h["volumes"], "dates": h["dates"],
                   "dividend_adjusted": True, "source": "fmp"}
            if consider(pay) >= full_bars:
                return finish(pay)
        except Exception as e:
            log.warning("%s fmp history failed: %s", ticker, e)

    try:
        d = stooq.fetch_chart(ticker)
        pay = {"closes": d.get("closes", []), "volumes": d.get("volumes", []),
               "dates": d.get("dates", []), "dividend_adjusted": False, "source": "stooq"}
        if consider(pay) >= full_bars:
            return finish(pay)
    except Exception as e:
        log.warning("%s stooq long chart failed: %s", ticker, e)

    if best and best[0] >= min_bars:              # partial history, but usable
        if fresh and n_fresh > best[0]:           # H3: today's cache is still the longest
            out = dict(fresh); out["from_cache"] = True
            return out
        return finish(best[1])
    if fresh and n_fresh >= min_bars:             # live sources failed -> same-day cache
        out = dict(fresh); out["from_cache"] = True
        return out
    stale = pricecache.read_any(ticker)           # R4: last good series keeps momentum alive
    if stale and len(stale.get("closes") or []) >= min_bars:
        out = dict(stale); out["from_cache"] = True; out["stale_cache"] = True
        return out
    raise RuntimeError(f"no long price data for {ticker} (yahoo+fmp+stooq)")


# --------------------------------------------------------------------------- #
#  Ladder 3 — risk-desk RETURNS: fmp adj -> stooq -> proxy (+daily risk cache) #
#  (moved from pipeline/risk_prices.py 2026-07-19; cache never keeps failures) #
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
        with open(RISK_CACHE_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


SAVE_RETRIES = 5            # Windows: a reader's open handle blocks os.replace
SAVE_BACKOFF_SECS = 0.05    # 0.05 + 0.10 + 0.15 + 0.20 = 0.5s worst case


def save_cache(c):
    """Atomically replace the risk cache. Never raises — the cache is best-effort.
    Returns True when the new content is actually on disk.

    2026-08-10. On Windows `os.replace` onto a path that ANOTHER open handle holds
    fails with PermissionError (WinError 32), and PermissionError is an OSError —
    so the old bare `except OSError: pass` swallowed it and left the OLDER cache in
    place while reporting nothing. That silently defeats the very fix this module
    exists for: the stale entry is served back, `todo` never empties, and the run
    degrades to the proxy exactly as in the "0.60 all day" bug.

    The racing reader is ours: `fetch_returns` mirrors this file to Drive on a
    background thread, and `MediaFileUpload` keeps it open for the whole upload.
    So retry across that window, and if it still will not land, SAY SO rather than
    return as though the write succeeded.
    """
    tmp = f"{RISK_CACHE_PATH}.{os.getpid()}.tmp"
    try:
        os.makedirs(os.path.dirname(RISK_CACHE_PATH) or ".", exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(c, fh)
    except OSError as e:
        log.warning("risk cache not written: %s", e)
        return False
    for attempt in range(SAVE_RETRIES):
        try:
            os.replace(tmp, RISK_CACHE_PATH)
            return True
        except PermissionError:
            if attempt == SAVE_RETRIES - 1:
                break
            time.sleep(SAVE_BACKOFF_SECS * (attempt + 1))
        except OSError as e:
            log.warning("risk cache not replaced: %s", e)
            break
    try:
        os.unlink(tmp)          # do not leave a .tmp behind on the failure path
    except OSError:
        pass
    log.warning("risk cache save gave up after %d attempts - %s is held open by "
                "another handle; the PREVIOUS cache is still in place and this "
                "run's series will be refetched next time", SAVE_RETRIES,
                RISK_CACHE_PATH)
    return False


def _remote_good(key):
    """GOOD entries (n>0) from the Drive-shared risk cache IF it is for the same
    key (same day + same holdings set); else None. Throttled; never raises."""
    global _last_remote_pull
    now = time.time()
    if now - _last_remote_pull < REMOTE_PULL_MIN_SECS:
        return None
    _last_remote_pull = now
    try:
        c = gdrive_store.drive_pull_json(REMOTE_CACHE_NAME)
        if c and c.get("key") == key:
            good = {t: v for t, v in (c.get("data") or {}).items()
                    if (v or {}).get("n")}
            return good or None
    except Exception:
        pass
    return None


def _fetch_one(ticker, fmp_key, may_use_fmp, days):
    """Return (series_dict_or_None, source, used_fmp_call). Never raises.

    C1 (2026-07-20): the ladder now OPENS THE POOL momentum already paid for —
    pricecache holds yahoo-adjusted daily series for every holding after each
    Run Daily — and tries Yahoo directly for names not in the pool yet (a NEW
    holding before its first Run Daily, or the QQQ/GLD/IBIT benchmark refs).
    This removes the dependency on FMP's free-tier per-symbol entitlement that
    caused the deterministic 13-ticker proxy gap.

        0) pricecache fresh (<=TTL)     -> "yahoo-cache"
        1) yahoo direct (1 quick try)   -> "yahoo"  (+ written into pricecache
           so the next Run Daily reuses it for free; IP-blocked on cloud hosts
           -> falls through fast)
        2) FMP adjusted (quota-guarded) -> "fmp"
        3) Stooq                        -> "stooq"
        4) pricecache ANY age           -> "yahoo-cache-stale" (beats a proxy)
        5) nothing                      -> "proxy"
    """
    tail = days + 1                     # need `days` returns -> days+1 closes
    # Tier 0: fresh pricecache — series Run Daily already fetched today
    try:
        pc = pricecache.read_fresh(ticker)
        if pc and len(pc.get("closes") or []) >= 61:
            return ({"closes": pc["closes"][-tail:],
                     "dates": (pc.get("dates") or [])[-tail:]}, "yahoo-cache", False)
    except Exception:
        pass
    # Tier 1: yahoo direct — one quick attempt (new holding / benchmark refs)
    try:
        d = yahoo.fetch_chart(ticker, rng="2y", interval="1d", retries=1, timeout=8)
        closes = d.get("adj_closes") or d.get("closes") or []
        if len(closes) >= 61:
            pay = {"closes": closes, "volumes": d.get("volumes", []),
                   "dates": d.get("dates", []),
                   "dividend_adjusted": "adj_closes" in d, "source": "yahoo"}
            pricecache.write(ticker, pay)      # momentum reuses it for free
            return ({"closes": closes[-tail:], "dates": pay["dates"][-tail:]},
                    "yahoo", False)
    except Exception:
        pass
    # Tier 2: FMP adjusted (only if allowed by the quota pre-check)
    if may_use_fmp and fmp_key:
        try:
            j = fmp.fetch_history(ticker, fmp_key, days=days)
            ph = fmp.parse_history(j)
            if ph.get("closes"):
                return ph, "fmp", True
        except Exception:
            pass                       # fall through to free source
    # Tier 3: Stooq (free)
    try:
        sc = stooq.fetch_chart(ticker)
        if sc.get("closes"):
            return sc, "stooq", False
    except Exception:
        pass
    # Tier 4: stale pricecache — old realized series still beats an assumed 0.60
    try:
        pc = pricecache.read_any(ticker)
        if pc and len(pc.get("closes") or []) >= 61:
            return ({"closes": pc["closes"][-tail:],
                     "dates": (pc.get("dates") or [])[-tail:]},
                    "yahoo-cache-stale", False)
    except Exception:
        pass
    # Tier 5: nothing
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
    cached = (cache.get("data") or {}) if cache.get("key") == key else {}
    # FIX (2026-07-19): trust cache only for GOOD series (n>0). A failed fetch used
    # to be cached as n=0 and served back all day ("poisoned" cache -> the whole
    # matrix degraded to the proxy 0.60 until the next calendar day).
    good = {t: cached[t] for t in tickers if (cached.get(t) or {}).get("n")}
    todo = [t for t in tickers if t not in good]
    if use_cache and not todo:                                   # full GOOD hit
        return {t: good[t] for t in tickers}, 0, {"cache": "hit", "key": key}

    # 2026-07-19b: before spending network/quota, merge the Drive-SHARED cache —
    # series another machine (e.g. the local box) already paid FMP for today.
    drive_merged = 0
    if use_cache and todo:
        rem = _remote_good(key)
        if rem:
            for t in list(todo):
                if t in rem:
                    good[t] = rem[t]
            todo = [t for t in tickers if t not in good]
            drive_merged = len(rem)
            if not todo:                       # Drive supplied everything missing
                save_cache({"key": key, "saved": _today(), "data": dict(good)})
                return ({t: good[t] for t in tickers}, 0,
                        {"cache": "drive-hit", "key": key, "quota_degraded": False,
                         "drive_cache_merged": drive_merged})

    data, calls = dict(good), 0
    for t in todo:
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
        # persist only GOOD series so failures are retried on the very next run
        good_now = {t: v for t, v in data.items() if v.get("n")}
        save_cache({"key": key, "saved": _today(), "data": good_now})
        if good_now:
            # mirror the shared cache back to Drive (background; guard-free —
            # a rebuildable cache; skipped when Drive isn't configured)
            try:
                import store_sync
                store_sync.schedule_push_named(RISK_CACHE_PATH, REMOTE_CACHE_NAME)
            except Exception:
                pass
    degraded = any(v["source"] != "fmp" for v in data.values()) and bool(fmp_key)
    return data, calls, {"cache": ("partial" if good else "miss"), "key": key,
                         "quota_degraded": degraded, "drive_cache_merged": drive_merged}
