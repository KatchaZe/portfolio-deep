"""SPEC R32-R37 (2026-11-22, share-data fix): the industry share counts every Damodaran member
SEC holds revenue for, once, on one revenue rule and one calendar-year rule.

Each test names the failure mode an independent audit found (share_xcheck report, D1-D4):
R32 dotted share-class tickers (BRK.A, BF.B, RUSH.A, QVCG.A) resolve to one CIK; two CIKs for
    one row never match.
R33 a member missing from today's company_tickers.json is matched by its exact normalised
    name to one SEC frames entity with revenue in the panel years, else listed as
    "หา CIK ไม่ได้" with a count.
R34 ifrs-full Revenue frames in USD count (GFS); a non-USD IFRS filer is named, not counted.
R35 the revenue a filer-year counts is the concept of the newest filing, Revenues first, a
    contract-revenue concept only when Revenues is materially below it in that filing, a
    1000x unit slip dropped; never the largest across filings.
R36 a fiscal year ending within 7 days of June 30 is the calendar year it ends in.
R37 the sub-group stores a scope note: it is Damodaran's SIC group.
Display only (PP1): none of this reaches the relative model's peer sizing."""
import json
import os
import unittest

from domain import frames_revenue as FR
from domain import member_cik as MC
from domain import peer_panel as PP

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = json.load(open(os.path.join(HERE, "fixtures", "share_frames_2026_11.json"), encoding="utf-8"))
Y5 = [2021, 2022, 2023, 2024, 2025]
AS_OF = "2026-11-16"


def _entries(cik, year):
    """{concept: {val, end, accn}} for one filer-year from the real frames fixture."""
    out = {}
    for concept, rows in FIX["frames"].get(str(year), {}).items():
        for d in rows:
            if d["cik"] == cik:
                out["us-gaap:" + concept] = {"val": d["val"], "end": d["end"], "accn": d["accn"]}
    return out


class ShareClassTickers(unittest.TestCase):
    """R32. Damodaran writes BRK.A; SEC writes BRK-A / BRK-B, RUSHA, QVCG (D2)."""
    SEC = {"BRK-A": 1067983, "BRK-B": 1067983, "BF-A": 14693, "BF-B": 14693, "RUSHA": 1012019,
           "RUSHB": 1012019, "QVCG": 1254699, "MOG-A": 67887, "MOG-B": 67887, "XYZ": 5, "XYZ-B": 6}
    TITLES = {1067983: "BERKSHIRE HATHAWAY INC", 1254699: "QVC Group, Inc.", 5: "XYZ Corp", 6: "Other Co"}

    def res(self, t, name=""):
        return MC.resolve(t, name, None, self.SEC, self.TITLES, MC.NameIndex({}), set())

    def test_dash_and_joined_spellings_find_the_one_cik(self):
        for t, cik in (("BRK.A", 1067983), ("BF.B", 14693), ("RUSH.A", 1012019), ("MOG.A", 67887)):
            r = self.res(t)
            self.assertEqual((r["cik"], r["how"]), (cik, "share_class"), t)

    def test_bare_base_needs_the_sec_title_to_agree(self):
        self.assertEqual(self.res("QVCG.A", "QVC Group Inc. (NasdaqCM:QVCG.A)")["cik"], 1254699)
        r = self.res("QVCG.A", "Some Other Company Inc.")
        self.assertIsNone(r["cik"])
        self.assertTrue(r["reason"])

    def test_two_ciks_for_one_row_never_match(self):
        r = self.res("XYZ.A", "XYZ Corp")
        self.assertIsNone(r["cik"])
        self.assertIn("2", r["reason"])

    def test_share_classes_of_one_filer_count_once(self):
        members = {"BRK.A": 1067983, "BRK.B": 1067983, "MKL": 2}
        frames = {y: {1067983: [370e9, f"{y}-12-31"], 2: [15e9, f"{y}-12-31"]} for y in Y5}
        s = PP.industry_share(members, frames, 2, Y5, AS_OF)
        self.assertEqual(s["n_members"], 2)
        self.assertAlmostEqual(s["target_share"]["2025"], 15 / 385)
        self.assertEqual([m["symbols"] for m in s["chart"]["members"]][0], ["BRK.A", "BRK.B"])


