"""
Offline test of the FMP parser against the REAL ABBV values captured by the probe
(GAAP EPS 2.37, revenue 61.16B, net income 4.226B, shares 1.774B). No network.

Run from the app root:  python -m tests.test_fmp_parse
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from domain.facts import FinancialFacts
from sources import fmp


# A bundle shaped exactly like FMP's stable responses, using ABBV's real numbers.
ABBV_BUNDLE = {
    "profile": [{"companyName": "AbbVie Inc.", "sector": "Healthcare",
                 "beta": 0.305, "price": 227.23, "currency": "USD"}],
    "quote": [{"price": 227.23}],
    "income_annual": [
        {"date": "2025-12-31", "fiscalYear": "2025", "reportedCurrency": "USD",
         "revenue": 61160000000, "operatingIncome": 14521000000, "netIncome": 4226000000,
         "epsDiluted": 2.37, "weightedAverageShsOutDil": 1774000000,
         "incomeBeforeTax": 5300000000, "incomeTaxExpense": 1000000000,
         "depreciationAndAmortization": 9800000000},
        {"date": "2024-12-31", "revenue": 56334000000, "netIncome": 4278000000},
        {"date": "2023-12-31", "revenue": 54318000000},
        {"date": "2022-12-31", "revenue": 58054000000},
    ],
    "income_ttm": {"_status": 200, "_body": "(not on free tier)"},   # may be absent
    "balance_annual": [{"totalDebt": 60000000000, "cashAndCashEquivalents": 5500000000,
                        "totalStockholdersEquity": 3300000000}],
    "cashflow_annual": [{"capitalExpenditure": -1000000000, "stockBasedCompensation": 600000000,
                         "depreciationAndAmortization": 9800000000}],
    "key_metrics_ttm": [{"roicTTM": 0.14}],
    "estimates": [
        {"date": "2026-12-31", "epsAvg": 12.10, "revenueAvg": 64500000000},
        {"date": "2027-12-31", "epsAvg": 13.40, "revenueAvg": 69000000000},
        {"date": "2025-12-31", "epsAvg": 10.00, "revenueAvg": 56300000000},  # past — must be ignored
    ],
}


def test_parse_abbv():
    f = fmp.parse(ABBV_BUNDLE, FinancialFacts("ABBV"))
    assert f.currency == "USD"
    assert f.sector == "Healthcare"
    assert abs(f.beta - 0.305) < 1e-9
    assert f.price == 227.23
    assert f.revenue == 61160000000, f.revenue
    assert f.operating_income == 14521000000
    assert f.net_income == 4226000000
    assert f.eps_gaap == 2.37, "GAAP EPS must come through cleanly"
    assert f.shares_diluted == 1774000000
    assert f.total_debt == 60000000000 and f.cash == 5500000000 and f.equity == 3300000000
    assert f.capex == 1000000000           # abs of negative capex
    assert f.sbc == 600000000
    # adjusted forward EPS = next FUTURE year's epsAvg (12.10), NOT the past row (10.00)
    assert f.forward_eps == 12.10, f.forward_eps
    # growth from estimate revenue CAGR 64.5B -> 69.0B over 1 yr
    assert f.growth_lt and 0.05 < f.growth_lt < 0.12, f.growth_lt
    # clean annual revenue series for CAGR
    assert f.revenue_annuals[0] == 61160000000 and len(f.revenue_annuals) == 4
    # tax rate derived
    assert 0.15 < f.tax_rate < 0.25
    # provenance recorded
    assert f.provenance.get("eps_gaap", "").startswith("fmp")
    print("FMP parser OK on real ABBV schema:")
    print(f"  revenue {f.revenue/1e9:.2f}B  netInc {f.net_income/1e9:.2f}B  epsGAAP {f.eps_gaap} "
          f"fwdEPS(adj) {f.forward_eps}  growth {f.growth_lt:.1%}  shares {f.shares_diluted/1e9:.3f}B")


if __name__ == "__main__":
    test_parse_abbv()
    print("\nALL FMP PARSE TESTS PASSED")
