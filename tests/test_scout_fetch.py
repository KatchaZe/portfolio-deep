"""tools/scout_fetch: the IFRS CY-revenue path, the member assembly and the WACC reader."""
import datetime as dt
import unittest

from sources import damodaran, fmp
from tests.test_ifrs_members import gfs_cf
from tools import scout_fetch as sf

TODAY = dt.date(2026, 11, 24)
YEARS = [2021, 2022, 2023, 2024, 2025]


def sec(cik=7, currency="USD", ifrs_cy=None, total_debt=500.0, debt_notes=()):
    x = {"currency": currency, "accounting_standard": "us-gaap", "latest_period_end": "2026-09-30",
         "revenue": 1000e6, "operating_income": 100e6, "net_income": 80e6, "shares_diluted": 10e6,
         "total_debt": total_debt, "operating_leases": 50.0, "cash": 200.0, "minority_interest": None,
         "equity": 900.0, "revenue_annuals_dated": [["2025-12-31", 1000e6], ["2024-12-31", 800e6]]}
    return {"cik": cik, "x": x, "ebit_ttm_raw": 100e6, "addback": 0.0, "ebit_derived": False,
            "gp_basis": "filed GrossProfit", "fy": [], "ifrs_cy": ifrs_cy or {}, "debt_notes": list(debt_notes),
            "fin_lease_in_debt": False, "flags": [], "sec_gap": None, "sec_sic": None, "stale_cache": False}


META = {"ticker": "ABC", "name": "Abc Inc", "cik": 7, "cik_how": "ticker", "domicile": "us", "sic": "3674"}
PRICE = {"close": 50.0, "day": "2026-11-21", "src": "yahoo"}


