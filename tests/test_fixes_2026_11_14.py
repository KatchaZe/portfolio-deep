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
        self.named = {}                                # aux name -> uploaded bytes

    def files(self):
        return self

    def list(self, q="", **kw):
        name = q.split("'")[1] if "'" in q else gdrive_store.REMOTE_NAME
        if name == gdrive_store.REMOTE_NAME:
            return _Req(lambda: {"files": [{"id": "fid1", "name": name}]})
        return _Req(lambda: {"files": [{"id": "aux:" + name, "name": name}]
                             if name in self.named else []})

    def create(self, body, media_body, fields=None):
        def run():
            self.named[body["name"]] = media_body
            return {"id": "aux:" + body["name"], "version": "1"}
        return _Req(run)

    def get(self, fileId, fields):
        def run():
            if self.get_raises:
                raise RuntimeError("boom")
            return {"version": self.versions[0], "md5Checksum": getattr(self, "md5", None)}
        return _Req(run)

    def get_media(self, fileId):
        return "media"

    def update(self, fileId, media_body, fields=None):
        def run():
            if fileId.startswith("aux:"):
                self.named[fileId[4:]] = media_body
                return {"id": fileId, "version": "2"}
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
                               conflict_file=None, conflict_remote=None, last_error=None)
    gdrive_store._download = lambda svc, fid: b'{"remote": true}'
    gdrive_store._upload_media = lambda data: data      # FakeDrive keeps the bytes
    path = os.path.join(tmp, "portfolio.json")
    with open(path, "w") as fh:
        fh.write('{"local": true}')
    return path


_REAL_DOWNLOAD, _REAL_MEDIA = gdrive_store._download, gdrive_store._upload_media


def _teardown():
    os.environ.pop("GDRIVE_SA_JSON", None)
    gdrive_store._enabled = None
    gdrive_store._service = None
    gdrive_store._ids = {}
    gdrive_store._download, gdrive_store._upload_media = _REAL_DOWNLOAD, _REAL_MEDIA
    gdrive_store.STATUS.update(push_result=None, remote_version=None, conflict_file=None,
                               conflict_remote=None, pull_result=None, local_sidecar=None)
    store_sync._pull_state = None
    store_sync._mark_mirrored(store_sync._edit_count())


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



# --------------------------------------------------------------------------
# Task 3 (#2): WACC - g >= 3pp in the terminal, and minority interest (NCI, book
# value) deducted in every firm -> equity bridge like debt.
from types import SimpleNamespace                       # noqa: E402
from domain.facts import FinancialFacts                 # noqa: E402
from domain.engine import deep_v82 as E, fcff_dcf, young_dcf   # noqa: E402

_HCA_RF = 0.041


def _hca_cap(**kw):
    base = dict(roic_used=0.20, wacc_term=0.062, ke_term=0.085, ic=40e9, nopat=8e9)
    base.update(kw)
    return SimpleNamespace(**base)


def _op_facts(ticker="OPCO", **kw):
    """A plain profitable operating company (the ELV-shaped fixture)."""
    base = dict(price=430.0, shares_diluted=219e6, market_cap=93e9, revenue=201e9,
                operating_income=9.6e9, revenue_annuals=[199e9, 177e9, 171e9, 156e9, 138e9],
                revenue_annuals_dated=[["2025-12-31", 199e9], ["2024-12-31", 177e9],
                                       ["2023-12-31", 171e9], ["2022-12-31", 156e9],
                                       ["2021-12-31", 138e9]],
                cash=10e9, total_debt=31e9, equity=42e9, income_before_tax=9.9e9,
                tax_expense=2.0e9, capex=1.3e9, dep_amort=1.4e9, interest_expense=1.4e9,
                net_income=7.9e9, rev_growth_path=[[0.04, 12], [0.05, 11]],
                fiscal_year="2025-12-31")
    base.update(kw)
    f = FinancialFacts(ticker)
    for k, v in base.items():
        setattr(f, k, v)
    return f


def test_terminal_spread_at_least_3pp():
    f = FinancialFacts("HCAX")
    gr = E._growth(f, _hca_cap(), None, _HCA_RF)
    assert E.WACC_G_MIN_SPREAD == 0.03
    assert gr.g_stable <= 0.062 - 0.03 + 1e-12, gr.g_stable
    assert gr.g_stable <= _HCA_RF and gr.g_stable <= gr.ke_stable - 0.03 + 1e-12
    # the guard only binds when it has to: a wide spread keeps g_stable = rf
    wide = E._growth(FinancialFacts("WIDE"), _hca_cap(wacc_term=0.09), None, _HCA_RF)
    assert abs(wide.g_stable - _HCA_RF) < 1e-12, wide.g_stable
    print("g_stable leaves >= 3pp under the terminal WACC OK")


def test_young_uses_same_g_stable():
    from tests.test_young_dcf import PREPROFIT
    real = E._growth
    seen = {}

    def spy(*a, **k):
        gr = real(*a, **k)._replace(g_stable=0.021)   # != rf, so plumbing is visible
        seen["g"] = gr.g_stable
        return gr
    E._growth = spy
    try:
        v = E.DeepV82Engine().evaluate(FinancialFacts("YOUNGCO", **PREPROFIT), rf=0.045)
    finally:
        E._growth = real
    y = v.young_dcf
    assert y and y.get("model"), "young model ran"
    assert y["model"]["g_stable"] == seen["g"], (y["model"]["g_stable"], seen["g"])
    print("young-company DCF takes g_stable from _growth OK")


def test_nci_deducted():
    # the bridge itself: firm 100, cash 10, debt 30, NCI 8, 1 share -> equity 72
    kw = dict(revenue=1000.0, margin_now=0.10, margin_target=0.10, g_high=0.0,
              g_stable=0.0, sales_to_capital=2.0, tax_now=0.0, tax_marginal=0.0,
              wacc=0.10, wacc_stable=0.10, roic_stable=0.10, cash=10.0, debt=30.0,
              shares=1.0, growth_path=[0.0] * 5)
    out = fcff_dcf.value(**kw, minority_interest=8.0)
    firm = out["firm_value"]
    assert abs(firm - 1000.0) < 1, firm            # 100/yr forever at 10%, scaled x10
    assert abs(out["equity_value"] - (firm + 10 - 30 - 8)) < 1, out["equity_value"]
    small = fcff_dcf.value(**dict(kw, revenue=100.0), minority_interest=8.0)
    assert abs(small["equity_value"] - 72.0) < 0.5, small["equity_value"]
    # young-company leg: NCI sits in net debt and in the distress floor
    d = dict(current_revenue=100.0, g_high=0.0, g_stable=0.0, horizon=10, current_margin=0.1,
             target_margin=0.1, sales_to_capital=2.0, tax=0.0, wacc=0.1, roic_stable=0.1,
             wacc_stable=0.1, tax_terminal=0.0, net_debt=30 - 10 + 8, shares=1.0)
    gc = young_dcf.going_concern(d)
    assert abs(gc["equity_value"] - (gc["firm_value"] - 28)) < 1e-6
    # engine: the same NCI comes off the FCFF DCF equity and onto every EV
    a = E.DeepV82Engine().evaluate(_op_facts(), rf=0.045)
    b = E.DeepV82Engine().evaluate(_op_facts(minority_interest=8e9), rf=0.045)
    assert a.dcf and b.dcf
    assert abs((a.dcf["equity_value"] - b.dcf["equity_value"]) - 8e9) < 2, \
        (a.dcf["equity_value"], b.dcf["equity_value"])
    assert abs((a.dcf["firm_value"] - b.dcf["firm_value"])) < 2
    ra, rb = a.reverse_dcf, b.reverse_dcf
    assert ra.get("enterprise_value") and rb.get("enterprise_value"), ra
    assert abs(rb["enterprise_value"] - ra["enterprise_value"] - 8e9) < 2
    assert abs((a.anchor_value - b.anchor_value) - 8e9 / 219e6) < 0.02, \
        (a.anchor_value, b.anchor_value)
    print("NCI deducted in the equity bridge OK")


