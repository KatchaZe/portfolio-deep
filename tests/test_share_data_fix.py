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


def _e(val, accn, end="2024-12-31"):
    return {"val": val, "end": end, "accn": accn}


class Excise(unittest.TestCase):
    """E59, R41. Industry share counts NET revenue, after excise, for every filer. Values are
    the SEC frames entries in sec_cache (fetched 2026-11-20) and each 10-K's net sales:
    TAP FY2024 net sales $11,627.0M (sales $13,734.3M, excise $2,107.3M); STZ FY2025 net
    sales $10,208.7M (excise $748.2M); SAM FY2024 net revenue $2,012.926M (excise
    $124.876M); BF FY2025 net sales $3,975M."""
    X = "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"
    I = "us-gaap:RevenueFromContractWithCustomerIncludingAssessedTax"
    T = "us-gaap:ExciseAndSalesTaxes"

    def test_swapped_tags_take_the_smaller_and_say_so(self):
        a = "0000024545-26-000006"
        got = FR.choose({self.X: _e(13734300000, a), self.I: _e(11627000000, a), self.T: _e(2107300000, a)})
        self.assertEqual((got["val"], got["concept"], got["note"]), (11627000000, self.I, "excise_tags_swapped"))

    def test_correct_tags_keep_the_excluding_value(self):
        a = "0000016918-26-000011"
        got = FR.choose({self.X: _e(10208700000, a), self.I: _e(10956900000, a), self.T: _e(748200000, a)})
        self.assertEqual((got["val"], got["note"]), (10208700000, None))

    def test_revenues_net_of_excise_is_not_a_component_line(self):
        a = "0001193125-26-067467"
        got = FR.choose({"us-gaap:Revenues": _e(3975000000, a), self.I: _e(5056000000, a), self.T: _e(1081000000, a)})
        self.assertEqual((got["val"], got["concept"]), (3975000000, "us-gaap:Revenues"))
        sam = FR.choose({"us-gaap:Revenues": _e(2012926000, a), self.I: _e(2137802000, a), self.T: _e(124876000, a)})
        self.assertEqual(sam["val"], 2012926000)

    def test_revenues_tagged_gross_gives_way_to_the_net_figure(self):
        a = "0000000001-25-000001"
        got = FR.choose({"us-gaap:Revenues": _e(1200.0, a), self.X: _e(1000.0, a), self.T: _e(200.0, a)})
        self.assertEqual((got["val"], got["concept"], got["note"]), (1000.0, self.X, "excise_net"))

    def test_no_excise_evidence_changes_nothing(self):
        a = "0001524472-22-000010"                   # Xylem CY2021: no excise, Including is not gross
        got = FR.choose({self.X: _e(5195000000, a), self.I: _e(4998000000, a)})
        self.assertEqual((got["val"], got["note"]), (5195000000, None))
        far = FR.choose({self.X: _e(5195000000, a), self.I: _e(4998000000, a), self.T: _e(50000000, a)})
        self.assertEqual(far["val"], 5195000000)

    def test_swapped_tags_flag_names_the_filer(self):
        frames = {y: {1: [10.0, f"{y}-12-31", self.I, "excise_tags_swapped"], 2: [30.0, f"{y}-12-31"]} for y in Y5}
        s = PP.industry_share({"TAP": 1, "B": 2}, frames, 1, Y5, AS_OF)
        self.assertIn("แท็ก SEC สลับกัน ใช้ค่ารายได้สุทธิที่ต่ำกว่า 1 ราย (TAP)", s["flags"])


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
        self.assertEqual(len(calls), len(dc.FRAMES_CONCEPTS) + 1 + 1 + len(dc.IFRS_OTHER_CURRENCIES))

    def test_excise_fetch_failure_keeps_the_panel_and_is_noted(self):
        import tempfile
        from sources import damodaran_companies as dc
        calls, base = [], self._fake([])

        def get(url, headers=None, timeout=None):
            calls.append(url)
            if "/ExciseAndSalesTaxes/" in url:
                raise ConnectionError("excise down")
            return base.get(url)
        req = type("Req", (), {"get": staticmethod(get)})
        with tempfile.TemporaryDirectory() as d:
            dc._frames(d, 2025, "test", req, throttle=lambda: None)
            panel, _n, _o = dc.panel_frames_of(d, 2025)
            cache = json.load(open(os.path.join(d, "sec_frames_revenue_CY2025.json")))
        self.assertEqual(panel[1045810][0], 215938000000)
        self.assertIs(cache["excise_read"], False)

    def test_non_usd_ifrs_members_are_flagged_not_counted(self):
        members = {"A": 1, "B": 2, "EU": 3}
        frames = {y: {1: [10.0, f"{y}-12-31"], 2: [30.0, f"{y}-12-31"]} for y in Y5}
        s = PP.industry_share(members, frames, 1, Y5, AS_OF, ifrs_other={3: "EUR", 99: "JPY"})
        f = [x for x in s["flags"] if "IFRS" in x]
        self.assertEqual(f, ["รายงาน IFRS สกุล EUR ไม่นับ 1 ราย (EU)"])
        self.assertEqual(s["members_ifrs_other"], [["EUR", 1]])


class NoRevenue(unittest.TestCase):
    """E58. A member with a CIK and no revenue in any panel year (GFS on an offline cache with
    no IFRS frames) was counted in n_members with no reason and no flag. Each such member
    is named with a Thai reason and counted in a flag."""
    members = {"A": 1, "B": 2, "GFS": 1709048}

    def frames(self):
        return {y: {1: [10.0, f"{y}-12-31"], 2: [30.0, f"{y}-12-31"]} for y in Y5}

    def test_member_with_no_revenue_anywhere_is_named_and_flagged(self):
        s = PP.industry_share(self.members, self.frames(), 1, Y5, AS_OF)
        self.assertEqual([r[0] for r in s["no_revenue"]], ["GFS"])
        self.assertIn("ifrs-full", s["no_revenue"][0][2])
        self.assertTrue(any("ไม่มีรายได้ใน SEC frames" in f and "1 ราย" in f and "GFS" in f for f in s["flags"]))

    def test_unread_ifrs_frames_are_the_named_reason(self):
        s = PP.industry_share(self.members, self.frames(), 1, Y5, AS_OF, ifrs_unread=[2024, 2025])
        self.assertIn("IFRS", s["no_revenue"][0][2])
        self.assertIn("CY2024", s["no_revenue"][0][2])

    def test_a_balance_sheet_with_no_revenue_is_a_different_reason(self):
        assets = {y: {1, 2, 1709048} for y in Y5}
        s = PP.industry_share(self.members, self.frames(), 1, Y5, AS_OF, assets=assets)
        self.assertIn("รายได้ 0", s["no_revenue"][0][2])

    def test_non_usd_ifrs_member_is_not_named_twice(self):
        s = PP.industry_share(self.members, self.frames(), 1, Y5, AS_OF, ifrs_other={1709048: "EUR"})
        self.assertEqual(s["no_revenue"], [])

    def test_sub_group_carries_the_reason(self):
        sics = {"A": "3674", "B": "3674", "GFS": "3674"}
        sub = PP.sub_group(self.members, self.frames(), 1, Y5, AS_OF, sics=sics, target_symbol="A")
        self.assertEqual([r[0] for r in sub["no_revenue"]], ["GFS"])
        self.assertTrue(any(f.startswith("sub-group SIC 3674: ไม่มีรายได้") for f in sub["flags"]))


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
