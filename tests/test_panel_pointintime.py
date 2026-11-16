"""Point-in-time reference for the peer panel, and proof that each timing guard goes red.

Offline. Uses the raw SEC companyfacts fixtures only; imports nothing from the feature.
Hand-checked figures are the ones each company printed on its income statement:
  MSFT FY2021 (June) revenue $168,088M, gross margin $115,856M -> 68.93%; FY2018 $110,360M
  ABBV 2021 net revenues $56,197M; 2024 $56,334M (so the 2021->2024 CAGR is ~0.1%)
  AVGO FY2024 revenue $51,574M; ORCL FY2025 revenue $57,399M; NVO 2024 sales DKK 290,403M
"""
import copy
import os
import unittest

from verify import compare as C
from verify import reference as R
from verify import timing as T

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
TICKERS = ["ABBV", "AVGO", "MSFT", "NVO", "ORCL"]
AS_OF = ["2022-06-30", "2023-06-30", "2024-06-30", "2025-06-30"]
FACTS = {t: R.load(os.path.join(FIX, t, "sec_companyfacts.json")) for t in TICKERS}
PRICES = {"ABBV": 150.0, "AVGO": 500.0, "MSFT": 300.0, "ORCL": 80.0, "NVO": 100.0}


def ref(t, as_of, price=None):
    return R.company_reference(FACTS[t], as_of, price=price, symbol=t)


def build_panel(target, peers, as_of, price_date=None):
    """A PeerPanel in the SPEC shape made from the reference, to test the harness itself."""
    refs = {t: ref(t, as_of, PRICES[t]) for t in [target] + peers}
    rows = []
    for t, r in refs.items():
        row = {"symbol": t, "is_target": t == target, "period_end": r["period_end"],
               "fy0_end": r["fy0_end"], "price_date": price_date or as_of, "outlier": []}
        for m in R.METRICS:
            row[m] = r[m]
        rows.append(row)
    tr = R.table_reference(rows)
    for key, m in (("scale", "revenue_ttm"), ("growth", "cagr3_fy"), ("margin", "op_margin_ttm")):
        order = sorted((x for x in rows if x[m] is not None), key=lambda x: -x[m])
        for x in rows:
            x.setdefault("rank3", {})[key] = order.index(x) + 1 if x in order[:3] else None
    for x in rows:
        x["outlier"] = sorted(m for m in R.METRICS if R.iqr_outlier(x[m], tr["stats"][m]))
    tvm = {m: (v * 100 if v is not None and m in R.RATIO_METRICS else v)
           for m, v in tr["target_vs_median"].items()}
    flags = ["%s: %s" % (t, f) for t, r in refs.items() for f in r["flags"]]
    flags += ["%s: %s not available" % (x["symbol"], m) for x in rows for m in R.METRICS if x[m] is None]
    panel = {"as_of": as_of, "version": 1, "industry": "test", "industry_basis": "test",
             "table": {"rows": rows, "stats": tr["stats"], "target_vs_median": tvm},
             "flags": flags + ["industry_share: not built in this fixture"]}
    return panel, refs


def fails(results):
    return [x for x in results if x["status"] == "FAIL"]


