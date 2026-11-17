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


class _PanelRun(unittest.TestCase):
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


class D_TargetRow(_PanelRun):
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


def series(ends, values, filed=None):
    return [dur(e, v, days=(_d(e) - _d(s0)).days if s0 else 364, filed=filed,
                start=(_d(s0) + dt.timedelta(days=1)).isoformat() if s0 else None)
            for s0, e, v in zip([None] + list(ends[:-1]), ends, values)]


class B_FiscalYears(unittest.TestCase):
    def snap(self, cf, sym="P"):
        p, why = point(sym, cf)
        self.assertIsNone(why)
        return p, PP.table_row(p)

    def test_R2_B_a_revenue_concept_switch_leaves_no_hole_in_the_fiscal_years(self):
        # GOOGL: the FY2025 10-K moved revenue to `Revenues` for 2023-25; FY2022 exists
        # only under the ASC 606 concept, and the two agree where both are filed
        ends = YEARS5
        cf = company(ends, [258e9, 283e9, 307e9, 350e9, 403e9])
        g = cf["facts"]["us-gaap"]
        g["Revenues"]["units"]["USD"] = [e for e in g["Revenues"]["units"]["USD"]
                                         if not e["end"].startswith("2022")]
        g["RevenueFromContractWithCustomerExcludingAssessedTax"] = {"units": {"USD": series(
            ends[:3], [258e9, 283e9, 307e9], filed="2024-01-31")}}
        p, row = self.snap(cf)
        self.assertEqual(tuple((p[k] or {}).get("end") for k in ("fy0", "fy3")), ("2025-12-31", "2022-12-31"))
        self.assertAlmostEqual(row["cagr3_fy"], (403 / 283) ** (1 / 3) - 1)

    def test_R2_B_a_fill_from_a_concept_that_disagrees_is_refused(self):
        # MELI: ASC 606 revenue is a subset of the top line (no credit income); a year
        # missing from `Revenues` must not be filled with the smaller definition
        ends = YEARS5
        cf = company(ends, [7e9, 11e9, 15e9, 21e9, 29e9])
        g = cf["facts"]["us-gaap"]
        g["Revenues"]["units"]["USD"] = [e for e in g["Revenues"]["units"]["USD"]
                                         if not e["end"].startswith("2022")]
        g["RevenueFromContractWithCustomerExcludingAssessedTax"] = {"units": {"USD": series(
            ends[:3], [5.7e9, 7.5e9, 10.5e9], filed="2024-02-20")}}
        p, row = self.snap(cf)
        self.assertIsNone(p["fy3"])
        self.assertIsNone(row["cagr3_fy"])

    def test_R2_B_52_53_week_years_ending_around_new_year(self):
        # JNJ: FY2022 ends 2023-01-01 and FY2023 2023-12-31, two ends in one calendar
        # year; EXEL/STAA: FY2024 ends 2025-01-03, no end in calendar 2024 at all
        for ends, fy1, fy3 in (
                (["2022-01-02", "2023-01-01", "2023-12-31", "2024-12-29", "2025-12-28"], "2024-12-29", "2023-01-01"),
                (["2021-12-31", "2022-12-30", "2023-12-29", "2025-01-03", "2026-01-02"], "2025-01-03", "2022-12-30"),
                (["2021-10-31", "2022-10-30", "2023-10-29", "2024-11-03", "2025-11-02"], "2024-11-03", "2022-10-30")):
            with self.subTest(fy0=ends[-1]):
                cf = facts(Revenues=series(ends, [10e9, 11e9, 12e9, 13e9, 14e9]),
                           OperatingIncomeLoss=series(ends, [2e9, 2.2e9, 2.4e9, 2.6e9, 2.8e9]),
                           GrossProfit=series(ends, [5e9, 5.5e9, 6e9, 6.5e9, 7e9]),
                           NetIncomeLoss=series(ends, [1e9] * 5),
                           WeightedAverageNumberOfDilutedSharesOutstanding=[dur(ends[-1], 1e9, days=91)],
                           CashAndCashEquivalentsAtCarryingValue=[inst(ends[-1], 1e9)],
                           LongTermDebt=[inst(ends[-1], 2e9)])
                p, why = point_on("P", cf, dt.date(2026, 5, 1))
                self.assertIsNone(why)
                self.assertEqual(tuple((p[k] or {}).get("end") for k in ("fy0", "fy1", "fy3")), (ends[-1], fy1, fy3))
                row = PP.table_row(p)
                self.assertAlmostEqual(row["cagr3_fy"], (14 / 11) ** (1 / 3) - 1)
                self.assertAlmostEqual(row["growth_fy"], 14 / 13 - 1)
                self.assertAlmostEqual(row["gross_margin_fy"], .5)

    def test_R2_B_a_stored_point_keeps_five_dated_fiscal_years(self):
        cf = company(YEARS5, [100e9, 110e9, 120e9, 130e9, 140e9],
                     extra={"GrossProfit": series(YEARS5, [40e9, 44e9, 48e9, 52e9, 56e9])})
        p, _ = point("P", cf)
        c = PP.compact(p)
        self.assertEqual([r[0] for r in c["fy_hist"]["revenue"]], list(reversed(YEARS5)))
        self.assertEqual(c["fy_hist"]["gross_profit"][0], ["2025-12-31", 56e9])


