"""SPEC R25-R28 (2026-11-22): gross margin is compared only within the same business.

R25 one SIC source (Damodaran's company list) for target, peers and sub-group; a
    financial target (SIC 6000-6799) gets no gross-margin number, signal or hint.
R26 a GM comparison uses peers with the target's 4-digit SIC and a GM, at least 3;
    else the 3-digit SIC with at least 3; else none, with a Thai reason and the counts.
R27 votes: how many signals could vote, how many did, which do not apply.
R28 the same-SIC sub-group: share, top-5 and HHI on the industry's own rules, S2 on it
    when 5 filers have revenue in both years.
Each test names the failure mode it exists for. Display only (PP1)."""
import unittest
from types import SimpleNamespace

from domain import contracts
from domain import moat_trend as MT
from domain import peer_panel as PP
from pipeline import peers
from tests.test_peer_panel import TODAY, Y5, frames_for, pp, snap, tfacts

AS_OF = "2026-11-16"


def gm_point(sym, gm0=.5, gm3=.5, rev0=100.0, rev3=80.0, oi0=None, oi3=None, opm=.1):
    p = snap(rev0, rev3, oi0, oi3, gm0, gm3)
    p.update(symbol=sym, revenue_ttm=rev0, ebit_ttm=rev0 * opm)
    return p


class SicParse(unittest.TestCase):
    def test_every_spelling_of_one_code_is_one_code(self):
        for raw in ("6324", 6324, 6324.0, "06324", " 6324 ", "6324.0"):
            self.assertEqual(PP.sic4(raw), "6324", repr(raw))
        self.assertEqual(PP.sic4("100"), "0100", "agriculture codes lose their zero in the xls float")

    def test_missing_zero_and_junk_are_no_code(self):
        for raw in (None, "", "0", "0000", "00000", "abc", True, False, 10000, -5, "63 24"):
            self.assertIsNone(PP.sic4(raw), repr(raw))

    def test_financial_range_both_edges(self):
        for code, want in (("5999", False), ("6000", True), ("6324", True), ("6798", True),
                           ("6799", True), ("6800", False), (None, False), ("0000", False)):
            self.assertEqual(PP.is_financial(code), want, code)


class GmScope(unittest.TestCase):
    def scope(self, tsic, codes, legs=("fy0",), gms=None):
        ps = [gm_point(f"P{i}", *(gms[i] if gms else (.5, .5))) for i in range(len(codes))]
        sics = {"T": tsic, **{f"P{i}": c for i, c in enumerate(codes)}}
        return PP.gm_scope({"symbol": "T"}, ps, sics, legs)

    def test_exactly_three_same_sic_is_enough(self):
        s = self.scope("3674", ["3674"] * 3 + ["3672"] * 5)
        self.assertEqual((s["level"], s["n"], s["symbols"]), (4, 3, ["P0", "P1", "P2"]))

    def test_two_same_sic_fall_back_to_three_digits(self):
        s = self.scope("3845", ["3845", "3845", "3841"])
        self.assertEqual((s["level"], s["n"], s["n4"]), (3, 3, 2))

    def test_two_at_three_digits_is_a_skip_naming_the_counts(self):
        s = self.scope("3845", ["3845", "3841", "3851", "3851", "3851"])
        self.assertIsNone(s["level"])
        self.assertIsNone(s["peers"])
        self.assertEqual((s["n4"], s["n3"]), (1, 2))
        self.assertIn("1 ราย", s["reason"])
        self.assertIn("2 ราย", s["reason"])
        self.assertIn("SIC 3845", s["reason"])

    def test_three_digit_fallback_never_crosses_into_another_group(self):
        s = self.scope("3845", ["3845", "3841", "3851", "3851", "3851", "3800", "3840"])
        self.assertEqual(s["level"], 3)
        self.assertEqual(s["symbols"], ["P0", "P1", "P6"], "3851 and 3800 share only two digits")

    def test_a_comparator_without_gm_is_not_counted(self):
        s = self.scope("3674", ["3674"] * 3, gms=[(.5, .5), (None, .5), (.5, .5)])
        self.assertEqual((s["level"], s["n4"]), (None, 2))

    def test_S3_needs_gm_at_both_legs(self):
        codes = ["3674"] * 3
        gms = [(.5, .5), (.5, None), (.5, .5)]
        self.assertEqual(self.scope("3674", codes, ("fy0",), gms)["level"], 4)
        self.assertIsNone(self.scope("3674", codes, ("fy0", "fy3"), gms)["level"])

    def test_missing_or_zero_sic_is_a_skip_never_a_match(self):
        for t in (None, "0000", "0", ""):
            s = self.scope(t, ["0000", "0000", "0000", None])
            self.assertIsNone(s["level"], t)
            self.assertIn("ไม่มีรหัส SIC", s["reason"])
        s = self.scope("3674", [None, "0000", "", "3674", "3674"])
        self.assertEqual(s["n4"], 2, "a peer with no code is never the same business")

    def test_mismatched_spellings_match(self):
        s = self.scope(6324.0, ["06324", 6324, " 6324"])
        self.assertFalse(s["applies"], "6324 in any spelling is an insurer")
        s = self.scope("3674", ["03674", 3674, 3674.0])
        self.assertEqual(s["level"], 4)

    def test_financial_target_has_no_gm_comparison(self):
        s = self.scope("6324", ["6324"] * 5)
        self.assertEqual((s["applies"], s["level"], s["peers"]), (False, None, None))
        self.assertEqual(s["reason"], PP.GM_NA_TH)
        self.assertEqual(contracts.undeclared_panel_keys(PP.record(s)), [])


