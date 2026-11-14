"""
Review fixes 2026-11-14, task A1 + A2.

A1  Drive push is an optimistic lock on Drive's file version: a moved or unreadable
    remote version blocks the push, keeps the remote copy beside the local file, and
    stays blocked ("conflict") until restart.
A2  set_holding rejects negative / non-finite input before it counts as an edit, and
    save() refuses NaN rather than replacing the good file with an unparseable one.
"""
import os
import sys
import json
import glob
import tempfile
import datetime as dt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import store
import store_sync
from sources import gdrive_store


class _Req:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


class FakeDrive:
    """Just enough of the Drive v3 client for portfolio.json: list / get / update."""

    def __init__(self, push_version="7", get_raises=False):
        self.versions = [push_version]                 # what get() serves
        self.get_raises = get_raises
        self.uploads = []
        self.next_version = 100

    def files(self):
        return self

    def list(self, **kw):
        return _Req(lambda: {"files": [{"id": "fid1", "name": gdrive_store.REMOTE_NAME}]})

    def get(self, fileId, fields):
        def run():
            if self.get_raises:
                raise RuntimeError("boom")
            return {"version": self.versions[0]}
        return _Req(run)

    def get_media(self, fileId):
        return "media"

    def update(self, fileId, media_body, fields=None):
        def run():
            self.uploads.append(fileId)
            self.next_version += 1
            return {"id": fileId, "version": str(self.next_version)}
        return _Req(run)


def _setup(tmp, drive, remote_version):
    os.environ["GDRIVE_SA_JSON"] = "{}"
    gdrive_store._enabled = True
    gdrive_store._service = drive
    gdrive_store._ids = {}
    gdrive_store.STATUS.update(push_result=None, remote_version=remote_version,
                               conflict_file=None, last_error=None)
    gdrive_store._download = lambda svc, fid: b'{"remote": true}'
    path = os.path.join(tmp, "portfolio.json")
    with open(path, "w") as fh:
        fh.write('{"local": true}')
    return path


def _teardown():
    os.environ.pop("GDRIVE_SA_JSON", None)
    gdrive_store._enabled = None
    gdrive_store._service = None
    gdrive_store._ids = {}
    gdrive_store.STATUS.update(push_result=None, remote_version=None, conflict_file=None)
    store_sync._pull_state = None


def test_push_blocked_when_remote_version_moved():
    with tempfile.TemporaryDirectory() as tmp:
        drive = FakeDrive(push_version="8")
        path = _setup(tmp, drive, remote_version="7")
        try:
            assert gdrive_store.drive_push(path) is False
            assert gdrive_store.STATUS["push_result"] == "conflict"
            assert drive.uploads == [], "must not upload over a moved remote"
            side = glob.glob(path + ".remote-*")
            assert len(side) == 1, side
            with open(side[0], "rb") as fh:
                assert fh.read() == b'{"remote": true}'
            assert os.path.basename(side[0]) == gdrive_store.STATUS["conflict_file"]
            # sticky: even with the versions matching again, no automatic retry
            drive.versions = ["7"]
            assert gdrive_store.drive_push(path) is False
            assert drive.uploads == []
        finally:
            _teardown()
    print("push blocked on moved remote version OK")


def test_conflict_blocks_schedule_push_until_restart():
    with tempfile.TemporaryDirectory() as tmp:
        drive = FakeDrive()
        path = _setup(tmp, drive, remote_version="7")
        try:
            store_sync._pull_state = "pulled"
            gdrive_store.STATUS["push_result"] = "conflict"
            store_sync.schedule_push(path)
            store_sync.wait_push()
            assert drive.uploads == []
            assert store_sync.status()["push_blocked"] is True
        finally:
            _teardown()
    print("conflict blocks schedule_push OK")


def test_push_blocked_when_version_check_errors():
    with tempfile.TemporaryDirectory() as tmp:
        drive = FakeDrive(get_raises=True)
        path = _setup(tmp, drive, remote_version="7")
        try:
            assert gdrive_store.drive_push(path) is False
            assert drive.uploads == []
            assert gdrive_store.STATUS["push_result"] == "conflict"
        finally:
            _teardown()
    print("push blocked when version check errors OK")


def test_push_ok_and_version_advances():
    with tempfile.TemporaryDirectory() as tmp:
        drive = FakeDrive(push_version="7")
        path = _setup(tmp, drive, remote_version="7")
        try:
            assert gdrive_store.drive_push(path) is True
            assert drive.uploads == ["fid1"]
            assert gdrive_store.STATUS["push_result"] == "pushed"
            assert gdrive_store.STATUS["remote_version"] == "101"
            drive.versions = ["101"]                 # the next push sees our own version
            assert gdrive_store.drive_push(path) is True
        finally:
            _teardown()
    print("push ok and version advances OK")


