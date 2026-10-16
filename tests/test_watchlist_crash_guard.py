"""A watchlist row's advice must honour the S9 crash guard exactly like the same
holding's advice. The watchlist used to build advice before it fetched the market
regime, so in a risk-off market it advised timing entries on momentum that the
holdings card vetoed."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from domain import momentum
from domain.facts import FinancialFacts
from domain.engine.contract import Valuation
from pipeline import watchlist, fundamentals, daily, batch
from sources import yahoo

CRASH_TEXT = "S9 crash guard"


def _run(regime):
    saved = (fundamentals.analyze, fundamentals.fetch_roc_table, daily.short_indicators,
             daily.composite_momentum, daily.spy_series, momentum.market_state,
             yahoo.fetch_treasury_10y)
    ff = FinancialFacts("TST", company="Test Co")
    ff.price = 100.0
    fundamentals.analyze = lambda *a, **k: (ff, Valuation(version="8.2", recommendation="BUY",
                                                         anchor_value=150.0), 0)
    fundamentals.fetch_roc_table = lambda *a, **k: None
    daily.short_indicators = lambda *a, **k: {}
    daily.composite_momentum = lambda *a, **k: ({"mom_label": "Strong"}, None)
    daily.spy_series = lambda *a, **k: {"closes": [1.0] * 300}
    momentum.market_state = lambda closes: {"regime": regime}
    yahoo.fetch_treasury_10y = lambda *a, **k: (0.045, True)
    try:
        return watchlist.fetch_watchlist(["TST"], "", 0, 250)["rows"][0]
    finally:
        (fundamentals.analyze, fundamentals.fetch_roc_table, daily.short_indicators,
         daily.composite_momentum, daily.spy_series, momentum.market_state,
         yahoo.fetch_treasury_10y) = saved


def test_risk_off_watchlist_advice_carries_the_veto():
    row = _run("risk_off")
    assert row["momentum_v2"]["crash_guard"] is True, row["momentum_v2"]
    assert CRASH_TEXT in (row.get("advice") or ""), row.get("advice")


def test_risk_on_watchlist_advice_has_no_veto():
    row = _run("risk_on")
    assert row["momentum_v2"]["crash_guard"] is False
    assert CRASH_TEXT not in (row.get("advice") or "")


if __name__ == "__main__":
    from tests._harness import run_suite
    run_suite(globals(), "ALL test_watchlist_crash_guard OK")