class NameFallback(unittest.TestCase):
    """R33. Qorvo, SkyWater and Masimo are not in today's company_tickers.json yet SEC
    frames hold their revenue (D1). Real names from indname.xls and the frames cache."""
    ENT = {1604778: "Qorvo, Inc.", 1819974: "SkyWater Technology, Inc.", 937556: "MASIMO CORP",
           1944013: "CRANE COMPANY", 1625297: "Indivior Pharmaceuticals, Inc.",
           886158: "BED BATH & BEYOND INC.", 1130713: "BED BATH & BEYOND,\xa0INC.", 777: "Quiet Co"}
    REV = {1604778, 1819974, 937556, 1944013, 1625297, 886158, 1130713}

    def res(self, t, name):
        return MC.resolve(t, name, None, {}, {}, MC.NameIndex(self.ENT), self.REV)

    def test_real_pairs_match_by_exact_normalised_name(self):
        for t, name, cik in (("QRVO", "Qorvo, Inc. (NasdaqGS:QRVO)", 1604778),
                             ("SKYT", "SkyWater Technology, Inc. (NasdaqCM:SKYT)", 1819974),
                             ("MASI", "Masimo Corporation (NasdaqGS:MASI)", 937556)):
            r = self.res(t, name)
            self.assertEqual((r["cik"], r["how"]), (cik, "name"), t)

    def test_a_near_name_is_not_a_match(self):
        for t, name in (("CHAC", "Crane Harbor Acquisition Corp. (NasdaqGM:CHAC)"),
                        ("INDV", "Indivior PLC (NasdaqGS:INDV)")):
            r = self.res(t, name)
            self.assertIsNone(r["cik"], t)
            self.assertTrue(r["reason"], t)

    def test_two_entities_with_the_name_is_ambiguous(self):
        r = self.res("BBBY", "Bed Bath & Beyond, Inc. (NYSE:BBBY)")
        self.assertIsNone(r["cik"])
        self.assertIn("2", r["reason"])

    def test_a_name_hit_with_no_revenue_in_the_panel_years_is_refused(self):
        r = self.res("QT", "Quiet Co. (NYSE:QT)")
        self.assertIsNone(r["cik"])
        self.assertTrue(r["reason"])

    def test_a_renamed_filer_matches_on_the_name_damodaran_lists(self):
        idx = MC.NameIndex({1563568: ["Envirotech Vehicles, Inc.", "AZIO AI HOLDINGS, INC."]})
        r = MC.resolve("EVTV", "Envirotech Vehicles, Inc. (NasdaqCM:EVTV)", None, {}, {}, idx, {1563568})
        self.assertEqual(r["cik"], 1563568)

    def test_sec_state_suffix_is_not_part_of_the_name(self):
        self.assertEqual(MC.sec_normalise("SEALED AIR CORP/DE"), MC.sec_normalise("Sealed Air Corporation"))

    def test_unresolved_members_are_counted_in_a_thai_flag(self):
        members = {"A": 1, "B": 2, "QX": None, "QY": None}
        frames = {y: {1: [10.0, f"{y}-12-31"], 2: [30.0, f"{y}-12-31"]} for y in Y5}
        s = PP.industry_share(members, frames, 1, Y5, AS_OF,
                              unresolved={"QX": "ไม่พบชื่อ", "QY": "ชื่อตรง 2 ราย"})
        f = [x for x in s["flags"] if "หา CIK ไม่ได้" in x]
        self.assertEqual(len(f), 1, s["flags"])
        self.assertIn("2 ราย", f[0])
        self.assertEqual(s["members_unresolved"], 2)
        self.assertAlmostEqual(s["coverage_incl_unresolved"]["2025"], 2 / 4)
        self.assertEqual(s["coverage"]["2025"], 1.0)


