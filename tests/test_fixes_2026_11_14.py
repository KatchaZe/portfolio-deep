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


# ---- Task 2: TTM needs four contiguous quarters ---------------------------------
def _qrows(ends_vals, tag="Revenues", annual=None):
    rows = []
    for end, val in ends_vals:
        e = dt.date.fromisoformat(end)
        rows.append({"start": (e - dt.timedelta(days=90)).isoformat(), "end": end,
                     "val": val, "filed": end, "form": "10-Q"})
    if annual:
        rows.append(annual)
    return {"us-gaap": {tag: {"units": {"USD": rows}}}}


def test_ttm_rejects_gap():
    from sources import sec_edgar as se
    x = se._Xbrl(_qrows([("2025-06-30", 1), ("2025-09-30", 2), ("2026-06-30", 4), ("2026-09-30", 8)]))
    assert x.ttm("Revenues") is None
    assert x.ttm_gapped("Revenues")
    print("ttm gap rejected OK")


def test_ttm_gap_uses_recent_annual_only():
    from sources import sec_edgar as se
    ann = {"start": "2025-10-01", "end": "2026-09-30", "val": 99, "filed": "2026-11-01", "form": "10-K"}
    old = dict(ann, start="2024-10-01", end="2025-09-30")
    q = [("2025-06-30", 1), ("2025-09-30", 2), ("2026-06-30", 4), ("2026-09-30", 8)]
    # annual ends at latest_end but cur (a quarter) is latest; build so cur is a quarter
    rows = _qrows(q, annual=dict(ann, end="2026-06-30", start="2025-07-01"))
    assert se._Xbrl(rows).ttm("Revenues") is not None
    rows = _qrows(q, annual=old)
    assert se._Xbrl(rows).ttm("Revenues") is None      # annual 12 months stale
    print("ttm annual-recency OK")


def test_ttm_53_week_quarter_ok():
    from sources import sec_edgar as se
    x = se._Xbrl(_qrows([("2025-03-31", 1), ("2025-06-30", 2), ("2025-10-06", 4), ("2025-12-29", 8)]))
    # gaps 91, 98, 84 days
    assert x.ttm("Revenues") == 15 and not x.ttm_gapped('Revenues')
    print("ttm 53-week OK")


def test_ttm_annual_path_unchanged():
    from sources import sec_edgar as se
    rows = [{"start": "2024-10-01", "end": "2025-09-30", "val": 100, "filed": "2025-11-01", "form": "10-K"},
            {"start": "2025-10-01", "end": "2025-12-31", "val": 30, "filed": "2026-02-01", "form": "10-Q"},
            {"start": "2024-10-01", "end": "2024-12-31", "val": 20, "filed": "2025-02-01", "form": "10-Q"}]
    x = se._Xbrl({"us-gaap": {"Revenues": {"units": {"USD": rows}}}})
    assert x.ttm("Revenues") == 110 and not x.ttm_gapped('Revenues')
    print("ttm annual path OK")


def _two_tag_facts(good_tag_rows, other_tag, other_rows):
    ns = {"Revenues": {"units": {"USD": good_tag_rows}}}
    ns[other_tag] = {"units": {"USD": other_rows}}
    return {"facts": {"us-gaap": ns}}


def _qs(ends):
    out = []
    for end in ends:
        e = dt.date.fromisoformat(end)
        out.append({"start": (e - dt.timedelta(days=90)).isoformat(), "end": end,
                    "val": 10, "filed": end, "form": "10-Q"})
    return out


def test_ttm_flag_names_field_when_no_value():
    from sources import sec_edgar as se
    cf = {"facts": _qrows([("2025-06-30", 1), ("2025-09-30", 2), ("2026-06-30", 4), ("2026-09-30", 8)])}
    flags = se.extract(cf)["xbrl_flags"]
    assert "TTM: quarters not contiguous (revenue)" in flags, flags
    print("ttm flag in extract OK")


def test_ttm_no_flag_when_losing_probe_is_gapped():
    from sources import sec_edgar as se
    good = _qs(["2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31", "2026-03-31"])
    new = "RevenueFromContractWithCustomerExcludingAssessedTax"
    cf = _two_tag_facts(good, new, _qs(["2025-12-31", "2026-03-31"]))
    d = se.extract(cf)
    assert d["revenue"] == 40, d["revenue"]
    assert d["xbrl_flags"] == [], d["xbrl_flags"]
    # and a gapped tag that lost to a healthy one raises nothing either
    cf = _two_tag_facts(good, new, _qs(["2025-06-30", "2025-09-30", "2026-06-30", "2026-09-30"]))
    d = se.extract(cf)
    assert d["revenue"] == 40 and d["xbrl_flags"] == [], (d["revenue"], d["xbrl_flags"])
    print("ttm no false-positive flag OK")


def test_ttm_gap_boundary_100_and_101_days():
    from sources import sec_edgar as se
    def rows(gap):
        ends = []
        e = dt.date(2026, 9, 30)
        for _ in range(4):
            ends.append(e.isoformat())
            e -= dt.timedelta(days=gap)
        return [{"start": (dt.date.fromisoformat(x) - dt.timedelta(days=90)).isoformat(),
                 "end": x, "val": 5, "filed": x, "form": "10-Q"} for x in ends]
    ok = se._Xbrl({"us-gaap": {"Revenues": {"units": {"USD": rows(100)}}}})
    assert ok.ttm("Revenues") == 20
    bad = se._Xbrl({"us-gaap": {"Revenues": {"units": {"USD": rows(101)}}}})
    assert bad.ttm("Revenues") is None
    print("ttm 100/101 day boundary OK")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("ALL FIXES 2026-11-14 TESTS PASSED")
