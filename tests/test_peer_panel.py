"""Peer panel and moat trend (docs/peer-panel/SPEC.md): five-year SEC revenue frames,
the extra fields on stored peer points, the pure panel and moat modules, and their
wiring. Offline: every fetch is faked, store_sync is stubbed to raise.

Tests named test_E<n>_... are the guards for docs/peer-panel/PAST_ERRORS.md row E<n>."""
import datetime as dt
import json
import os
import tempfile
import unittest
from types import SimpleNamespace

import store_sync
from sources import damodaran_companies as dc


def _refuse(*a, **k):
    raise AssertionError("a peer-panel test reached store_sync (real storage)")


for _name in dir(store_sync):
    if not _name.startswith("_") and callable(getattr(store_sync, _name)) \
            and getattr(getattr(store_sync, _name), "__module__", None) == "store_sync":
        setattr(store_sync, _name, _refuse)

TODAY = dt.date(2026, 11, 16)


class FakeFrames:
    """SEC frames by (concept, year); counts calls."""

    def __init__(self, answers, fail_years=()):
        self.answers, self.fail_years, self.calls = answers, set(fail_years), 0

    def get(self, url, **k):
        self.calls += 1
        concept = url.split("/us-gaap/")[1].split("/")[0]
        year = int(url.split("/CY")[1].split(".")[0])
        if year in self.fail_years:
            raise ConnectionError("down")
        data = self.answers.get((concept, year), [])
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"data": data})


class Frames(unittest.TestCase):
    def test_five_calendar_years_oldest_first(self):
        self.assertEqual(dc.panel_years(TODAY), [2021, 2022, 2023, 2024, 2025])
        self.assertEqual(dc.panel_years(dt.date(2026, 3, 31)), [2020, 2021, 2022, 2023, 2024])

    def test_E21_frames_keep_each_filers_period_end_and_the_larger_concept(self):
        ans = {}
        for y in dc.panel_years(TODAY):
            ans[("Revenues", y)] = [{"cik": 1, "val": 10 + y, "end": f"{y}-06-30"},
                                    {"cik": 2, "val": 5, "end": f"{y}-12-31"}]
            ans[("RevenueFromContractWithCustomerExcludingAssessedTax", y)] = [
                {"cik": 1, "val": 7, "end": f"{y}-06-29"}, {"cik": 2, "val": 9, "end": f"{y}-12-30"}]
        tmp = tempfile.mkdtemp()
        rq = FakeFrames(ans)
        got, notes = dc.load_revenue_frames_years(tmp, TODAY, "ua", requests_mod=rq)
        self.assertEqual(notes, [])
        self.assertEqual(sorted(got), [2021, 2022, 2023, 2024, 2025])
        self.assertEqual(got[2025][1], [2035, "2025-06-30"])      # Revenues won, its end kept
        self.assertEqual(got[2025][2], [9, "2025-12-30"])         # the other concept won
        calls = rq.calls
        again, _ = dc.load_revenue_frames_years(tmp, TODAY, "ua", requests_mod=rq)
        self.assertEqual(again, got)
        self.assertEqual(rq.calls, calls, "a second load inside the TTL is served from cache")
        rev, note = dc.load_revenue_frames(tmp, 2025, "ua", requests_mod=rq)
        self.assertEqual(rev, {1: 2035, 2: 9})                   # the old reader is unchanged

    def test_E13_a_failed_year_is_named_not_silent(self):
        ans = {("Revenues", y): [{"cik": 1, "val": 1, "end": f"{y}-12-31"}] for y in dc.panel_years(TODAY)}
        got, notes = dc.load_revenue_frames_years(tempfile.mkdtemp(), TODAY, "ua",
                                                  requests_mod=FakeFrames(ans, fail_years={2022}))
        self.assertNotIn(2022, got)
        self.assertEqual(len(notes), 1)
        self.assertIn("CY2022", notes[0])

    def test_E15_a_cache_written_before_end_dates_reads_with_no_end(self):
        tmp = tempfile.mkdtemp()
        import time
        with open(os.path.join(tmp, "sec_frames_revenue_CY2025.json"), "w") as fh:
            json.dump({"at": time.time(), "rev": {"1": 5}}, fh)
        rev, end, note = dc._frames(tmp, 2025, "ua", requests_mod=FakeFrames({}))
        self.assertEqual((rev, end, note), ({1: 5}, {}, None))


if __name__ == "__main__":
    unittest.main()
