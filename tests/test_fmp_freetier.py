"""
test_fmp_freetier — regression for the LIVE free-tier rules verified 2026-07:
  * 'limit' > 5 makes stable endpoints 402 -> every fetch must clamp limit <= 5
    (this was why the EPS/Rev circles were empty on Render: /earnings?limit=8)
  * legacy /api/v3 endpoints 403 for post-Aug-2025 accounts -> the STABLE
    endpoint must be attempted FIRST for price history.
Offline — a capturing fake requests module records the calls.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sources import fmp


class _Cap:
    """Fake requests module: records (url, params), returns a canned 200 payload."""
    def __init__(self, payload):
        self.calls = []
        self._payload = payload

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        payload = self._payload

        class R:
            status_code = 200

            def json(self):
                return payload
        return R()


def test_earnings_limit_capped():
    cap = _Cap([{"date": "2026-01-01", "epsActual": 1.0, "epsEstimated": 0.9}])
    fmp.fetch_earnings("NVDA", "k", requests_mod=cap)          # default limit
    url, params = cap.calls[0]
    assert "/earnings" in url and params["limit"] <= 5, (url, params)
    cap2 = _Cap([{"date": "2026-01-01", "epsActual": 1.0}])
    fmp.fetch_earnings("NVDA", "k", requests_mod=cap2, limit=8)  # explicit 8 -> clamped
    assert cap2.calls[0][1]["limit"] <= 5, cap2.calls[0]
    print("earnings limit<=5 OK")


def test_estimates_limit_capped():
    cap = _Cap([{"date": "2027-01-01", "revenueAvg": 1.0}])
    fmp.fetch_estimates("NVDA", "k", requests_mod=cap)
    url, params = cap.calls[0]
    assert "analyst-estimates" in url and params["limit"] <= 5, (url, params)
    print("estimates limit<=5 OK")


def test_history_stable_first():
    cap = _Cap({"historical": [{"date": "2026-01-02", "adjClose": 1.0, "volume": 1}]})
    fmp.fetch_history("NVDA", "k", requests_mod=cap)
    url, _ = cap.calls[0]
    assert "historical-price-eod/full" in url, url   # stable BEFORE dead legacy
    print("history stable-first OK")


if __name__ == "__main__":
    test_earnings_limit_capped()
    test_estimates_limit_capped()
    test_history_stable_first()
    print("\nALL FMP-FREETIER TESTS PASSED ✅")