def test_nci_absent_no_change():
    kw = dict(revenue=1000.0, margin_now=0.10, margin_target=0.12, g_high=0.05,
              g_stable=0.02, sales_to_capital=2.0, tax_now=0.2, tax_marginal=0.25,
              wacc=0.09, wacc_stable=0.08, roic_stable=0.12, cash=10.0, debt=30.0,
              shares=3.0)
    assert fcff_dcf.value(**kw) == fcff_dcf.value(**kw, minority_interest=0.0)
    a = E.DeepV82Engine().evaluate(_op_facts(), rf=0.045)
    b = E.DeepV82Engine().evaluate(_op_facts(minority_interest=None), rf=0.045)
    assert a.dcf == b.dcf and a.anchor_value == b.anchor_value
    assert a.reverse_dcf == b.reverse_dcf and a.composite == b.composite
    print("NCI absent -> value unchanged OK")


def test_contracts_cover_minority_interest():
    from domain import contracts
    from pipeline import normalize
    assert contracts.CLOCK["minority_interest"] == contracts.INSTANT
    assert contracts.UNIT["minority_interest"] == contracts.MONEY
    assert "minority_interest" in normalize._MONEY
    assert hasattr(FinancialFacts("X"), "minority_interest")
    from sources import sec_edgar as se
    rule, ladder = se.FIELD_LADDERS["minority_interest"]
    assert list(ladder) == ["MinorityInterest", "MinorityInterestInConsolidatedSubsidiaries"]
    assert "minority_interest" in se.POPULATED
    print("contracts cover minority_interest OK")


# --- Task 4: liquidation floor + young-firm uncertainty floor -------------------
def test_young_fv_not_below_net_cash():
    from domain.engine import young_dcf as Y
    v, floored = Y.floored_value(0.0, 0.85, 7.14)
    assert v == 7.14 and floored is True
    v, floored = Y.floored_value(20.0, 0.85, 7.14)
    assert floored is False and abs(v - Y.failure_adjusted(20.0, 0.85, 7.14)) < 1e-12
    print("young fv floored at net cash OK")


def test_floor_applies_to_scenarios():
    from domain.engine import scenarios as S, young_dcf as Y
    d = {"current_revenue": 100.0, "g_high": 0.2, "g_stable": 0.03, "horizon": 10,
         "current_margin": -0.5, "target_margin": 0.01, "sales_to_capital": 0.3,
         "tax": 0.2, "wacc": 0.15, "roic_stable": 0.05, "wacc_stable": 0.09,
         "tax_terminal": 0.25, "net_debt": 0.0, "shares": 10.0,
         "annual_dilution": 0.0, "p_survival": 0.85, "distress_value_per_share": 7.14}
    gc = Y.going_concern(d)
    assert gc["going_concern_per_share"] < 1.0, gc["going_concern_per_share"]
    ydcf = {"model": d, "p_survival": 0.85, "distress_per_share": 7.14}
    _, pack = S.young(ydcf, 20.0)
    vals = [c["value"] for c in pack["cases"]]
    assert vals and all(v >= 7.14 - 1e-9 for v in vals), vals
    mc = S.story_mc_young(ydcf, 20.0, 0.0, 0.03)
    assert mc["p10"] >= 7.14 - 0.011, mc
    print("floor applies to scenarios and story MC OK")


def test_young_mc_floored():
    from domain.engine import young_dcf as Y
    d = {"current_revenue": 100.0, "g_high": 0.2, "g_stable": 0.03, "horizon": 10,
         "current_margin": -0.5, "target_margin": 0.01, "sales_to_capital": 0.3,
         "tax": 0.2, "wacc": 0.15, "roic_stable": 0.05, "wacc_stable": 0.09,
         "tax_terminal": 0.25, "net_debt": 0.0, "shares": 10.0,
         "annual_dilution": 0.0, "p_survival": 0.85, "distress_value_per_share": 7.14}
    mc = Y.monte_carlo(d, n_sims=300)
    assert mc["p10"] >= 7.14 - 0.011, mc
    print("young MC floored OK")


def test_young_uncertainty_at_least_very_high():
    from domain.engine import uncertainty as U
    sc = {"story_mc": {"p10": 9.0, "p50": 10.0, "p90": 12.0}}   # width 0.3 -> Low
    assert U.assess(sc)["rating"] == "Low"
    b = U.assess(sc, None, floor="Very High")
    assert b["rating"] == "Very High" and b["floored_by"] == "Very High", b
    assert U.assess(sc, None, floor="Very High")["add_min_mos"] == U.BANDS["Very High"][1]
    print("young uncertainty floor OK")


def test_floor_does_not_lower_extreme():
    from domain.engine import uncertainty as U
    sc = {"story_mc": {"p10": 1.0, "p50": 2.0, "p90": 5.0}}     # width 2.0
    b = U.assess(sc, None, floor="Very High")
    assert b["rating"] == "Extreme" and "floored_by" not in b, b
    print("floor does not lower Extreme OK")


def _young_eval(revenue, cash=1.0e9):
    from tests.test_young_dcf import PREPROFIT, RF
    from domain.engine.deep_v82 import DeepV82Engine
    from domain.facts import FinancialFacts
    kw = dict(PREPROFIT, revenue=revenue, cash=cash, total_debt=0.0, operating_income=-0.5e9,
              cfo=-0.5e9, revenue_annuals=[revenue, revenue * .8, revenue * .6, revenue * .4],
              growth_lt=0.05, fwd_growth_near=0.05)
    return DeepV82Engine().evaluate(FinancialFacts("YNGCO", **kw), rf=RF).young_dcf


