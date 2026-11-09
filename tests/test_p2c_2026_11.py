"""
P2 portfolio construction, 2026-11 (methodology review against Bridgewater / AQR /
Vanguard).

P2-6  target weights from a risk budget (equal risk x implied-return tilt x
      uncertainty), under the hard caps.
P2-4  the band rebalance around them: trade only past 2pp, back to 1.75pp.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from domain.risk import targets as T
from pipeline import risk_report
from tests.test_alloc_corr import _provider, _store


def _diag(vols):
    return [[(v * v if i == j else 0.0) for j, _ in enumerate(vols)] for i, v in enumerate(vols)]


def _rc(cov, w):
    n = len(w)
    sw = [sum(cov[i][j] * w[j] for j in range(n)) for i in range(n)]
    var = sum(w[i] * sw[i] for i in range(n))
    return [w[i] * sw[i] / var for i in range(n)]


def test_equal_budget_gives_equal_risk():
    cov = [[0.04, 0.01, 0.0], [0.01, 0.09, 0.02], [0.0, 0.02, 0.16]]
    w = T.risk_budget_weights(cov, [1, 1, 1])
    assert abs(sum(w) - 1) < 1e-9 and all(abs(r - 1 / 3) < 1e-6 for r in _rc(cov, w)), (w, _rc(cov, w))
    w2 = T.risk_budget_weights(cov, [2, 1, 0])
    rc = _rc(cov, w2)
    assert w2[2] == 0.0 and abs(rc[0] - 2 / 3) < 1e-6, (w2, rc)
    print("P2-6 risk budgeting solves the budget OK", [round(x, 3) for x in w])


def test_budget_from_value():
    assert T.budget({"rating": "SELL / AVOID", "net_mos": 0.3})[0] == 0.0
    assert T.budget({"rating": "BUY"})[0] is None                      # no fair value: keep
    assert T.budget({"rating": "BUY", "net_mos": 0.2, "excess_pct": 5, "uncertainty": "Low"})[0] == 1.5
    assert T.budget({"rating": "BUY", "net_mos": 0.2, "excess_pct": -20, "uncertainty": "Low"})[0] == 0.5
    assert abs(T.budget({"rating": "HOLD", "net_mos": 0.0, "uncertainty": "Extreme"})[0] - 0.5) < 1e-12
    print("P2-6 budget tilt + uncertainty OK")


def _val(**k):
    return dict({"rating": "BUY", "net_mos": 0.2, "excess_pct": 0.0, "uncertainty": "Low"}, **k)


def test_caps_and_blocks_hold_and_the_cut_goes_to_cash():
    tk = ["A", "B", "C"]
    cov = _diag([0.2, 0.2, 0.2])
    w = {"A": 0.30, "B": 0.40, "C": 0.30}
    sizing = [{"ticker": "A", "hard_max_pct": 20.0}, {"ticker": "B", "hard_max_pct": 100.0},
              {"ticker": "C", "hard_max_pct": 100.0, "add_blocker": "MoS 3% < 15%"}]
    out = T.targets(tk, cov, w, {t: _val() for t in tk}, sizing, cash_frac=0.0, total_value=10000)
    by = {r["ticker"]: r for r in out["rows"]}
    assert by["A"]["target_pct"] == 20.0, by["A"]                        # 33.3 capped
    assert by["C"]["target_pct"] == 30.0 and "ADD blocked" in by["C"]["basis"], by["C"]
    assert by["B"]["target_pct"] == 50.0 and "รับไม่ได้" in by["B"]["basis"], by["B"]   # takes A's cut
    assert out["target_cash_pct"] == 0.0, out
    print("P2-6 caps / blocked ADD; the cut goes to who can still add OK")


def test_only_what_nobody_can_take_is_cash():
    tk = ["A", "B"]
    cov = _diag([0.2, 0.2])
    out = T.targets(tk, cov, {"A": 0.5, "B": 0.5}, {t: _val() for t in tk},
                    [{"ticker": t, "hard_max_pct": 30.0} for t in tk])
    assert [r["target_pct"] for r in out["rows"]] == [30.0, 30.0] and out["target_cash_pct"] == 40.0, out
    print("P2-6 cash = only what no cap can absorb OK")


def test_unvalued_holdings_keep_their_weight():
    tk = ["A", "B", "GLD"]
    cov = _diag([0.2, 0.2, 0.15])
    w = {"A": 0.5, "B": 0.3, "GLD": 0.2}
    out = T.targets(tk, cov, w, {"A": _val(), "B": _val()},
                    [{"ticker": t, "hard_max_pct": 100.0} for t in tk])
    by = {r["ticker"]: r for r in out["rows"]}
    assert by["GLD"]["target_pct"] == 20.0 and by["GLD"]["action"] == "HOLD", by["GLD"]
    assert by["A"]["target_pct"] == 40.0 and by["B"]["target_pct"] == 40.0, out["rows"]
    print("P2-6 unvalued holdings keep their weight OK")


def test_a_sell_exits_even_inside_the_band():
    tk = ["A", "B", "R"]
    cov = _diag([0.2] * 3)
    w = {"A": 0.49, "B": 0.495, "R": 0.015}                              # R: 1.5% < the 2pp band
    out = T.targets(tk, cov, w, {"A": _val(), "B": _val(), "R": _val(rating="SELL / AVOID")},
                    [{"ticker": t, "hard_max_pct": 100.0} for t in tk], total_value=10000)
    r = {x["ticker"]: x for x in out["rows"]}["R"]
    assert r["action"] == "SELL" and r["trade_pct"] == -1.5 and r["trade_usd"] == -150, r
    print("P2-6 SELL exits inside the band OK")


def test_targets_keep_each_sector_under_the_cap():
    tk = ["H1", "H2", "H3", "T1"]
    cov = _diag([0.2] * 4)                                               # ERC: 25% each
    w = {t: 0.25 for t in tk}
    sizing = [{"ticker": t, "hard_max_pct": 100.0, "sector": "Healthcare" if t[0] == "H" else "Technology"}
              for t in tk]
    out = T.targets(tk, cov, w, {t: _val() for t in tk}, sizing, sector_cap=0.40)
    by = {r["ticker"]: r["target_pct"] for r in out["rows"]}
    assert abs(by["H1"] + by["H2"] + by["H3"] - 40.0) < 0.15, by          # 75% -> 40%
    assert by["T1"] == 40.0 and abs(out["target_cash_pct"] - 20.0) < 0.15, (by, out)
    print("P2-6 targets respect the sector cap OK", by)


def test_band_trades_only_past_two_points():
    tk = ["A", "B", "C", "D"]
    cov = _diag([0.2] * 4)                                               # targets 25% each
    w = {"A": 0.28, "B": 0.235, "C": 0.24, "D": 0.245}
    out = T.targets(tk, cov, w, {t: _val() for t in tk},
                    [{"ticker": t, "hard_max_pct": 100.0} for t in tk], total_value=100000)
    by = {r["ticker"]: r for r in out["rows"]}
    assert by["A"]["action"] == "SELL" and abs(by["A"]["trade_pct"] - (26.75 - 28.0)) < 1e-9, by["A"]
    assert by["A"]["trade_usd"] == -1250
    assert all(by[t]["action"] == "HOLD" and by[t]["trade_pct"] == 0 for t in "BCD"), out["rows"]
    print("P2-4 200/175 band OK")


def test_build_carries_targets():
    spec = {"AAA": dict(sector="Technology"), "BBB": dict(sector="Healthcare"),
            "CCC": dict(sector="Energy")}
    p, _ = risk_report.build(_store(spec), "", 0, 250, 20.0, 1.0, False,
                             fetch_returns=_provider({}), fetch_long=lambda t: {})
    tg = p["targets"]
    assert tg["status"] == "ok" and len(tg["rows"]) == 3, tg
    # no fair values: each keeps its weight, but no more than the 20% single-name cap
    assert all(r["target_pct"] == 20.0 and "คงน้ำหนักเดิม" in r["basis"] for r in tg["rows"]), tg
    print("P2-6 targets in the risk payload OK")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("ALL P2C 2026-11 TESTS PASSED")
