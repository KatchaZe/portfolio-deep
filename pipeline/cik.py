"""SEC ticker -> (CIK, company name), from config.CIKS or SEC's cached ticker map."""
import os
import json
import time
import logging
import threading

import config

log = logging.getLogger("portfolio.cik")
_cik_map = None
_cik_lock = threading.Lock()       # parallel workers share the lazy map
MAP_TTL_SECS = 30 * 86400          # the map changes rarely


def resolve_cik(ticker):
    global _cik_map
    t = ticker.upper().strip()
    if t in config.CIKS:
        return config.CIKS[t], None
    with _cik_lock:
        if _cik_map is None:
            _cik_map = _load_cik_map()
    v = _cik_map.get(t)
    return (v[0], v[1]) if v else (None, None)


def _index(raw):
    return {row["ticker"].upper(): (str(row["cik_str"]).zfill(10), row.get("title"))
            for row in raw.values()}


def _load_cik_map():
    cache = os.path.join(config.CACHE_DIR, "company_tickers.json")
    try:
        if os.path.exists(cache) and (time.time() - os.path.getmtime(cache)) < MAP_TTL_SECS:
            with open(cache, encoding="utf-8") as fh:
                return _index(json.load(fh))
    except Exception as e:
        log.warning("CIK map cache read failed: %s", e)
    try:
        import requests
        r = requests.get("https://www.sec.gov/files/company_tickers.json",
                         headers={"User-Agent": config.SEC_USER_AGENT}, timeout=20)
        raw = r.json()
        try:
            os.makedirs(config.CACHE_DIR, exist_ok=True)
            tmp = f"{cache}.tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(raw, fh)
            os.replace(tmp, cache)
        except Exception as e:
            log.warning("CIK map cache write failed: %s", e)
        return _index(raw)
    except Exception as e:
        log.warning("CIK map fetch failed: %s", e)
        return {}