def test_floored_young_is_uncertain():
    y = _young_eval(0.5e9)          # small POSITIVE going concern ($1.1) under $4.17 net cash
    assert y["floored"] is True and y["going_concern_per_share"] > 0, y
    assert y["band_ratio"] == 1.0, "floored band collapses to a point (reads as tight)"
    assert y["uncertain"] is True and "liquidation floor" in y["blocked_reason"], y
    ok = _young_eval(0.9e9)         # going concern above net cash: untouched
    assert ok["floored"] is False and ok["uncertain"] is False and ok["blocked_reason"] is None, ok
    print("floored young value is flagged uncertain OK")


def test_non_young_uncertainty_has_no_floored_by():
    from domain.engine import deep_v82 as E
    v = E.DeepV82Engine().evaluate(_op_facts(), rf=0.045)
    assert not v.anchor_method.startswith("Young-Company")
    assert "floored_by" not in (v.uncertainty or {}), v.uncertainty
    print("non-young uncertainty has no floored_by OK")


def test_early_stage_uses_industry_beta():
    f = FinancialFacts("RKLB", beta=2.61, beta_unlevered_industry=1.2, beta_industry="Aerospace/Defense",
                       market_cap=40e9, total_debt=0.0, operating_income=-0.2e9)
    b = E._bottom_up_beta(f, [])
    assert abs(b - E.relevered_beta(1.2, 0.0, 40e9)) < 1e-9 and b < 2.0, b
    # the beta stage cannot know the model yet: no p_survival claim here (final #2)
    assert any("2.61" in x and "too noisy" in x for x in f.flags), f.flags
    assert not any("p_survival" in x for x in f.flags), f.flags
    print("early stage uses industry beta OK")


def test_no_industry_beta_keeps_regression():
    f = FinancialFacts("RKLB", beta=2.61, market_cap=40e9, operating_income=-0.2e9)
    assert E._bottom_up_beta(f, []) == 2.61
    print("no industry beta keeps regression OK")


def test_early_stage_beta_source_label():
    f = _op_facts()
    f.operating_income, f.beta, f.beta_unlevered_industry = -0.2e9, 2.61, 1.2
    v = E.DeepV82Engine().evaluate(f, rf=0.045)
    km = v.key_metrics
    assert km["beta_source"] == "bottom-up industry (early stage: failure risk carried by p_survival)", km
    assert km["beta_regression"] == 2.61 and km["beta"] < 2.0, km
    assert v.anchor_method.startswith("Young-Company"), v.anchor_method
    assert any("p_survival" in x for x in v.flags), v.flags
    print("early stage beta_source label OK")



# --- Task 6 (B1 + B6): NOT RATED, and one recommendation set on the Risk Desk ---
def _anc(method="FCFF DCF", dcf=None, ydcf=None):
    return E._Anchor(method=method, value=10.0, fv_peg=None, fv_fvp=None, ydcf=ydcf, dcf=dcf)


_GOOD = {"D": 4.5, "E_exec": 4.5, "E_econ": 4.5, "P": 4.5}


def test_not_rated_each_reason():
    ok_unc = {"rating": "Medium"}
    cases = {
        "extreme": (dict(unc={"rating": "Extreme"}, anc=_anc(dcf={"terminal_share_pct": 60})), []),
        "floored": (dict(unc={"rating": "Very High", "floored_by": "liquidation"},
                         anc=_anc("Young-Company DCF (failure-adjusted)",
                                  ydcf={"floored": True, "terminal_share_of_value_pct": 40})), []),
        "terminal": (dict(unc=ok_unc, anc=_anc(dcf={"terminal_share_pct": 125.0})), []),
        "young_terminal": (dict(unc=ok_unc, anc=_anc("Young-Company DCF (failure-adjusted)",
                                                     ydcf={"floored": False,
                                                           "terminal_share_of_value_pct": 130.0})), []),
        "ttm": (dict(unc=ok_unc, anc=_anc(dcf={"terminal_share_pct": 60})),
                ["TTM: quarters not contiguous (revenue)"]),
    }
    for name, (kw, flags) in cases.items():
        f = FinancialFacts("NRX")
        f.flags = list(flags)
        why = E.not_rated_reasons(kw["unc"], kw["anc"], f)
        assert why, (name, why)
        f.flags = list(flags)
        comp, reco = E._overall(f, dict(_GOOD), [], kw["unc"], kw["anc"])
        assert reco == "NOT RATED" and comp is not None, (name, comp, reco)
        assert any(x.startswith("NOT RATED") for x in f.flags), (name, f.flags)
    # the share the primary model does NOT use cannot rate a row out
    f = FinancialFacts("NRX")
    assert E.not_rated_reasons(ok_unc, _anc(dcf={"terminal_share_pct": 99.9},
                                            ydcf={"terminal_share_of_value_pct": 150}), f) == []
    # floored but not the anchor (FCFF DCF primary) -> rated
    assert E.not_rated_reasons(ok_unc, _anc(dcf={"terminal_share_pct": 50},
                                            ydcf={"floored": True}), f) == []
    comp, reco = E._overall(f, dict(_GOOD), [], ok_unc, _anc(dcf={"terminal_share_pct": 50}))
    assert reco == "BUY", reco
    print("NOT RATED for each of the four reasons, rated otherwise OK")


def test_floored_young_engine_row_is_not_rated():
    from tests.test_young_dcf import PREPROFIT, RF
    r = 0.5e9
    kw = dict(PREPROFIT, revenue=r, cash=1.0e9, total_debt=0.0, operating_income=-0.5e9,
              cfo=-0.5e9, revenue_annuals=[r, r * .8, r * .6, r * .4],
              growth_lt=0.05, fwd_growth_near=0.05)
    v = E.DeepV82Engine().evaluate(FinancialFacts("YNGCO", **kw), rf=RF)
    assert v.young_dcf["floored"] is True
    assert v.recommendation == "NOT RATED" and v.signal is None, (v.recommendation, v.signal)
    assert v.composite is not None and v.stars, v.composite        # fundamentals still shown
    assert any("liquidation" in x for x in v.flags if x.startswith("NOT RATED")), v.flags
    assert v.verdict.startswith("NOT RATED"), v.verdict
    print("floored young engine row -> NOT RATED, signal None OK")


def test_signal_not_rated_is_none():
    assert E._signal("NOT RATED") is None
    assert E._signal("SELL / AVOID") == "SELL" and E._signal("HOLD / Accumulate") == "HOLD"
    print("_signal(NOT RATED) is None OK")


def test_advice_not_rated_allows_hold_only():
    from domain import advice
    assert advice.align("NOT RATED", "REDUCE")[0] == "HOLD"
    assert advice.align("NOT RATED", "BUY")[0] == "HOLD"
    assert advice.align("NOT RATED", "ACCUMULATE")[0] == "HOLD"
    for keep in ("HOLD", "HOLD?", "WATCH"):
        assert advice.align("NOT RATED", keep) == (keep, None), keep
    print("advice: NOT RATED allows HOLD / HOLD? / WATCH only OK")


