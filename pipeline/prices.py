"""
pipeline/prices.py — every price-series fetch ladder, each declared as an ordered
list of (source, fetch) tiers and run through one `_attempts` loop.

    fetch_daily_series    (momentum, short)  yahoo chart -> stooq            (>=30 bars)
    fetch_daily_adjusted  (momentum, long)   yahoo adj -> fmp adj -> stooq   (+pricecache)
    fetch_returns         (risk desk)        pricecache -> yahoo -> fmp -> stooq -> stale-cache -> proxy (+daily risk cache)

Error contract: per-tier failures are caught and logged; the two momentum
ladders raise RuntimeError only when EVERY tier fails (caller leaves the cell
blank); fetch_returns never raises. Risk-cache policy: only GOOD series (n>0)
are persisted/served, so a failed fetch is retried on the very next run.
"""
import os
import json
import hashlib
import logging
import datetime as dt

import time

import config
from sources import yahoo, stooq, fmp, gdrive_store
from pipeline import batch
from pipeline import pricecache
from domain import momentum
from domain import risk

log = logging.getLogger("portfolio.prices")

DEFAULT_DAYS = 400          # ~1.5y of daily data -> stable vol/correlation
RISK_CACHE_PATH = os.path.join(config.DATA_DIR, "risk_cache.json")
REMOTE_CACHE_NAME = "risk_cache.json"   # Drive-shared copy: one machine pays the
_last_remote_pull = 0.0                 # FMP quota, every instance reuses it
REMOTE_PULL_MIN_SECS = 60

SHORT_MIN_BARS = 30         # RSI/MACD/DBBMV need at least this many closes
STOOQ_TAIL = 180            # Stooq returns full history; the short series needs only the tail
RETURNS_MIN_CLOSES = 61     # 60 daily returns need 61 closes


def _attempts(ticker, tiers, quiet=False):
    """Run (source, fetch) tiers in order; yield (source, result) for each that did
    not raise. A raising tier is logged (unless quiet) and skipped. Each ladder
    decides from the yielded results when to stop."""
    for source, fetch in tiers:
        try:
            got = fetch()
        except Exception as e:
            if not quiet:
                log.warning("%s %s failed: %s", ticker, source, e)
            continue
        yield source, got


def _clean_prices(d):
    """R1: drop clearly-corrupt bars (non-positive / lone reverting spikes). Pure."""
    cc, vv, dd, _q = momentum.clean_series(d.get("closes"), d.get("volumes"), d.get("dates"))
    return {**d, "closes": cc, "volumes": vv, "dates": dd}


def _cleaned(payload):
    """R1 in place on a long payload, keeping the quality flags for display."""
    cc, vv, dd, q = momentum.clean_series(payload.get("closes"),
                                          payload.get("volumes"), payload.get("dates"))
    payload["closes"], payload["volumes"], payload["dates"], payload["quality"] = cc, vv, dd, q
    return payload


def _served_from_cache(rec, stale=False):
    out = dict(rec)
    out["from_cache"] = True
    if stale:
        out["stale_cache"] = True
    return out


def _yahoo_adjusted(ticker, rng, **kw):
    d = yahoo.fetch_chart(ticker, rng=rng, interval="1d", **kw)
    return {"closes": d.get("adj_closes") or d.get("closes") or [],
            "volumes": d.get("volumes", []), "dates": d.get("dates", []),
            "dividend_adjusted": "adj_closes" in d, "source": "yahoo"}


def _fmp_adjusted(ticker, fmp_key):
    h = fmp.parse_history(fmp.fetch_history(ticker, fmp_key, days=1300))  # ~5y for reversal
    return {"closes": h["closes"], "volumes": h["volumes"], "dates": h["dates"],
            "dividend_adjusted": True, "source": "fmp"}


def _stooq_split_only(ticker):
    d = stooq.fetch_chart(ticker)
    return {"closes": d.get("closes", []), "volumes": d.get("volumes", []),
            "dates": d.get("dates", []), "dividend_adjusted": False, "source": "stooq"}


# --------------------------------------------------------------------------- #
#  Ladder 1 — momentum SHORT series: yahoo chart -> stooq (>=30 bars)          #
# --------------------------------------------------------------------------- #
def fetch_daily_series(ticker, rng="3mo", interval="1d"):
    """Daily closes/volumes/dates for the short indicators. Yahoo is often blocked
    from datacenter IPs, so Stooq (no key) keeps momentum alive on cloud hosts.
    A short Yahoo series is the last resort; raises only if both sources fail."""
    def stooq_tail():
        d = _clean_prices(stooq.fetch_chart(ticker))
        return {k: d[k][-STOOQ_TAIL:] for k in ("closes", "volumes", "dates")}

    tiers = (("yahoo chart", lambda: _clean_prices(
                 yahoo.fetch_chart(ticker, rng=rng, interval=interval))),
             ("stooq chart", stooq_tail))
    yahoo_short = None
    for source, d in _attempts(ticker, tiers):
        if d and len(d.get("closes", [])) >= SHORT_MIN_BARS:
            return d
        if source == "yahoo chart":
            yahoo_short = d
    if yahoo_short:
        return yahoo_short
    raise RuntimeError(f"no price data for {ticker} (yahoo+stooq)")


