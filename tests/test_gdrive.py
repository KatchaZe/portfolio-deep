"""
test_gdrive — Google Drive store backend safety:
  * when NOT configured (no env vars): enabled()==False, pull/push are no-ops,
    and store.load/save behave exactly like the local-only version
  * push failure inside save() must NOT raise (best-effort mirror)
Offline/synthetic — no network, no real Drive.
"""
import os
import sys
import json
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import store
from sources import gdrive_store

# EVERY credential env var the backend recognises (see gdrive_store._auth_mode):
# the old list missed the OAuth trio, so on a machine with real OAuth configured
# enabled() stayed True and test_disabled_when_no_env failed (hermeticity bug).
_DRIVE_ENV = ("GDRIVE_SA_JSON", "GDRIVE_FOLDER_ID",
              "GDRIVE_OAUTH_CLIENT_ID", "GDRIVE_OAUTH_CLIENT_SECRET",
              "GDRIVE_OAUTH_REFRESH_TOKEN")


def _clear_drive_env():
    for k in _DRIVE_ENV:
        os.environ.pop(k, None)


def _reset_drive_cache():
    gdrive_store._enabled = None
    gdrive_store._service = None
    gdrive_store._file_id = None
    store._drive_pull_state = None
    store._drive_last_try = 0.0


def test_disabled_when_no_env():
    _clear_drive_env()
    _reset_drive_cache()
    assert gdrive_store.enabled() is False
    assert gdrive_store.drive_pull("/tmp/whatever.json") == "absent"
    assert gdrive_store.drive_push("/tmp/whatever.json") is False
    print("disabled-when-no-env OK")


def test_store_roundtrip_local_only(tmp):
    _clear_drive_env()
    _reset_drive_cache()
    config.DATA_DIR = tmp
    store.PATH = os.path.join(tmp, "portfolio.json")
    s = store.load()
    store.set_holding(s, "nvda", 10, 100.0)
    store.save(s)                       # save() calls drive_push -> must be a no-op
    again = store.load()                # load() calls drive_pull -> must be a no-op
    assert again["holdings"]["NVDA"]["shares"] == 10
    print("store roundtrip local-only OK")


def test_push_failure_never_raises(tmp):
    # Pretend Drive is enabled but make the upload blow up; save() must still work.
    os.environ["GDRIVE_SA_JSON"] = "{}"
    os.environ["GDRIVE_FOLDER_ID"] = "folder123"
    _reset_drive_cache()
    gdrive_store._enabled = True                       # force-enable
    orig = gdrive_store._client
    gdrive_store._client = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        config.DATA_DIR = tmp
        store.PATH = os.path.join(tmp, "portfolio.json")
        s = store.load()                               # pull fails -> local fallback
        assert store._drive_pull_state == "error"      # failure is now REMEMBERED
        store.set_holding(s, "msft", 5, 50.0)
        store.save(s)                                  # push BLOCKED (pull never ok) -> must NOT raise
        assert store.load()["holdings"]["MSFT"]["shares"] == 5
        st = store.persist_status()
        assert st["push_blocked"] is True              # guard active while pull is failing
        print("push-failure-never-raises OK")
    finally:
        gdrive_store._client = orig
        _clear_drive_env()
        _reset_drive_cache()


def test_push_blocked_until_pull_succeeds(tmp):
    """THE data-loss regression (2026-07): a cold-started instance whose pull
    FAILED must never push its empty default store over the good Drive backup.
    Once a pull succeeds (or remote is confirmed absent), pushing resumes."""
    os.environ["GDRIVE_SA_JSON"] = "{}"
    _reset_drive_cache()
    gdrive_store._enabled = True                       # force-enable
    pushes = []
    orig_pull, orig_push = gdrive_store.drive_pull, gdrive_store.drive_push
    gdrive_store.drive_pull = lambda path: "error"     # simulate token-expired / outage
    gdrive_store.drive_push = lambda path: pushes.append(path) or True
    try:
        config.DATA_DIR = tmp
        store.PATH = os.path.join(tmp, "portfolio.json")
        s = store.load()                               # pull errors -> empty default store
        store.save(s)                                  # would have wiped Drive before the fix
        store.wait_push()                              # push runs on a worker now (C1)
        assert pushes == [], "push must be BLOCKED while pull is failing"
        # recovery: next load retries the pull (throttle bypassed) and it succeeds
        store._drive_last_try = 0.0
        gdrive_store.drive_pull = lambda path: "absent"
        s = store.load()
        store.save(s)
        store.wait_push()                              # wait for the async worker before asserting
        assert len(pushes) == 1, "push must RESUME once a pull succeeds"
        print("push-blocked-until-pull-succeeds OK")
    finally:
        gdrive_store.drive_pull, gdrive_store.drive_push = orig_pull, orig_push
        os.environ.pop("GDRIVE_SA_JSON", None)
        _reset_drive_cache()


if __name__ == "__main__":
    # snapshot the real machine's Drive env and ALWAYS restore it — the suite
    # must not depend on (or disturb) local credentials.
    _saved = {k: os.environ.get(k) for k in _DRIVE_ENV}
    try:
        test_disabled_when_no_env()
        with tempfile.TemporaryDirectory() as d:
            test_store_roundtrip_local_only(d)
        with tempfile.TemporaryDirectory() as d:
            test_push_failure_never_raises(d)
        with tempfile.TemporaryDirectory() as d:
            test_push_blocked_until_pull_succeeds(d)
    finally:
        for k, v in _saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    print("\nALL GDRIVE TESTS PASSED ✅")