def _tg_val(rating="BUY", **kw):
    return dict({"rating": rating, "net_mos": 0.30, "excess_pct": 2.0, "uncertainty": "Medium"}, **kw)


def test_targets_keep_weight_for_not_rated():
    from domain.risk import targets as T, portfolio as RP
    assert T.budget({"rating": "NOT RATED", "net_mos": 0.3}) == (None, "ไม่จัดอันดับ → คงน้ำหนักเดิม")
    assert RP.add_blocker({"rating": "NOT RATED", "net_mos": 0.5}) == "rating NOT RATED"
    tk = ["A", "B", "N"]
    cov = [[0.04, 0.01, 0.01], [0.01, 0.04, 0.01], [0.01, 0.01, 0.04]]
    w = {"A": 0.30, "B": 0.10, "N": 0.60}
    vals = {"A": _tg_val(), "B": _tg_val(), "N": _tg_val("NOT RATED")}
    # inside its caps: no valuation-driven trade, however far from a budget weight
    out = T.targets(tk, cov, w, vals, [{"ticker": t, "hard_max_pct": 100.0} for t in tk],
                    total_value=10000)
    n = {r["ticker"]: r for r in out["rows"]}["N"]
    assert n["action"] == "HOLD" and n["trade_pct"] == 0 and n["target_pct"] == 60.0, n
    assert "ไม่จัดอันดับ" in n["basis"], n
    # over its 20% hard cap: trimmed to the cap like an ADD-blocked name, never an exit
    out = T.targets(tk, cov, w, vals, [{"ticker": t, "hard_max_pct": 20.0,
                                        "add_blocker": "rating NOT RATED" if t == "N" else None}
                                       for t in tk], total_value=10000)
    n = {r["ticker"]: r for r in out["rows"]}["N"]
    assert n["target_pct"] == 20.0 and n["action"] == "SELL" and n["exit"] is False, n
    assert abs(n["trade_pct"] - (21.75 - 60.0)) < 1e-9, n
    # a sector over its cap trims the NOT RATED name pro rata too
    sec = [{"ticker": t, "hard_max_pct": 100.0, "sector": "Tech"} for t in tk]
    out = T.targets(tk, cov, w, vals, sec, total_value=10000, sector_cap=0.40)
    by = {r["ticker"]: r for r in out["rows"]}
    assert by["N"]["target_pct"] == 24.0 and by["N"]["action"] == "SELL", by["N"]
    assert sum(r["target_pct"] for r in out["rows"]) <= 40.0 + 1e-6, by
    # the sizing table says TRIM over the hard cap, HOLD otherwise - never ADD
    rc = [{"ticker": "N", "capital_pct": 30.0, "abs_risk_share_pct": 30.0},
          {"ticker": "M", "capital_pct": 5.0, "abs_risk_share_pct": 5.0}]
    nv = {"N": {"rating": "NOT RATED", "net_mos": 0.5}, "M": {"rating": "NOT RATED", "net_mos": 0.5}}
    act = {r["ticker"]: r["action"] for r in RP.position_sizing(rc, {}, single_name_cap=0.20,
                                                                  risk_share_cap=1.0, value=nv)}
    assert act["N"] == "TRIM" and act["M"].startswith("HOLD"), act
    print("NOT RATED: no valuation trade, still bounded by the caps OK")


def test_existing_sell_still_exits():
    from domain.risk import targets as T
    assert T.budget({"rating": "SELL / AVOID", "net_mos": 0.3})[0] == 0.0
    tk = ["A", "B", "S"]
    cov = [[0.04, 0.01, 0.01], [0.01, 0.04, 0.01], [0.01, 0.01, 0.04]]
    out = T.targets(tk, cov, {"A": 0.4, "B": 0.4, "S": 0.2},
                    {"A": _tg_val(), "B": _tg_val(), "S": _tg_val("SELL / AVOID")},
                    [{"ticker": t, "hard_max_pct": 100.0} for t in tk], total_value=10000)
    s = {r["ticker"]: r for r in out["rows"]}["S"]
    assert s["action"] == "SELL" and s["target_pct"] == 0.0 and s["trade_pct"] == -20.0, s
    print("SELL / AVOID still exits whole OK")


def test_trades_from_targets_shape_and_costs():
    from domain.risk import portfolio as RP
    rows = [{"ticker": "A", "current_pct": 40.0, "target_pct": 20.0, "action": "SELL",
             "trade_pct": -18.25, "trade_usd": -1825},
            {"ticker": "B", "current_pct": 10.0, "target_pct": 30.0, "action": "BUY",
             "trade_pct": 18.25, "trade_usd": 1825},
            {"ticker": "C", "current_pct": 30.0, "target_pct": 0.0, "action": "SELL", "exit": True,
             "trade_pct": -29.99, "trade_usd": -2999},
            {"ticker": "D", "current_pct": 10.0, "target_pct": 11.0, "action": "HOLD",
             "trade_pct": 0.0, "trade_usd": 0}]
    w = {"A": 0.40, "B": 0.10, "C": 0.30, "D": 0.10}           # 10% cash
    sizing = [{"ticker": "A", "hard_max_pct": 20.0}, {"ticker": "B", "hard_max_pct": 40.0},
              {"ticker": "C", "hard_max_pct": 40.0}, {"ticker": "D", "hard_max_pct": 40.0}]
    value = {"A": {"price": 120.0, "avg_cost": 100.0, "tax_rate": 0.15},
             "C": {"price": 50.0, "avg_cost": 80.0, "tax_rate": 0.15}}
    reb = RP.trades_from_targets(rows, w, sizing, 10000, 20, value=value)
    tr = {t["ticker"]: t for t in reb["trades"]}
    assert set(tr) == {"A", "B", "C"}, tr                       # D inside the band
    assert tr["A"]["action"] == "TRIM" and tr["A"]["from_pct"] == 40.0 and tr["A"]["to_pct"] == 21.8
    assert tr["A"]["priority"].startswith("Must"), tr["A"]      # over its hard cap
    assert tr["C"]["action"] == "TRIM" and tr["C"]["to_pct"] == 0.0 and "SELL" in tr["C"]["priority"]
    assert tr["B"]["action"] == "ADD" and tr["B"]["to_pct"] == 28.2, tr["B"]
    for t in tr.values():
        for k in ("amount_usd", "realized_gain_usd", "tax_usd", "cost_usd", "priority"):
            assert k in t, (k, t)
    # A: $1,825 sold at 120 over 100 -> gain 304.17, tax 45.63; C sold at a loss: no tax
    assert abs(tr["A"]["realized_gain_usd"] - 304.17) < 0.01 and abs(tr["A"]["tax_usd"] - 45.625) < 0.01
    assert tr["C"]["realized_gain_usd"] < 0 and tr["C"]["tax_usd"] == 0.0
    assert tr["A"]["cost_usd"] == round(1825 * 20 / 2 / 10000, 2)
    pw = reb["proposed_weights"]
    assert abs(pw["A"] - 0.2175) < 1e-9 and pw["C"] == 0 and abs(pw["D"] - 0.10) < 1e-9, pw
    assert reb["cash_pct_after"] == 40.0 and reb["to_cash_pct"] == 30.0, reb
    assert reb["no_trade"] is False and "Targets" in reb["rule"], reb
    assert RP.trades_from_targets([], w, sizing, 10000, 20)["no_trade"] is True
    # a target that rounds to 0 (a sector cap squeezing a BUY-rated name) is NOT an exit:
    # Targets keeps 1.75pp, so must the Rebalance table
    z = RP.trades_from_targets([{"ticker": "A", "current_pct": 30.0, "target_pct": 0.0,
                                 "action": "SELL", "exit": False, "trade_pct": -28.25,
                                 "trade_usd": -2825}], {"A": 0.30}, sizing, 10000, 20)
    za = z["trades"][0]
    assert za["to_pct"] == 1.8 and abs(z["proposed_weights"]["A"] - 0.0175) < 1e-9, za
    assert "SELL" not in za["priority"] and abs(za["amount_usd"] - 2825) < 0.01, za
    print("trades_from_targets: shape, costs, cash OK")