class PointInTime(unittest.TestCase):
    def test_reference_uses_only_facts_filed_by_as_of(self):
        print("\n%-5s %-10s %-10s %-10s %12s %12s %7s %7s %7s  %s" % (
            "tick", "as_of", "FY0", "FY3", "rev FY0 $M", "rev FY3 $M", "GM FY0", "OpM FY0", "CAGR3", "note"))
        for t in TICKERS:
            for a in AS_OF:
                r = ref(t, a)
                used = [f for fs in r["used"].values() for f in fs]
                self.assertTrue(used, (t, a))
                self.assertTrue(all(f["filed"] <= a for f in used), (t, a))
                self.assertLessEqual(r["period_end"], a)
                for res in T.check_lookahead(r, a) + T.check_durations(r) + T.check_fy_gap(r):
                    self.assertNotEqual(res["status"], "FAIL", (t, a, res))
                pct = lambda v: "   n/a " if v is None else "%6.2f%%" % (v * 100)
                print("%-5s %-10s %-10s %-10s %12.0f %12.0f %s %s %s  %s" % (
                    t, a, r["fy0_end"], r["fy3_end"], r["revenue_fy0"] / 1e6, r["revenue_fy3"] / 1e6,
                    pct(r["gross_margin_fy0"]), pct(r["op_margin_fy0"]), pct(r["cagr3_fy"]),
                    (r["currency"] + " " + (r["excluded"] or "")).strip() if r["excluded"] else ""))

    def test_hand_checked_values(self):
        m = ref("MSFT", "2022-06-30")
        self.assertEqual(m["fy0_end"], "2021-06-30")           # FY2022 10-K came out in late July 2022
        self.assertEqual(m["revenue_fy0"], 168_088_000_000)
        self.assertEqual(m["revenue_fy3"], 110_360_000_000)
        self.assertAlmostEqual(m["gross_margin_fy0"], 115_856 / 168_088, places=6)
        self.assertAlmostEqual(m["cagr3_fy"], (168_088 / 110_360) ** (1 / 3) - 1, places=9)
        a = ref("ABBV", "2025-06-30")
        self.assertEqual((a["fy0_end"], a["fy3_end"]), ("2024-12-31", "2021-12-31"))
        self.assertEqual((a["revenue_fy0"], a["revenue_fy3"]), (56_334_000_000, 56_197_000_000))
        self.assertAlmostEqual(a["cagr3_fy"], 0.0008, places=4)
        self.assertEqual(ref("AVGO", "2025-06-30")["revenue_fy0"], 51_574_000_000)
        self.assertEqual(ref("ORCL", "2025-06-30")["revenue_fy0"], 57_399_000_000)
        self.assertEqual(ref("NVO", "2025-06-30")["revenue_fy0"], 290_403_000_000)

    def test_as_of_changes_the_answer(self):
        """A reference that ignored as_of would return today's fiscal year for every date."""
        ends = {a: ref("MSFT", a)["fy0_end"] for a in AS_OF}
        self.assertEqual(ends, {"2022-06-30": "2021-06-30", "2023-06-30": "2022-06-30",
                                "2024-06-30": "2023-06-30", "2025-06-30": "2024-06-30"})
        self.assertEqual(ref("ORCL", "2022-06-30")["fy0_end"], "2022-05-31")   # 10-K filed 2022-06-21
        self.assertEqual(ref("ORCL", "2022-06-20")["fy0_end"], "2021-05-31")

    def test_ttm_is_four_quarters_and_equals_fy_at_a_year_end(self):
        r = ref("MSFT", "2025-06-30")
        self.assertEqual(r["ttm_basis"], "4Q")
        self.assertEqual(len(r["ttm_quarters"]), 4)
        self.assertIn(R.span_days(*r["ttm_window"]), (365, 366))
        at_year_end = ref("MSFT", "2024-08-15")
        self.assertEqual(at_year_end["period_end"], "2024-06-30")
        self.assertEqual(at_year_end["revenue_ttm"], at_year_end["revenue_fy0"])
        fy, ytd = 245_122_000_000, None
        co = R.Company(FACTS["MSFT"], "2025-06-30")
        d = co.durations(R.REV["us-gaap"], pick="max")
        cur, prior = d[("2024-07-01", "2025-03-31")]["val"], d[("2023-07-01", "2024-03-31")]["val"]
        self.assertEqual(r["revenue_ttm"], fy + cur - prior)        # FY + YTD - prior YTD

    def test_ttm_refuses_a_gap(self):
        co = R.Company(FACTS["MSFT"], "2025-06-30")
        d = co.durations(R.REV["us-gaap"], pick="max")
        holed = {k: v for k, v in d.items() if k[1] != "2024-12-31"}
        t = R.ttm(holed)
        self.assertIsNone(t["val"])
        self.assertEqual(t["basis"], "gap")

    def test_fy3_is_joined_by_date_not_position(self):
        """E2/E3: with FY2021 removed, FY3 is missing; it is not the next year along."""
        cf = copy.deepcopy(FACTS["ABBV"])
        for c in R.REV["us-gaap"]:
            node = cf["facts"]["us-gaap"].get(c)
            if node:
                node["units"]["USD"] = [e for e in node["units"]["USD"]
                                        if not (e["end"] == "2021-12-31" and e.get("start") == "2021-01-01")]
        r = R.company_reference(cf, "2025-06-30")
        self.assertIsNone(r["fy3_end"])
        self.assertIsNone(r["cagr3_fy"])
        self.assertTrue(any("3 years before" in f for f in r["flags"]))

    def test_stale_gross_profit_is_not_used(self):
        """E4: ORCL stopped filing GrossProfit in 2018; the margin is null with a flag."""
        r = ref("ORCL", "2025-06-30")
        self.assertIsNone(r["gross_margin_fy"])
        self.assertTrue(any("gross profit not filed" in f for f in r["flags"]))

    def test_non_usd_filer_is_excluded(self):
        self.assertIn("ifrs-full", ref("NVO", "2025-06-30")["excluded"])
        self.assertIsNone(ref("MSFT", "2025-06-30")["excluded"])

    def test_frames_calendar_year_matches_sec_tags(self):
        """E21. MSFT's June year lands in the calendar year it ENDS in, although most of
        its days are in the year before; ORCL's May year lands in the year before."""
        for t in ("ABBV", "AVGO", "MSFT", "ORCL"):
            res = T.check_frames(FACTS[t], symbol=t)
            self.assertEqual([x["status"] for x in res], ["PASS"], res)
        self.assertEqual(R.frame_cy("2024-07-01", "2025-06-30"), 2025)
        self.assertEqual(T._majority_year("2024-07-01", "2025-06-30"), 2024)
        self.assertEqual(R.frame_cy("2024-06-01", "2025-05-31"), 2024)
        self.assertEqual(R.frame_cy("2024-11-04", "2025-11-02"), 2025)
        self.assertEqual(R.frame_cy("2025-01-01", "2025-12-31"), 2025)


