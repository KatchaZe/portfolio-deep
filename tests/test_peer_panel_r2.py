"""Peer panel fix round 2 (live verifier run 2026-11-17). Tests named test_R2_<letter>_...
map to the letters of that round; each was seen red before its fix. Offline: real
sec_edgar.extract on small synthetic companyfacts, every fetch faked, store_sync stubbed."""
import datetime as dt
import unittest

import store_sync
from domain import peer_panel as PP
from pipeline import peers


def _refuse(*a, **k):
    raise AssertionError("a peer-panel test reached store_sync (real storage)")


for _name in dir(store_sync):
    if not _name.startswith("_") and callable(getattr(store_sync, _name)) \
            and getattr(getattr(store_sync, _name), "__module__", None) == "store_sync":
        setattr(store_sync, _name, _refuse)

TODAY = dt.date(2026, 11, 16)


def _d(s):
    return dt.date.fromisoformat(s)


def _start(end, days=364):
    return (_d(end) - dt.timedelta(days=days - 1)).isoformat()


def dur(end, val, days=364, filed=None, frame=None, start=None):
    e = {"start": start or _start(end, days), "end": end, "val": val,
         "filed": filed or (_d(end) + dt.timedelta(days=45)).isoformat(), "form": "10-K"}
    if frame:
        e["frame"] = frame
    return e


def inst(end, val, filed=None):
    return {"end": end, "val": val, "filed": filed or (_d(end) + dt.timedelta(days=40)).isoformat(),
            "form": "10-Q"}


def facts(cik=1, **concepts):
    """companyfacts with us-gaap concepts {name: [entries]} in USD (shares for counts)."""
    ns = {}
    for c, es in concepts.items():
        unit = "shares" if c.startswith("WeightedAverage") else "USD"
        ns[c] = {"units": {unit: es}}
    return {"cik": cik, "facts": {"us-gaap": ns}}


def company(ends, revenue, oi_margin=0.2, rev_concept="Revenues", latest="2026-09-30", extra=None):
    """A filer with fiscal years `ends` (oldest first, revenue per year) and a TTM that
    ends at `latest` (FY + YTD - prior YTD), cash 1B + short-term investments 4B, debt 2B."""
    rev = [dur(e, v) for e, v in zip(ends, revenue)]
    oi = [dur(e, v * oi_margin) for e, v in zip(ends, revenue)]
    ni = [dur(e, v * oi_margin * .8) for e, v in zip(ends, revenue)]
    if latest > ends[-1]:
        prior = (_d(latest) - dt.timedelta(days=364)).isoformat()
        for series, scale in ((rev, 1), (oi, oi_margin), (ni, oi_margin * .8)):
            for s0, e0, share in ((ends[-1], latest, .8), (ends[-2], prior, .75)):
                series.append(dur(e0, revenue[-1] * scale * share, filed="2026-11-01",
                                  start=(_d(s0) + dt.timedelta(days=1)).isoformat()))
    c = {rev_concept: rev, "OperatingIncomeLoss": oi, "NetIncomeLoss": ni,
         "WeightedAverageNumberOfDilutedSharesOutstanding": [dur(latest, 1e9, days=91, filed="2026-11-01")],
         "CashAndCashEquivalentsAtCarryingValue": [inst(latest, 1e9)],
         "ShortTermInvestments": [inst(latest, 4e9)],
         "LongTermDebt": [inst(latest, 2e9)]}
    c.update(extra or {})
    return facts(**c)


YEARS5 = ["2021-12-31", "2022-12-31", "2023-12-31", "2024-12-31", "2025-12-31"]


def point(sym, cf):
    saved = peers.resolve_cik
    peers.resolve_cik = lambda s: ("0000000001", s)
    try:
        return peers.peer_point(sym, TODAY, fetch_facts=lambda c: cf, complete=None,
                                fetch_price=lambda s, d: (50.0, "2026-11-13", "yahoo"))
    finally:
        peers.resolve_cik = saved


class F_ManagedCareCash(unittest.TestCase):
    def test_R2_F_a_health_insurer_peer_counts_cash_only_not_short_term_investments(self):
        cf = company(YEARS5, [100e9, 110e9, 120e9, 130e9, 140e9])
        p, why = point("ELV", cf)
        self.assertIsNone(why)
        self.assertEqual(p["ev"], 50e9 + 2e9 - 1e9)          # cash 1B only
        q, _ = point("MSFT", cf)
        self.assertEqual(q["ev"], 50e9 + 2e9 - 5e9)          # cash + securities for anyone else


class D_TargetRow(unittest.TestCase):
    def setUp(self):
        from tests import test_peer_panel as T
        self.T = T
        self._cik = peers.resolve_cik
        peers.resolve_cik = lambda t: ((str(int(t[1:]) + 1).zfill(10), t) if t[1:].isdigit() else (None, None))

    def tearDown(self):
        peers.resolve_cik = self._cik

    def run_panel(self, f):
        T = self.T
        pts = [PP.compact(dict(T.pp(f"P{i}"), addback=0.0)) for i in range(4)]
        block = {"model": {"panel_points": pts, "version": peers.MODEL_VERSION}, "excluded": [], "notes": []}
        from types import SimpleNamespace
        return peers.panel_and_moat(f, SimpleNamespace(key_metrics={"wacc_pct": 8.0}), block,
                                    T.universe(), TODAY)[0]

    def test_R2_D_a_foreign_target_without_sec_quarters_keeps_its_ttm_period_end(self):
        # TSM / NVO / ASML: 20-F filers, TTM from the quarterly fallback, FX converted;
        # the facts keep the TTM end in fiscal_year and no SEC revenue quarters
        f = self.T.tfacts(revenue_quarters={}, fiscal_year="2026-09-30")
        row = self.run_panel(f)["table"]["rows"][0]
        self.assertTrue(row["is_target"])
        self.assertEqual(row["period_end"], "2026-09-30")

    def test_R2_D_a_target_that_cannot_form_a_row_says_why(self):
        panel = self.run_panel(self.T.tfacts(revenue=None))           # SPCX: no TTM revenue
        self.assertFalse(any(r["is_target"] for r in panel["table"]["rows"]))
        self.assertTrue(any("no target row" in f and "no TTM revenue" in f for f in panel["flags"]),
                        panel["flags"])


