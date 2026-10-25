"""Every request FMP sees is counted, once, in one ledger that never goes down
within a day; an over-limit answer ends the day. Live 2026-10-25: the app said
231/250, FMP's dashboard 274/250 (uncounted /api/v3 fallbacks, and a store counter
that came back lower after a restart); past the limit every FMP field went quietly
to a fallback."""
import json
import os
import tempfile
import unittest

import config
import store
from sources import fmp


class R:
    def __init__(self, status=200, body=None, text=""):
        self.status_code, self._body, self.text = status, body, text

    def json(self):
        return self._body


class Fake:
    def __init__(self, *answers):
        self.answers, self.sent = list(answers), []

    def get(self, url, params=None, timeout=None):
        self.sent.append(url)
        return self.answers.pop(0) if self.answers else R(200, [])


class Ledger(unittest.TestCase):
    def setUp(self):
        self.old_ledger, self.old_session = fmp.LEDGER, fmp._session_cached
        fmp.LEDGER = fmp.Ledger(os.path.join(tempfile.mkdtemp(), "fmp_ledger.json"))

    def tearDown(self):
        fmp.LEDGER, fmp._session_cached = self.old_ledger, self.old_session

    def real(self, *answers):
        fmp._session_cached = Fake(*answers)      # what _http() hands out for real calls
        return fmp._session_cached

    def test_one_request_one_count_and_no_v3(self):
        http = self.real(R(200, []))              # /stable answers empty
        self.assertEqual(fmp.fetch_estimates("ASML", "k"), [])
        self.assertEqual(len(http.sent), 1)       # no /api/v3 retry any more
        self.assertNotIn("/api/v3", http.sent[0])
        self.assertEqual(fmp.LEDGER.used(), 1)

    def test_over_limit_ends_the_day(self):
        http = self.real(R(429, None, '{"Error Message": "Limit Reach . Please upgrade"}'))
        self.assertEqual(fmp.fetch_profile("NVO", "k"), [])
        self.assertTrue(fmp.LEDGER.exhausted())
        self.assertEqual(fmp.LEDGER.used(), config.QUOTA_CAP)
        fmp.fetch_earnings("NVO", "k")
        fmp.fetch(  "NVO", "k", sleep=0)
        self.assertEqual(len(http.sent), 1)       # nothing more went out

    def test_limit_text_on_a_200_counts_too(self):
        self.real(R(200, {"Error Message": "Limit Reach"}, '{"Error Message": "Limit Reach"}'))
        fmp.fetch_quote("NVO", "k")
        self.assertTrue(fmp.LEDGER.exhausted())

    def test_cap_stops_sending(self):
        http = self.real()
        fmp.LEDGER.raise_to(config.QUOTA_CAP)
        fmp.fetch_estimates("ASML", "k")
        self.assertEqual(http.sent, [])

    def test_injected_fake_is_not_counted(self):
        fmp.fetch_estimates("ASML", "k", requests_mod=Fake(R(200, [{"date": "2026"}])))
        self.assertEqual(fmp.LEDGER.used(), 0)

    def test_yesterday_does_not_count(self):
        with open(fmp.LEDGER.path, "w") as fh:
            json.dump({"date": "2000-01-01", "n": 200, "exhausted": True}, fh)
        self.assertEqual(fmp.LEDGER.used(), 0)
        self.assertFalse(fmp.LEDGER.exhausted())

    def test_store_restored_lower_never_lowers_the_count(self):
        self.real()
        for _ in range(3):
            fmp.fetch_estimates("X", "k")
        fmp.LEDGER.raise_to(274)
        s = {"fmp_usage": {store.today(): 207}}            # older Drive copy
        self.assertEqual(store.fmp_used_today(s), 274)
        self.assertEqual(store.sync_fmp_usage(s), 274)
        s = {"fmp_usage": {store.today(): 280}}            # store knows more
        store.sync_fmp_usage(s)
        self.assertEqual(fmp.LEDGER.used(), 280)

    def test_bundle_counts_each_endpoint(self):
        self.real()
        fmp.fetch("NVO", "k", sleep=0)
        self.assertEqual(fmp.LEDGER.used(), len(fmp.ENDPOINTS))


if __name__ == "__main__":
    unittest.main()