class HarnessGoesRed(unittest.TestCase):
    """Each mutation is a past error; the harness must report it."""

    def setUp(self):
        self.as_of = "2025-06-30"
        self.panel, self.refs = build_panel("MSFT", ["ORCL", "AVGO", "ABBV"], self.as_of)
        self.facts = {t: FACTS[t] for t in self.refs}

    def row(self, sym):
        return next(x for x in self.panel["table"]["rows"] if x["symbol"] == sym)

    def run_all(self):
        results, scale = C.compare_all(self.panel, None, self.refs)
        return results + T.run_all(self.panel, self.facts, self.refs, scale)

    def test_clean_panel_is_green(self):
        bad = fails(self.run_all())
        self.assertEqual(bad, [], bad[:3])

    def test_percent_units_are_detected(self):
        for x in self.panel["table"]["rows"]:
            for m in R.RATIO_METRICS:
                x[m] = None if x[m] is None else x[m] * 100
        for m in R.RATIO_METRICS:
            st = self.panel["table"]["stats"][m]
            for k in ("max", "p75", "median", "p25", "min"):
                st[k] = None if st[k] is None else st[k] * 100
        results, scale = C.compare_all(self.panel, None, self.refs)
        self.assertEqual(scale, 100.0)
        self.assertEqual(fails(results), [])

    def test_e22_lookahead(self):
        later = ref("MSFT", "2026-06-30")
        self.row("MSFT").update(period_end=later["period_end"], fy0_end=later["fy0_end"])
        names = {x["check"] for x in fails(self.run_all()) if "check" in x}
        self.assertIn("a.lookahead.period_end", names)
        self.assertIn("a.lookahead.fy0_end", names)

    def test_e1_ttm_over_fy_called_growth(self):
        r = self.refs["MSFT"]
        self.row("MSFT")["growth_fy"] = r["revenue_ttm"] / r["revenue_fy1"] - 1
        bad = fails(self.run_all())
        self.assertTrue(any(x.get("field") == "growth_fy" for x in bad))
        mix = [x for x in bad if x.get("check") == "f.mixing.growth_fy"]
        self.assertTrue(mix and "TTM revenue / FY-1 revenue" in mix[0]["detail"], mix)

    def test_e2_cagr_by_list_position(self):
        r = self.refs["AVGO"]
        self.row("AVGO")["cagr3_fy"] = r["wrong"]["cagr3_fy"]["FY0 over the 3rd list item, newest first (FY2 by position)"]
        bad = fails(self.run_all())
        self.assertTrue(any(x.get("check") == "f.mixing.cagr3_fy" for x in bad), bad[:3])

    def test_e1_margin_on_mixed_clocks(self):
        r = self.refs["AVGO"]
        self.row("AVGO")["op_margin_ttm"] = r["op_income_ttm"] / r["revenue_fy0"]
        self.assertTrue(any(x.get("check") == "f.mixing.op_margin_ttm" for x in fails(self.run_all())))

    def test_e7_oldest_year_read_as_newest(self):
        co = R.Company(FACTS["ABBV"], self.as_of)
        oldest = min(R.annuals(co.durations(R.REV["us-gaap"], pick="max")))
        self.row("ABBV")["fy0_end"] = oldest
        bad = fails(self.run_all())
        self.assertTrue(any(x.get("field") == "fy0_end" for x in bad))
        self.assertTrue(any(str(x.get("check", "")).startswith("d.fy0_not_stale") for x in bad))

    def test_e8_foreign_filer_in_the_table(self):
        panel, refs = build_panel("MSFT", ["ORCL", "NVO"], self.as_of)
        bad = fails(C.compare_all(panel, None, refs)[0])
        self.assertTrue(any(x["scope"] == "NVO" and "E8" in x["note"] for x in bad), bad[:3])

    def test_peer_period_and_price_date_limits_both_sides(self):
        self.row("ORCL")["price_date"] = "2025-06-25"          # 5 days: allowed
        self.assertEqual(fails(T.check_peer_alignment(self.panel)), [])
        self.row("ORCL")["price_date"] = "2025-06-24"          # 6 days
        self.assertTrue(any(x["check"] == "d.price_date.ORCL" for x in fails(T.check_peer_alignment(self.panel))))
        self.row("ORCL")["price_date"] = "2025-07-01"          # after as_of
        self.assertTrue(any(x["check"] == "d.price_date.ORCL" for x in fails(T.check_peer_alignment(self.panel))))
        self.row("ORCL")["price_date"] = self.as_of
        self.row("ORCL")["period_end"] = "2024-08-31"          # 7 months before the target
        self.assertTrue(any(x["check"] == "d.period_end.ORCL" for x in fails(T.check_peer_alignment(self.panel))))

    def test_money_and_ratio_tolerances_both_sides(self):
        base = self.row("MSFT")["revenue_ttm"]
        for factor, red in ((1.004, False), (0.996, False), (1.006, True), (0.994, True)):
            self.row("MSFT")["revenue_ttm"] = base * factor
            hit = any(x["scope"] == "MSFT" and x["field"] == "revenue_ttm" for x in fails(C.compare_rows(self.panel, self.refs)[0]))
            self.assertEqual(hit, red, factor)
        self.row("MSFT")["revenue_ttm"] = base
        gm = self.row("MSFT")["gross_margin_fy"]
        for delta, red in ((0.0015, False), (-0.0015, False), (0.0025, True), (-0.0025, True)):
            self.row("MSFT")["gross_margin_fy"] = gm + delta
            hit = any(x["scope"] == "MSFT" and x["field"] == "gross_margin_fy" for x in fails(C.compare_rows(self.panel, self.refs)[0]))
            self.assertEqual(hit, red, delta)

    def test_stats_and_silent_nulls(self):
        self.panel["table"]["stats"]["revenue_ttm"]["median"] *= 1.02
        self.assertTrue(any(x["scope"] == "table.stats.revenue_ttm" for x in fails(C.compare_table(self.panel, 1.0))))
        self.panel["flags"] = []
        self.assertTrue(fails(C.compare_flags(self.panel)))        # ORCL gross margin is null

    def test_multiples_without_a_price(self):
        """Peers have no stored price: EV/Sales, EV/EBIT and P/E must still agree with
        the balance sheet and with each other."""
        refs = {t: ref(t, self.as_of) for t in self.refs}          # no price
        ok = fails(C.compare_rows(self.panel, refs)[0])
        self.assertEqual(ok, [], ok[:2])
        self.row("AVGO")["ev_ebit"] *= 1.05
        self.assertTrue(any("implied" in x["field"] for x in fails(C.compare_rows(self.panel, refs)[0])))


