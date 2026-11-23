"""tools/scout_fetch: the IFRS CY-revenue path, the member assembly and the WACC reader."""
import datetime as dt
import unittest

from sources import damodaran, fmp
from tools import scout_fetch as sf

TODAY = dt.date(2026, 11, 24)
YEARS = [2021, 2022, 2023, 2024, 2025]


def fact(val, end, frame, filed="2025-03-01", accn="0001-25-000001"):
    return {"val": val, "start": None, "end": end, "frame": frame, "filed": filed, "accn": accn}


def ifrs_facts(entries, unit="USD"):
    return {"facts": {"ifrs-full": {"Revenue": {"units": {unit: entries}}}}}


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


class IfrsCalendarYears(unittest.TestCase):
    def test_reads_annual_usd_frames_only(self):
        cf = ifrs_facts([fact(5e9, "2023-12-31", "CY2023"), fact(6e9, "2024-12-31", "CY2024"),
                         fact(1.5e9, "2024-12-31", "CY2024Q4I")])
        cf["facts"]["ifrs-full"]["Revenue"]["units"]["TWD"] = [fact(9e11, "2024-12-31", "CY2024")]
        rows = sf.cy_rows_from_facts(cf, "2026-11-24")
        self.assertEqual({y: r[0] for y, r in rows.items()}, {2023: 5e9, 2024: 6e9})
        self.assertEqual(rows[2024][2], "ifrs-full:Revenue")

    def test_fact_filed_after_as_of_is_left_out(self):
        cf = ifrs_facts([fact(6e9, "2024-12-31", "CY2024", filed="2026-12-01")])
        self.assertEqual(sf.cy_rows_from_facts(cf, "2026-11-24"), {})

    def test_june_year_end_rebucketed_as_the_frames_are(self):
        cf = ifrs_facts([fact(4e9, "2024-06-30", "CY2023")])
        self.assertEqual(list(sf.cy_rows_from_facts(cf)), [2024])

    def test_us_gaap_wins_over_ifrs_in_one_year(self):
        cf = ifrs_facts([fact(6e9, "2024-12-31", "CY2024")])
        cf["facts"]["us-gaap"] = {"Revenues": {"units": {"USD": [fact(7e9, "2024-12-31", "CY2024")]}}}
        self.assertEqual(sf.cy_rows_from_facts(cf)[2024][:3], [7e9, "2024-12-31", "us-gaap:Revenues"])


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
