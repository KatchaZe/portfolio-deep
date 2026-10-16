"""
Google-Drive mirror policy for the local store: WHEN pulling and pushing is safe.
store.py owns the local file, sources/gdrive_store.py owns the Drive I/O, and this
module owns the state that decides between them:

  * pull on cold start, retried (throttled) after a failure;
  * a background push worker, so save() never does network I/O under store.LOCK;
  * the PUSH GUARD: no portfolio push until one pull has succeeded, so a cold-started
    empty-disk instance can never overwrite the good Drive backup;
  * the LOCAL GUARD: a user edit made while Drive was unreachable is newer than the
    remote, so a later pull retry must not overwrite it.

Per-process state: the app must run as exactly ONE worker.
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

# User edits are counted; `_mirrored` is the count known to be on Drive. An edit is
# un-mirrored while the two differ. Only explicit user mutations count (store.py calls
# mark_local_edit), never store.save(): saving the empty default store after a failed
# cold-start pull must not outrank a good Drive backup. A push snapshots `_edits`
# before uploading, so an edit made mid-upload stays un-mirrored.
_edits = 0
_mirrored = 0
_edits_lock = threading.Lock()


def _pull_ok():
    return _pull_state in ("pulled", "absent")


def mark_local_edit():
    """Record a user edit that has not reached Drive yet."""
    global _edits
    with _edits_lock:
        _edits += 1


def _has_unmirrored_edit():
    with _edits_lock:
        return _edits != _mirrored


def _edit_count():
    with _edits_lock:
        return _edits


def _mark_mirrored(count):
    global _mirrored
    with _edits_lock:
        _mirrored = max(_mirrored, count)


def ensure_pull(path):
    """Restore `path` from Drive (if configured) on cold start. Never raises."""
    global _pull_state, _last_try
    if not gdrive_store.enabled() or _pull_ok():
        return
    now = time.time()
    if _pull_state == "error" and now - _last_try < RETRY_SECS:
        return
    _last_try = now

    if _has_unmirrored_edit() and os.path.exists(path):
        # Local wins: keep it, unblock pushing so it reaches Drive, and keep whatever
        # the remote held in a sidecar rather than discarding either version.
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
    if _pull_ok():
        _mark_mirrored(_edit_count())


# --- background push ------------------------------------------------------- #
# One daemon worker drains a de-duplicated queue. It re-reads the file at push
# time, so repeated saves coalesce and the newest on-disk content always wins.
_push_lock = threading.Lock()
_push_thread = None
_pending = []            # [(path, remote_name)]; remote_name None = guarded portfolio push


def _push_worker():
    while True:
        with _push_lock:
            if not _pending:
                return
            path, name = _pending.pop(0)
        if name is not None:
            gdrive_store.drive_push_json(name, path)
            continue
        count = _edit_count()
        if gdrive_store.drive_push(path):
            _mark_mirrored(count)


def _enqueue(path, name):
    global _push_thread
    with _push_lock:
        if (path, name) not in _pending:
            _pending.append((path, name))
        if _push_thread is not None and _push_thread.is_alive():
            return
        _push_thread = threading.Thread(target=_push_worker,
                                        name="drive-push", daemon=True)
        _push_thread.start()


def schedule_push(path):
    """Mirror the portfolio file to Drive, unless the push guard blocks it."""
    if not gdrive_store.enabled():
        return
    if not _pull_ok():
        log.error("Drive push BLOCKED: initial pull has not succeeded (state=%s) - "
                  "keeping the remote backup intact. Will retry the pull on next load.",
                  _pull_state)
        gdrive_store.STATUS["push_result"] = "skipped"
        return
    _enqueue(path, None)


def schedule_push_named(path, remote_name):
    """Mirror an auxiliary cache file (e.g. data/risk_cache.json). No guard: caches
    are rebuildable."""
    if not gdrive_store.enabled():
        return
    _enqueue(path, remote_name)


def wait_push(timeout=10.0):
    """Block until the push worker is idle (tests, graceful shutdown)."""
    t = _push_thread
    if t is not None and t.is_alive():
        t.join(timeout)


def status():
    """Drive persistence health for /api/persist."""
    return {**gdrive_store.status(),
            "pull_state": _pull_state,
            "push_blocked": bool(gdrive_store.enabled() and not _pull_ok())}