class B_TargetSide(_PanelRun):
    def test_R2_B_the_target_row_reads_the_panel_fiscal_years(self):
        # GOOGL as the TARGET: the scored series lacks FY2022, the panel series has it
        T = self.T
        full = T.ext()["revenue_annuals_dated"]
        f = T.tfacts(revenue_annuals_dated=[r for r in full if not r[0].startswith("2022")],
                     revenue_fy_dated=full)
        row = self.run_panel(f)["table"]["rows"][0]
        self.assertTrue(row["is_target"])
        self.assertAlmostEqual(row["cagr3_fy"], (10 / 7) ** (1 / 3) - 1)

    def test_R2_B_a_foreign_target_converts_the_panel_series_to_usd(self):
        from pipeline import normalize
        cf = company(YEARS5, [100e9, 110e9, 120e9, 130e9, 140e9])
        cf["facts"]["us-gaap"]["Revenues"]["units"] = {"DKK": cf["facts"]["us-gaap"]["Revenues"]["units"]["USD"]}
        ff = normalize.build("NVO", cf, None, None, fx_rate=0.15)
        self.assertEqual(ff.revenue_fy_dated[0], ["2025-12-31", 140e9 * 0.15])


class G_Diag(unittest.TestCase):
    def test_R2_G_panel_diag_prints_the_sources_offline_and_writes_nothing(self):
        import contextlib, hashlib, io, json, os, tempfile
        import config
        from tools import panel_diag
        store = os.path.join(tempfile.mkdtemp(), "portfolio.json")
        row = {"symbol": "MSFT", "is_target": False, "cagr3_fy": 0.1}
        with open(store, "w") as fh:
            json.dump({"results": {"ORCL": {"peer_panel": {"as_of": "2026-05-15", "table": {"rows": [row]}}}}}, fh)
        before = hashlib.sha256(open(store, "rb").read()).hexdigest()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = panel_diag.main(["MSFT", "--store", store, "--facts-dir", config.FIXTURE_DIR, "--no-frames"])
        out = buf.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("stored row in ORCL's panel", out)
        self.assertIn("FY3: {'end': '2022-06-30'", out)
        self.assertIn("TTM revenue built from (RevenueFromContractWithCustomerExcludingAssessedTax): FY", out)
        self.assertIn("revenue  RevenueFromContractWithCustomerExcludingAssessedTax  USD  2021-07-01  2022-06-30", out)
        self.assertEqual(hashlib.sha256(open(store, "rb").read()).hexdigest(), before)


def point_on(sym, cf, today):
    saved = peers.resolve_cik
    peers.resolve_cik = lambda s: ("0000000001", s)
    try:
        return peers.peer_point(sym, today, fetch_facts=lambda c: cf, complete=None,
                                fetch_price=lambda s, d: (50.0, (today - dt.timedelta(days=1)).isoformat(), "yahoo"))
    finally:
        peers.resolve_cik = saved


if __name__ == "__main__":
    unittest.main()