# --------------------------------------------------------------------------- #
#  Ladder 2 — momentum LONG adjusted series: yahoo -> fmp -> stooq (+cache)    #
# --------------------------------------------------------------------------- #
def _bars(series):
    return len((series or {}).get("closes") or [])


def _first_full_else_longest(attempts, full_bars):
    """The first payload with >= full_bars, else the first of the longest
    non-empty ones (None when every tier failed or came back empty)."""
    best = None
    for _source, pay in attempts:
        n = _bars(pay)
        if n >= full_bars:
            return pay
        if n and n > _bars(best):
            best = pay
    return best


def fetch_daily_adjusted(ticker, fmp_key="", rng="2y", full_bars=250, min_bars=60):
    """Adjusted daily closes (oldest->newest) for the momentum composite.

    Tiers, all dividend+split adjusted EXCEPT Stooq (split-only -> flagged):
        1) Yahoo adjclose   (free, full history; IP-block risk on cloud)
        2) FMP adjClose     (API key; spent only if Yahoo is short)
        3) Stooq            (split-only -> dividend_adjusted=False; no key)

    Returns the FIRST source with >= full_bars. Otherwise the longest of the live
    answers and today's cache, if it has >= min_bars (a tie goes to live), so a
    partial momentum still shows; then a stale cache (flagged). Never fabricates
    or back-fills. Every live answer returned is cached. Raises only when nothing
    reaches min_bars."""
    fresh = pricecache.read_fresh(ticker)
    n_fresh = _bars(fresh)
    if fresh and n_fresh >= full_bars:
        return _served_from_cache(fresh)
    # A same-day PARTIAL cache must not block a longer fetch, but FMP quota is not
    # re-spent to improve a partial already paid for today: only free tiers retry.
    spend_fmp = bool(fmp_key) and not (fresh and n_fresh >= min_bars)

    tiers = [("yahoo long chart", lambda: _cleaned(_yahoo_adjusted(ticker, rng)))]
    if spend_fmp:
        tiers.append(("fmp history", lambda: _cleaned(_fmp_adjusted(ticker, fmp_key))))
    tiers.append(("stooq long chart", lambda: _cleaned(_stooq_split_only(ticker))))

    live = _first_full_else_longest(_attempts(ticker, tiers), full_bars)
    n_live = _bars(live)
    if live and n_live >= full_bars:
        pricecache.write(ticker, live)
        return live
    if live and n_live >= min_bars and not (fresh and n_fresh > n_live):
        pricecache.write(ticker, live)
        return live
    if fresh and n_fresh >= min_bars:
        return _served_from_cache(fresh)
    stale = pricecache.read_any(ticker)
    if stale and _bars(stale) >= min_bars:
        return _served_from_cache(stale, stale=True)
    raise RuntimeError(f"no long price data for {ticker} (yahoo+fmp+stooq)")


# --------------------------------------------------------------------------- #
#  Ladder 3 — risk-desk RETURNS (+daily risk cache that never keeps failures)  #
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
    """Atomically replace the risk cache. Never raises; returns True only when the
    new content is actually on disk.

    On Windows `os.replace` onto a path another handle holds open raises
    PermissionError. Swallowing it would leave the OLDER cache in place, whose stale
    entries are then served back and degrade the run to the proxy. Our own Drive
    mirror no longer holds the file open, so the retry is a backstop for handles we
    do not own (antivirus, backup agents, a second instance)."""
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


def _good(entries):
    """Only GOOD series (n>0) are served or persisted: a cached failure would be
    served back all day and degrade the whole matrix to the proxy 0.60."""
    return {t: v for t, v in entries.items() if (v or {}).get("n")}


def _save(key, data):
    save_cache({"key": key, "saved": _today(), "data": data})


def _mirror_to_drive():
    """Background push of the shared cache. It is rebuildable, so no push guard;
    a no-op when Drive isn't configured."""
    try:
        import store_sync
        store_sync.schedule_push_named(RISK_CACHE_PATH, REMOTE_CACHE_NAME)
    except Exception:
        pass


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
            return _good(c.get("data") or {}) or None
    except Exception:
        pass
    return None


