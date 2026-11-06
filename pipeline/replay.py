"""
Window returns for the historical replays (domain/risk/stress.historical_replay).

A past window's return never changes, so each (symbol, episode) is fetched once and
kept in CACHE_DIR/replay_windows.json, a "did not trade then" answer (None) included.
A failed fetch is not cached: the next build tries again.
"""
import json
import logging
import os

import config
from domain import risk as riskeng

log = logging.getLogger(__name__)
PATH = os.path.join(config.CACHE_DIR, "replay_windows.json")
_NA = "n/a"


def _load():
    try:
        with open(PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save(cache):
    try:
        os.makedirs(os.path.dirname(PATH), exist_ok=True)
        tmp = PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cache, fh)
        os.replace(tmp, PATH)
    except OSError as e:
        log.warning("replay cache not saved: %s", e)


def window_returns(symbols, fetch=None, scenarios=None, max_fetches=60, workers=8):
    """{episode key: {symbol: return}} for every symbol that traded in the episode.
    `fetch` (symbol, start, end) -> return | None; without one only the cache is read.
    Uncached windows are fetched in parallel, at most `max_fetches` per call (the
    rest on the next build), so a first run cannot stall the Risk Desk."""
    from concurrent.futures import ThreadPoolExecutor
    scenarios = scenarios or riskeng.HISTORICAL
    cache = _load()
    todo = [(sym, scn) for scn in scenarios for sym in dict.fromkeys(symbols)
            if f"{sym}|{scn['key']}" not in cache][:max_fetches] if fetch else []

    def one(job):
        sym, scn = job
        try:
            return job, fetch(sym, scn["start"], scn["end"]), True
        except Exception as e:
            log.info("replay %s %s unavailable: %s", sym, scn["key"], e)
            return job, None, False
    if todo:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for (sym, scn), r, ok in ex.map(one, todo):
                if ok:
                    cache[f"{sym}|{scn['key']}"] = _NA if r is None else round(r, 6)
        _save(cache)
    out = {}
    for scn in scenarios:
        got = out.setdefault(scn["key"], {})
        for sym in dict.fromkeys(symbols):
            v = cache.get(f"{sym}|{scn['key']}")
            if v is not None and v != _NA:
                got[sym] = v
    return out
