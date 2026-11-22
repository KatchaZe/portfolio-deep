"""Fix round 2026-11-22 on the same-business panel (SPEC R29-R30, D1-D8).

D1 S2's fallback reason names the rule that failed and its counts, not "0 firms".
D4 the sub-group's own flags reach the panel's flags.
D6 the same-business view's medians are over its own rows, none under 3.
D7 S2 carries n_members beside matched_n.
R29 a target Damodaran lists only on its home exchange gets its SIC from his non-US rows by
    a unique name-and-country match; members and shares stay on the US rows.
R30 per-member share series for a market-share chart (matched set as R13), and the
    target's rank.
Each test names the failure mode it exists for. Display only (PP1)."""
import unittest
from types import SimpleNamespace

from domain import contracts
from domain import global_sic as GS
from domain import moat_trend as MT
from domain import peer_panel as PP
from pipeline import peers
from sources import damodaran_companies as dc
from tests.test_peer_panel import TODAY, Y5, frames_for, pp, tfacts
from tests.test_peer_panel_sic import AS_OF, sub_universe


def tesla_like():
    """A 9-filer SIC 3711 sub-group where CY2022 has 6 of 9 reporting (67% < 70%)."""
    members = {f"A{i}": i + 1 for i in range(9)}
    rev = {i + 1: [10.0 + i] * 5 for i in range(9)}
    for c in (7, 8, 9):
        rev[c][1] = None                       # three filers miss CY2022
    sics = {t: "3711" for t in members}
    return PP.sub_group(members, frames_for(Y5, rev), 1, Y5, AS_OF, {}, None, sics, "A0")


class FallbackReason(unittest.TestCase):
    def test_coverage_failure_is_named_with_its_counts_not_zero_firms(self):
        sub = tesla_like()
        self.assertIsNone(sub["matched_n"], "the failed year leaves no matched set")
        ind = {"years": Y5, "matched_share_then": .1, "matched_share_now": .1, "matched_n": 50,
               "n_members": 60, "sub_group": sub}
        sig, _ = MT.share_change(ind)
        r = sig["basis_reason"]
        self.assertEqual(sig["basis"], "industry")
        self.assertIn("CY2022 มีข้อมูลใน SEC 6 จาก 9 ราย (67% ต่ำกว่าเกณฑ์ 70%)", r)
        self.assertNotIn("มี 0 ราย", r)

    def test_too_few_matched_names_the_count_and_the_years(self):
        sub = {"sic": "5960", "years": Y5, "coverage": {"2022": 1.0, "2025": 1.0}, "matched_n": 3}
        self.assertIn("มี 3 รายที่มีรายได้ทั้ง CY2022 และ CY2025 (ต้อง ≥5)", MT.sub_fallback_reason(sub))

    def test_matched_set_under_seventy_percent_is_its_own_cause(self):
        sub = {"sic": "1000", "years": Y5, "coverage": {"2022": .9, "2025": .9}, "matched_n": 6,
               "n_with_revenue": {"2022": 10}, "share_change_pp": None, "matched_share_then": .1,
               "matched_top5_then": None}
        self.assertIn("มีรายได้ทั้ง CY2022 และ CY2025 6 จาก 10 ราย", MT.sub_fallback_reason(sub))

    def test_target_missing_from_the_sub_group_is_its_own_cause(self):
        sub = {"sic": "1000", "years": Y5, "coverage": {"2022": .9, "2025": .9}, "matched_n": 6,
               "share_change_pp": None, "matched_share_then": None}
        self.assertIn("ไม่มีรายได้ของหุ้นนี้", MT.sub_fallback_reason(sub))

    def test_a_group_of_one_keeps_its_own_reason(self):
        r = MT.sub_fallback_reason({"sic": "2834", "reason": "กลุ่มธุรกิจเดียวกัน (SIC 2834) มีแค่หุ้นนี้"})
        self.assertIn("มีแค่หุ้นนี้", r)
        self.assertIn("S2 ใช้ทั้งอุตสาหกรรม", r)

    def test_S2_says_how_many_members_beside_how_many_matched(self):
        sub = {"sic": "6324", "years": Y5, "matched_share_then": .3, "matched_share_now": .4,
               "matched_n": 5, "n_members": 7}
        sig, _ = MT.share_change({"years": Y5, "sub_group": sub})
        self.assertEqual((sig["matched_n"], sig["n_members"]), (5, 7))


class SubFlags(unittest.TestCase):
    def test_the_sub_groups_coverage_flag_reaches_the_panel(self):
        sub = tesla_like()
        panel = PP.build(dict(pp("A0"), symbol="A0"), [pp("A1")], AS_OF, "Auto", "b",
                         {"flags": [], "sub_group": sub}, sics={f"A{i}": "3711" for i in range(9)})
        self.assertTrue(any(f.startswith("sub-group SIC 3711: CY2022: 6 of 9") for f in panel["flags"]),
                        panel["flags"])


