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
import store_sync
from sources import gdrive_store

_TICKER_RE = re.compile(r"[^A-Z0-9.\-]")

# Drive pull/push state + worker moved to store_sync.py (2026-07-19 split):
# store.py stays pure local-disk persistence; the mirror subsystem (pull
# state machine, background push worker, data-loss guard) lives there.


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
            "rev_snapshots": {}, "rev_surprises": {},
            # Monthly manual assumptions (ERP / MARKET_PE) entered via the
            # dashboard "Assumptions" form. Lives in portfolio.json -> mirrored
            # to Google Drive by the normal save() flow, restored on cold start.
            "assumptions": {}}


def _apply_assumptions(s):
    """Overlay store-backed manual assumptions onto the config module so every
    call-time reader (engine Ke/WACC, validate band, market overlay) uses the
    freshest value WITHOUT a restart. No-op when nothing was saved. Never raises."""
    a = s.get("assumptions") or {}
    try:
        if a.get("erp"):
            config.ERP = float(a["erp"])
            config.ERP_AS_OF = a.get("erp_as_of") or config.ERP_AS_OF
        if a.get("market_pe"):
            config.MARKET_PE = float(a["market_pe"])
            config.MARKET_PE_AS_OF = a.get("market_pe_as_of") or config.MARKET_PE_AS_OF
    except Exception:
        pass


def set_assumptions(s, erp_pct=None, market_pe=None):
    """Persist manual monthly assumptions (dashboard form). Values are validated
    by the caller (app.py). Stamps as-of = current YYYY-MM + source=manual.
    Returns the stored assumptions dict."""
    a = s.setdefault("assumptions", {})
    month = dt.date.today().isoformat()[:7]
    if erp_pct is not None:
        a["erp"] = round(float(erp_pct) / 100.0, 5)
        a["erp_as_of"] = month
    if market_pe is not None:
        a["market_pe"] = round(float(market_pe), 2)
        a["market_pe_as_of"] = month
    a["updated"] = dt.date.today().isoformat()
    a["source"] = "manual"
    _apply_assumptions(s)
    return a


def load():
    os.makedirs(config.DATA_DIR, exist_ok=True)
    store_sync.ensure_pull(PATH)     # Drive restore on cold start (see store_sync)
    if not os.path.exists(PATH):
        return json.loads(json.dumps(_DEFAULT))      # fresh deep copy of defaults
    with open(PATH, encoding="utf-8") as fh:
        s = json.load(fh)
    for k, v in _DEFAULT.items():
        s.setdefault(k, json.loads(json.dumps(v)))
    _apply_assumptions(s)        # manual ERP/MARKET_PE override -> config (call-time readers)
    return s


# Background Drive push + the data-loss guard live in store_sync (C1 fix).
wait_push = store_sync.wait_push      # kept as store API (tests / shutdown hook)


def save(s):
    """Atomic write: dump to a temp file then os.replace() (atomic on the same
    filesystem) so a crash mid-write can never corrupt the live store.
    The Google-Drive mirror runs on a background worker (see store_sync.schedule_push) so
    a Drive outage can never stall a request holding LOCK."""
    os.makedirs(config.DATA_DIR, exist_ok=True)
    tmp = f"{PATH}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(s, fh, indent=2, ensure_ascii=False)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, PATH)
    # Mirror to Google Drive (if configured) — best-effort, background worker,
    # data-loss guard included. The whole subsystem lives in store_sync now.
    store_sync.schedule_push(PATH)


def persist_status():
    """Drive persistence health for the UI badge (/api/persist)."""
    st = gdrive_store.status()
    st.update(store_sync.status())
    return st


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
    # keep only ~the last week (8 dated entries incl. today)
    cutoff = (dt.date.today() - dt.timedelta(days=7)).isoformat()
    s["fmp_usage"] = {k: v for k, v in s["fmp_usage"].items() if k >= cutoff}
    return s["fmp_usage"][d]


def fmp_used_today(s):
    return s["fmp_usage"].get(today(), 0)