class RevenueRule(unittest.TestCase):
    """R35. The max of four concepts picked TMDX CY2021 RevenueFromContract $31.385M from its
    FY2021 10-K over Revenues $30.262M that every later 10-K reports (D3)."""

    def test_named_filers_keep_their_value_except_tmdx_2021(self):
        old = FIX["old_max_of_four"]
        changed = []
        for t in ("NVDA", "ABT", "MDT", "UNH", "CI", "ELV", "CNC", "HUM", "MOH", "ALHC", "CLOV", "HQY",
                  "MELI", "TMDX"):
            cik = FIX["ciks"][t]
            for y in Y5:
                e = _entries(cik, y)
                if not e:
                    continue
                got = FR.choose(e)["val"]
                if got != old[str(y)][str(cik)][0]:
                    changed.append((t, y, got, old[str(y)][str(cik)][0]))
        self.assertEqual(changed, [("TMDX", 2021, 30262000, 31385000)])

    def test_a_concept_the_company_stopped_reporting_loses(self):
        ma = FR.choose(_entries(FIX["ciks"]["MA"], 2021))
        self.assertEqual((ma["val"], ma["concept"]), (18884000000, "us-gaap:Revenues"))

    def test_revenues_far_below_contract_revenue_in_one_filing_is_a_component_line(self):
        for t, y, want in (("BLK", 2021, 19374000000), ("GIS", 2021, 18992800000), ("BNSF", 2021, 23282000000)):
            got = FR.choose(_entries(FIX["ciks"][t], y))
            self.assertEqual(got["val"], want, t)
            self.assertTrue(got["note"], t)

    def test_a_thousandfold_unit_slip_is_dropped_and_noted(self):
        got = FR.choose(_entries(FIX["ciks"]["TIGO"], 2022))
        self.assertEqual(got["val"], 81323000)
        self.assertIn("1000", got["note"])

    def test_never_above_revenues_of_the_same_filing_without_a_note(self):
        for t, cik in FIX["ciks"].items():
            for y in Y5:
                e = _entries(cik, y)
                rv = e.get("us-gaap:Revenues")
                if not e or not rv:
                    continue
                got = FR.choose(e)
                if got["val"] > rv["val"] * 1.01:
                    self.assertTrue(got["note"], (t, y))

    def test_ifrs_counts_only_without_a_us_gaap_value(self):
        e = {"ifrs-full:Revenue": {"val": 6.791e9, "end": "2025-12-31", "accn": "0001709048-26-000010"}}
        self.assertEqual(FR.choose(e)["concept"], "ifrs-full:Revenue")
        e["us-gaap:Revenues"] = {"val": 1.0, "end": "2025-12-31", "accn": "0001709048-26-000010"}
        self.assertEqual(FR.choose(e)["concept"], "us-gaap:Revenues")


class JuneYears(unittest.TestCase):
    """R36. SEC frames put SYNA's FY ending 2026-06-27 in CY2025 beside RMD's ending
    2025-06-30, and drop SYNA's FY2024 (D4)."""

    def test_window_rule(self):
        for end, cy in (("2025-06-28", 2025), ("2026-06-27", 2026), ("2025-06-30", 2025),
                        ("2025-07-06", 2025), ("2025-06-23", 2025)):
            self.assertEqual(FR.june_cy(end), cy, end)
        for end in ("2025-07-08", "2025-06-22", "2025-05-31", "2025-12-31"):
            self.assertIsNone(FR.june_cy(end), end)

    def test_syna_and_rmd_share_one_calendar_year(self):
        by_sec = {}
        for y in Y5:
            for t in ("SYNA", "RMD"):
                e = _entries(FIX["ciks"][t], y)
                if e:
                    c = FR.choose(e)
                    by_sec.setdefault(y, {})[FIX["ciks"][t]] = [c["val"], c["end"], c["concept"], c["note"]]
        cy = FR.rebucket(by_sec)
        syna, rmd = FIX["ciks"]["SYNA"], FIX["ciks"]["RMD"]
        self.assertEqual(cy[2025][syna][1], "2025-06-28")
        self.assertEqual(cy[2025][rmd][1], "2025-06-30")
        self.assertEqual(cy[2023][syna][1], "2023-06-24")
        for y, rows in cy.items():
            for c, row in rows.items():
                if FR.june_cy(row[1]):
                    self.assertEqual(FR.june_cy(row[1]), y)

    def test_target_clock_uses_the_same_rule(self):
        self.assertEqual(PP.frames_cy("2025-06-28", {"2025-06-28": 2024}), 2025)
        self.assertEqual(PP.frames_cy("2025-05-31", {"2025-05-31": 2024}), 2024)