class SubMedian(unittest.TestCase):
    def table(self, codes):
        sics = {"T": "3845", **{f"P{i}": c for i, c in enumerate(codes)}}
        tgt = dict(pp("T"), symbol="T")
        ps = [dict(pp(f"P{i}", rev=(i + 1) * 1e9), symbol=f"P{i}") for i in range(len(codes))]
        return PP.table(tgt, ps, AS_OF, sics)[0]

    def test_median_is_over_the_same_business_rows_only(self):
        t = self.table(["3845", "3845", "3845", "3841", "3841"])
        self.assertEqual(t["sub_stats"]["revenue_ttm"]["n"], 3)
        self.assertEqual(t["sub_stats"]["revenue_ttm"]["median"], 2e9, "P0..P2: 1, 2, 3 bn")
        self.assertEqual(t["stats"]["revenue_ttm"]["median"], 3e9, "the whole group: 1..5 bn")

    def test_under_three_rows_is_no_median_with_its_count(self):
        t = self.table(["3845", "3845", "3841"])
        self.assertEqual((t["sub_stats"]["revenue_ttm"]["n"], t["sub_stats"]["revenue_ttm"]["median"]), (2, None))

    def test_a_financial_row_gives_no_gm_to_the_median(self):
        sics = {"T": "6324", "P0": "6324", "P1": "6324", "P2": "6324"}
        ps = [dict(pp(f"P{i}"), symbol=f"P{i}") for i in range(3)]
        t = PP.table(dict(pp("T"), symbol="T"), ps, AS_OF, sics)[0]
        self.assertEqual(t["sub_stats"]["gross_margin_fy"]["n"], 0)
        self.assertEqual(contracts.undeclared_panel_keys(t), [])


class Chart(unittest.TestCase):
    def share(self, **kw):
        rows, members, foreign, fr = sub_universe()
        kw = dict(dict(members=members, frames=fr, target_cik=1, years=Y5, as_of=AS_OF, foreign=foreign),
                  **kw)
        return PP.industry_share(**kw)

    def test_each_years_shares_sum_to_one_and_the_target_matches_target_share(self):
        s = self.share()
        c = s["chart"]
        for i, y in enumerate(c["years"]):
            self.assertAlmostEqual(sum(m["share"][i] or 0 for m in c["members"]), 1.0, places=5, msg=y)
        unh = next(m for m in c["members"] if m["cik"] == 1)
        self.assertAlmostEqual(unh["share"][-1], s["target_share"]["2025"], places=6)
        self.assertEqual(c["window"], [2022, 2025])
        self.assertEqual(contracts.undeclared_panel_keys(s), [])

    def test_matched_set_and_change_follow_R13(self):
        rows, members, foreign, fr = sub_universe()
        del fr[2022][5]                                     # CNC has no CY2022 revenue
        s = self.share(frames=fr)
        c = {m["cik"]: m for m in s["chart"]["members"]}
        self.assertIs(c[5]["matched"], False)
        self.assertIsNone(c[5]["change_pp"], "out of the matched set: no change")
        self.assertEqual(sum(1 for m in c.values() if m["matched"]), s["matched_n"])
        self.assertAlmostEqual(c[1]["change_pp"], s["share_change_pp"], places=3)
        self.assertAlmostEqual(c[1]["share_then"], s["matched_share_then"], places=6)
        self.assertAlmostEqual(sum(m["share_now"] or 0 for m in c.values()), 1.0, places=5)

    def test_a_year_that_fails_coverage_has_no_shares_and_no_matched_set(self):
        rows, members, foreign, fr = sub_universe()
        for c in (2, 3, 4, 5, 6):
            del fr[2022][c]
        s = self.share(frames=fr)
        self.assertIsNone(s["top5_share"]["2022"])
        i = s["chart"]["years"].index(2022)
        self.assertTrue(all(m["share"][i] is None for m in s["chart"]["members"]))
        self.assertTrue(all(m["matched"] is None and m["change_pp"] is None for m in s["chart"]["members"]))

    def test_target_rank_by_newest_revenue_among_the_members(self):
        s = self.share(target_cik=5)                        # CNC 150: UNH 400, CVS 350, CI 200, ELV 170
        self.assertEqual(s["chart"]["target_rank"], {"rank": 5, "of": s["n_members"], "cy": 2025})
        self.assertIsNone(self.share(target_cik=999)["chart"]["target_rank"]["rank"])

    def test_sub_group_carries_its_own_chart(self):
        rows, members, foreign, fr = sub_universe()
        g = PP.sub_group(members, fr, 1, Y5, AS_OF, foreign, None, rows, "UNH")
        self.assertEqual(len(g["chart"]["members"]), g["n_members"])
        self.assertEqual(g["chart"]["target_rank"]["rank"], 1)