def test_dated_backup_pushed_once_per_day():
    with tempfile.TemporaryDirectory() as tmp:
        drive = FakeDrive(push_version="7")
        path = _setup(tmp, drive, remote_version="7")
        names = []
        real = gdrive_store.drive_push_json
        gdrive_store.drive_push_json = lambda name, p: names.append(name) or True
        store_sync._last_backup_day = None
        try:
            store_sync._pull_state = "pulled"
            for _ in range(2):
                drive.versions = [gdrive_store.STATUS["remote_version"]]
                store_sync.schedule_push(path)
                store_sync.wait_push()
            assert names == [f"portfolio.backup-{dt.date.today().isoformat()}.json"], names
        finally:
            gdrive_store.drive_push_json = real
            store_sync._last_backup_day = None
            _teardown()
    print("dated backup once per day OK")


def _fresh_store(tmp):
    config.DATA_DIR = tmp
    store.PATH = os.path.join(tmp, "portfolio.json")
    return store.load()


def test_set_holding_rejects_bad_shares():
    with tempfile.TemporaryDirectory() as tmp:
        s = _fresh_store(tmp)
        store.set_holding(s, "NVDA", 10, 100.0)
        before = store_sync._edit_count()
        snap = json.dumps(s["holdings"], sort_keys=True)
        for bad in (-5, float("nan"), float("inf")):
            for kw in ({"shares": bad}, {"avg_cost": bad}):
                try:
                    store.set_holding(s, "NVDA", **kw)
                except ValueError:
                    pass
                else:
                    raise AssertionError(f"{kw} was accepted")
        assert store_sync._edit_count() == before
        assert json.dumps(s["holdings"], sort_keys=True) == snap
    print("set_holding rejects bad input OK")


def test_set_holding_allows_zero():
    with tempfile.TemporaryDirectory() as tmp:
        s = _fresh_store(tmp)
        store.set_holding(s, "NVDA", 10, 100.0)
        store.set_holding(s, "NVDA", 0, 0)
        assert s["holdings"]["NVDA"]["shares"] == 0
    print("set_holding allows zero OK")


def test_save_refuses_nan():
    with tempfile.TemporaryDirectory() as tmp:
        s = _fresh_store(tmp)
        store.save(s)
        with open(store.PATH, "rb") as fh:
            good = fh.read()
        s["junk"] = float("nan")
        try:
            store.save(s)
        except ValueError:
            pass
        else:
            raise AssertionError("save accepted NaN")
        with open(store.PATH, "rb") as fh:
            assert fh.read() == good
        assert not glob.glob(store.PATH + "*.tmp"), "temp file left behind"
    print("save refuses NaN OK")


def _pull_with(tmp, local_bytes, remote_bytes):
    drive = FakeDrive(push_version="9")
    path = _setup(tmp, drive, remote_version=None)
    gdrive_store._download = lambda svc, fid: remote_bytes
    gdrive_store.STATUS["local_sidecar"] = None
    if local_bytes is None:
        os.remove(path)
    else:
        with open(path, "wb") as fh:
            fh.write(local_bytes)
    return path, gdrive_store.drive_pull(path)


def test_pull_keeps_differing_local_as_sidecar():
    with tempfile.TemporaryDirectory() as tmp:
        try:
            path, res = _pull_with(tmp, b'{"local": "post-conflict edit"}', b'{"remote": 1}')
            assert res == "pulled"
            side = glob.glob(path + ".local-*")
            assert len(side) == 1, side
            assert open(side[0], "rb").read() == b'{"local": "post-conflict edit"}'
            assert open(path, "rb").read() == b'{"remote": 1}'
            assert gdrive_store.STATUS["local_sidecar"] == os.path.basename(side[0])
        finally:
            _teardown()
    print("pull keeps differing local OK")


def test_pull_identical_or_missing_local_makes_no_sidecar():
    for local in (b'{"same": 1}', None):
        with tempfile.TemporaryDirectory() as tmp:
            try:
                path, res = _pull_with(tmp, local, b'{"same": 1}')
                assert res == "pulled"
                assert glob.glob(path + ".local-*") == []
                assert gdrive_store.STATUS["local_sidecar"] is None
            finally:
                _teardown()
    print("pull without differing local makes no sidecar OK")


def test_holding_422_for_input_but_500_for_save_fault():
    from fastapi.testclient import TestClient
    import app
    with tempfile.TemporaryDirectory() as tmp:
        _fresh_store(tmp)
        client = TestClient(app.app, raise_server_exceptions=False)
        assert client.post("/api/holding?ticker=NVDA&shares=-5").status_code == 422
        real = store.save
        def bad_save(s):
            raise ValueError("Out of range float values are not JSON compliant")
        store.save = bad_save
        try:
            r = client.post("/api/holding?ticker=NVDA&shares=5")
        finally:
            store.save = real
        assert r.status_code == 500, (r.status_code, r.text)
    print("422 for input, 500 for save fault OK")


def test_holding_endpoint_returns_422():
    from fastapi.testclient import TestClient
    import app
    with tempfile.TemporaryDirectory() as tmp:
        _fresh_store(tmp)
        r = TestClient(app.app).post("/api/holding?ticker=NVDA&shares=-5")
        assert r.status_code == 422 and "shares" in r.json()["error"], (r.status_code, r.text)
    print("/api/holding 422 OK")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("ALL FIXES 2026-11-14 TESTS PASSED")
