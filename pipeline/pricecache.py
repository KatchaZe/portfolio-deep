"""
Disk cache for the long (2y) adjusted price series used by the momentum composite (R4).

Two jobs:
  1. skip re-fetching the same ticker more than once per trading day  -> saves
     Yahoo/FMP calls and protects the FMP free-tier quota, and
  2. survive a total source outage by serving the last good series (flagged stale)
     so momentum still renders -> "open anytime, anywhere".

Cache lives in data/cache/ (gitignored). Every operation swallows its own errors:
the cache must NEVER break a refresh. fetch (disk I/O) and the freshness policy are
kept separate from the caller so they unit-test offline with an injected dir/now.
"""
import json
import os
import time

import config


def _path(ticker, cache_dir=None):
    d = cache_dir or config.CACHE_DIR
    safe = "".join(ch for ch in (ticker or "").upper() if ch.isalnum() or ch in "-._")
    return os.path.join(d, f"prices_{safe or 'X'}.json")


def write(ticker, payload, cache_dir=None, now=None):
    """Persist {saved, payload} atomically. Returns True on success, never raises."""
    try:
        d = cache_dir or config.CACHE_DIR
        os.makedirs(d, exist_ok=True)
        rec = {"saved": float(now if now is not None else time.time()), "payload": payload}
        p = _path(ticker, d)
        tmp = p + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(rec, fh)
        os.replace(tmp, p)
        return True
    except Exception:
        return False


def _read_rec(ticker, cache_dir=None):
    try:
        with open(_path(ticker, cache_dir)) as fh:
            return json.load(fh)
    except Exception:
        return None


def read_fresh(ticker, ttl_hours=None, cache_dir=None, now=None):
    """Cached payload if saved within ttl_hours (default config.PRICE_CACHE_TTL_HOURS),
    else None. Used to skip a same-day re-fetch."""
    rec = _read_rec(ticker, cache_dir)
    if not rec:
        return None
    ttl = (config.PRICE_CACHE_TTL_HOURS if ttl_hours is None else ttl_hours) * 3600
    age = float(now if now is not None else time.time()) - rec.get("saved", 0)
    return rec.get("payload") if 0 <= age <= ttl else None


def read_any(ticker, cache_dir=None):
    """Cached payload regardless of age (stale fallback when every live source fails),
    else None."""
    rec = _read_rec(ticker, cache_dir)
    return rec.get("payload") if rec else None