def test_rebalance_matches_targets():
    from tests.test_alloc_corr import _store, _provider
    from pipeline import risk_report
    import random
    random.seed(11)
    spec = {"AA": dict(sector="Technology", shares=60), "BB": dict(sector="Healthcare", shares=5),
            "CC": dict(sector="Energy", shares=20), "DD": dict(sector="Utilities", shares=15),
            "NR": dict(sector="Industrials", shares=30)}
    s = _store(spec)
    s["results"] = {"AA": {"recommendation": "HOLD", "anchor_value": 105.0},
                    "BB": {"recommendation": "BUY", "anchor_value": 180.0},
                    "CC": {"recommendation": "SELL / AVOID", "anchor_value": 60.0},
                    "DD": {"recommendation": "HOLD / Accumulate", "anchor_value": 130.0},
                    "NR": {"recommendation": "NOT RATED", "anchor_value": 200.0}}
    series = {t: [random.gauss(0.0004, 0.015) for _ in range(250)] for t in spec}
    p, _ = risk_report.build(s, "", 0, 250, 20.0, 1.0, False,
                             fetch_returns=_provider(series), fetch_long=lambda t: {})
    tg, reb = p["targets"], p["rebalance"]
    assert tg["status"] == "ok", tg
    trades = {t["ticker"]: t for t in reb["trades"]}
    moved = 0
    for r in tg["rows"]:
        if r["action"] == "HOLD":
            assert r["ticker"] not in trades, (r, trades.get(r["ticker"]))
            continue
        moved += 1
        t = trades[r["ticker"]]
        assert t["action"] == ("TRIM" if r["action"] == "SELL" else "ADD"), (r, t)
        assert abs(t["amount_usd"] - abs(r["trade_usd"])) <= 1.0, (r, t)
    assert moved and len(trades) == moved, (tg["rows"], trades)
    nr = {r["ticker"]: r for r in tg["rows"]}["NR"]
    assert nr["exit"] is False and nr["target_pct"] <= 20.0, nr          # cap, not valuation
    assert trades["NR"]["action"] == "TRIM" and trades["NR"]["to_pct"] > 20.0, trades["NR"]
    assert trades["NR"]["priority"].startswith("Must (เกิน hard cap)"), trades["NR"]
    assert "CC" in trades and trades["CC"]["to_pct"] == 0.0, trades.get("CC")
    assert trades["CC"]["priority"].startswith("Must") and reb["proposed_weights"]["CC"] == 0
    # the sizing table agrees with the plan: NR is over its 20% cap -> TRIM
    nr_size = {r["ticker"]: r for r in p["position_sizing"]}["NR"]
    assert nr_size["action"] == "TRIM", nr_size
    assert reb["status"] == "ok", reb
    assert reb["after"] is not None and "Targets" in reb["rule"], reb
    print("Risk Desk rebalance = Targets in shares/dollars OK:", sorted(trades))


def test_targets_failure_is_not_no_trade():
    import random
    import subprocess
    import shutil
    from tests.test_alloc_corr import _store, _provider
    from pipeline import risk_report
    random.seed(5)
    spec = {"AA": dict(sector="Technology"), "BB": dict(sector="Healthcare")}
    s = _store(spec)
    s["results"] = {"AA": {"recommendation": "BUY", "anchor_value": 150.0},
                    "BB": {"recommendation": "HOLD", "anchor_value": 100.0}}
    series = {t: [random.gauss(0.0004, 0.015) for _ in range(250)] for t in spec}
    real = risk_report.riskeng.risk_budget_targets

    def boom(*a, **k):
        raise ValueError("singular")
    risk_report.riskeng.risk_budget_targets = boom
    try:
        p, _ = risk_report.build(s, "", 0, 250, 20.0, 1.0, False,
                                 fetch_returns=_provider(series), fetch_long=lambda t: {})
    finally:
        risk_report.riskeng.risk_budget_targets = real
    reb = p["rebalance"]
    assert p["targets"]["status"] == "error" and reb["status"] == "unavailable", reb
    assert reb["trades"] == [] and reb["after"] is None and reb.get("note"), reb
    if not shutil.which("node"):
        return
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = "\n".join(open(os.path.join(root, "static", "js", f), encoding="utf-8").read()
                    for f in ("format.js", "charts.js", "risk.js"))
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
        json.dump(p, fh, default=str)
    js = ("global.document={getElementById:()=>null,querySelectorAll:()=>[]};global.window={};"
          "global.localStorage={getItem:()=>null,setItem:()=>{}};" + src +
          ";const d=JSON.parse(require('fs').readFileSync(process.argv[1],'utf8'));"
          "console.log(JSON.stringify([rebalanceHTML(d,d.capital_vs_risk),panelExplain(d),"
          "storyCosts(d.rebalance)]));")
    out = subprocess.run(["node", "-e", js, fh.name], capture_output=True, text=True)
    os.unlink(fh.name)
    html, explain, story = json.loads(out.stdout.strip().splitlines()[-1])
    assert "Targets คำนวณไม่ได้ — ไม่มีรายการ rebalance" in html and "NO TRADE" not in html, html
    assert "NO TRADE" not in explain and "Targets คำนวณไม่ได้" in explain, explain
    assert "NO TRADE" not in story, story
    print("targets failure shows a warning, not NO TRADE OK")