def table_case(tsic="3674", peers_=(("3674", .5), ("3674", .5), ("3674", .5), ("1000", .1), ("1000", .1))):
    tgt = dict(pp("T"), symbol="T")
    ps = []
    for i, (c, gm) in enumerate(peers_):
        p = pp(f"P{i}")
        p["fy0"] = dict(p["fy0"], gross_profit=p["fy0"]["revenue"] * gm)
        ps.append(p)
    sics = {"T": tsic, **{f"P{i}": c for i, (c, _) in enumerate(peers_)}}
    return tgt, ps, sics


class Table(unittest.TestCase):
    def test_gm_median_is_over_the_same_sic_only(self):
        tgt, ps, sics = table_case()
        tbl, _ = PP.table(tgt, ps, AS_OF, sics)
        self.assertEqual(tbl["stats"]["gross_margin_fy"]["n"], 3)
        self.assertAlmostEqual(tbl["stats"]["gross_margin_fy"]["median"], .5)
        self.assertAlmostEqual(tbl["target_vs_median"]["gross_margin_fy"], 0.0, places=6)
        self.assertEqual(tbl["stats"]["op_margin_ttm"]["n"], 5, "operating margin stays on every peer")
        self.assertEqual(tbl["gm_compare"]["symbols"], ["P0", "P1", "P2"])
        by = {r["symbol"]: r for r in tbl["rows"]}
        self.assertEqual([by[s]["gm_comparator"] for s in ("P0", "P3")], [True, False])
        self.assertEqual([by[s]["same_business"] for s in ("T", "P0", "P3")], [True, True, False])
        self.assertAlmostEqual(by["P3"]["gross_margin_fy"], .1, msg="other businesses still show their own GM")

    def test_a_different_sic_peer_is_never_a_gm_outlier_reference(self):
        tgt, ps, sics = table_case(peers_=[("3674", .50), ("3674", .51), ("3674", .52), ("3674", .53),
                                           ("1000", .10)])
        tbl, _ = PP.table(tgt, ps, AS_OF, sics)
        self.assertEqual(tbl["stats"]["gross_margin_fy"]["n"], 4)
        for r in tbl["rows"]:
            if not r["gm_comparator"] and not r["is_target"]:
                self.assertNotIn("gross_margin_fy", r["outlier"], r["symbol"])

    def test_gm_skip_leaves_no_median_and_says_why(self):
        tgt, ps, sics = table_case(peers_=[("3674", .5), ("3674", .5), ("1000", .1)])
        panel = PP.build(tgt, ps, AS_OF, "Semiconductor", "b", None, sics=sics)
        tbl = panel["table"]
        self.assertIsNone(tbl["stats"]["gross_margin_fy"]["median"])
        self.assertIsNone(tbl["target_vs_median"]["gross_margin_fy"])
        self.assertTrue(any(f.startswith("gross margin: ") and "2 ราย" in f for f in panel["flags"]))

    def test_financial_target_shows_no_gm_number_anywhere(self):
        tgt, ps, sics = table_case("6324", [("6324", .9), ("6324", .2), ("6324", .07), ("5122", .04)])
        panel = PP.build(tgt, ps, AS_OF, "Healthcare Support Services", "b", None, sics=sics)
        tbl = panel["table"]
        self.assertEqual([r["gross_margin_fy"] for r in tbl["rows"]], [None] * 5)
        self.assertTrue(all(r["gm_na"] for r in tbl["rows"]))
        self.assertEqual(tbl["stats"]["gross_margin_fy"]["n"], 0)
        self.assertIsNone(tbl["target_vs_median"]["gross_margin_fy"])
        self.assertFalse(any("gross_margin_fy" in r["outlier"] for r in tbl["rows"]))
        self.assertFalse(any("gross_margin_fy:" in f for f in panel["flags"]), "no per-row GM null flags")
        self.assertIn(f"gross margin: {PP.GM_NA_TH}", panel["flags"])
        self.assertEqual(contracts.undeclared_panel_keys(panel), [])

    def test_a_financial_peer_of_a_non_financial_target_shows_no_gm(self):
        tgt, ps, sics = table_case("8090", [("8090", .3), ("8090", .3), ("8090", .3), ("6324", .89)])
        tbl, _ = PP.table(tgt, ps, AS_OF, sics)
        ins = next(r for r in tbl["rows"] if r["symbol"] == "P3")
        self.assertEqual((ins["gross_margin_fy"], ins["gm_na"], ins["gm_comparator"]), (None, True, False))

    def test_no_sic_map_is_a_skip(self):
        tgt, ps, _ = table_case()
        tbl, _ = PP.table(tgt, ps, AS_OF)
        self.assertIsNone(tbl["gm_compare"]["level"])
        self.assertIsNone(tbl["stats"]["gross_margin_fy"]["median"])