class FramesByConcept:
    """SEC frames keyed by (concept, year); a concept/year with no entry answers 404,
    as SEC does for a concept nobody filed that year."""

    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def get(self, url, **k):
        from types import SimpleNamespace
        concept = url.split("/us-gaap/")[1].split("/")[0]
        year = int(url.split("/CY")[1].split(".")[0])
        self.calls.append((concept, year))
        if (concept, year) not in self.answers:
            import requests
            err = requests.HTTPError("404 Client Error")
            err.response = SimpleNamespace(status_code=404)

            def boom():
                raise err
            return SimpleNamespace(status_code=404, raise_for_status=boom, json=lambda: {})
        return SimpleNamespace(status_code=200, raise_for_status=lambda: None,
                               json=lambda: {"data": self.answers[(concept, year)]})


class A_FramesConcepts(unittest.TestCase):
    def test_R2_A_a_filer_tagging_only_another_revenue_concept_is_counted(self):
        from sources import damodaran_companies as dc
        import tempfile
        y = 2025
        ans = {("Revenues", y): [{"cik": 1, "val": 10, "end": "2025-12-31"}],
               ("RevenueFromContractWithCustomerExcludingAssessedTax", y): [{"cik": 2, "val": 5, "end": "2025-12-31"}],
               ("SalesRevenueNet", y): [{"cik": 3, "val": 7, "end": "2025-09-30"},
                                        {"cik": 1, "val": 4, "end": "2025-12-31"}],
               ("RevenueFromContractWithCustomerIncludingAssessedTax", y): [{"cik": 4, "val": 8, "end": "2025-06-30"},
                                                                            {"cik": 2, "val": 6, "end": "2025-12-30"}]}
        rev, end, note = dc._frames(tempfile.mkdtemp(), y, "ua", requests_mod=FramesByConcept(ans))
        self.assertIsNone(note)
        self.assertEqual(rev, {1: 10, 2: 6, 3: 7, 4: 8})          # the largest concept per filer
        self.assertEqual(end[2], "2025-12-30")

    def test_R2_A_a_concept_with_no_frame_that_year_is_skipped_not_a_failed_year(self):
        from sources import damodaran_companies as dc
        import tempfile
        ans = {("Revenues", 2025): [{"cik": 1, "val": 10, "end": "2025-12-31"}]}
        rev, _end, note = dc._frames(tempfile.mkdtemp(), 2025, "ua", requests_mod=FramesByConcept(ans))
        self.assertEqual((rev, note), ({1: 10}, None))

    def test_R2_A_a_cache_built_on_a_shorter_concept_list_is_refreshed(self):
        from sources import damodaran_companies as dc
        import json, os, tempfile, time
        tmp = tempfile.mkdtemp()
        with open(os.path.join(tmp, "sec_frames_revenue_CY2025.json"), "w") as fh:
            json.dump({"at": time.time(), "rev": {"1": 5}, "end": {"1": "2025-12-31"}}, fh)
        rq = FramesByConcept({("SalesRevenueNet", 2025): [{"cik": 3, "val": 7, "end": "2025-09-30"}]})
        rev, _end, note = dc._frames(tmp, 2025, "ua", requests_mod=rq)
        self.assertEqual((rev, note), ({3: 7}, None))
        self.assertTrue(rq.calls)


class C_ShareGroup(unittest.TestCase):
    def setUp(self):
        self._cik = peers.resolve_cik
        peers.resolve_cik = lambda t: ((str(int(t[1:]) + 1).zfill(10), t) if t[1:].isdigit() else (None, None))

    def tearDown(self):
        peers.resolve_cik = self._cik

    def test_R2_C_a_foreign_domiciled_us_gaap_filer_counts_in_the_share_group(self):
        # MELI is domiciled in Uruguay and files a 10-K in USD: it is in SEC's frames,
        # so it belongs to "US SEC filers in the industry" and has a share. A foreign
        # member absent from every year's frames (a 20-F filer) is not a member.
        from tests import test_peer_panel as T
        from types import SimpleNamespace
        rows = {f"T{i}": ["Retail", "5311", "United States"] for i in range(1, 9)}
        rows["T0"] = ["Retail", "5960", "Uruguay"]            # the target, in frames
        rows["T9"] = ["Retail", "5311", "Mexico"]             # 20-F filer, no frames
        fr = T.frames_for(T.Y5, {i + 1: [10.0] * 5 for i in range(9)})
        u = peers.Universe(rows, {}, {}, as_of="2026-01", frames=fr)
        panel, _ = peers.panel_and_moat(T.tfacts(), SimpleNamespace(key_metrics={"wacc_pct": 8.0}),
                                        None, u, TODAY)
        sh = panel["industry_share"]
        self.assertEqual(sh["n_members"], 9)
        self.assertAlmostEqual(sh["target_share"]["2025"], 1 / 9)
        self.assertEqual(sh["n_with_revenue"]["2025"], 9)
        self.assertTrue(any("not US-domiciled" in f for f in sh["flags"]), sh["flags"])


if __name__ == "__main__":
    unittest.main()