class MoatRules(unittest.TestCase):
    def moat(self, dirs, values=None):
        names = ["spread_slope", "share_change", "gm_gap_change", "incremental_margin"]
        values = values or [d * 2.0 if d is not None else None for d in dirs]
        sig = [{"name": n, "value": v, "direction": d, "window": "FY2021..FY2024"}
               for n, d, v in zip(names, dirs, values) if d is not None]
        total = sum(dirs_ for dirs_ in dirs if dirs_ is not None)
        label = None if len(sig) < 2 else "improving" if total >= 2 else "eroding" if total <= -2 else "stable"
        return {"label": label, "n_signals": len(sig), "signals": sig, "source_hints": [],
                "thresholds": "provisional", "flags": []}

    def structural(self, m):
        return [x for x in fails(C.compare_moat(m)) if x["field"] in ("label", "n_signals", "direction", "direction sign")]

    def test_label_boundaries(self):
        for dirs in ([1, 1, 0, 0], [1, 0, 0, 0], [-1, -1, 0, 0], [-1, 0, 0, 0], [1, None, None, None], [1, 1, None, None]):
            self.assertEqual(self.structural(self.moat(dirs)), [], dirs)
        m = self.moat([1, 0, 0, 0])
        m["label"] = "improving"                    # +1 is not enough
        self.assertTrue(self.structural(m))
        m = self.moat([-1, 0, 0, 0])
        m["label"] = "eroding"
        self.assertTrue(self.structural(m))
        m = self.moat([1, None, None, None])
        m["label"] = "stable"                       # one signal: label must be null
        self.assertTrue(self.structural(m))

    def test_missing_signal_is_not_zero(self):
        m = self.moat([1, 1, None, None])
        m["signals"].append({"name": "gm_gap_change", "value": None, "direction": 0, "window": None})
        self.assertTrue(any("E5" in x["note"] for x in fails(C.compare_moat(m))))

    def test_direction_against_its_own_sign(self):
        m = self.moat([1, 1, 0, 0], [-0.5, 2.0, 0.0, 0.0])
        self.assertTrue(any(x["field"] == "direction sign" for x in fails(C.compare_moat(m))))

    def test_one_threshold_must_explain_both_sides(self):
        ok = {"A": self.moat([1, 0, 0, 0], [1.2, 0.1, 0, 0]), "B": self.moat([-1, 0, 0, 0], [-1.1, 0.2, 0, 0]),
              "C": self.moat([0, 0, 0, 0], [0.9, 0.0, 0, 0])}
        self.assertEqual(fails(C.threshold_consistency(ok)), [])
        ok["D"] = self.moat([0, 0, 0, 0], [-1.5, 0.0, 0, 0])      # a fall bigger than B's, called flat
        self.assertTrue(fails(C.threshold_consistency(ok)))

    def test_share_change_on_a_zero_base(self):
        panel = {"industry_share": {"years": [2021, 2022, 2023, 2024, 2025],
                                    "target_share": {"2022": 0.0, "2025": 0.02}}}
        m = self.moat([None, 1, 1, None], [None, 5.0, 2.0, None])
        self.assertTrue(any("E6" in x["note"] for x in fails(C.compare_moat(m, panel))))

    def test_s3_s4_reference_arithmetic(self):
        t, peers = ref("MSFT", "2025-06-30"), [ref("AVGO", "2025-06-30"), ref("ABBV", "2025-06-30")]
        m = R.moat_reference(t, peers)
        inc = (109_433 - 69_916) / (245_122 - 168_088)            # MSFT operating income FY2024, FY2021
        self.assertAlmostEqual(m["incremental_margin"]["incremental"], inc * 100, places=6)
        self.assertAlmostEqual(m["incremental_margin"]["average"], 109_433 / 245_122 * 100, places=6)
        shrinking = dict(t, revenue_fy0=t["revenue_fy3"] - 1)
        self.assertIsNone(R.moat_reference(shrinking, peers)["incremental_margin"])


