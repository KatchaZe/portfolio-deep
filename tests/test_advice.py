"""
test_advice — Damodaran action-synthesis line (domain/advice.py).
Pure/offline. The advice must (1) never raise, (2) follow the value->quality->
timing hierarchy, (3) produce a sensible Action for the main archetypes.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from domain import advice


def row(**kw):
    base = {"composite": 3.5, "upside_pct": None, "net_upside_pct": None,
            "key_metrics": {}, "eq_verdict": None, "momentum_v2": None,
            "rev_verdict": None, "rev_implied_cagr": None, "rev_actual_1y": None}
    base.update(kw)
    return base


def test_cheap_quality_momentum():
    a = advice.build(row(upside_pct=25, net_upside_pct=20,
                         key_metrics={"spread_pct": 15}, eq_verdict="CLEAN",
                         momentum_v2={"mom_label": "Positive"}))
    # REV-7: the line used to call +25% upside "margin of safety". It is not — MoS is
    # (V-P)/VALUE, which for +25% upside is +20%. Both are now stated, correctly named.
    assert "ส่วนเผื่อความปลอดภัย" in a and "Action:" in a
    assert "MoS +20% ของมูลค่า" in a, a
    assert "ครบทั้งสามเงื่อนไข" in a
    print("cheap+quality+momentum OK (upside vs margin-of-safety stated separately)")


def test_expensive_good_business():
    a = advice.build(row(upside_pct=-40, composite=4.2,
                         key_metrics={"spread_pct": 30}, eq_verdict="CLEAN",
                         momentum_v2={"mom_label": "Positive"}))
    assert "ราคาแพง" in a or "แพง" in a
    assert "ไม่เพิ่ม" in a
    print("expensive-good-business OK")


def test_preprofit_aggressive():
    a = advice.build(row(rev_implied_cagr=74.3, rev_actual_1y=55.8,
                         rev_verdict="Aggressive", composite=2.2))
    assert "pre-profit" in a and "74" in a and "56" in a or "55" in a
    print("pre-profit OK")


def test_value_trap():
    a = advice.build(row(upside_pct=30, key_metrics={"spread_pct": -5},
                         eq_verdict="LOW"))
    assert "value trap" in a
    print("value-trap OK")


def test_crash_guard_vetoes_momentum():
    a = advice.build(row(upside_pct=20, key_metrics={"spread_pct": 12},
                         eq_verdict="CLEAN",
                         momentum_v2={"mom_label": "Strong", "crash_guard": True}))
    assert "RISK-OFF" in a
    assert "ครบทั้งสามเงื่อนไข" not in a       # momentum must NOT count while vetoed
    print("crash-guard veto OK")


def test_never_raises_on_garbage():
    assert advice.build({}) == "" or isinstance(advice.build({}), str)
    assert isinstance(advice.build({"upside_pct": "x", "key_metrics": None}), str)
    print("garbage-safety OK")


# --------------------------------------------------------------------------
# one Action: the card cell and the advice line come from the same decision
# --------------------------------------------------------------------------
def _cell_and_line(**kw):
    r = row(**kw)
    return advice.action(r), advice.build(r)


def test_expensive_quality_with_momentum_is_no_add_not_accumulate():
    """ASML 2026-10-16: MoS -109%, spread +27pp, momentum Positive. The old
    composite x momentum matrix showed ACCUMULATE while the line said do not add."""
    cell, line = _cell_and_line(mos_pct=-108.8, upside_pct=-52, composite=3.6,
                                key_metrics={"spread_pct": 27.2},
                                momentum_v2={"mom_label": "Positive"})
    assert cell == "NO ADD", cell
    assert line.endswith(advice.ACTIONS["NO ADD"]), line


def test_cheap_quality_weak_momentum_is_accumulate_not_watch():
    """NVO 2026-10-16: MoS +41%, spread +19pp, momentum Negative showed WATCH."""
    cell, _ = _cell_and_line(mos_pct=40.6, upside_pct=68, composite=3.2,
                             key_metrics={"spread_pct": 18.6},
                             momentum_v2={"mom_label": "Negative"})
    assert cell == "ACCUMULATE", cell


def test_risk_off_turns_a_buy_side_action_into_wait_on_the_server():
    for lab in ("Strong", "Negative"):
        cell, line = _cell_and_line(mos_pct=30, upside_pct=43,
                                    key_metrics={"spread_pct": 15},
                                    momentum_v2={"mom_label": lab, "crash_guard": True})
        assert cell == "WAIT", (lab, cell)
        assert advice.ACTIONS["WAIT"] in line


def test_every_rule_names_a_known_action():
    seen = set()
    for gv in (True, False):
        for bv in (True, False):
            for gq in (True, False):
                for gm in (True, False):
                    for cr in (True, False):
                        for comp in (None, 2.0, 3.2, 3.8):
                            for hp in (True, False):
                                a = advice._action(gv, bv, gq, gm, cr, comp, hp)
                                assert a is None or a in advice.ACTIONS, a
                                seen.add(a)
    assert seen - {None} == set(advice.ACTIONS), set(advice.ACTIONS) - seen


def test_cell_matches_line_on_every_stored_holding():
    """Invariant on the real store: the Action cell's key is the action the advice
    line spells out. 2026-10-16 had 10 of 21 cards disagree."""
    import json
    from pipeline import portfolio
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "portfolio.json")
    if not os.path.exists(path):
        print("  SKIP: no stored portfolio")
        return
    with open(path, encoding="utf-8") as fh:
        rows = portfolio.view(json.load(fh))["rows"]
    bad = [r["ticker"] for r in rows
           if r.get("action") and not (r.get("advice") or "").endswith(advice.ACTIONS[r["action"]])]
    assert not bad, bad
    print(f"cell == line on all {len(rows)} stored holdings")


if __name__ == "__main__":
    from tests._harness import run_suite
    run_suite(globals(), "ALL ADVICE TESTS PASSED ✅")
