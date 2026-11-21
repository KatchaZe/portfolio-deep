"""FMP quota (2026-11-22): what a ticker's refresh costs is measured on the real code
paths (tools/fmp_budget), the up-front partition is that measurement, statements /
estimates / profile are cached on disk and re-bought when SEC lists a newer filing, and
the badge tells "sent N calls" apart from "FMP refused at HH:MM"."""
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config                                        # noqa: E402
import store                                         # noqa: E402
from pipeline import batch, fmp_cache, fundamentals  # noqa: E402
from sources import fmp, sec_gap                     # noqa: E402
from tests.frontend_source import frontend_files     # noqa: E402
from tools import fmp_budget                         # noqa: E402

TODAY = dt.date.today().isoformat()


class R:
    def __init__(self, status=200, body=None, text=""):
        self.status_code, self._body, self.text = status, body, text

    def json(self):
        return self._body


class Fake:
    """The real session as _http() hands it out: every request is counted on LEDGER."""

    def __init__(self, answer=None):
        self.sent, self.answer = [], answer

    def get(self, url, params=None, timeout=None):
        self.sent.append(url.rsplit("/", 1)[-1])
        return self.answer or R(200, [{"symbol": "X", "price": 10.0, "sector": "Tech",
                                       "date": "2027-06-30", "revenueAvg": 2.0}])


class Isolated(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.old = (fmp.LEDGER, fmp._session_cached, fmp_cache.CACHE)
        fmp.LEDGER = fmp.Ledger(os.path.join(self.tmp, "ledger.json"))
        fmp_cache.CACHE = fmp_cache.FmpCache(os.path.join(self.tmp, "fmp_cache.json"))
        self.http = fmp._session_cached = Fake()

    def tearDown(self):
        fmp.LEDGER, fmp._session_cached, fmp_cache.CACHE = self.old


class Cache(Isolated):
    def test_profile_cache_hit_sends_nothing_and_drops_the_price(self):
        first = fundamentals._fmp_profile("X", "k")
        second = fundamentals._fmp_profile("X", "k")
        self.assertEqual(self.http.sent, ["profile"])
        self.assertEqual((first[1], second[1]), (1, 0))
        self.assertEqual(first[2], "live")
        self.assertTrue(second[2].startswith("cache " + TODAY), second[2])
        self.assertNotIn("price", second[0][0], "a month-old price must not become the price")
        self.assertEqual(second[0][0]["sector"], "Tech")

    def test_estimates_cache_hit_sends_nothing(self):
        from domain.facts import FinancialFacts
        for _ in range(2):
            ff = FinancialFacts("X")
            fundamentals._estimate_path(ff, "X", "k", None, "2026-01-01")
        self.assertEqual(self.http.sent, ["analyst-estimates"])
        self.assertTrue(ff.provenance["fmp_cache.estimates"].startswith("cache "))

    def test_a_filing_after_the_fetch_forces_a_refetch(self):
        calls = []

        def fetch():
            calls.append(1)
            return [{"v": len(calls)}]
        c, day0 = fmp_cache.CACHE, 1_700_000_000.0          # 2023-11-14
        c.get("estimates", "X", fetch, bool, newest_filed="2023-11-01", now=day0)
        hit = c.get("estimates", "X", fetch, bool, newest_filed="2023-11-01", now=day0 + 86400)
        self.assertTrue(hit.hit)
        filed = time_day(day0 + 2 * 86400)
        new = c.get("estimates", "X", fetch, bool, newest_filed=filed, now=day0 + 2 * 86400)
        self.assertFalse(new.hit)
        self.assertEqual(new.data, [{"v": 2}])
        self.assertTrue(new.source.startswith("refetched: SEC filing " + filed), new.source)
        self.assertEqual(len(calls), 2)
        # the profile is not tied to filings, only to its 30 days
        c.get("profile", "X", fetch, bool, now=day0)
        self.assertTrue(c.get("profile", "X", fetch, bool, newest_filed="2099-01-01",
                              now=day0 + 29 * 86400).hit)
        self.assertFalse(c.get("profile", "X", fetch, bool, now=day0 + 30 * 86400).hit)

    def test_statements_refetched_after_a_new_10q(self):
        n = []
        bundle = {"income_annual": [{"revenue": 1}], "balance_annual": [{}], "cashflow_annual": [{}],
                  "_calls": 8}
        orig = (fundamentals.fmp.fetch, fundamentals.fmp.parse, fundamentals.dataquality.assess,
                fundamentals.dataquality.apply_stale_fallback)
        fundamentals.fmp.fetch = lambda t, k: n.append(1) or bundle
        fundamentals.fmp.parse = lambda b, alt: None
        fundamentals.dataquality.assess = lambda ff, *a: [{"code": "stale_financials"}]
        fundamentals.dataquality.apply_stale_fallback = lambda *a, **k: (True, [])
        try:
            from domain.facts import FinancialFacts
            spend = [fundamentals._stale_financials_fallback(FinancialFacts("X"), "X", "X", "k", "USD",
                                                             None, batch.Budget(99), filed)
                     for filed in ("2026-01-01", "2026-01-01", TODAY)]
        finally:
            (fundamentals.fmp.fetch, fundamentals.fmp.parse, fundamentals.dataquality.assess,
             fundamentals.dataquality.apply_stale_fallback) = orig
        self.assertEqual(spend, [8, 0, 8])
        self.assertEqual(len(n), 2)

    def test_cache_is_mirrored_and_merged_from_drive(self):
        import store_sync
        from sources import gdrive_store
        pushed, old = [], (store_sync.schedule_push_named, gdrive_store.drive_pull_json)
        store_sync.schedule_push_named = lambda p, name: pushed.append(name)
        import time
        remote = {"entries": {"profile:Y": {"at": time.time() - 60, "data": [{"sector": "Remote"}]}}}
        gdrive_store.drive_pull_json = lambda name: remote if name == "fmp_cache.json" else None
        try:
            fmp_cache.CACHE.get("profile", "X", lambda: [{"sector": "A"}], bool)
            got = fmp_cache.CACHE.get("profile", "Y", lambda: self.fail("Drive copy not used"), bool)
        finally:
            store_sync.schedule_push_named, gdrive_store.drive_pull_json = old
        self.assertEqual(pushed, ["fmp_cache.json"])
        self.assertEqual(got.data, [{"sector": "Remote"}])

    def test_newest_filing_comes_from_the_documents_already_read(self):
        cf = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [
            {"form": "10-K", "filed": "2026-02-01"}, {"form": "10-Q", "filed": "2026-07-28"},
            {"form": "8-K", "filed": "2026-09-01"}]}}}}}
        self.assertEqual(sec_gap.newest_filing(cf), "2026-07-28")
        self.assertEqual(sec_gap.newest_filing(dict(cf, _latest_filed="2026-10-30")), "2026-10-30")
        self.assertIsNone(sec_gap.newest_filing(None))