def _fetch_one(ticker, fmp_key, may_use_fmp, days):
    """Return (series_dict_or_None, source, used_fmp_call). Never raises.

    Opens the pool momentum already paid for (pricecache holds yahoo-adjusted
    series for every holding after each Run Daily), so the risk desk does not
    depend on FMP's per-symbol free-tier entitlement. The source name of the tier
    that answered is the series' `source`; nothing answering means "proxy"."""
    tail = days + 1                     # need `days` returns -> days+1 closes

    def cached(read):
        pc = read(ticker)
        if _bars(pc) >= RETURNS_MIN_CLOSES:
            return {"closes": pc["closes"][-tail:],
                    "dates": (pc.get("dates") or [])[-tail:]}, False
        return None

    def yahoo_direct():
        """One quick try for names not in the pool yet (a new holding, the
        benchmark refs); written back so the next Run Daily reuses it for free."""
        pay = _yahoo_adjusted(ticker, "2y", retries=1, timeout=8)
        if _bars(pay) >= RETURNS_MIN_CLOSES:
            pricecache.write(ticker, pay)
            return {"closes": pay["closes"][-tail:], "dates": pay["dates"][-tail:]}, False
        return None

    def fmp_adjusted():
        ph = fmp.parse_history(fmp.fetch_history(ticker, fmp_key, days=days))
        return (ph, True) if ph.get("closes") else None

    def stooq_any():
        sc = stooq.fetch_chart(ticker)
        return (sc, False) if sc.get("closes") else None

    tiers = [("yahoo-cache", lambda: cached(pricecache.read_fresh)),
             ("yahoo", yahoo_direct)]
    if may_use_fmp and fmp_key:
        tiers.append(("fmp", fmp_adjusted))
    tiers += [("stooq", stooq_any),
              # an old realized series still beats an assumed 0.60 correlation
              ("yahoo-cache-stale", lambda: cached(pricecache.read_any))]
    for source, got in _attempts(ticker, tiers, quiet=True):
        if got is not None:
            series, used_fmp = got
            return series, source, used_fmp
    return None, "proxy", False


def _returns_record(series, source):
    """One risk-desk entry from a (closes, dates) series, or the empty proxy entry."""
    rets = risk.daily_returns(series["closes"]) if series else []
    dates = (series or {}).get("dates") or []
    # Carry the DATE of every return: pairing series by position turns a one-bar
    # source lag into a lag-1 cross-correlation. daily_returns() emits one return
    # per price gap, so the return dates are the close dates minus the first.
    rdates = list(dates[1:])
    if len(rdates) != len(rets):                # never ship a mismatched pair
        rdates = []
    return {
        "returns": rets,
        "dates": rdates,
        "vol": round(risk.annualized_vol(rets), 4) if rets else None,
        "source": source,
        "as_of": dates[-1] if dates else None,
        "n": len(rets),
    }


def _local_good(key, tickers):
    cache = load_cache()
    cached = (cache.get("data") or {}) if cache.get("key") == key else {}
    return _good({t: cached.get(t) for t in tickers})


def _fetch_missing(todo, fmp_key, quota_used, quota_cap, days, prefer_fmp):
    """Run `_fetch_one` per name; returns ({ticker: record}, FMP calls spent).
    Quota rule: spend an FMP call only while batch.usable_quota has room."""
    records, calls = {}, 0
    for t in todo:
        may_use_fmp = bool(prefer_fmp and fmp_key and calls < batch.usable_quota(quota_used, quota_cap))
        series, source, used = _fetch_one(t, fmp_key, may_use_fmp, days)
        if used:
            calls += 1
        records[t] = _returns_record(series, source)
    return records, calls


def fetch_returns(tickers, fmp_key="", quota_used=0, quota_cap=250,
                  days=DEFAULT_DAYS, prefer_fmp=True, use_cache=True):
    """{ticker: {returns, dates, vol, source, as_of, n}} for the risk desk.
    Returns (data, fmp_calls, meta); never raises.

    Each name comes from the first that has it: today's local risk cache, the
    Drive-shared copy another machine paid for, then `_fetch_one`'s ladder."""
    tickers = list(dict.fromkeys(t for t in tickers if t))      # de-dupe, keep order
    key = holdings_key(tickers)
    good = _local_good(key, tickers) if use_cache else {}
    todo = [t for t in tickers if t not in good]
    if use_cache and not todo:
        return {t: good[t] for t in tickers}, 0, {"cache": "hit", "key": key}

    drive_merged = 0
    remote = _remote_good(key) if use_cache else None
    if remote:
        good.update({t: remote[t] for t in todo if t in remote})
        todo = [t for t in todo if t not in good]
        drive_merged = len(remote)
        if not todo:
            _save(key, dict(good))
            return ({t: good[t] for t in tickers}, 0,
                    {"cache": "drive-hit", "key": key, "quota_degraded": False,
                     "drive_cache_merged": drive_merged})

    fetched, calls = _fetch_missing(todo, fmp_key, quota_used, quota_cap, days, prefer_fmp)
    data = {**good, **fetched}
    if use_cache:
        good_now = _good(data)
        _save(key, good_now)
        if good_now:
            _mirror_to_drive()
    degraded = any(v["source"] != "fmp" for v in data.values()) and bool(fmp_key)
    return data, calls, {"cache": ("partial" if good else "miss"), "key": key,
                         "quota_degraded": degraded, "drive_cache_merged": drive_merged}