class ShareRules(unittest.TestCase):
    def test_share_reference_and_coverage_rule(self):
        by_year = {"2022": {"T": 10.0, "a": 30.0, "b": 60.0}, "2025": {"T": 30.0, "a": 30.0, "b": 40.0}}
        s = R.share_reference(by_year, "T", 3)
        self.assertAlmostEqual(s["target_share"]["2022"], 0.10)
        self.assertAlmostEqual(s["hhi"]["2025"], 0.09 + 0.09 + 0.16)
        self.assertAlmostEqual(s["share_change_pp"], 20.0)
        thin = R.share_reference(by_year, "T", 5)                  # 3 of 5 = 60% coverage
        self.assertIsNone(thin["target_share"]["2025"])
        self.assertIsNone(thin["share_change_pp"])

    def test_share_change_arithmetic_is_checked(self):
        panel = {"flags": [], "industry_share": {
            "group": "damodaran_industry_us_sec_filers", "years": [2021, 2022, 2023, 2024, 2025],
            "n_members": 10, "n_with_revenue": {str(y): 9 for y in range(2021, 2026)},
            "target_share": {"2021": 0.09, "2022": 0.10, "2023": 0.11, "2024": 0.12, "2025": 0.13},
            "top5_share": {str(y): 0.7 for y in range(2021, 2026)},
            "hhi": {str(y): 0.15 for y in range(2021, 2026)},
            "share_change_pp": 3.0, "top5_change_pp": 0.0}}
        self.assertEqual(fails(C.compare_share(panel)), [])
        panel["industry_share"]["share_change_pp"] = 4.0           # newest minus 4 years earlier
        self.assertTrue(any(x["field"] == "share_change_pp" for x in fails(C.compare_share(panel))))
        panel["industry_share"]["share_change_pp"] = 3.0
        panel["industry_share"]["n_with_revenue"]["2025"] = 6      # 60% coverage, share still shown
        self.assertTrue(any("coverage" in x["field"] for x in fails(C.compare_share(panel))))


if __name__ == "__main__":
    unittest.main(verbosity=1)
