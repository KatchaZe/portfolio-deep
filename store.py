"""
Local JSON store (data/portfolio.json). Holdings (shares + avg cost), cached
fundamentals and engine results are kept only for portfolio tickers; the watchlist
keeps names only. Removing a holding deletes all its cached data.

This module owns the local file. The Drive mirror policy lives in store_sync.py:
load() asks it for the cold-start pull, save() hands it the push, and every user
mutation below calls store_sync.mark_local_edit() so a later pull cannot overwrite it.
"""
import os
import re
import copy
import json
import threading
import contextlib
import datetime as dt

import config
import store_sync

_TICKER_RE = re.compile(r"[^A-Z0-9.\-]")

PATH = os.path.join(config.DATA_DIR, "portfolio.json")

# Serializes load -> mutate -> save so concurrent requests can't lose updates.
# Re-entrant so a job may nest store calls. Reads are safe lock-free because
# save() is atomic.
LOCK = threading.RLock()

_DEFAULT = {"holdings": {}, "watchlist": [], "facts": {}, "results": {},
            "momentum": {}, "fmp_usage": {}, "updated": {},
            "rev_snapshots": {}, "rev_surprises": {},
            # manual monthly ERP / MARKET_PE from the dashboard form
            "assumptions": {}}

_PER_TICKER = ("holdings", "facts", "results", "momentum", "rev_snapshots", "rev_surprises")


def clean_ticker(t):
    """Uppercase, strip, drop non-ticker characters (e.g. a stray Thai vowel that
    produced 'ืNVDA'). Returns '' if nothing valid remains."""
    return _TICKER_RE.sub("", (t or "").upper().strip())


def today():
    return dt.date.today().isoformat()


# --- file ------------------------------------------------------------------ #
def load():
    os.makedirs(config.DATA_DIR, exist_ok=True)
    store_sync.ensure_pull(PATH)
    if not os.path.exists(PATH):
        return copy.deepcopy(_DEFAULT)
    with open(PATH, encoding="utf-8") as fh:
        s = json.load(fh)
    for k, v in _DEFAULT.items():
        s.setdefault(k, copy.deepcopy(v))
    _apply_assumptions(s)
    return s


def save(s):
    """Atomic write (temp file + os.replace) so a crash mid-write can never corrupt
    the live store, then queue the Drive mirror on its background worker so a Drive
    outage can never stall a request holding LOCK.

    This does NOT count as a user edit: save() also persists the empty default store
    after a failed cold-start pull, and that must never outrank a good Drive backup.
    """
    os.makedirs(config.DATA_DIR, exist_ok=True)
    tmp = f"{PATH}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(s, fh, indent=2, ensure_ascii=False)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, PATH)
    store_sync.schedule_push(PATH)


@contextlib.contextmanager
def transaction():
    """`with transaction() as s:` loads under LOCK, yields the store, and saves it
    on a clean exit. An exception skips the save. Keep slow network work outside."""
    with LOCK:
        s = load()
        yield s
        save(s)


# --- assumptions ----------------------------------------------------------- #
def _apply_assumptions(s):
    """Overlay saved manual assumptions onto the config module so every call-time
    reader (engine Ke/WACC, validate band, market overlay) uses them without a
    restart. Never raises."""
    a = s.get("assumptions") or {}
    try:
        # newest dated ERP wins: a manual value saved this month beats last month's
        # page, and the page beats a manual value it has since superseded
        from sources import damodaran
        live = damodaran.cached_implied_erp(config.CACHE_DIR)
        manual_as_of = (a.get("erp_as_of") or "") if a.get("erp") else None
        if live and (manual_as_of is None or live["as_of"] > manual_as_of):
            config.ERP, config.ERP_AS_OF = float(live["erp"]), live["as_of"]
            config.ERP_SOURCE = "damodaran-live"
        elif a.get("erp"):
            config.ERP = float(a["erp"])
            config.ERP_AS_OF = a.get("erp_as_of") or config.ERP_AS_OF
            config.ERP_SOURCE = "manual"
        if a.get("market_pe"):
            config.MARKET_PE = float(a["market_pe"])
            config.MARKET_PE_AS_OF = a.get("market_pe_as_of") or config.MARKET_PE_AS_OF
    except Exception:
        pass
    # revenue mixes typed on the dashboard; replaced wholesale so a cleared one goes
    config.REVENUE_MIX_MANUAL = {t: dict(m) for t, m in (a.get("revenue_mix") or {}).items()
                                 if isinstance(m, dict) and m}


