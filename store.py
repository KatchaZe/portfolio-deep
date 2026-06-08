"""
Local JSON store. Holdings (with shares + avg cost) and the cached fundamentals
+ engine results are persisted only for portfolio tickers. Watchlist keeps names
only (data re-fetched each run). Removing a holding deletes all its cached data.
"""
import os
import json
import datetime as dt

import config

PATH = os.path.join(config.DATA_DIR, "portfolio.json")

_DEFAULT = {"holdings": {}, "watchlist": [], "facts": {}, "results": {},
            "momentum": {}, "fmp_usage": {}, "updated": {},
            "rev_snapshots": {}, "rev_surprises": {}}


def load():
    os.makedirs(config.DATA_DIR, exist_ok=True)
    if not os.path.exists(PATH):
        return json.loads(json.dumps(_DEFAULT))      # fresh deep copy of defaults
    with open(PATH, encoding="utf-8") as fh:
        s = json.load(fh)
    for k, v in _DEFAULT.items():
        s.setdefault(k, json.loads(json.dumps(v)))
    return s


def save(s):
    os.makedirs(config.DATA_DIR, exist_ok=True)
    with open(PATH, "w", encoding="utf-8") as fh:
        json.dump(s, fh, indent=2, ensure_ascii=False)


def today():
    return dt.date.today().isoformat()


# --- holdings -------------------------------------------------------------- #
def set_holding(s, ticker, shares=None, avg_cost=None):
    t = ticker.upper().strip()
    h = s["holdings"].setdefault(t, {"shares": 0, "avg_cost": 0.0, "added": today()})
    if shares is not None:
        h["shares"] = float(shares)
    if avg_cost is not None:
        h["avg_cost"] = float(avg_cost)
    return h


def remove_holding(s, ticker):
    t = ticker.upper().strip()
    for k in ("holdings", "facts", "results", "momentum", "rev_snapshots", "rev_surprises"):
        s.get(k, {}).pop(t, None)


# --- watchlist ------------------------------------------------------------- #
def add_watch(s, ticker):
    t = ticker.upper().strip()
    if t and t not in s["watchlist"]:
        s["watchlist"].append(t)


def remove_watch(s, ticker):
    t = ticker.upper().strip()
    if t in s["watchlist"]:
        s["watchlist"].remove(t)


# --- FMP quota counter ----------------------------------------------------- #
def add_fmp_calls(s, n):
    d = today()
    s["fmp_usage"][d] = s["fmp_usage"].get(d, 0) + n
    # keep only last 7 days
    cutoff = (dt.date.today() - dt.timedelta(days=7)).isoformat()
    s["fmp_usage"] = {k: v for k, v in s["fmp_usage"].items() if k >= cutoff}
    return s["fmp_usage"][d]


def fmp_used_today(s):
    return s["fmp_usage"].get(today(), 0)
