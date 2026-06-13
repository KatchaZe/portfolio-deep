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


def _reset_drive_cache():
    gdrive_store._enabled = None
    gdrive_store._service = None
    gdrive_store._file_id = None
    store._drive_pulled = False


def test_disabled_when_no_env():
    for k in ("GDRIVE_SA_JSON", "GDRIVE_FOLDER_ID"):
        os.environ.pop(k, None)
    _reset_drive_cache()
    assert gdrive_store.enabled() is False
    assert gdrive_store.drive_pull("/tmp/whatever.json") is False
    assert gdrive_store.drive_push("/tmp/whatever.json") is False
    print("disabled-when-no-env OK")


def test_store_roundtrip_local_only(tmp):
    for k in ("GDRIVE_SA_JSON", "GDRIVE_FOLDER_ID"):
        os.environ.pop(k, None)
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
        store.set_holding(s, "msft", 5, 50.0)
        store.save(s)                                  # push fails -> must NOT raise
        assert store.load()["holdings"]["MSFT"]["shares"] == 5
        print("push-failure-never-raises OK")
    finally:
        gdrive_store._client = orig
        for k in ("GDRIVE_SA_JSON", "GDRIVE_FOLDER_ID"):
            os.environ.pop(k, None)
        _reset_drive_cache()


if __name__ == "__main__":
    test_disabled_when_no_env()
    with tempfile.TemporaryDirectory() as d:
        test_store_roundtrip_local_only(d)
    with tempfile.TemporaryDirectory() as d:
        test_push_failure_never_raises(d)
    print("\nALL GDRIVE TESTS PASSED ✅")