def moat(tsic, peer_codes, tgt_gm=(.9, .8), peer_gm=(.2, .2), spread=5.0, share=None, rev3=80.0):
    tgt = gm_point("T", *tgt_gm, rev3=rev3, oi0=20.0, oi3=10.0)
    ps = [gm_point(f"P{i}", *peer_gm) for i in range(len(peer_codes))]
    sics = {"T": tsic, **{f"P{i}": c for i, c in enumerate(peer_codes)}}
    return MT.build({}, tgt, ps, share, 8.0, spread, sics)


def kinds(m):
    return {h["type"] for h in m["source_hints"]}


class Moat(unittest.TestCase):
    def test_financial_target_gets_no_gm_signal_or_hint(self):
        out = moat("6324", ["6324"] * 4)
        self.assertNotIn("gm_gap_change", {s["name"] for s in out["signals"]})
        self.assertIn(f"S3: {PP.GM_NA_TH}", out["flags"])
        self.assertFalse(kinds(out) & {"intangible", "cost_advantage"})
        self.assertEqual(out["votes"], {"possible": 3, "cast": out["n_signals"],
                                        "not_applicable": ["gm_gap_change"]})

    def test_same_sic_target_keeps_S3_and_the_hint_on_comparators_only(self):
        out = moat("3674", ["3674"] * 3 + ["9999"] * 3, peer_gm=(.2, .2))
        s3 = next(s for s in out["signals"] if s["name"] == "gm_gap_change")
        self.assertEqual((s3["comparators"]["level"], s3["comparators"]["n"]), (4, 3))
        self.assertEqual(s3["comparators"]["symbols"], ["P0", "P1", "P2"])
        self.assertAlmostEqual(s3["value"], (.9 - .2) - (.8 - .2))
        self.assertIn("intangible", kinds(out))
        gm = next(h for h in out["source_hints"] if h["type"] == "intangible")["measures"][0]
        self.assertEqual(gm["comparators"], {"level": 4, "sic": "3674", "n": 3})
        self.assertEqual(out["votes"], {"possible": 4, "cast": out["n_signals"], "not_applicable": []})

    def test_skip_at_two_comparators_drops_the_vote_and_records_it(self):
        out = moat("3674", ["3674", "3674", "9999", "9999"])
        self.assertNotIn("gm_gap_change", {s["name"] for s in out["signals"]})
        s3 = next(f for f in out["flags"] if f.startswith("S3"))
        self.assertIn("2 ราย", s3)
        self.assertEqual(out["votes"]["possible"], 4, "a skipped vote is still a possible vote")
        self.assertEqual(out["votes"]["cast"], out["n_signals"])
        self.assertFalse(kinds(out) & {"intangible", "cost_advantage"})
        self.assertTrue(any(f.startswith("hints: gross margin hints skipped") for f in out["flags"]))

    def test_target_without_gm_is_named_before_the_sic_rule(self):
        out = moat("3674", ["3674"] * 3, tgt_gm=(None, .5))
        self.assertIn("S3: target gross margin missing at FY0 or FY3", out["flags"])

    def test_R24_still_gates_hints_with_a_valid_comparison(self):
        out = moat("3674", ["3674"] * 3, spread=-1.0)
        self.assertEqual(out["source_hints"], [])
        self.assertIn(MT.NO_EXCESS_FLAG, out["flags"])

    def test_R21_still_guards_S4(self):
        out = moat("3674", ["3674"] * 3, rev3=95.5)          # +4.7% in 3 years
        self.assertNotIn("incremental_margin", {s["name"] for s in out["signals"]})
        self.assertTrue(any(f.startswith("S4: revenue grew") for f in out["flags"]))

    def test_label_counts_only_the_votes_cast(self):
        sh = {"years": Y5, "target_share": {}, "matched_share_then": .1, "matched_share_now": .2}
        out = moat("6324", ["6324"] * 3, share=sh)           # S2 +1, S4 +1 (20/100 vs 10/80)
        self.assertEqual(out["label"], "improving")
        self.assertEqual(out["votes"]["cast"], 2)


