"""
test_risk — the risk engine math (domain/engine/risk.py). Pure / offline.
Locks the core invariants so a refactor can't silently break the Allocation tab's
risk numbers: weights sum to 1, RC sums to σp, signed %RC sums to 100%, a true
diversifier gets a NEGATIVE risk contribution, DR>=1, and the score stays 0-100.
"""
import os
import sys
import math

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from domain.engine import risk as R


def test_weights_and_concentration():
    w = R.capital_weights({"A": 50, "B": 30, "C": 20})
    assert abs(sum(w.values()) - 1.0) < 1e-9, w
    c = R.concentration(w)
    assert abs(c["hhi"] - 0.38) < 1e-6, c          # .5²+.3²+.2² = .38
    assert abs(c["eff_n"] - 1 / 0.38) < 0.01, c
    # dropping non-positive values
    assert R.capital_weights({"A": 0, "B": -5}) == {}
    print("weights/concentration OK", c)


def test_single_holding_edges():
    w = R.capital_weights({"ONLY": 1000})
    c = R.concentration(w)
    assert c["eff_n"] == 1.0 and c["n"] == 1, c
    cov = R.proxy_cov(["ONLY"], [0.2], {"ONLY": "equity"})
    sp = R.portfolio_vol([1.0], cov)
    assert abs(sp - 0.2) < 1e-9, sp
    rows, summ = R.risk_contributions(["ONLY"], [1.0], cov)
    assert abs(rows[0]["signed_risk_pct"] - 100.0) < 1e-6, rows
    print("single-holding edges OK")


def _synthetic():
    # A,B strongly co-move; C moves OPPOSITE -> C should be a diversifier
    A = [(0.01 if i % 2 == 0 else -0.01) for i in range(200)]
    B = [(0.012 if i % 2 == 0 else -0.011) for i in range(200)]
    C = [(-0.009 if i % 2 == 0 else 0.011) for i in range(200)]
    tk = ["A", "B", "C"]
    w = R.capital_weights({"A": 50, "B": 30, "C": 20})
    wv = [w[t] for t in tk]
    cov = R.cov_matrix({"A": A, "B": B, "C": C}, tk)
    return tk, w, wv, cov


def test_risk_contribution_invariants():
    tk, w, wv, cov = _synthetic()
    sp = R.portfolio_vol(wv, cov)
    rows, summ = R.risk_contributions(tk, wv, cov)
    # signed %RC sums to ~100
    assert abs(sum(r["signed_risk_pct"] for r in rows) - 100.0) < 1.0, rows
    # RC (signed_risk_pct/100 * sp) sums back to sp
    rc_sum = sum((r["signed_risk_pct"] / 100.0) * sp for r in rows)
    assert abs(rc_sum - sp) < 1e-6, (rc_sum, sp)
    # the anti-correlated name C must reduce risk -> negative signed RC
    c_row = next(r for r in rows if r["ticker"] == "C")
    assert c_row["signed_risk_pct"] < 0, c_row
    # absolute risk shares sum to 100
    assert abs(sum(r["abs_risk_share_pct"] for r in rows) - 100.0) < 1.0, rows
    print("RC invariants OK; C signed%RC =", c_row["signed_risk_pct"])


def test_dr_and_score():
    tk, w, wv, cov = _synthetic()
    sp = R.portfolio_vol(wv, cov)
    vols = [math.sqrt(cov[i][i]) for i in range(len(tk))]
    dr = R.diversification_ratio(wv, vols, sp)
    assert dr >= 0.99, dr                           # DR never below ~1
    _, summ = R.risk_contributions(tk, wv, cov)
    c = R.concentration(w)
    sc = R.diversification_score(dr, 1.05, summ["enb_abs"], c["eff_n"], c["n"], 0.0)
    assert 0 <= sc["score"] <= 100, sc
    for k in ("true_diversification", "risk_balance", "gap_coverage", "concentration"):
        assert 0 <= sc["sub"][k] <= 100, sc
    print("DR/score OK: DR", round(dr, 2), "score", sc["score"])


def test_beta_risk_contribution():
    w = R.capital_weights({"HI": 20, "LO": 80})       # small money, high beta
    rows = R.beta_risk_contribution(w, {"HI": 3.0, "LO": 0.5})
    hi = next(r for r in rows if r["ticker"] == "HI")
    # 20% capital * beta 3 vs 80% * 0.5 -> HI risk share = 60/(60+40)=60%
    assert abs(hi["risk_pct"] - 60.0) < 0.1, rows
    assert hi["risk_pct"] > hi["capital_pct"], hi    # the headline "small money, big risk"
    print("beta RC OK:", hi)


def test_stress_var_reverse():
    w = R.capital_weights({"A": 60, "B": 40})
    betas = {"A": 1.5, "B": 0.8}
    sect = {"A": "Technology", "B": "Healthcare"}
    st = R.stress_test(w, betas, sect)
    assert st[0]["loss_pct"] <= st[-1]["loss_pct"], st   # sorted worst-first
    assert all(r["loss_pct"] <= 0 for r in st), st
    rev = R.reverse_stress(w, betas, 20)
    assert rev["market_move_pct"] < 0, rev
    v = R.var_cvar(0.25)
    assert v["var99_pct"] > v["var95_pct"] > 0, v
    assert R.var_cvar(0)["var95_pct"] is None
    print("stress/var/reverse OK:", st[0], rev["market_move_pct"])


def test_crisis_raises_correlation():
    tk, w, wv, cov = _synthetic()
    cov_c = R.crisis_cov(cov, tk, {t: "equity" for t in tk})
    # crisis equity correlation floored at 0.9 -> A,B corr should be >= normal
    corr_n = R.corr_from_cov(cov)
    corr_c = R.corr_from_cov(cov_c)
    i, j = tk.index("A"), tk.index("C")              # A,C were negatively correlated
    assert corr_c[i][j] >= corr_n[i][j] - 1e-9, (corr_n[i][j], corr_c[i][j])
    print("crisis correlation floor OK")


if __name__ == "__main__":
    test_weights_and_concentration()
    test_single_holding_edges()
    test_risk_contribution_invariants()
    test_dr_and_score()
    test_beta_risk_contribution()
    test_stress_var_reverse()
    test_crisis_raises_correlation()
    print("\nALL RISK TESTS PASSED")
