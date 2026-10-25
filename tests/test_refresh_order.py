"""A short FMP day must not starve the same tail tickers on every refresh, and a
ticker it skips must say so on its card. Fixture: the live store of 2026-10-25,
where 44 calls admitted the first 8 holdings and ASML (19th, waiting on its
quarterly base) was skipped again."""
import json
import os
import unittest

import config
from pipeline import batch, fundamentals, portfolio

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "refresh", "store_2026-10-25.json")


def live():
    with open(FIX, encoding="utf-8") as fh:
        return json.load(fh)


class Order(unittest.TestCase):
    def test_retry_first_on_the_live_store(self):
        s = live()
        facts = {t: {"flags": f} for t, f in s["flags"].items()}
        order = batch.refresh_order(s["holdings"], facts, {})
        todo, errors = batch.partition_by_quota(order, batch.FMP_CALLS_PER_TICKER,
                                                s["fmp_used"], config.QUOTA_CAP)
        self.assertEqual(len(todo), 4)                 # 20 left, 5 per ticker
        self.assertEqual(todo[:2], ["TSM", "ASML"])    # both carry a retry flag
        # list order alone (the old behaviour) leaves ASML out
        old, _ = batch.partition_by_quota(s["holdings"], 5, s["fmp_used"], config.QUOTA_CAP)
        self.assertNotIn("ASML", old)

    def test_oldest_refresh_next_then_list_order(self):
        at = {"A": "2026-10-25T03:29Z", "B": "2026-10-25T02:51Z"}
        self.assertEqual(batch.refresh_order(["A", "B", "C"], {}, at), ["C", "B", "A"])
        self.assertEqual(batch.refresh_order(["A", "B"], {}, {}), ["A", "B"])

    def test_every_retry_flag_carries_the_mark(self):
        root = os.path.dirname(os.path.dirname(__file__))
        for p in ("pipeline/quarterly.py", "pipeline/fundamentals.py"):
            src = open(os.path.join(root, p), encoding="utf-8").read()
            self.assertNotIn("retried on a later refresh)\"", src, p)
            self.assertNotIn("วันถัดไปจะลองใหม่", src, p)


class Skipped(unittest.TestCase):
    def store(self):
        return {"facts": {"ASML": {"flags": ["old"]}, "NVO": {}}, "results": {},
                "updated": {"ASML": "2026-10-25"}, "fmp_usage": {}, "rev_snapshots": {},
                "refreshed_at": {"ASML": "2026-10-25T02:51Z"}}

    def test_skip_is_recorded_and_cleared(self):
        s = self.store()
        fundamentals.commit_fundamentals(s, {}, 0, skipped={"ASML": "FMP quota"},
                                         now="2026-10-25T03:29Z")
        self.assertEqual(s["skipped"]["ASML"]["reason"], "FMP quota")
        self.assertEqual(s["refreshed_at"]["ASML"], "2026-10-25T02:51Z")   # not bumped
        note = portfolio.skipped_note(s, "ASML")
        self.assertIn("2026-10-25 09:51", note)       # last good run, Bangkok time
        self.assertIn("10:29", note)
        self.assertIn("FMP quota", note)
        self.assertIsNone(portfolio.skipped_note(s, "NVO"))

        from domain.facts import FinancialFacts

        class V:
            def to_dict(self):
                return {}
        fundamentals.commit_fundamentals(s, {"ASML": (FinancialFacts("ASML"), V())}, 0,
                                         now="2026-10-26T01:00Z")
        self.assertNotIn("ASML", s["skipped"])
        self.assertEqual(s["refreshed_at"]["ASML"], "2026-10-26T01:00Z")
        self.assertIsNone(portfolio.skipped_note(s, "ASML"))


if __name__ == "__main__":
    unittest.main()
