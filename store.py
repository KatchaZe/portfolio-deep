"""
Local JSON store. Holdings (with shares + avg cost) and the cached fundamentals
+ engine results are persisted only for portfolio tickers. Watchlist keeps names
only (data re-fetched each run). Removing a holding deletes all its cached data.
"""
import os
import re
import json
import threading
import datetime as dt

import config
from sources import gdrive_store

_TICKER_RE = re.compile(r"[^A-Z0-9.\-]")

# Has the remote (Google Drive) copy been pulled into the local file yet this
# process? We pull once, lazily, on the first load() so a cold-started Render
# instance restores the portfolio before serving anything.
_drive_pulled = False


def clean_ticker(t):
    """Normalize a user-typed ticker: uppercase, strip, drop any non-ticker
    characters (e.g. a stray Thai vowel that produced 'ืNVDA'). Returns '' if nothing valid remains."""
    return _TICKER_RE.sub("", (t or "").upper().strip())

PATH = os.path.join(config.DATA_DIR, "portfolio.json")

# Serializes load->mutate->save so concurrent requests can't clobber each other
# (lost-update). Re-entrant so a job may nest store calls. Held by app.py around
# each mutating job; reads are safe lock-free because save() is atomic.
LOCK = threading.RLock()

_DEFAULT = {"holdings": {}, "watchlist": [], "facts": {}, "results": {},
            "momentum": {}, "fmp_usage": {}, "updated": {},
            "rev_snapshots": {}, "rev_surprises": {}}


def load():
    os.makedirs(config.DATA_DIR, exist_ok=True)
    # On the first load of this process, restore from Google Drive (if configured)
    # so a cold-started / redeployed instance gets its portfolio back. No-op when
    # Drive isn't set up; never raises (falls back to whatever is on local disk).
    global _drive_pulled
    if not _drive_pulled:
        _drive_pulled = True
        gdrive_store.drive_pull(PATH)
    if not os.path.exists(PATH):
        return json.loads(json.dumps(_DEFAULT))      # fresh deep copy of defaults
    with open(PATH, encoding="utf-8") as fh:
        s = json.load(fh)
    for k, v in _DEFAULT.items():
        s.setdefault(k, json.loads(json.dumps(v)))
    return s


def save(s):
    """Atomic write: dump to a temp file then os.replace() (atomic on the same
    filesystem) so a crash mid-write can never corrupt the live store."""
    os.makedirs(config.DATA_DIR, exist_ok=True)
    tmp = f"{PATH}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(s, fh, indent=2, ensure_ascii=False)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, PATH)
    # Mirror to Google Drive (if configured) so the data survives a restart.
    # Best-effort: a Drive failure is logged but never breaks the local save.
    gdrive_store.drive_push(PATH)


def today():
    return dt.date.today().isoformat()


# --- holdings -------------------------------------------------------------- #
def set_holding(s, ticker, shares=None, avg_cost=None):
    t = clean_ticker(ticker)
    h = s["holdings"].setdefault(t, {"shares": 0, "avg_cost": 0.0, "added": today()})
    if shares is not None:
        h["shares"] = float(shares)
    if avg_cost is not None:
        h["avg_cost"] = float(avg_cost)
    return h


def remove_holding(s, ticker):
    t = clean_ticker(ticker)
    for k in ("holdings", "facts", "results", "momentum", "rev_snapshots", "rev_surprises"):
        s.get(k, {}).pop(t, None)


# --- watchlist ------------------------------------------------------------- #
def add_watch(s, ticker):
    t = clean_ticker(ticker)
    if t and t not in s["watchlist"]:
        s["watchlist"].append(t)


def remove_watch(s, ticker):
    t = clean_ticker(ticker)
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
