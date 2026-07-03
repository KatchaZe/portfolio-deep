"""
Local JSON store. Holdings (with shares + avg cost) and the cached fundamentals
+ engine results are persisted only for portfolio tickers. Watchlist keeps names
only (data re-fetched each run). Removing a holding deletes all its cached data.
"""
import os
import re
import json
import time
import threading
import datetime as dt

import config
from sources import gdrive_store

_TICKER_RE = re.compile(r"[^A-Z0-9.\-]")

# Drive pull state for THIS process:
#   None      -> not attempted yet
#   "pulled"  -> remote restored onto local disk  (push allowed)
#   "absent"  -> Drive reachable, no remote yet   (push allowed - first run)
#   "error"   -> pull FAILED. Push is BLOCKED until a retry succeeds, because a
#                cold-started (empty-disk) instance that failed to pull would
#                otherwise push an EMPTY store over the good Drive backup -
#                this was the "portfolio wiped after 30-60 min" data-loss bug.
_drive_pull_state = None
_drive_last_try = 0.0
_DRIVE_RETRY_SECS = 60           # retry a failed pull at most once a minute


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


def _ensure_drive_pull():
    """Restore from Google Drive (if configured) so a cold-started / redeployed
    instance gets its portfolio back. Unlike the old one-shot version, a FAILED
    pull is retried (throttled) on later loads instead of being latched forever
    with an empty store. Never raises."""
    global _drive_pull_state, _drive_last_try
    if not gdrive_store.enabled():
        return
    if _drive_pull_state in ("pulled", "absent"):
        return                                     # already restored this process
    now = time.time()
    if _drive_pull_state == "error" and now - _drive_last_try < _DRIVE_RETRY_SECS:
        return                                     # throttle retries
    _drive_last_try = now
    _drive_pull_state = gdrive_store.drive_pull(PATH)


def load():
    os.makedirs(config.DATA_DIR, exist_ok=True)
    _ensure_drive_pull()
    if not os.path.exists(PATH):
        return json.loads(json.dumps(_DEFAULT))      # fresh deep copy of defaults
    with open(PATH, encoding="utf-8") as fh:
        s = json.load(fh)
    for k, v in _DEFAULT.items():
        s.setdefault(k, json.loads(json.dumps(v)))
    return s


# --- background Drive push (C1 fix) ----------------------------------------- #
# save() must NOT do network I/O: callers hold LOCK around save(), and a slow /
# hung Drive upload would freeze every mutating endpoint behind that lock.
# A single daemon worker pushes AFTER (outside) the local write; repeated saves
# coalesce via a pending flag — the newest on-disk file always wins because the
# worker re-reads PATH at push time.
_push_lock = threading.Lock()
_push_pending = False
_push_thread = None


def _push_worker():
    global _push_pending
    while True:
        with _push_lock:
            if not _push_pending:
                return
            _push_pending = False
        gdrive_store.drive_push(PATH)          # never raises (best-effort mirror)


def _schedule_push():
    global _push_pending, _push_thread
    with _push_lock:
        _push_pending = True
        if _push_thread is not None and _push_thread.is_alive():
            return                             # running worker will pick it up
        _push_thread = threading.Thread(target=_push_worker,
                                        name="drive-push", daemon=True)
        _push_thread.start()


def wait_push(timeout=10.0):
    """Block until the push worker is idle — used by tests (deterministic
    assertions) and available for a graceful shutdown hook."""
    t = _push_thread
    if t is not None and t.is_alive():
        t.join(timeout)


def save(s):
    """Atomic write: dump to a temp file then os.replace() (atomic on the same
    filesystem) so a crash mid-write can never corrupt the live store.
    The Google-Drive mirror runs on a background worker (see _schedule_push) so
    a Drive outage can never stall a request holding LOCK."""
    os.makedirs(config.DATA_DIR, exist_ok=True)
    tmp = f"{PATH}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(s, fh, indent=2, ensure_ascii=False)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, PATH)
    # Mirror to Google Drive (if configured) so the data survives a restart.
    # Best-effort: a Drive failure is logged but never breaks the local save.
    if not gdrive_store.enabled():
        return
    # GUARD (data-loss fix): if Drive is configured but this process has never
    # managed a successful pull ("error" or not yet attempted), do NOT push -
    # on Render's ephemeral disk the local store may be a fresh EMPTY default,
    # and pushing it would overwrite the good remote backup permanently.
    if _drive_pull_state not in ("pulled", "absent"):
        import logging
        logging.getLogger("portfolio.store").error(
            "Drive push BLOCKED: initial pull has not succeeded (state=%s) - "
            "keeping the remote backup intact. Will retry the pull on next load.",
            _drive_pull_state)
        gdrive_store.STATUS["push_result"] = "skipped"
        return
    _schedule_push()


def persist_status():
    """Drive persistence health for the UI badge (/api/persist)."""
    st = gdrive_store.status()
    st["pull_state"] = _drive_pull_state
    st["push_blocked"] = bool(gdrive_store.enabled()
                              and _drive_pull_state not in ("pulled", "absent"))
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
