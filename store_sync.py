"""
store_sync.py — Google-Drive mirror subsystem for the local store (split out of
store.py 2026-07-19 so store.py stays pure local-disk persistence).

Owns BOTH directions plus the data-loss guard:
  * pull-on-cold-start state machine (retry throttled after a failed pull);
  * background push worker (C1: save() must never do network I/O under LOCK);
  * GUARD: push is BLOCKED until one pull has succeeded ("pulled"/"absent") —
    a cold-started empty-disk instance must never overwrite the good Drive
    backup (the "portfolio wiped after 30-60 min" incident).

Per-process state — the app must run as exactly ONE worker (H5).
"""
import time
import logging
import threading

from sources import gdrive_store

log = logging.getLogger("portfolio.store_sync")

# Pull state for THIS process:
#   None      -> not attempted yet
#   "pulled"  -> remote restored onto local disk  (push allowed)
#   "absent"  -> Drive reachable, no remote yet   (push allowed - first run)
#   "error"   -> pull FAILED -> push BLOCKED until a retry succeeds
_pull_state = None
_last_try = 0.0
RETRY_SECS = 60           # retry a failed pull at most once a minute


def ensure_pull(path):
    """Restore `path` from Google Drive (if configured) on cold start. A FAILED
    pull is retried (throttled) on later calls instead of latching forever.
    Never raises."""
    global _pull_state, _last_try
    if not gdrive_store.enabled():
        return
    if _pull_state in ("pulled", "absent"):
        return                                     # already restored this process
    now = time.time()
    if _pull_state == "error" and now - _last_try < RETRY_SECS:
        return                                     # throttle retries
    _last_try = now
    _pull_state = gdrive_store.drive_pull(path)


# --- background push (C1 fix) ---------------------------------------------- #
# A single daemon worker pushes AFTER (outside) the local write; repeated saves
# coalesce via a pending flag — the newest on-disk file always wins because the
# worker re-reads the path at push time.
_push_lock = threading.Lock()
_push_pending = False
_push_thread = None
_push_path = None


def _push_worker():
    global _push_pending
    while True:
        with _push_lock:
            if not _push_pending:
                return
            _push_pending = False
        gdrive_store.drive_push(_push_path)    # never raises (best-effort mirror)


def schedule_push(path):
    """Mirror `path` to Drive on the background worker — applying the data-loss
    guard: refuse while the initial pull has not succeeded. Never raises."""
    global _push_pending, _push_thread, _push_path
    if not gdrive_store.enabled():
        return
    if _pull_state not in ("pulled", "absent"):
        log.error("Drive push BLOCKED: initial pull has not succeeded (state=%s) - "
                  "keeping the remote backup intact. Will retry the pull on next load.",
                  _pull_state)
        gdrive_store.STATUS["push_result"] = "skipped"
        return
    with _push_lock:
        _push_path = path
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


def status():
    """{pull_state, push_blocked} for the UI persistence badge."""
    return {"pull_state": _pull_state,
            "push_blocked": bool(gdrive_store.enabled()
                                 and _pull_state not in ("pulled", "absent"))}
