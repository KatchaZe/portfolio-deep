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
import os
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

# FIX (2026-08-16) — DATA LOSS. The push guard above protects the REMOTE copy while
# the initial pull is failing, but nothing protected the LOCAL one. The sequence,
# reproduced in _audit_app/r3_drive_pull_clobbers.py:
#   1. cold start, Drive unreachable      -> _pull_state = "error"
#   2. user adds NVDA 250sh               -> saved locally; push correctly BLOCKED,
#                                            so this edit exists in exactly one place
#   3. Drive recovers, retry pull succeeds -> drive_pull() overwrites the local file
#                                            with the stale remote. NVDA is gone.
# No error, no backup, no warning — the guard that was protecting the remote is what
# made the local copy the only copy, and then the retry destroyed it. An edit made
# while the remote was unreachable is strictly NEWER than that remote, so the retry
# must not overwrite it.
# _local_dirty means precisely: "there is a user edit here that has NOT reached
# Drive". It is set by the explicit mutations in store.py (never by save() — an
# app-initiated write of the empty default store must not count, or the 2026-07 wipe
# comes straight back), and cleared as soon as the edit is safely mirrored, whether
# that happens via a successful push or a successful pull. `_dirty_gen` makes the
# clear race-free: the push worker only clears the flag if no NEW edit arrived while
# it was uploading.
_local_dirty = False
_dirty_gen = 0
_dirty_lock = threading.Lock()


def mark_local_edit():
    """Record that the local file has been written since the last successful pull,
    so a later pull retry cannot silently overwrite that edit."""
    global _local_dirty, _dirty_gen
    with _dirty_lock:
        _local_dirty = True
        _dirty_gen += 1


def _clear_local_edit(gen=None):
    """Clear the dirty flag — but only if no newer edit landed since `gen`."""
    global _local_dirty
    with _dirty_lock:
        if gen is None or gen == _dirty_gen:
            _local_dirty = False


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

    if _local_dirty and os.path.exists(path):
        # The user edited while Drive was down. Local wins: adopt it as the source of
        # truth, unblock pushing so the edit reaches Drive, and keep whatever the
        # remote held in a sidecar rather than discarding either version.
        try:
            gdrive_store.drive_pull(path + ".remote")
        except Exception:
            pass
        _pull_state = "pulled"
        log.warning("Drive pull SKIPPED: local %s was edited while Drive was "
                    "unreachable, so it is newer than the remote. Keeping local and "
                    "pushing it up; the remote copy was saved to %s.remote",
                    path, path)
        gdrive_store.STATUS["pull_result"] = "local-kept"
        schedule_push(path)
        return

    _pull_state = gdrive_store.drive_pull(path)
    if _pull_state in ("pulled", "absent"):
        _clear_local_edit()


# --- background push (C1 fix) ---------------------------------------------- #
# A single daemon worker pushes AFTER (outside) the local write; repeated saves
# coalesce via a pending flag — the newest on-disk file always wins because the
# worker re-reads the path at push time.
_push_lock = threading.Lock()
_push_thread = None
_pending = []            # [(path, remote_name)] — remote_name None = guarded portfolio push


def _push_worker():
    while True:
        with _push_lock:
            if not _pending:
                return
            path, name = _pending.pop(0)
        if name is None:
            with _dirty_lock:
                gen = _dirty_gen
            if gdrive_store.drive_push(path):      # never raises (best-effort mirror)
                # the local edit is now ON Drive, so it no longer needs protecting
                # from a pull. `gen` guards the race with an edit made mid-upload.
                _clear_local_edit(gen)
        else:
            gdrive_store.drive_push_json(name, path)


def _enqueue(path, name):
    global _push_thread
    with _push_lock:
        if (path, name) not in _pending:
            _pending.append((path, name))
        if _push_thread is not None and _push_thread.is_alive():
            return                             # running worker will pick it up
        _push_thread = threading.Thread(target=_push_worker,
                                        name="drive-push", daemon=True)
        _push_thread.start()


def schedule_push(path):
    """Mirror `path` to Drive on the background worker — applying the data-loss
    guard: refuse while the initial pull has not succeeded. Never raises."""
    if not gdrive_store.enabled():
        return
    if _pull_state not in ("pulled", "absent"):
        log.error("Drive push BLOCKED: initial pull has not succeeded (state=%s) - "
                  "keeping the remote backup intact. Will retry the pull on next load.",
                  _pull_state)
        gdrive_store.STATUS["push_result"] = "skipped"
        return
    _enqueue(path, None)


def schedule_push_named(path, remote_name):
    """Best-effort mirror of an AUXILIARY file (e.g. data/risk_cache.json) so a
    cloud instance can reuse it (2026-07-19b). NO data-loss guard — caches are
    rebuildable; skipped entirely when Drive isn't configured. Never raises."""
    if not gdrive_store.enabled():
        return
    _enqueue(path, remote_name)


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
