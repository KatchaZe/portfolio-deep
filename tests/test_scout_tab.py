"""The Sector scout tab (domain/scout.py, pipeline/scout_job.py, /api/scout*): the mockup v2
rules hold on the user's own scout run (Semiconductor, 2026-11-24, fixtures/scout), and the
tab is display only (PP1)."""
import copy
import datetime as dt
import json
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from domain import scout  # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures", "scout", "semiconductor.json")
HOLD = ["AVGO", "NVDA", "TSM", "MSFT"]
TODAY = dt.date(2026, 11, 24)


def data():
    with open(FIX, encoding="utf-8") as fh:
        return json.load(fh)


def build(D=None, book=None):
    return scout.build(D or data(), HOLD, TODAY, book)


class Rules(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.I = build()
        cls.by = {r["t"]: r for r in cls.I["rows"]}
        cls.R = cls.I["ranks"]["all"]

    def test_excluded_rows_are_never_ranked(self):
        for r in self.I["rows"]:
            if r["excl"]:
                self.assertFalse(any(self.R[t].get(r["t"]) for t in ("value", "quality", "moat", "comb")), r["t"])

    def test_value_ranked_rows_pass_every_value_rule(self):
        for t in self.R["value"]:
            r = self.by[t]
            self.assertFalse({f[0] for f in r["flags"]} & {"F5", "F6", "F7"}, t)
            self.assertGreaterEqual(r["rev"], scout.REV_MIN, t)
            self.assertGreater(r["ebit"], 0, t)

    def test_moat_needs_revenue_of_200m(self):
        for t in self.R["moat"]:
            self.assertGreaterEqual(self.by[t]["rev"], scout.REV_MIN, t)

    def test_foreign_stale_rows_rank_with_a_flag(self):
        f3 = [r for r in self.I["rows"] if any(f[0] == "F3" for f in r["flags"])]
        self.assertTrue(f3)
        self.assertTrue(any(self.R["value"].get(r["t"]) for r in f3), "TOYO, a foreign annual filer, still ranks")
        self.assertFalse(any(r["excl"] and r["excl"][0] == "R3" for r in f3))

    def test_sic_views_keep_the_order_of_the_industry_ranks(self):
        for vk, vr in self.I["ranks"].items():
            for tier, rk in vr.items():
                for a in rk:
                    for b in rk:
                        A, B = self.R[tier][a], self.R[tier][b]
                        self.assertEqual(rk[a] < rk[b], A < B, (vk, tier, a, b))

    def test_coverage_gate_keeps_a_thin_component_out_of_the_score(self):
        for k, ok in self.I["scored"].items():
            self.assertEqual(ok, self.I["cover"][k] >= scout.COVER_MIN * 100 - 0.5, k)

    def test_group2_and_filing_never_move_a_score(self):
        D = data()
        for m in D["members"]:
            m["group2"] = {"bars": 500, "first": "2024-11-22", "last": "2026-11-23", "source": "probe",
                           "vol_ann": {"value": 9.9}, "beta_spy": {"value": -3.0}, "top_corr": {"value": {"ticker": "ZZ", "value": 1.0}},
                           "momentum": {"value": {"mom_label": "Strong"}}, "indicators": {"value": {"rsi": 99}}}
            m["filing"] = {"latest_filed": "2099-01-01", "next_expected_by": "2000-01-01"}
        again = {r["t"]: r for r in build(D, book={"NVDA": (9.9, "2026-11-19")})["rows"]}
        for r in self.I["rows"]:
            k = ("s_comb", "s_value", "s_quality", "s_moat")
            self.assertEqual([r[x] for x in k], [again[r["t"]][x] for x in k], r["t"])

    def test_a_holding_shows_the_apps_own_beta(self):
        r = {x["t"]: x for x in build(book={"NVDA": (1.94, "2026-11-19")})["rows"]}["NVDA"]
        self.assertEqual((r["info"]["beta"], r["info"]["beta_end"], r["info"]["beta_src"]), (1.94, "2026-11-19", "store (Run Daily)"))

    def test_a_refused_market_cap_excludes_under_rule_2_with_the_readers_reason(self):
        D = data()
        m = next(x for x in D["members"] if x["ticker"] == "MU")
        m["market"]["market_cap"] = {"value": None, "source": None, "reason": "share count implausible: 630 shares"}
        r = {x["t"]: x for x in build(D)["rows"]}["MU"]
        self.assertEqual(r["excl"][0], "R2")

    def test_holdings_are_reference_rows(self):
        self.assertTrue(self.by["NVDA"]["hold"])
        self.assertEqual([x["t"] for x in self.I["refs"]], ["MSFT", "TSM"])   # TSM is not a US member

    def test_slim_payload_names_every_source_once(self):
        s = scout.slim(self.I)
        self.assertEqual(len(s["sources"]), len(set(s["sources"])))
        self.assertTrue(all(set(r) >= {"t", "m", "flags"} for r in s["rows"]))


class DisplayOnly(unittest.TestCase):
    def test_no_scoring_code_imports_the_scout(self):
        for d in ("domain/engine", "pipeline", "domain"):
            for f in os.listdir(os.path.join(ROOT, d)):
                if not f.endswith(".py") or f in ("scout.py", "scout_job.py"):
                    continue
                src = open(os.path.join(ROOT, d, f), encoding="utf-8").read()
                self.assertIsNone(re.search(r"^\s*(from|import)\s.*\bscout", src, re.M), f"{d}/{f} imports the scout")


class Job(unittest.TestCase):
    def test_queue_survives_a_restart_as_interrupted(self):
        import tempfile
        import config
        from pipeline import scout_job as J
        old = config.CACHE_DIR
        config.CACHE_DIR = tempfile.mkdtemp()
        try:
            J._save({"state": "running", "queue": ["Semiconductor", "Healthcare Products"], "current": "Semiconductor", "runs": {}})
            s = J.status()
            self.assertEqual(s["state"], "interrupted")
            self.assertEqual(s["queue"], ["Semiconductor", "Healthcare Products"])
        finally:
            config.CACHE_DIR = old

    def test_holding_industries_default(self):
        from pipeline import scout_job as J
        store = {"holdings": {"NVDA": {}, "MSFT": {}, "HCA": {}},
                 "facts": {"NVDA": {"damodaran_industry": "Semiconductor"},
                           "MSFT": {"damodaran_industry": "Software (System & Application)"}, "HCA": {}}}
        self.assertEqual(J.holding_industries(store), {"Semiconductor": ["NVDA"], "Software (System & Application)": ["MSFT"]})

    def test_run_one_refuses_fmp_in_its_own_threads_only(self):
        """E90: the job thread and scout_fetch's workers get an exception from any FMP
        call; the app's own threads (holdings refresh) still reach FMP."""
        import threading
        from concurrent import futures
        from types import SimpleNamespace
        from pipeline import scout_job as J
        from sources import fmp
        from tools import scout_fetch as sf
        seen = {}

        def fake_run(args):
            def call():
                try:
                    return fmp._send(SimpleNamespace(get=lambda *a, **k: SimpleNamespace(status_code=200, text="sent")), "u", {}, 1).text
                except RuntimeError as e:
                    return "refused: " + str(e)
            seen["job"] = call()
            with futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix=sf.WORKER_PREFIX) as ex:
                seen["worker"] = ex.submit(call).result()
            return 1                                   # stop run_one before it reads output
        old = sf.run
        sf.run = fake_run
        try:
            t = threading.Thread(target=lambda: self.assertRaises(RuntimeError, J.run_one, "Semiconductor"),
                                 name="scout-job")
            t.start()
            t.join()
        finally:
            sf.run = old
        self.assertTrue(seen.get("job", "").startswith("refused"), seen)
        self.assertTrue(seen.get("worker", "").startswith("refused"), seen)
        self.assertEqual(fmp._send(SimpleNamespace(get=lambda *a, **k: SimpleNamespace(status_code=200, text="sent")), "u", {}, 1).text, "sent",
                         "a non-scout thread still reaches FMP")


