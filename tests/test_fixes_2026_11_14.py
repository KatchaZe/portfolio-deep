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


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("ALL FIXES 2026-11-14 TESTS PASSED")