def test_ui_and_docs_name_not_rated():
    import subprocess
    import shutil
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    html = open(os.path.join(root, "index.html"), encoding="utf-8").read()
    assert "ไม่จัดอันดับ" in html and "NOT RATED" in html, "How-to/Ref must explain NOT RATED"
    row = html.split("<b>DEEP recommendation</b>", 1)[1].split("</tr>", 1)[0]
    for why in ("ไม่จัดอันดับ", "NOT RATED", "Extreme", "liquidation", "terminal", "TTM"):
        assert why in row, why
    card = open(os.path.join(root, "static", "js", "card.js"), encoding="utf-8").read()
    assert '"NOT RATED":"nr"' in card and '["nr","ไม่จัดอันดับ"]' in card, "card filter group"
    assert '["NOT RATED","--muted"]' in card, "summary band group"
    risk = open(os.path.join(root, "static", "js", "risk.js"), encoding="utf-8").read()
    assert "จาก Targets" in risk
    if shutil.which("node"):
        js = open(os.path.join(root, "static", "js", "format.js"), encoding="utf-8").read()
        out = subprocess.run(["node", "-e", js + ';console.log(JSON.stringify([RECO_LABEL["NOT RATED"],'
                              'RECO_COLOR["NOT RATED"]]))'], capture_output=True, text=True)
        assert json.loads(out.stdout.strip().splitlines()[-1]) == ["ไม่จัดอันดับ", "--muted"], out
    print("UI + How-to name NOT RATED (ไม่จัดอันดับ) OK")


def test_ledger_start_from_first_deposit():
    from domain import ledger
    txns = [{"id": 1, "type": "DEPOSIT", "date": "2026-01-02", "amount": 10000},
            {"id": 2, "type": "BUY", "date": "2026-01-05", "ticker": "AAPL", "shares": 10, "price": 100}]
    book = ledger.replay(txns)
    assert book["start"] == "2026-01-02", book["start"]
    v = ledger.view(book, {"AAPL": 110.0}, "2026-06-01")
    assert v["start"] == "2026-01-02" and v["xirr_pct"] is not None, v["xirr_note"]
    print("ledger start from first deposit OK")


def test_ledger_open_start_unchanged():
    from domain import ledger
    txns = [{"id": 1, "type": "OPEN", "date": "2026-01-01", "ticker": "MSFT", "shares": 5,
             "price": 300, "mkt_price": 300},
            {"id": 2, "type": "BUY", "date": "2026-01-05", "ticker": "AAPL", "shares": 10, "price": 100}]
    assert ledger.replay(txns)["start"] == "2026-01-01"
    print("ledger OPEN start unchanged OK")


def test_add_holding_checks_r_ok():
    import subprocess
    import shutil
    if not shutil.which("node"):
        print("node missing: addHolding r.ok check skipped")
        return
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "static", "js", "portfolio.js"), encoding="utf-8").read()
    fn = src.split("async function addHolding(){", 1)[1].split("\nasync function removeHolding", 1)[0]
    js = """
const calls = {render: 0};
global.renderPortfolio = () => { calls.render++; };
global.renderQuota = () => {};
global.__riskCache = null;
const els = {t: {value: 'abc'}, sh: {value: '-5'}, ac: {value: ''}, spA: {style: {}},
             addMsg: {textContent: ''}};
global.document = {getElementById: (id) => els[id]};
global.fetch = async () => ({ok: false, status: 422, json: async () => ({error: 'shares must be >= 0'})});
(async () => {
  eval(FN);
  await addHolding();
  console.log(JSON.stringify({render: calls.render, msg: els.addMsg.textContent}));
})();
""".replace("FN", json.dumps("async function addHolding(){" + fn))
    fh = tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8")
    fh.write(js)
    fh.close()
    out = subprocess.run(["node", fh.name], capture_output=True, text=True)
    os.unlink(fh.name)
    res = json.loads(out.stdout.strip().splitlines()[-1])
    assert res["render"] == 0, res
    assert res["msg"] == "\u2717 shares must be >= 0", res
    print("addHolding non-ok response shows error, does not render OK")



# ---- Final review fixes: TTM gate on value-driving fields only (#1) -------------
def _msft_cf():
    root = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(root, "fixtures", "MSFT", "sec_companyfacts.json")) as fh:
        return json.load(fh)


def _populated(cf):
    from sources import sec_edgar as se
    ff = FinancialFacts("TTMX")
    se.populate(ff, cf)
    return ff


_OK_UNC = {"rating": "Medium"}


def test_sporadic_ttm_gap_is_not_rated_out():
    cf = _msft_cf()
    # restructuring filed in Q0, Q1, Q4, Q5 only: sporadic, not a broken year
    cf["facts"]["us-gaap"]["RestructuringCharges"] = {"units": {"USD": _qs(
        ["2026-03-31", "2025-12-31", "2025-03-31", "2024-12-31"])}}
    ff = _populated(cf)
    assert not any(str(x).startswith("TTM: quarters not contiguous") for x in ff.flags), ff.flags
    assert any("restructuring_ttm" in str(x) for x in ff.flags), ff.flags   # info kept
    assert E.not_rated_reasons(_OK_UNC, _anc(dcf={"terminal_share_pct": 60}), ff) == [], ff.flags
    # a stored flag in the old wording for a sporadic field cannot rate a row out either
    f = FinancialFacts("OLD")
    f.flags = ["TTM: quarters not contiguous (restructuring_ttm)"]
    assert E.not_rated_reasons(_OK_UNC, _anc(dcf={"terminal_share_pct": 60}), f) == []
    print("sporadic TTM gap keeps the row rated OK")


def test_gapped_revenue_is_not_rated():
    cf = {"facts": _qrows([("2025-06-30", 1), ("2025-09-30", 2), ("2026-06-30", 4), ("2026-09-30", 8)])}
    ff = _populated(cf)
    why = E.not_rated_reasons(_OK_UNC, _anc(dcf={"terminal_share_pct": 60}), ff)
    assert why and "revenue" in why[0], why
    print("gapped revenue -> NOT RATED OK")


def _derived_ebit_cf(interest_ends):
    ends = ["2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30", "2026-09-30"]
    ns = {"Revenues": {"units": {"USD": _qs(ends)}},
          se_PRETAX(): {"units": {"USD": _qs(ends)}},
          "InterestExpense": {"units": {"USD": _qs(interest_ends)}}}
    return {"facts": {"us-gaap": ns}}


def se_PRETAX():
    from sources import sec_edgar as se
    return se.PRETAX[0]


def test_derived_ebit_with_gapped_interest_is_not_rated():
    from sources import sec_edgar as se
    ok = se.extract(_derived_ebit_cf(["2025-12-31", "2026-03-31", "2026-06-30", "2026-09-30"]))
    assert ok["operating_income_derived"] and ok["operating_income"] == 80, ok["operating_income"]
    assert ok["xbrl_flags"] == [], ok["xbrl_flags"]
    cf = _derived_ebit_cf(["2025-06-30", "2025-09-30", "2026-06-30", "2026-09-30"])
    d = se.extract(cf)
    assert d["operating_income_derived"], d
    assert "TTM: quarters not contiguous (interest_expense)" in d["xbrl_flags"], d["xbrl_flags"]
    ff = _populated(cf)
    why = E.not_rated_reasons(_OK_UNC, _anc(dcf={"terminal_share_pct": 60}), ff)
    assert why and "interest_expense" in why[0], (why, ff.flags)
    # a filer with a real operating-income line: a gapped interest TTM does not drive EBIT
    f = FinancialFacts("OPX")
    f.flags = ["TTM: quarters not contiguous (interest_expense)"]
    assert E.not_rated_reasons(_OK_UNC, _anc(dcf={"terminal_share_pct": 60}), f) == []
    print("derived EBIT with gapped interest -> NOT RATED OK")