def nulls_without_reason(node, path=""):
    out = []
    if isinstance(node, dict):
        if "value" in node and "reason" in node and node["value"] is None and not node["reason"]:
            out.append(path)
        for k, v in node.items():
            out += nulls_without_reason(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out += nulls_without_reason(v, f"{path}[{i}]")
    return out


class IfrsMember(unittest.TestCase):
    """The extract labels an IFRS filer "ifrs" (not "ifrs-full"); the scout used to test the
    wrong label, so GFS's calendar-year revenue was never read. Red on 6e74b39."""

    def test_gfs_gets_calendar_year_revenue(self):
        part = sf.sec_part("GFS", 1709048, gfs_cf(), "2026-11-24")
        self.assertEqual(part["x"]["accounting_standard"], "ifrs")
        r = sf.assemble(dict(META, ticker="GFS", cik=1709048), part, PRICE, {}, YEARS, TODAY, 0.1055)
        got = {y: round(r["revenue_cy"][str(y)]["value"] / 1e6) for y in YEARS}
        self.assertEqual(got, {2021: 6585, 2022: 8108, 2023: 7392, 2024: 6750, 2025: 6791})
        self.assertIn("companyfacts ifrs-full:Revenue", r["revenue_cy"]["2025"]["source"])

    def test_non_ifrs_member_reads_no_companyfacts_cy(self):
        part = sf.sec_part("X", 7, {"facts": {"us-gaap": {}}}, "2026-11-24")
        self.assertEqual(part["ifrs_cy"], {})


class Assembly(unittest.TestCase):
    def test_frames_row_then_ifrs_then_a_reason(self):
        frames = {2024: {7: [3e9, "2024-12-31", "us-gaap:Revenues", None]}, 2025: {8: [9e9, "2025-12-31", "x", None]}}
        r = sf.assemble(META, sec(ifrs_cy={"2023": [2e9, "2023-12-31", "ifrs-full:Revenue", None]}),
                        PRICE, frames, YEARS, TODAY)
        cy = r["revenue_cy"]
        self.assertEqual(cy["2024"]["value"], 3e9)
        self.assertIn("SEC frames CY2024", cy["2024"]["source"])
        self.assertEqual(cy["2023"]["value"], 2e9)
        self.assertIn("companyfacts ifrs-full:Revenue", cy["2023"]["source"])
        self.assertIsNone(cy["2025"]["value"], "another CIK's frame row is never read")
        self.assertIn("CY2025", cy["2025"]["reason"])

    def test_ev_is_relative_point(self):
        r = sf.assemble(META, sec(), PRICE, {}, YEARS, TODAY)
        self.assertEqual(r["market"]["market_cap"]["value"], 50.0 * 10e6)
        self.assertEqual(r["market"]["ev"]["value"], 50.0 * 10e6 + 500.0 + 50.0 - 200.0)
        self.assertTrue(r["checks"]["revenue_ge_200m"] and r["checks"]["ebit_positive"])

    def test_no_cik_explains_every_field(self):
        meta = dict(META, cik=None, cik_reason="no SEC filer named Abc")
        r = sf.assemble(meta, None, {"close": None, "reason": "no T-1 close"}, {}, YEARS, TODAY)
        self.assertEqual(r["revenue_cy"]["2024"]["reason"], "no SEC filer named Abc")
        self.assertEqual(r["balance"]["debt"]["reason"], "no SEC filer named Abc")
        self.assertEqual(nulls_without_reason(r), [])

    def test_non_usd_and_unknown_debt_are_named(self):
        r = sf.assemble(META, sec(currency="TWD", total_debt=None, debt_notes=["untagged at 2026-06-30"]),
                        PRICE, {}, YEARS, TODAY)
        self.assertIn("TWD", r["revenue_cy"]["2024"]["reason"])
        self.assertEqual(r["balance"]["debt"]["reason"], "untagged at 2026-06-30")
        self.assertIn("TWD", r["market"]["ev"]["reason"])
        self.assertEqual(nulls_without_reason(r), [])


class ReaderGuards(unittest.TestCase):
    """E77 / E80 in the scout record. Red on 809b652."""

    def test_E80_unknown_debt_with_evidence_makes_ev_a_lower_bound(self):
        r = sf.assemble(META, sec(total_debt=None, debt_notes=["untagged at 2026-06-30"]), PRICE, {}, YEARS, TODAY)
        self.assertTrue(r["market"]["ev"]["lower_bound"])
        s_ = sec(total_debt=None)
        s_["fy"] = [{"end": "2025-12-31", "debt": 0}]
        self.assertFalse(sf.assemble(META, s_, PRICE, {}, YEARS, TODAY)["market"]["ev"]["lower_bound"])

    def test_E77_a_misscaled_count_gives_no_market_cap_and_says_why(self):
        s_ = sec()
        s_["x"]["shares_diluted"] = 2000.0           # $100K market cap on $1B of revenue
        r = sf.assemble(META, s_, PRICE, {}, YEARS, TODAY)
        self.assertIsNone(r["market"]["market_cap"]["value"])
        self.assertIn("share count implausible", r["market"]["market_cap"]["reason"])
        self.assertIsNone(r["market"]["shares"]["value"])
        self.assertIsNone(r["market"]["ev"]["value"])
        self.assertEqual(nulls_without_reason(r), [])


class Labels(unittest.TestCase):
    def test_debt_free_member_is_labelled_debt_free(self):
        s_ = sec(total_debt=None)
        s_["fy"] = [{"end": "2025-12-31", "debt": 0}]
        r = sf.assemble(META, s_, PRICE, {}, YEARS, TODAY)
        self.assertTrue(r["balance"]["debt"]["reason"].startswith("debt-free"))
        s_["fy"] = [{"end": "2025-12-31", "debt": 40.0}]
        r = sf.assemble(META, s_, PRICE, {}, YEARS, TODAY)
        self.assertTrue(r["balance"]["debt"]["reason"].startswith("no debt concept"))

    def test_missing_raw_ebit_does_not_claim_facts_were_unread(self):
        s_ = sec()
        s_["ebit_ttm_raw"] = None
        r = sf.assemble(META, s_, PRICE, {}, YEARS, TODAY)
        self.assertNotIn("not read", r["ttm"]["ebit_raw"]["reason"])


class Group1Assembly(unittest.TestCase):
    def window(self, *roic):
        return {"value": [{"end": f"{2021 + i}-12-31", "roic_pct": v} for i, v in enumerate(roic)]}

    def part(self, cap_window):
        s_ = sec()
        s_["g1"] = {"fy": [], "derived": {"cap_window": cap_window}, "latest_filed": "2026-11-05"}
        return s_

    def test_cap_years_counted_at_the_industry_wacc(self):
        r = sf.assemble(META, self.part(self.window(8.0, 12.0, 15.0, 9.0, 11.0)), PRICE, {}, YEARS, TODAY, 0.10)
        cap = r["group1"]["derived"]["cap_years"]
        self.assertEqual((cap["value"], cap["of"]), (3, 5))
        self.assertIn("10.00%", cap["source"])

    def test_old_cache_part_says_group1_is_missing(self):
        r = sf.assemble(META, sec(), PRICE, {}, YEARS, TODAY, 0.10)
        self.assertIn("older cache", r["group1"]["reason"])
        self.assertEqual(nulls_without_reason(r), [])

    def test_next_filing_due_after_the_quarter_and_the_fast_lag(self):
        r = sf.assemble(META, self.part(self.window(1, 2, 3, 4)), PRICE, {}, YEARS, TODAY, 0.10)
        self.assertEqual(r["filing"]["next_expected_by"], "2027-01-20",
                         "2026-09-30 + 91 + 21 days")
        self.assertEqual(r["filing"]["latest_filed"], "2026-11-05")


class Group2(unittest.TestCase):
    def series(self, n, f):
        import datetime as d
        days = [(d.date(2024, 1, 1) + d.timedelta(days=i)).isoformat() for i in range(n)]
        closes = [100.0 + f(i) for i in range(n)]
        return {"closes": closes, "dates": days, "volumes": [1e6] * n, "source": "test", "dividend_adjusted": True}

    def known_beta(self, n=500, beta=1.7, alpha=0.001, step=5):
        """(asset, spy) whose weekly returns, sampled every `step` bars back from the last
        date, satisfy r_asset = alpha + beta x r_spy exactly; every other day is noise, so
        a beta sampled from any other anchor is far from `beta`."""
        import math
        spy, a = [0.0] * n, [0.0] * n
        sample = set(range(n - 1, -1, -step))
        s_lvl, a_lvl, k = 100.0, 50.0, 0
        for i in range(n):
            if i in sample:
                if k:
                    rm = 0.03 * math.sin(1.3 * k) + 0.01 * math.cos(0.7 * k)
                    s_lvl *= 1 + rm
                    a_lvl *= 1 + alpha + beta * rm
                k += 1
                spy[i], a[i] = s_lvl, a_lvl
            else:
                spy[i] = s_lvl * (1 + 0.04 * math.sin(2.1 * i))
                a[i] = a_lvl * (1 + 0.09 * math.cos(1.7 * i))
        return self.series(n, lambda i: a[i] - 100.0), self.series(n, lambda i: spy[i] - 100.0)

    def check_E82(self):
        from pipeline import daily
        from tools import scout_groups
        m, spy = self.known_beta()
        g = scout_groups.group2("ABC", m, None, spy, {})
        self.assertEqual(g["beta_spy"]["value"], 1.7, "scout beta = the constructed 1.7")
        self.assertEqual(daily.beta_vs_spy(m, spy), (1.7, m["dates"][-1]), "app beta = 1.7 to the last date")
        self.assertEqual(g["beta_spy"]["window_end"], m["dates"][-1])
        cut = 380                                    # an earlier as-of samples other weeks
        short = {k: (v[:cut] if isinstance(v, list) else v) for k, v in m.items()}
        self.assertEqual(scout_groups.group2("ABC", short, None, spy, {})["beta_spy"]["window_end"], m["dates"][cut - 1])

    def test_E82_beta_matches_an_independent_construction(self):
        self.check_E82()

    def test_E82_the_check_goes_red_on_a_mutant(self):
        """The check above can fail: a beta sampled forward from the first common date (the
        as-of mistake E82 was about) gives another number on the same series."""
        from domain import momentum
        real = momentum.regression_beta

        def forward(closes, dates, mc, md, step=5, min_points=40):
            mk = {str(d)[:10]: c for d, c in zip(md, mc)}
            pairs = [(c, mk[str(d)[:10]]) for d, c in zip(dates, closes) if str(d)[:10] in mk][::step]
            ra = [pairs[i][0] / pairs[i - 1][0] - 1 for i in range(1, len(pairs))]
            rm = [pairs[i][1] / pairs[i - 1][1] - 1 for i in range(1, len(pairs))]
            n = len(ra)
            ma, mm = sum(ra) / n, sum(rm) / n
            return round(sum((x - ma) * (y - mm) for x, y in zip(ra, rm)) / sum((y - mm) ** 2 for y in rm), 2)
        momentum.regression_beta = forward
        try:
            with self.assertRaises(AssertionError):
                self.check_E82()
        finally:
            momentum.regression_beta = real

    def test_highest_correlation_names_the_holding_and_short_overlaps_are_skipped(self):
        import math
        me = self.series(300, lambda i: 10 * math.sin(i / 7.0) + i * 0.05)
        twin = self.series(300, lambda i: 10 * math.sin(i / 7.0) + i * 0.05 + (i % 3) * 0.01)
        other = self.series(300, lambda i: 10 * math.cos(i / 3.0))
        short = self.series(60, lambda i: 10 * math.sin(i / 7.0))
        g = sf.scout_groups.group2("ME", me, None, None, {"TWIN": twin, "OTHER": other, "SHORT": short, "ME": me})
        self.assertEqual(g["top_corr"]["value"]["ticker"], "TWIN")
        self.assertNotIn("SHORT", g["corr"]["value"], "fewer shared days than CORR_MIN_DAYS")
        self.assertNotIn("ME", g["corr"]["value"])
        self.assertIsNone(g["beta_spy"]["value"])
        self.assertTrue(g["beta_spy"]["reason"])

    def test_no_history_gives_a_reason(self):
        g = sf.scout_groups.group2("ME", None, "offline run: no cached price history", None, {})
        self.assertEqual((g["bars"], g["reason"]), (0, "offline run: no cached price history"))


class Sources(unittest.TestCase):
    def test_wacc_column_by_header(self):
        # rows as wacc.html showed them on 2026-11-24 (January 2026 data)
        head = ["Industry Name", "Number of Firms", "Beta", "Cost of Equity", "E/(D+E)", "Std Dev in Stock",
                "Cost of Debt", "Tax Rate", "After-tax Cost of Debt", "D/(D+E)", "Cost of Capital"]
        rows = [["Semiconductor", "66", "1.52", "10.72%", "97.47%", "55.83%", "5.29%", "5.11%", "3.97%", "2.53%", "10.55%"],
                ["Software (System & Application)", "309", "1.28", "9.64%", "94.72%", "56.79%", "5.29%", "5.51%",
                 "3.97%", "5.28%", "9.34%"]]
        html = "<table>" + "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in [head] + rows) + "</table>"
        t = damodaran.parse_wacc_html(html)
        self.assertAlmostEqual(t["semiconductor"], 0.1055)
        self.assertAlmostEqual(t["software (system & application)"], 0.0934)

    def test_fmp_is_refused(self):
        real = fmp._send
        try:
            sf._guard_fmp()
            with self.assertRaises(RuntimeError):
                fmp._send(None, "u", {}, 1)
            self.assertEqual(sf.CALLS.n["fmp"], 1)
        finally:
            fmp._send = real


if __name__ == "__main__":
    unittest.main()
