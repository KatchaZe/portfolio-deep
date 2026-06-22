"""
test_finnhub — Finnhub + Alpha Vantage EPS-surprise parsers and Finnhub forward-EPS
parser. Pure/synthetic (no network); ensures both new sources emit the SAME row
shape as Yahoo/FMP so the reconcile is source-agnostic, and degrade safely.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sources import finnhub, alphavantage


def test_finnhub_parse_earnings():
    raw = [  # Finnhub returns newest-first; parser must sort oldest->newest
        {"period": "2026-03-31", "actual": 1.10, "estimate": 1.00, "surprisePercent": 10.0},
        {"period": "2025-12-31", "actual": 0.99, "estimate": 1.00},                 # -1% -> meet
        {"period": "2025-09-30", "actual": 0.80, "estimate": 1.00},                 # -20% -> miss
        {"period": "2026-06-30", "actual": None, "estimate": 1.20},                 # future -> skipped
    ]
    out = finnhub.parse_earnings(raw)
    assert [e["quarter"] for e in out] == ["2025-09-30", "2025-12-31", "2026-03-31"], out
    assert [e["grade"] for e in out] == ["miss", "meet", "beat"], out
    assert finnhub.parse_earnings([]) == [] and finnhub.parse_earnings(None) == []
    print("finnhub earnings OK:", [e["grade"] for e in out])


def test_finnhub_parse_eps_estimate():
    raw = {"data": [{"period": "2027-12-31", "epsAvg": 9.5},
                    {"period": "2026-12-31", "epsAvg": 8.0}], "freq": "annual"}
    assert finnhub.parse_eps_estimate(raw) == 8.0          # nearest future period
    # premium / empty -> None (blend just uses the other sources)
    assert finnhub.parse_eps_estimate({}) is None
    assert finnhub.parse_eps_estimate({"data": []}) is None
    print("finnhub eps-estimate OK")


def test_alphavantage_parse_earnings():
    raw = {"symbol": "X", "quarterlyEarnings": [
        {"fiscalDateEnding": "2026-03-31", "reportedEPS": "1.20", "estimatedEPS": "1.00",
         "surprisePercentage": "20.0"},
        {"fiscalDateEnding": "2025-12-31", "reportedEPS": "0.95", "estimatedEPS": "1.00"},  # -5% miss
    ]}
    out = alphavantage.parse_earnings(raw)
    assert [e["quarter"] for e in out] == ["2025-12-31", "2026-03-31"], out
    assert [e["grade"] for e in out] == ["miss", "beat"], out
    # throttled / empty responses -> [] (no crash)
    assert alphavantage.parse_earnings({"Note": "rate limit"}) == []
    assert alphavantage.parse_earnings({}) == []
    print("alphavantage earnings OK:", [e["grade"] for e in out])


def test_no_key_fetch_is_safe():
    # without a key the fetchers return empty, never call the network
    assert finnhub.fetch_earnings("X", "") == []
    assert finnhub.fetch_eps_estimate("X", "") == {}
    assert alphavantage.fetch_earnings("X", "") == {}
    print("no-key fetch safe OK")


if __name__ == "__main__":
    test_finnhub_parse_earnings()
    test_finnhub_parse_eps_estimate()
    test_alphavantage_parse_earnings()
    test_no_key_fetch_is_safe()
    print("\nALL FINNHUB/ALPHAVANTAGE TESTS PASSED")