def sub_universe():
    """An HSS-like industry: insurers 6324 (one with two share classes), a 5912 pharmacy,
    a member with no code, a 20-F foreign member and a 10-K foreign member."""
    rows = {"UNH": "6324", "CI": "6324", "ELV": "6324", "HUM": "6324", "CNC": "6324",
            "MOH": "6324", "MOHB": "6324", "CVS": "5912", "NOC": None, "ZERO": "0000",
            "FOR10K": "6324", "FOR20F": "6324"}
    members = {"UNH": 1, "CI": 2, "ELV": 3, "HUM": 4, "CNC": 5, "MOH": 6, "MOHB": 6, "CVS": 7,
               "NOC": 8, "ZERO": 9}
    foreign = {"FOR10K": 10, "FOR20F": 11}
    rev = {1: [400] * 5, 2: [200] * 5, 3: [170] * 5, 4: [100] * 5, 5: [150] * 5, 6: [40] * 5,
           7: [350] * 5, 8: [5] * 5, 9: [5] * 5, 10: [10] * 5}
    return rows, members, foreign, frames_for(Y5, rev)


class SubGroup(unittest.TestCase):
    def sub(self, **kw):
        rows, members, foreign, fr = sub_universe()
        args = dict(members=members, frames=fr, target_cik=1, years=Y5, as_of=AS_OF, foreign=foreign,
                    assets=None, sics=rows, target_symbol="UNH")
        args.update(kw)
        return PP.sub_group(**args)

    def test_only_the_same_four_digit_sic_and_the_target_once(self):
        s = self.sub()
        syms = [m["symbols"] for m in s["members"]]
        self.assertEqual(s["n_members"], 7, "6 US insurer filers + the foreign 10-K filer")
        self.assertIn(["MOH", "MOHB"], syms, "two share classes are one filer")
        self.assertEqual(sum(1 for m in syms if "UNH" in m), 1)
        self.assertFalse(any(x in sum(syms, []) for x in ("CVS", "NOC", "ZERO", "FOR20F")))
        self.assertLessEqual(sum(m["share"] for m in s["members"] if m["share"]), 1 + 1e-9)
        self.assertAlmostEqual(sum(m["share"] for m in s["members"] if m["share"]), 1.0)
        self.assertAlmostEqual(s["target_share"]["2025"], 400 / 1070)
        self.assertEqual((s["sic"], s["sic_source"]), ("6324", "damodaran_indname"))
        self.assertEqual(contracts.undeclared_panel_keys(s), [])

    def test_same_rules_as_the_industry_years_and_coverage(self):
        s = self.sub(as_of="2025-06-30")
        self.assertEqual(s["years"], [2021, 2022, 2023, 2024], "CY2025 is not complete on 2025-06-30 (R10)")

    def test_revenue_in_one_year_only_is_out_of_the_matched_set(self):
        rows, members, foreign, fr = sub_universe()
        del fr[2022][5]                                     # CNC has no CY2022 revenue
        s = self.sub(frames=fr)
        self.assertEqual(s["matched_n"], 6)
        self.assertAlmostEqual(s["matched_share_then"], 400 / (1070 - 150))

    def test_zero_revenue_filer_counts_for_coverage_not_share(self):
        rows, members, foreign, fr = sub_universe()
        for y in Y5:
            fr[y][4] = [0.0, f"{y}-12-31"]                  # HUM files, books no revenue
        s = self.sub(frames=fr)
        self.assertEqual(s["n_covered"]["2025"], 7)
        self.assertEqual(s["n_with_revenue"]["2025"], 6)
        hum = next(m for m in s["members"] if m["symbols"] == ["HUM"])
        self.assertEqual((hum["revenue"], hum["share"]), (None, None))

    def test_sub_group_of_one_says_so(self):
        rows, members, foreign, fr = sub_universe()
        s = self.sub(target_symbol="CVS", target_cik=7)
        self.assertEqual(s["n_members"], 1)
        self.assertIn("มีแค่หุ้นนี้", s["reason"])

    def test_no_target_sic_is_no_group(self):
        for code in (None, "0000"):
            rows, *_ = sub_universe()
            rows = dict(rows, UNH=code)
            s = self.sub(sics=rows)
            self.assertEqual((s["sic"], s["n_members"], s["members"]), (None, 0, []))
            self.assertIn("ไม่มีรหัส SIC", s["reason"])

    def test_S2_on_the_sub_group_only_with_five_matched(self):
        ind = {"years": Y5, "matched_share_then": .10, "matched_share_now": .10, "matched_n": 50}
        sub = {"sic": "6324", "years": Y5, "matched_share_then": .30, "matched_share_now": .40, "matched_n": 5}
        sig, _ = MT.share_change(dict(ind, sub_group=sub))
        self.assertEqual((sig["basis"], sig["direction"], sig["matched_n"]), ("sub_group", 1, 5))
        sig, _ = MT.share_change(dict(ind, sub_group=dict(sub, matched_n=4)))
        self.assertEqual((sig["basis"], sig["direction"]), ("industry", 0))
        self.assertIn("4 ราย", sig["basis_reason"])
        sig, _ = MT.share_change(dict(ind, sub_group=dict(sub, matched_share_then=None)))
        self.assertEqual(sig["basis"], "industry", "a sub-group without the target falls back")
        sig, _ = MT.share_change(ind)
        self.assertEqual(sig["basis"], "industry")

    def test_build_marks_which_members_are_in_the_panel(self):
        rows, members, foreign, fr = sub_universe()
        sub = self.sub()
        tgt = dict(pp("UNH"), symbol="UNH")
        panel = PP.build(tgt, [pp("CI"), pp("CVS")], AS_OF, "HSS", "b", {"flags": [], "sub_group": sub},
                         sics=rows)
        inp = {tuple(m["symbols"]): m["in_panel"] for m in sub["members"]}
        self.assertEqual((inp[("UNH",)], inp[("CI",)], inp[("ELV",)]), (True, True, False))


