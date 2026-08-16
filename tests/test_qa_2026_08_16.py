"""
Regression guards for the 2026-08-16 dashboard QA pass.

Each test here pins a bug that actually shipped and was visible on the live
dashboard, so the fix cannot silently regress:

  ENB      the Correlation tab read "ENB 12.28 -> 16.52" and "ENB / Eff N 1.06 ดี"
           in CRISIS mode — diversification IMPROVING as correlations ran to 0.9,
           and a ratio above the 1.0 the Ref tab documents as the ceiling. The
           number shown was the inverse-HHI of risk contributions, which rises when
           contributions even out. ENB is now DR², tied to the DR the same page
           already prints.
  None     AXON and TSLA cards read "market prices ~None% 10y CAGR", and AXON's
           flag read "(None -> None)".
  stars    UNH scored 2.25 and drew ★★☆☆☆ (2.0) while REGN's 2.28 drew ★★½☆☆ —
           round() is banker's rounding and composites move in quarter points.
  neutral  TSM lost the whole D pillar and HIMS lost E_econ, each imputed at 2.5
           and each carrying 20-30% weight, with not one line in the drawer's
           audit trail to say so.
  action   the Action matrix was fed the secondary RSI/MACD/Bollinger vote, which
           needs |sum| >= 2 to leave Neutral and returned Neutral for 21 rows out
           of 21 — so ACCUMULATE / STRONG BUY / TRIM could never occur.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from domain import diversification, indicators            # noqa: E402
from domain.engine import risk as riskeng                 # noqa: E402
from domain.engine.deep_v82 import stars                  # noqa: E402


# --------------------------------------------------------------------------
# ENB
# --------------------------------------------------------------------------
def _equal_vol_cov(n, rho, vol=0.30):
    return [[vol * vol * (1.0 if i == j else rho) for j in range(n)] for i in range(n)]


def _weights_like_the_portfolio():
    """21 names, roughly the committed book: a ~11% top position, 44.5% in the top 5."""
    head = [0.109, 0.104, 0.101, 0.067, 0.064]
    tail = [(1 - sum(head)) / 16] * 16
    w = head + tail
    return [x / sum(w) for x in w]


def test_enb_falls_when_correlations_rise():
    """The shipped bug, stated directly: crisis ENB was HIGHER than normal ENB."""
    w = _weights_like_the_portfolio()
    n, vol = len(w), [0.30] * len(w)

    def enb_at(rho):
        cov = _equal_vol_cov(n, rho)
        return riskeng.effective_bets(
            riskeng.diversification_ratio(w, vol, riskeng.portfolio_vol(w, cov)))

    normal, crisis = enb_at(0.17), enb_at(riskeng.CRISIS_EQUITY_CORR)
    assert crisis < normal, f"ENB must FALL in a crisis, got {normal} -> {crisis}"
    assert crisis < 2.0, f"at rho={riskeng.CRISIS_EQUITY_CORR} the book is ~1 bet, got {crisis}"


def test_enb_never_exceeds_effective_n():
    """Ref tab: 'ENB ÷ Eff N — 1.0 คือเพดาน'. The live dashboard printed 1.06."""
    w = _weights_like_the_portfolio()
    n, vol = len(w), [0.30] * len(w)
    eff_n = 1.0 / sum(x * x for x in w)
    # effective_bets() reports to 2dp, so allow half a display unit at rho=0 where
    # the two are mathematically equal. The shipped bug overshot by 0.94, not 0.005.
    tol = 0.005 + 1e-9
    for rho in (0.0, 0.05, 0.17, 0.5, 0.9, 0.99):
        cov = _equal_vol_cov(n, rho)
        enb = riskeng.effective_bets(
            riskeng.diversification_ratio(w, vol, riskeng.portfolio_vol(w, cov)))
        assert enb <= eff_n + tol, f"rho={rho}: ENB {enb} > Eff N {eff_n:.2f}"


def test_enb_agrees_with_the_diversification_ratio_on_the_same_page():
    """ENB and DR are printed side by side; ENB = DR² keeps them from contradicting."""
    for dr in (1.0, 1.05, 1.91, 2.13, 3.0):
        assert abs(riskeng.effective_bets(dr) - round(dr * dr, 2)) < 1e-9


def test_risk_balance_is_reported_under_its_own_name():
    """The old inverse-HHI number is still useful as 'how evenly is risk spread' —
    it just is not ENB. Keep it, but under a name that says what it measures."""
    w = _weights_like_the_portfolio()
    _rows, summ = riskeng.risk_contributions(
        [f"T{i}" for i in range(len(w))], w, _equal_vol_cov(len(w), 0.17))
    assert summ["risk_balance_n"] is not None
    assert summ["enb_abs"] == summ["risk_balance_n"]        # back-compat alias only


def test_fragility_never_reads_as_a_negative_loss():
    """With the broken ENB the card said 'การกระจายหาย ~-35%' — a negative loss."""
    ok = diversification.diversification_philosophy(
        n_holdings=21, enb=3.65, enb_crisis=1.10, eff_n=15.58)
    assert ok["gauge"]["fragility_pct"] > 0
    assert "หาย" in ok["gauge"]["fragility_text"]
    assert "-" not in ok["gauge"]["fragility_text"].split("~")[-1][:4]

    # and if an upstream regression ever inverts it again, say so plainly
    bad = diversification.diversification_philosophy(
        n_holdings=21, enb=12.28, enb_crisis=16.52, eff_n=15.58)
    assert "ผิดปกติ" in bad["gauge"]["fragility_text"]
    assert "หาย ~-" not in bad["story_normal"]


# --------------------------------------------------------------------------
# stars
# --------------------------------------------------------------------------
def test_quarter_point_composites_round_half_up():
    """UNH 2.25 drew two stars while REGN 2.28 drew two and a half."""
    assert stars(2.25) == stars(2.28), "2.25 and 2.28 must both round to 2.5"
    assert stars(2.25).count("½") == 1
    assert stars(2.75).count("★") == 3
    assert stars(2.24).count("½") == 0


def test_stars_are_monotonic():
    def val(s):
        return s.count("★") + 0.5 * s.count("½")
    prev = -1.0
    x = 0.0
    while x <= 5.0001:
        v = val(stars(round(x, 2)))
        assert v >= prev, f"stars({x}) went backwards"
        prev = v
        x += 0.05


# --------------------------------------------------------------------------
# action matrix
# --------------------------------------------------------------------------
def test_action_matrix_reaches_its_documented_cells():
    """Ref documents nine cells. With the secondary vote pinned at Neutral only
    three were reachable, so ACCUMULATE never appeared on any card."""
    assert indicators.action("HOLD", "Neutral", mom_label="Strong") == "ACCUMULATE"
    assert indicators.action("BUY", "Neutral", mom_label="Strong") == "STRONG BUY"
    assert indicators.action("SELL", "Neutral", mom_label="Weak") == "STRONG SELL"
    assert indicators.action("SELL", "Neutral", mom_label="Positive") == "TRIM"
    assert indicators.action("BUY", "Neutral", mom_label="Weak") == "WAIT"
    assert indicators.action("HOLD", "Neutral", mom_label="Neutral") == "HOLD"


def test_action_still_works_without_the_primary_label():
    """Callers that have not been updated must keep their old behaviour."""
    assert indicators.action("HOLD", "Bullish") == "ACCUMULATE"
    assert indicators.action("HOLD", None) == "HOLD"
    assert indicators.action(None, "Bullish") is None


def test_action_prefers_the_label_the_card_actually_shows():
    """The card said 'MOMENTUM Strong' while the action axis said Neutral (ABBV)."""
    assert indicators.action("HOLD", "Neutral", mom_label="Strong") != \
        indicators.action("HOLD", "Neutral")


# --------------------------------------------------------------------------
# no Python None in reader-facing strings
# --------------------------------------------------------------------------
def test_no_none_leaks_into_the_philosophy_narrative():
    for kw in ({"n_holdings": 21, "enb": None, "enb_crisis": None, "eff_n": None},
               {"n_holdings": 21, "enb": 3.65, "enb_crisis": 1.10, "eff_n": 15.58}):
        out = diversification.diversification_philosophy(**kw)
        for key in ("story_normal", "story_crisis"):
            assert "None" not in out[key], f"{key}: {out[key]}"
        for p in out["pillars"]:
            assert "None" not in str(p["value"]) and "None" not in str(p["note"])


if __name__ == "__main__":
    # Runnable without pytest, the way run_tests.py invokes every other suite.
    ns = dict(globals())
    for _name in sorted(n for n in ns if n.startswith("test_")):
        ns[_name]()
        print("OK", _name)
    print("\nALL test_qa_2026_08_16 PASSED")