def test_not_rated_flag_does_not_self_trigger():
    f = FinancialFacts("RERUN")
    f.flags = ["NOT RATED: TTM incomplete - TTM: quarters not contiguous (revenue)"]
    assert E.not_rated_reasons(_OK_UNC, _anc(dcf={"terminal_share_pct": 60}), f) == []
    print("persisted NOT RATED flag does not re-trigger OK")


# ---- Final review fixes: early-stage beta wording (#2) --------------------------
def test_fcff_valued_early_stage_beta_wording():
    f = _op_facts()
    # profitable but barely (thin margin, small): FCFF DCF values it, not young_dcf
    f.operating_income, f.beta, f.beta_unlevered_industry = 0.02 * f.revenue, 2.61, 1.2
    f.market_cap = 10e9
    v = E.DeepV82Engine().evaluate(f, rf=0.045)
    assert not v.anchor_method.startswith("Young-Company"), v.anchor_method
    km = v.key_metrics
    assert km["beta_source"] == ("bottom-up industry (early stage: single-stock regression "
                                 "beta 2.61 too noisy, kept as cross-check)"), km["beta_source"]
    assert not any("p_survival" in x for x in v.flags), [x for x in v.flags if "beta" in x]
    assert any("2.61" in x and "too noisy" in x for x in v.flags), v.flags
    print("FCFF-valued early-stage beta wording OK")


def test_risk_report_has_no_early_stage_rewrite():
    from pipeline import risk_report
    km = {"beta": 1.3, "beta_regression": 1.3, "beta_source": "bottom-up: Software"}
    assert risk_report._risk_beta("equity", km, None, None) == (1.3, "bottom-up: Software")
    src = open(risk_report.__file__, encoding="utf-8").read()
    assert "higher than the industry beta" not in src
    print("risk report: dead early-stage rewrite removed OK")



# ---- Final review fixes: Drive conflict survives an ephemeral disk (#3-#5) -------
import re as _re                                         # noqa: E402

_CONFLICT_RE = _re.compile(r"^portfolio\.conflict-[A-Za-z0-9-]{1,24}-\d{8}-\d{4}\.json$")


def _conflict_copies(drive):
    return {n: b for n, b in drive.named.items() if n.startswith("portfolio.conflict-")}


def test_conflict_pushes_conflict_copy_and_repushes_on_save():
    with tempfile.TemporaryDirectory() as tmp:
        drive = FakeDrive(push_version="8")
        path = _setup(tmp, drive, remote_version="7")
        try:
            assert gdrive_store.drive_push(path) is False
            name = gdrive_store.STATUS["conflict_remote"]
            assert name and _CONFLICT_RE.match(name), name
            assert _conflict_copies(drive) == {name: b'{"local": true}'}, drive.named
            assert drive.uploads == [], "the guarded portfolio.json is never touched"
            assert store_sync.status()["conflict_remote"] == name
            # a save while in conflict re-pushes the SAME conflict copy
            with open(path, "w") as fh:
                fh.write('{"local": "edited after conflict"}')
            store_sync._pull_state = "pulled"
            store_sync.schedule_push(path)
            store_sync.wait_push()
            assert _conflict_copies(drive) == {name: b'{"local": "edited after conflict"}'}, drive.named
            assert drive.uploads == []
        finally:
            _teardown()
    print("conflict copy pushed on conflict and re-pushed on save OK")


def test_pull_reads_version_before_download():
    with tempfile.TemporaryDirectory() as tmp:
        drive = FakeDrive(push_version="7")
        path = _setup(tmp, drive, remote_version=None)

        def racing_download(svc, fid):
            drive.versions = ["8"]          # another writer lands mid-download
            return b'{"remote": "v8 content"}'
        gdrive_store._download = racing_download
        try:
            assert gdrive_store.drive_pull(path) == "pulled"
            assert gdrive_store.STATUS["remote_version"] == "7", gdrive_store.STATUS["remote_version"]
            # what we hold may be older than v8: the next push must not overwrite it
            assert gdrive_store.drive_push(path) is False
            assert gdrive_store.STATUS["push_result"] == "conflict" and drive.uploads == []
        finally:
            _teardown()
    print("pull reads the version before downloading OK")


def _local_wins_setup(tmp, remote_bytes):
    drive = FakeDrive(push_version="7")
    path = _setup(tmp, drive, remote_version=None)
    gdrive_store._download = lambda svc, fid: remote_bytes
    with open(path, "wb") as fh:
        fh.write(b'{"holdings": {"NVDA": 1}}')     # the one holding added offline
    store_sync._pull_state, store_sync._last_try = "error", 0.0
    store_sync._last_backup_day = None
    store_sync.mark_local_edit()
    return drive, path


def test_local_edit_vs_differing_remote_enters_conflict():
    with tempfile.TemporaryDirectory() as tmp:
        drive, path = _local_wins_setup(tmp, b'{"holdings": {"full": "portfolio"}}')
        try:
            store_sync.ensure_pull(path)
            store_sync.wait_push()
            assert drive.uploads == [], "near-empty local must not replace the Drive portfolio"
            assert gdrive_store.STATUS["push_result"] == "conflict"
            assert store_sync.status()["push_blocked"] is True
            assert open(path, "rb").read() == b'{"holdings": {"NVDA": 1}}', "local kept"
            side = glob.glob(path + ".remote-*")
            assert len(side) == 1 and open(side[0], "rb").read() == b'{"holdings": {"full": "portfolio"}}'
            assert gdrive_store.STATUS["conflict_file"] == os.path.basename(side[0])
            assert not os.path.exists(path + ".remote"), "scratch copy left behind"
            assert list(_conflict_copies(drive).values()) == [b'{"holdings": {"NVDA": 1}}']
            assert not [n for n in drive.named if n.startswith("portfolio.backup-")], drive.named
            # a later save stays blocked from portfolio.json and from the dated backup
            store_sync.schedule_push(path)
            store_sync.wait_push()
            assert drive.uploads == []
            assert not [n for n in drive.named if n.startswith("portfolio.backup-")]
        finally:
            store_sync._last_backup_day = None
            _teardown()
    print("offline edit vs a different remote -> conflict, no overwrite OK")