GLOBAL = [["ASML Holding N.V.", "ENXTAM:ASML", "Netherlands", "3559", "Semiconductor Equip"],
          ["Novo Nordisk A/S", "CPSE:NOVO B", "Denmark", "2834", "Drugs (Pharmaceutical)"],
          ["Taiwan Semiconductor Manufacturing Company Limited", "TWSE:2330", "Taiwan", "3674", "Semiconductor"],
          ["Twin Co Ltd", "TSE:1", "Japan", "2000", "Food"], ["Twin Co. Limited", "HKSE:2", "Japan", "2000", "Food"]]


class GlobalSic(unittest.TestCase):
    def test_names_normalise_across_legal_forms_and_adr_words(self):
        for a, b in (("ASML HOLDING NV", "ASML Holding N.V."), ("Novo Nordisk A/S", "NOVO NORDISK AS"),
                     ("Taiwan Semiconductor Manufacturing Co., Ltd. ADR",
                      "Taiwan Semiconductor Manufacturing Company Limited (TWSE:2330)"),
                     ("Sony Group Corp Sponsored ADR", "Sony Group Corporation")):
            self.assertEqual(GS.normalise(a), GS.normalise(b), (a, b))
        self.assertEqual(GS.normalise("ASML Holding N.V."), "asml")
        self.assertNotEqual(GS.normalise("Novo Nordisk A/S"), GS.normalise("Novo Holdings A/S"))

    def test_unique_match_with_the_country_gives_the_sic_and_its_source(self):
        r = GS.resolve(GLOBAL, "Novo Nordisk A/S", "Denmark")
        self.assertEqual((r["sic"], r["source"]), ("2834", "Damodaran global: CPSE:NOVO B"))

    def test_two_rows_with_the_name_is_a_skip(self):
        r = GS.resolve(GLOBAL, "TWIN CO LTD", "Japan")
        self.assertIsNone(r["sic"])
        self.assertIn("ตรงกับ 2 แถว", r["reason"])

    def test_a_country_mismatch_is_a_skip(self):
        r = GS.resolve(GLOBAL, "ASML HOLDING NV", "United States")
        self.assertIsNone(r["sic"])
        self.assertIn("ประเทศไม่ตรง", r["reason"])

    def test_no_match_and_no_list_are_skips_with_reasons(self):
        self.assertIn("ไม่พบชื่อ", GS.resolve(GLOBAL, "Space Exploration Technologies Corp", "US")["reason"])
        self.assertIn("ไม่มีรายชื่อ", GS.resolve([], "Novo Nordisk A/S", "Denmark")["reason"])
        self.assertIn("ไม่มีรหัส SIC", GS.resolve([["X AG", "XETRA:X", "Germany", "", "y"]], "X AG", "Germany")["reason"])


# real rows of indname.xls (2026 vintage), names exactly as the workbook writes them
REAL = [["Taiwan Semiconductor Co., Ltd. (TPEX:5425)", "TPEX:5425", "Taiwan", "3674", "Semiconductor"],
        ["Taiwan Semiconductor Manufacturing Company Limited (TWSE:2330)", "TWSE:2330", "Taiwan", "3674",
         "Semiconductor"],
        ["Bannari Amman Spinning Mills Ltd (NSEI:BASML)", "NSEI:BASML", "India", "2200", "Apparel"],
        ["ASML Holding N.V. (ENXTAM:ASML)", "ENXTAM:ASML", "Netherlands", "3559", "Semiconductor Equip"],
        ["Novo Nordisk A/S (CPSE:NOVO B)", "CPSE:NOVO B", "Denmark", "2834", "Drugs (Pharmaceutical)"]]


class RealRows(unittest.TestCase):
    """The store's profile names for the three ADRs against the workbook's own rows."""
    def test_tsmc_is_not_taiwan_semiconductor_co(self):
        r = GS.resolve(REAL, "Taiwan Semiconductor Manufacturing Company Limited", "Taiwan")
        self.assertEqual(r["ticker"], "TWSE:2330", "a prefix or loose match picks TPEX:5425")
        self.assertNotEqual(GS.normalise(REAL[0][0]), GS.normalise(REAL[1][0]))

    def test_ticker_text_never_matches(self):
        self.assertEqual(GS.resolve(REAL, "ASML HOLDING NV", "Netherlands")["ticker"], "ENXTAM:ASML")
        self.assertIsNone(GS.resolve(REAL, "BASML", "India")["sic"], "BASML is a ticker, not a name")
        self.assertIsNone(GS.resolve(REAL, "NOVO B", "Denmark")["sic"])

    def test_novo(self):
        self.assertEqual(GS.resolve(REAL, "Novo Nordisk A/S", "Denmark")["sic"], "2834")