class Wiring(unittest.TestCase):
    """One source: the panel reads Damodaran's SIC even where the stored point carries
    a different SEC submissions SIC (CVS 5912 vs Damodaran 8000)."""
    def test_damodaran_sic_wins_over_the_point_sic(self):
        real = peers.resolve_cik
        peers.resolve_cik = lambda t: ((str(int(t[1:]) + 1).zfill(10), t) if t[1:].isdigit() else (None, None))
        try:
            rows = {f"T{i}": ["Software", "7372", "United States"] for i in range(6)}
            u = peers.Universe(rows, {}, {}, as_of="2026-01",
                               frames=frames_for(Y5, {i + 1: [10.0] * 5 for i in range(6)}))
            pts = [PP.compact(dict(pp(f"T{i}"), addback=0.0, sic=["9999", "x"])) for i in range(1, 5)]
            block = {"model": {"panel_points": pts, "version": peers.MODEL_VERSION}, "excluded": [], "notes": []}
            panel, moat_ = peers.panel_and_moat(tfacts(), SimpleNamespace(key_metrics={"wacc_pct": 8.0}),
                                                block, u, TODAY)
        finally:
            peers.resolve_cik = real
        self.assertEqual(panel["table"]["gm_compare"]["level"], 4)
        self.assertEqual(panel["table"]["gm_compare"]["n"], 4)
        sub = panel["industry_share"]["sub_group"]
        self.assertEqual((sub["sic"], sub["n_members"]), ("7372", 6))
        self.assertEqual(contracts.undeclared_panel_keys([panel, moat_]), [])


if __name__ == "__main__":
    unittest.main()
