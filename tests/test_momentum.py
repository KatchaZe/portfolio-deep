"""
test_momentum — faithful momentum composite + FMP history parser + Yahoo adjclose
extraction. All pure/synthetic (no network); locks the momentum source of truth.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from domain import momentum
from sources import fmp, yahoo


# --------------------------------------------------------------------------- #
#  momentum measures + composite                                             #
# --------------------------------------------------------------------------- #
def test_roc_sma_basic():
    closes = [100 + i for i in range(300)]            # 100..399 ascending
    assert abs(momentum.roc(closes, 63) - (399 / 336 - 1)) < 1e-9
    assert momentum.sma(closes, 200) == sum(range(200, 400)) / 200
    assert momentum.roc([1, 2], 63) is None           # too short
    print("roc/sma OK")


def test_mom_12_1():
    closes = [100 + i for i in range(300)]
    # p_recent = closes[-22] (378), p_year = closes[-253] (147)
    assert abs(momentum.mom_12_1(closes) - (378 / 147 - 1)) < 1e-9
    assert momentum.mom_12_1([1] * 100) is None       # < 252 -> None
    print("mom_12_1 OK")


def test_compute_uptrend_strong():
    closes = [100 + i for i in range(300)]             # pure uptrend
    m = momentum.compute("UP", closes, dividend_adjusted=True)
    assert m["mom_label"] == "Strong", m
    assert m["mom_score"] == 4 and m["mom_n"] == 4
    assert m["above_sma200"] is True
    assert m["rsi"] == 100.0
    assert all(m["components"].values())
    print("compute uptrend OK")


def test_compute_downtrend_negative():
    closes = [400 - i for i in range(300)]             # pure downtrend
    m = momentum.compute("DN", closes)
    assert m["mom_label"] == "Negative", m
    assert m["mom_score"] == 0
    assert m["above_sma200"] is False
    assert not any(m["components"].values())
    print("compute downtrend OK")


def test_compute_insufficient():
    m = momentum.compute("X", [1, 2, 3])
    assert m.get("error") == "insufficient_data", m
    print("insufficient OK")


def test_cross_sectional_rank():
    rows = [
        {"ticker": "A", "mom_12_1": 0.5},
        {"ticker": "B", "mom_12_1": 0.1},
        {"ticker": "C", "mom_12_1": 0.3},
        {"ticker": "D", "mom_12_1": None},            # unranked
    ]
    momentum.cross_sectional_rank(rows)
    by = {r["ticker"]: r for r in rows}
    assert by["A"]["mom_bucket"] == "Top"
    assert by["C"]["mom_bucket"] == "Mid"
    assert by["B"]["mom_bucket"] == "Bottom"
    assert by["D"]["mom_rank_pct"] is None and by["D"]["mom_bucket"] is None
    print("cross-sectional rank OK")


# --------------------------------------------------------------------------- #
#  FMP history parser                                                         #
# --------------------------------------------------------------------------- #
def test_fmp_parse_history_legacy():
    j = {"symbol": "ABBV", "historical": [
        {"date": "2026-06-17", "close": 102.0, "adjClose": 101.0, "volume": 300},
        {"date": "2026-06-15", "close": 100.0, "adjClose": 99.0, "volume": 100},
        {"date": "2026-06-16", "close": 101.0, "adjClose": 100.0, "volume": 200},
    ]}
    h = fmp.parse_history(j)
    assert h["dates"] == ["2026-06-15", "2026-06-16", "2026-06-17"], h   # sorted ascending
    assert h["closes"] == [99.0, 100.0, 101.0], h                        # adjClose preferred
    assert h["volumes"] == [100.0, 200.0, 300.0], h
    print("fmp parse_history legacy OK")


def test_fmp_parse_history_list_and_fallback():
    j = [{"date": "2026-06-16", "close": 50.0, "volume": 5},             # no adjClose -> close
         {"date": "2026-06-15", "close": 49.0}]                          # no volume -> 0
    h = fmp.parse_history(j)
    assert h["closes"] == [49.0, 50.0], h
    assert h["volumes"] == [0.0, 5.0], h
    assert fmp.parse_history({})["closes"] == []                         # empty safe
    print("fmp parse_history list/fallback OK")


# --------------------------------------------------------------------------- #
#  Yahoo adjclose extraction (offline, fake requests)                        #
# --------------------------------------------------------------------------- #
class _Resp:
    status_code = 200

    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


class _Req:
    def __init__(self, payload):
        self._p = payload

    def get(self, url, **kw):
        return _Resp(self._p)


def test_yahoo_fetch_chart_adjclose():
    payload = {"chart": {"result": [{
        "timestamp": [1700000000, 1700086400, 1700172800],
        "indicators": {
            "quote": [{"close": [10.0, 11.0, 12.0], "volume": [100, 200, 300]}],
            "adjclose": [{"adjclose": [9.0, 9.9, 10.8]}],
        },
    }]}}
    d = yahoo.fetch_chart("TST", requests_mod=_Req(payload))
    assert d["closes"] == [10.0, 11.0, 12.0], d
    assert d["adj_closes"] == [9.0, 9.9, 10.8], d                        # adjusted captured
    assert d["volumes"] == [100.0, 200.0, 300.0], d
    print("yahoo adjclose OK")


def test_yahoo_fetch_chart_no_adjclose():
    payload = {"chart": {"result": [{
        "timestamp": [1700000000, 1700086400],
        "indicators": {"quote": [{"close": [10.0, 11.0], "volume": [100, 200]}]},
    }]}}
    d = yahoo.fetch_chart("TST", requests_mod=_Req(payload))
    assert "adj_closes" not in d, d                                      # absent -> key omitted
    assert d["closes"] == [10.0, 11.0], d
    print("yahoo no-adjclose OK")


# --------------------------------------------------------------------------- #
#  R1 — data-quality guard (clean_series)                                     #
# --------------------------------------------------------------------------- #
def test_clean_series_nonpositive():
    cc, vv, dd, fl = momentum.clean_series([10, 0, 11, -5, 12], [1, 2, 3, 4, 5],
                                           ["a", "b", "c", "d", "e"])
    assert cc == [10, 11, 12] and vv == [1, 3, 5] and dd == ["a", "c", "e"], (cc, vv, dd)
    assert fl["dropped_nonpos"] == 2 and fl["bars"] == 3
    print("clean_series non-positive OK")


def test_clean_series_spike_vs_sustained():
    cc, _, _, fl = momentum.clean_series([100, 101, 300, 100, 101, 102])   # lone spike
    assert 300 not in cc and fl["dropped_spikes"] == 1, (cc, fl)
    cc2, _, _, fl2 = momentum.clean_series([100, 101, 160, 170, 175])      # sustained jump
    assert 160 in cc2 and fl2["dropped_spikes"] == 0, (cc2, fl2)
    assert fl2["max_jump_pct"] >= 58, fl2
    print("clean_series spike/sustained OK")


# --------------------------------------------------------------------------- #
#  R3 — staleness rule (spec mirror of refresh._momentum_meta)                #
# --------------------------------------------------------------------------- #
def test_staleness_rule():
    import datetime as dt

    def stale(as_of, today, after=4):
        age = (today - dt.date.fromisoformat(as_of)).days
        return age, age > after

    today = dt.date(2026, 6, 25)
    assert stale("2026-06-24", today) == (1, False)      # fresh
    assert stale("2026-06-18", today)[1] is True         # 7 days -> stale
    print("staleness rule OK")


# --------------------------------------------------------------------------- #
#  R2 — dividend-aware split-only warning                                     #
# --------------------------------------------------------------------------- #
def test_div_warn():
    assert momentum.div_warn(True, 0) is False        # adjusted feed -> never warn
    assert momentum.div_warn(True, None) is False
    assert momentum.div_warn(False, 1.5) is True      # split-only + dividend payer -> warn
    assert momentum.div_warn(False, 0) is False       # split-only + non-payer -> accurate, no warn
    assert momentum.div_warn(False, None) is True     # split-only + unknown -> conservative warn
    print("div_warn OK")


if __name__ == "__main__":
    test_clean_series_nonpositive()
    test_clean_series_spike_vs_sustained()
    test_staleness_rule()
    test_div_warn()
    test_roc_sma_basic()
    test_mom_12_1()
    test_compute_uptrend_strong()
    test_compute_downtrend_negative()
    test_compute_insufficient()
    test_cross_sectional_rank()
    test_fmp_parse_history_legacy()
    test_fmp_parse_history_list_and_fallback()
    test_yahoo_fetch_chart_adjclose()
    test_yahoo_fetch_chart_no_adjclose()
    print("\nALL test_momentum PASSED")