class FakeSheet:
    def __init__(self, rows):
        self.rows, self.nrows = rows, len(rows)

    def row_values(self, i):
        return self.rows[i]


class FakeBook:
    def __init__(self, rows):
        self.sheet = FakeSheet(rows)

    def sheet_by_name(self, name):
        assert name == dc.SHEET
        return self.sheet

    def release_resources(self):
        pass


HEAD = ["Company Name", "Exchange:Ticker", "Industry Group", "Primary Sector", "SIC Code", "Country"]


class GlobalWorkbook(unittest.TestCase):
    """A small synthetic workbook (xlrd's book object), parsed by the same code path."""
    def parse(self, rows):
        import xlrd
        real = xlrd.open_workbook
        xlrd.open_workbook = lambda **kw: FakeBook([HEAD] + rows)
        try:
            return dc.parse_indname_global(b"")
        finally:
            xlrd.open_workbook = real

    def rows(self, n=dc.MIN_GLOBAL_ROWS, sic=2834.0):
        out = [[f"Firm {i} A/S", f"CPSE:F{i}", "Drugs", "Health", sic, "Denmark"] for i in range(n)]
        return out + [["Merck & Co", "NYSE:MRK", "Drugs", "Health", 2834.0, "United States"]]

    def test_only_non_us_rows_with_their_sic_as_text(self):
        g = self.parse(self.rows())
        self.assertEqual(len(g), dc.MIN_GLOBAL_ROWS, "the NYSE row stays in the US table only")
        self.assertEqual(g[0], ["Firm 0 A/S", "CPSE:F0", "Denmark", "2834", "Drugs"])

    def test_rows_without_any_sic_stop_the_parse(self):
        with self.assertRaisesRegex(ValueError, "no SIC"):
            self.parse(self.rows(sic=""))

    def test_a_short_sheet_is_a_layout_change(self):
        with self.assertRaisesRegex(ValueError, "layout"):
            self.parse(self.rows(n=10))


class AdrWiring(unittest.TestCase):
    """The global SIC is the target's only: the sub-group is the US-filer members of the
    industry with that SIC, and the panel says where the code came from."""
    def test_adr_target_gets_global_sic_and_us_members(self):
        real = peers.resolve_cik
        peers.resolve_cik = lambda t: ((str(int(t[1:]) + 1).zfill(10), t) if t[1:].isdigit() else (None, None))
        try:
            rows = {f"T{i}": ["Drugs (Pharmaceutical)", "2834", "United States"] for i in range(1, 7)}
            glob = GLOBAL + [["Novo Nordisk Foundation Fund", "CPSE:X", "Denmark", "9999", "Other"]]
            u = peers.Universe(rows, {}, {}, as_of="2026-01", global_rows=glob,
                               frames=frames_for(Y5, {i + 1: [10.0] * 5 for i in range(1, 7)}))
            pts = [PP.compact(dict(pp(f"T{i}"), addback=0.0)) for i in range(1, 5)]
            block = {"model": {"panel_points": pts, "version": peers.MODEL_VERSION}, "excluded": [], "notes": []}
            f = tfacts(ticker="NVO", company="Novo Nordisk A/S", country="DK",
                       damodaran_industry="Drugs (Pharmaceutical)")
            panel, moat_ = peers.panel_and_moat(f, SimpleNamespace(key_metrics={"wacc_pct": 8.0}), block, u, TODAY)
        finally:
            peers.resolve_cik = real
        self.assertEqual(panel["target_sic"]["source"], "Damodaran global: CPSE:NOVO B")
        self.assertEqual(panel["target_sic"]["code"], "2834")
        self.assertEqual(panel["table"]["gm_compare"]["sic"], "2834")
        sub = panel["industry_share"]["sub_group"]
        self.assertEqual(sub["n_members"], 6, "US rows only; the global rows never join")
        self.assertNotIn("CPSE", str(sub["members"]))
        self.assertEqual(panel["industry_share"]["n_members"], 6, "industry membership unchanged")
        self.assertEqual(contracts.undeclared_panel_keys([panel, moat_]), [])

    def test_a_us_listed_target_keeps_the_us_row(self):
        u = peers.Universe({"T0": ["Software", "7372", "United States"]}, {}, {}, global_rows=GLOBAL)
        self.assertEqual(u.target_sic(tfacts())["source"], "damodaran_indname")


if __name__ == "__main__":
    unittest.main()