class Api(unittest.TestCase):
    def test_routes(self):
        import importlib
        import shutil
        import tempfile
        try:
            import httpx  # noqa: F401
            from fastapi.testclient import TestClient
        except Exception as e:
            print("  SKIP: %s (pip install httpx)" % type(e).__name__)
            return
        import config
        old = config.CACHE_DIR
        config.CACHE_DIR = tempfile.mkdtemp()
        try:
            os.makedirs(os.path.join(config.CACHE_DIR, "scout"))
            shutil.copy(FIX, os.path.join(config.CACHE_DIR, "scout", "semiconductor.json"))
            for k in ("RENDER", "RENDER_EXTERNAL_URL", "PUBLIC_DEPLOY", "APP_TOKEN"):
                os.environ.pop(k, None)
            import app
            importlib.reload(app)
            c = TestClient(app.app)
            got = c.get("/api/scout").json()
            self.assertIn("status", got)
            self.assertIn("Semiconductor", got["default"])
            v = c.get("/api/scout/view", params={"industry": "Semiconductor"})
            self.assertEqual(v.status_code, 200)
            self.assertEqual(v.json()["industry"], "Semiconductor")
            self.assertEqual(c.get("/api/scout/view", params={"industry": "Tobacco"}).status_code, 404)
            self.assertEqual(c.post("/api/scout/run", params={"industry": "No Such Industry"}).status_code, 422)

            # E89: the "ทำต่อ" button posts with no industry; it continues an interrupted queue
            from pipeline import scout_job as J
            ran = []
            old_run = J.run_one
            J.run_one = lambda industry, **k: ran.append(industry) or {"finished": "x"}
            try:
                self.assertEqual(c.post("/api/scout/run").status_code, 422, "nothing to continue: 422")
                J._save({"state": "running", "queue": ["Semiconductor"], "current": "Semiconductor", "runs": {}})
                r = c.post("/api/scout/run")
                self.assertEqual(r.status_code, 200, r.text)
                if J._thread:
                    J._thread.join(10)
                self.assertEqual(ran, ["Semiconductor"], "the interrupted industry ran again")
            finally:
                J.run_one = old_run
        finally:
            config.CACHE_DIR = old


if __name__ == "__main__":
    unittest.main()