def test_local_edit_identical_to_remote_is_mirrored():
    with tempfile.TemporaryDirectory() as tmp:
        drive, path = _local_wins_setup(tmp, b'{"holdings": {"NVDA": 1}}')
        try:
            store_sync.ensure_pull(path)
            store_sync.wait_push()
            assert gdrive_store.STATUS["push_result"] != "conflict"
            assert store_sync._pull_state == "pulled" and not store_sync._has_unmirrored_edit()
            assert gdrive_store.STATUS["remote_version"] == "7"
            assert not glob.glob(path + ".remote*")
        finally:
            store_sync._last_backup_day = None
            _teardown()
    print("offline edit equal to the remote -> no conflict OK")


def test_no_daily_backup_from_a_conflicted_store():
    with tempfile.TemporaryDirectory() as tmp:
        drive = FakeDrive(push_version="7")
        path = _setup(tmp, drive, remote_version="7")
        store_sync._last_backup_day = None
        try:
            gdrive_store.STATUS["push_result"] = "conflict"
            store_sync._backup_once_a_day(path)
            assert drive.named == {} and store_sync._last_backup_day is None, drive.named
        finally:
            store_sync._last_backup_day = None
            _teardown()
    print("no dated backup while in conflict OK")



# ---- Final review fixes: UI (#6, #7) ---------------------------------------------
def _node(js):
    import subprocess
    fh = tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8")
    fh.write(js)
    fh.close()
    try:
        out = subprocess.run(["node", fh.name], capture_output=True, text=True)
    finally:
        os.unlink(fh.name)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def _js_src(name):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return open(os.path.join(root, "static", "js", name), encoding="utf-8").read()


def _run_add_holding(response_js):
    fn = _js_src("portfolio.js").split("async function addHolding(){", 1)[1].split(
        "\nasync function removeHolding", 1)[0]
    js = """
const calls = {render: 0};
global.renderPortfolio = () => { calls.render++; };
global.renderQuota = () => {};
global.__riskCache = null;
const els = {t: {value: 'abc'}, sh: {value: '5'}, ac: {value: ''}, spA: {style: {}},
             addMsg: {textContent: ''}};
global.document = {getElementById: (id) => els[id]};
global.fetch = async () => (__RESP__);
(async () => {
  eval(__FN__);
  await addHolding();
  console.log(JSON.stringify({render: calls.render, msg: els.addMsg.textContent}));
})();
""".replace("__RESP__", response_js).replace(
        "__FN__", json.dumps("async function addHolding(){" + fn))
    return _node(js)


def test_add_holding_non_json_error_and_ok_path():
    import shutil
    if not shutil.which("node"):
        print("node missing: addHolding non-JSON check skipped")
        return
    res = _run_add_holding("{ok: false, status: 500, json: async () => "
                           "{ throw new SyntaxError('Unexpected token I'); }}")
    assert res == {"render": 0, "msg": "✗ 500"}, res
    res = _run_add_holding("{ok: true, status: 200, json: async () => ({view: {}})}")
    assert res["render"] == 1 and res["msg"] == "✓ ABC added/updated", res
    print("addHolding: non-JSON 500 shows status, 200 renders OK")


def _run_badge(persist):
    fn = _js_src("app.js").split("async function loadPersist(){", 1)[1].split(
        "\n// Render free tier", 1)[0]
    js = """
const el = {className: '', innerHTML: '', title: '', style: {}};
global.document = {getElementById: () => el};
global.fetch = async () => ({json: async () => (__PERSIST__)});
(async () => {
  eval(__FN__);
  await loadPersist();
  console.log(JSON.stringify(el));
})();
""".replace("__PERSIST__", json.dumps(persist)).replace(
        "__FN__", json.dumps("async function loadPersist(){" + fn))
    return _node(js)


def test_badge_amber_when_restart_pull_replaced_local():
    import shutil
    if not shutil.which("node"):
        print("node missing: badge check skipped")
        return
    ok = {"configured": True, "auth_mode": "oauth", "pull_state": "pulled",
          "push_blocked": False, "push_result": "pushed", "local_sidecar": None}
    el = _run_badge(ok)
    assert el["className"] == "quota", el
    el = _run_badge(dict(ok, local_sidecar="portfolio.json.local-20261115-0900"))
    assert el["className"] == "quota warn", el
    assert "portfolio.json.local-20261115-0900" in el["title"], el
    print("badge amber with the local sidecar named OK")


def test_badge_names_conflict_copy_without_restart_advice():
    import shutil
    if not shutil.which("node"):
        print("node missing: conflict badge check skipped")
        return
    el = _run_badge({"configured": True, "auth_mode": "oauth", "pull_state": "pulled",
                     "push_blocked": True, "push_result": "conflict",
                     "conflict_file": "portfolio.json.remote-20261115-0900",
                     "conflict_remote": "portfolio.conflict-srv-20261115-0900.json"})
    assert "portfolio.conflict-srv-20261115-0900.json" in el["title"], el
    assert "ข้างไฟล์ local" not in el["title"] and "ก่อน restart" not in el["title"], el["title"]
    print("conflict badge names the Drive conflict copy OK")


def test_version_bump_with_same_content_is_not_a_conflict():
    """Live 2026-11-15: Drive bumped portfolio.json's version after our own upload
    (755 -> 756) with the bytes unchanged, and the next push read it as a conflict.
    The lock compares content (md5) when the version alone moved."""
    import hashlib
    with tempfile.TemporaryDirectory() as tmp:
        drive = FakeDrive(push_version="7")
        path = _setup(tmp, drive, remote_version="7")
        try:
            assert gdrive_store.drive_push(path) is True          # we push '{"local": true}'
            ours = hashlib.md5(b'{"local": true}').hexdigest()
            assert gdrive_store.STATUS["remote_md5"] == ours
            drive.versions = ["102"]                               # Drive bumped, same bytes
            drive.md5 = ours
            assert gdrive_store.drive_push(path) is True, gdrive_store.STATUS["last_error"]
            assert gdrive_store.STATUS["push_result"] == "pushed"
            drive.versions = ["999"]                               # real change elsewhere
            drive.md5 = hashlib.md5(b"other writer").hexdigest()
            assert gdrive_store.drive_push(path) is False
            assert gdrive_store.STATUS["push_result"] == "conflict"
        finally:
            gdrive_store.STATUS["remote_md5"] = None
            _teardown()
    print("same-content version bump is not a conflict OK")


def test_pull_records_md5_of_downloaded_bytes():
    import hashlib
    with tempfile.TemporaryDirectory() as tmp:
        drive = FakeDrive(push_version="7")
        path = _setup(tmp, drive, remote_version=None)
        try:
            assert gdrive_store.drive_pull(path) == "pulled"
            assert gdrive_store.STATUS["remote_md5"] == hashlib.md5(b'{"remote": true}').hexdigest()
        finally:
            gdrive_store.STATUS["remote_md5"] = None
            _teardown()
    print("pull records md5 OK")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("ALL FIXES 2026-11-14 TESTS PASSED")