def reapply_assumptions():
    """Re-run the overlay after a fresh implied ERP was cached (network paths)."""
    _apply_assumptions(load())


def set_assumptions(s, erp_pct=None, market_pe=None):
    """Store the manual monthly values (validated by the caller), stamped with the
    current YYYY-MM. Returns the stored assumptions dict."""
    store_sync.mark_local_edit()
    a = s.setdefault("assumptions", {})
    month = today()[:7]
    if erp_pct is not None:
        a["erp"] = round(float(erp_pct) / 100.0, 5)
        a["erp_as_of"] = month
    if market_pe is not None:
        a["market_pe"] = round(float(market_pe), 2)
        a["market_pe_as_of"] = month
    a["updated"] = today()
    a["source"] = "manual"
    _apply_assumptions(s)
    return a


def parse_revenue_mix(text):
    """'US 56, China 20, Western Europe 24' -> ({market: weight}, None) or (None, error).
    Markets are country names, ISO-2 codes or a region country_risk knows."""
    from sources import country_risk
    mix, bad = {}, []
    for part in [p.strip() for p in (text or "").split(",") if p.strip()]:
        m = re.match(r"^(.*?)[\s:=]+([0-9]+(?:\.[0-9]+)?)%?$", part)
        if not m:
            return None, f"cannot read '{part}' (write: market weight, e.g. 'Japan 12')"
        market, w = m.group(1).strip(), float(m.group(2))
        if country_risk.market_crp(market, country_risk.CRP_FALLBACK) is None:
            bad.append(market)
        elif w > 0:
            mix[market] = mix.get(market, 0.0) + w
    if bad:
        return None, "unknown market: " + ", ".join(bad)
    return (mix, None) if mix else (None, "no market with a positive weight")


def set_revenue_mix(s, ticker, mix):
    """Save (or, with mix None, clear) a ticker's dashboard revenue mix."""
    store_sync.mark_local_edit()
    a = s.setdefault("assumptions", {})
    mixes = a.setdefault("revenue_mix", {})
    if mix:
        mixes[ticker] = mix
    else:
        mixes.pop(ticker, None)
    a["updated"] = today()
    _apply_assumptions(s)
    return mixes


# --- holdings -------------------------------------------------------------- #
def set_holding(s, ticker, shares=None, avg_cost=None):
    store_sync.mark_local_edit()
    t = clean_ticker(ticker)
    h = s["holdings"].setdefault(t, {"shares": 0, "avg_cost": 0.0, "added": today()})
    if shares is not None:
        h["shares"] = float(shares)
    if avg_cost is not None:
        h["avg_cost"] = float(avg_cost)
    return h


def remove_holding(s, ticker):
    store_sync.mark_local_edit()
    t = clean_ticker(ticker)
    for k in _PER_TICKER:
        s.get(k, {}).pop(t, None)


# --- watchlist ------------------------------------------------------------- #
def add_watch(s, ticker):
    store_sync.mark_local_edit()
    t = clean_ticker(ticker)
    if t and t not in s["watchlist"]:
        s["watchlist"].append(t)


def remove_watch(s, ticker):
    store_sync.mark_local_edit()
    t = clean_ticker(ticker)
    if t in s["watchlist"]:
        s["watchlist"].remove(t)


# --- FMP quota counter ----------------------------------------------------- #
def commit_watch_cache(s, entries):
    """Merge the watchlist's last-good subset + weekly peer fit, and drop entries for
    names no longer watched. An app write, not a user edit (not marked dirty)."""
    wc = s.setdefault("watch_cache", {})
    wc.update(entries or {})
    for t in [t for t in wc if t not in (s.get("watchlist") or [])]:
        del wc[t]


def add_fmp_calls(s, n):
    d = today()
    s["fmp_usage"][d] = s["fmp_usage"].get(d, 0) + n
    # keep only ~the last week (8 dated entries incl. today)
    cutoff = (dt.date.today() - dt.timedelta(days=7)).isoformat()
    s["fmp_usage"] = {k: v for k, v in s["fmp_usage"].items() if k >= cutoff}
    return s["fmp_usage"][d]


def fmp_used_today(s):
    return s["fmp_usage"].get(today(), 0)