class Ifrs(unittest.TestCase):
    """R34. GlobalFoundries files IFRS in USD: it counted as a member with no revenue (D1)."""

    class _Resp:
        def __init__(self, code, data=None):
            self.status_code, self._d = code, data

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(self.status_code)

        def json(self):
            return self._d

    def _fake(self, calls):
        R = self._Resp

        def get(url, headers=None, timeout=None):
            calls.append(url)
            if "/ifrs-full/Revenue/USD/" in url:
                return R(200, {"data": [{"cik": 1709048, "entityName": "GLOBALFOUNDRIES Inc.",
                                         "accn": "0001709048-26-000010", "end": "2025-12-31",
                                         "val": 6791000000}]})
            if "/ifrs-full/Revenue/EUR/" in url:
                return R(200, {"data": [{"cik": 4242, "entityName": "Euro Filer N.V.",
                                         "accn": "0000004242-26-000001", "end": "2025-12-31", "val": 5e9}]})
            if "/us-gaap/Revenues/USD/" in url:
                return R(200, {"data": [{"cik": 1045810, "entityName": "NVIDIA CORP",
                                         "accn": "0001045810-26-000021", "end": "2026-01-25",
                                         "val": 215938000000}]})
            return R(404)
        return type("Req", (), {"get": staticmethod(get)})

    def test_usd_ifrs_counts_and_other_currencies_are_named(self):
        import tempfile
        from sources import damodaran_companies as dc
        calls = []
        with tempfile.TemporaryDirectory() as d:
            rev, end, note = dc._frames(d, 2025, "test", self._fake(calls), throttle=lambda: None)
            panel, names, other = dc.panel_frames_of(d, 2025)
        self.assertIsNone(note)
        self.assertEqual(panel[1709048][0], 6791000000)
        self.assertEqual(panel[1709048][2], "ifrs-full:Revenue")
        self.assertNotIn(1709048, rev)               # relative sizing keeps the us-gaap max (PP1)
        self.assertEqual(other, {4242: "EUR"})
        self.assertNotIn(4242, panel)
        self.assertEqual(names[1709048], "GLOBALFOUNDRIES Inc.")
        self.assertEqual(len(calls), len(dc.FRAMES_CONCEPTS) + 1 + len(dc.IFRS_OTHER_CURRENCIES))

    def test_non_usd_ifrs_members_are_flagged_not_counted(self):
        members = {"A": 1, "B": 2, "EU": 3}
        frames = {y: {1: [10.0, f"{y}-12-31"], 2: [30.0, f"{y}-12-31"]} for y in Y5}
        s = PP.industry_share(members, frames, 1, Y5, AS_OF, ifrs_other={3: "EUR", 99: "JPY"})
        f = [x for x in s["flags"] if "IFRS" in x]
        self.assertEqual(f, ["รายงาน IFRS สกุล EUR ไม่นับ 1 ราย (EU)"])
        self.assertEqual(s["members_ifrs_other"], [["EUR", 1]])


class Scope(unittest.TestCase):
    """R37. The sub-group is Damodaran's SIC group; a same-business firm filed under another
    SIC is outside it, and the stored panel says so."""

    def test_sub_group_carries_its_scope_note(self):
        members = {"T": 1, "P": 2, "Q": 3}
        frames = {y: {c: [10.0 * c, f"{y}-12-31"] for c in (1, 2, 3)} for y in Y5}
        sub = PP.sub_group(members, frames, 1, Y5, AS_OF, sics={"T": "3674", "P": "3674", "Q": "3672"},
                           target_symbol="T")
        self.assertIn("3674", sub["scope_note"])
        self.assertIn("SIC อื่น", sub["scope_note"])
        empty = PP.sub_group(members, frames, 1, Y5, AS_OF, sics={}, target_symbol="T")
        self.assertIn("scope_note", empty)


if __name__ == "__main__":
    unittest.main()
