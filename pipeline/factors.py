"""
Factor data for the Risk Desk's factor exposure (domain/risk/factors.py).

The Ken French files update monthly, so they are kept in CACHE_DIR/ff_factors.json and
fetched again only when the copy is over REFRESH_DAYS old. A failed fetch keeps the
old copy; with no copy at all the panel says so.
"""
import datetime as dt
import json
import logging
import os

import config

log = logging.getLogger(__name__)
PATH = os.path.join(config.CACHE_DIR, "ff_factors.json")
REFRESH_DAYS = 30


def _load():
    try:
        with open(PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def factors(fetch=None, today=None):
    """{date: {...}} or {}. `fetch` () -> factors; without one only the cache is read."""
    cached = _load()
    today = today or dt.date.today()
    fresh = cached.get("fetched") and (today - dt.date.fromisoformat(cached["fetched"])).days < REFRESH_DAYS
    if fetch is not None and not fresh:
        try:
            data = fetch()
            cached = {"fetched": today.isoformat(), "data": data}
            os.makedirs(os.path.dirname(PATH), exist_ok=True)
            tmp = PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(cached, fh)
            os.replace(tmp, PATH)
        except Exception as e:
            log.warning("Ken French factors unavailable (cached copy used if any): %s", e)
    return cached.get("data") or {}