def time_day(epoch):
    return fmp_cache._day(epoch)


class Measured(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = fmp_budget.measure()

    def test_every_fmp_request_belongs_to_a_known_step(self):
        for sc, runs in self.m.items():
            for run, counts in runs.items():
                self.assertNotIn("unattributed", counts, (sc, run))

    def test_the_step_table_is_the_measurement(self):
        worst = {}
        for runs in self.m.values():
            for counts in runs.values():
                for step, n in counts.items():
                    worst[step] = max(worst.get(step, 0), n)
        self.assertEqual({s: n for s, n, _b in batch.FMP_STEPS}, worst)

    def test_partition_admits_at_the_measured_cost(self):
        upfront = sum(n for s, n, b in batch.FMP_STEPS if not b)
        self.assertEqual(batch.FMP_CALLS_PER_TICKER, upfront)
        self.assertEqual(batch.guaranteed_cost("k"), upfront)
        todo, err = batch.partition_by_quota(list("ABCDE"), batch.guaranteed_cost("k"),
                                             250 - 1 - 2 * upfront, 250)
        self.assertEqual((todo, err), (["A", "B"], ["C (quota)", "D (quota)", "E (quota)"]))

    def test_warm_cache_spends_only_earnings(self):
        self.assertEqual(self.m["typical"]["warm"], {"earnings": 1})
        self.assertEqual(self.m["typical"]["new filing"], {"earnings": 1, "estimates": 1})
        self.assertEqual(self.m["worst"]["new filing"].get("statements fallback"), 8)


class Badge(Isolated):
    def refuse(self):
        self.http.answer = R(200, [])
        fmp.fetch_quote("X", "k")
        fmp.fetch_quote("X", "k")
        self.http.answer = R(429, None, "Limit Reach")
        fmp.fetch_quote("X", "k")

    def test_ledger_keeps_sent_apart_from_refused(self):
        self.refuse()
        self.assertEqual(fmp.LEDGER.used(), 3)
        self.assertTrue(fmp.LEDGER.exhausted())
        self.assertRegex(fmp.LEDGER.refused_at(), r"^\d\d:\d\d$")
        s = {"fmp_usage": {}, "holdings": {}}
        store.sync_fmp_usage(s)
        self.assertEqual(s["fmp_usage"][store.today()], 3)
        self.assertEqual(store.fmp_used_today(s), config.QUOTA_CAP, "nothing more is admitted")
        import app
        q = app._quota(s)
        self.assertEqual((q["used"], q["exhausted"], q["refused_at"], q["headroom_calls"]),
                         (3, True, fmp.LEDGER.refused_at(), 0))

    def test_a_lost_ledger_file_is_restored_from_the_store(self):
        self.refuse()
        s = {"fmp_usage": {}}
        store.sync_fmp_usage(s)
        at = fmp.LEDGER.refused_at()
        fmp.LEDGER = fmp.Ledger(os.path.join(self.tmp, "restarted.json"))   # Render's disk
        store.sync_fmp_usage(s)
        self.assertTrue(fmp.LEDGER.exhausted())
        self.assertEqual((fmp.LEDGER.used(), fmp.LEDGER.refused_at()), (3, at))

    def test_reset_time_is_the_server_midnight_in_bangkok(self):
        utc = dt.datetime(2026, 11, 22, 15, 0, tzinfo=dt.timezone.utc)
        self.assertEqual(fmp.reset_time_bkk(utc), "07:00")
        self.assertEqual(fmp.reset_time_bkk(utc.astimezone(fmp.BKK)), "00:00")

    def test_badge_copy(self):
        if not shutil.which("node"):
            print("SKIP: node not available")
            return
        shipped = {os.path.basename(p): p for p in frontend_files()}
        js = open(shipped["portfolio.js"], encoding="utf-8").read()
        base = {"cap": 250, "holdings": 25, "reserved": 1, "per_ticker": 6, "reset_bkk": "07:00"}
        qs = {"used": dict(base, used=97, pct=39, warn=False, exhausted=False, refused_at=None,
                           headroom_calls=152, headroom_tickers=25),
              "refused": dict(base, used=97, pct=39, warn=True, exhausted=True, refused_at="14:05",
                              headroom_calls=0, headroom_tickers=0),
              "full": dict(base, used=250, pct=100, warn=True, exhausted=True, refused_at="23:40",
                           headroom_calls=0, headroom_tickers=0)}
        prog = js + "\nconsole.log(JSON.stringify(Object.fromEntries(Object.entries(%s).map(([k,q])=>[k,quotaText(q)]))));" % json.dumps(qs)
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as fh:
            fh.write(prog)
        try:
            out = json.loads(subprocess.run([shutil.which("node"), fh.name], capture_output=True,
                                            text=True, timeout=30).stdout)
        finally:
            os.unlink(fh.name)
        self.assertIn("ใช้ไป 97/250 ครั้ง", out["used"])
        self.assertIn("รีเซ็ต 07:00 น. เวลาไทย", out["used"])
        self.assertIn("6 ครั้ง/หุ้น", out["used"])
        self.assertIn("FMP ปฏิเสธเมื่อ 14:05 น.", out["refused"])
        self.assertIn("ส่งไปแล้ว 97 ครั้ง (ยังไม่ถึง 250)", out["refused"])
        self.assertNotIn("97/250", out["refused"], "a refusal never reads as calls used")
        self.assertIn("รีเซ็ต 07:00 น. เวลาไทย", out["refused"])
        self.assertIn("ส่งไปครบ 250 ครั้ง", out["full"])


if __name__ == "__main__":
    unittest.main()
