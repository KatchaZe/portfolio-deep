"""
P2 fixes, 2026-11 (methodology review against Morningstar / Barra / Vanguard).

P2-3  the measured moat sets how long the FCFF DCF runs before the stable state.
P2-7  the DEEP recommendation is the rating; the Action never points against it.
"""
import json
import os

import config
from domain import advice
from domain.engine import deep_v82 as E
from domain.engine import fcff_dcf


def _roic(vals):
    return {f"{2025 - i}-12-31": {"roic": v} for i, v in enumerate(vals)}


def test_moat_class_from_the_competitive_advantage_period():
    assert E.moat_class(_roic([20, 20, 20, 20, 20]), 0.09)[0] == "wide"
    assert E.moat_class(_roic([20, 20, 20, 5, 5]), 0.09)[0] == "narrow"
    assert E.moat_class(_roic([20, 20, 5, 5, 5]), 0.09)[0] == "none"
    assert E.moat_class(_roic([20, 20, 20, 20]), 0.09)[0] == "narrow"     # 4 years: not wide
    assert E.moat_class(_roic([20, 20, 20]), 0.09) == (None, None)       # too short to say
    print("P2-3 moat class OK")


def _kw(**over):
    kw = dict(revenue=1000.0, margin_now=0.25, margin_target=0.25, g_high=0.12,
              growth_path=[0.12, 0.11, 0.10], g_stable=0.03, sales_to_capital=1.5,
              tax_now=0.2, tax_marginal=0.25, wacc=0.09, wacc_stable=0.085,
              roic_stable=0.15, cash=0.0, debt=0.0, shares=100.0)
    kw.update(over)
    return kw


def test_dcf_horizon_follows_the_moat_period():
    narrow = fcff_dcf.value(**_kw())
    wide = fcff_dcf.value(**_kw(high_years=7, total_years=15))
    none = fcff_dcf.value(**_kw(high_years=3, total_years=8))
    assert len(narrow["path"]) == 10 and len(wide["path"]) == 15 and len(none["path"]) == 8
    assert none["per_share"] < narrow["per_share"] < wide["per_share"], (
        none["per_share"], narrow["per_share"], wide["per_share"])
    assert fcff_dcf.value(**_kw(high_years=5, total_years=10))["per_share"] == narrow["per_share"]
    print("P2-3 horizon none < narrow < wide OK",
          none["per_share"], narrow["per_share"], wide["per_share"])


def test_engine_reports_the_moat_and_the_5_plus_5_value():
    from pipeline import normalize, validate

    def load(n):
        p = os.path.join(config.FIXTURE_DIR, "MSFT", n + ".json")
        return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None
    ff = normalize.build("MSFT", load("sec_companyfacts"), load("fmp_profile"),
                         load("yahoo_quotesummary"))
    validate.validate(ff)
    v = E.DeepV82Engine().evaluate(ff, rf=0.045)
    src = v.dcf["sources"]
    assert src["moat"] == "wide" and src["total_years"] == 15, src
    assert src["value_at_5_5"] and src["value_at_5_5"] < v.fv_dcf, (src, v.fv_dcf)
    assert len(v.dcf["path"]) == 15
    print("P2-3 engine: MSFT wide,", v.fv_dcf, "vs 5+5", src["value_at_5_5"])


def _row(reco, mos, spread=5.0, mom="Strong"):
    return {"recommendation": reco, "mos_pct": mos, "upside_pct": (100 / (100 - mos) - 1) * 100,
            "key_metrics": {"spread_pct": spread}, "eq_verdict": "CLEAN",
            "momentum_v2": {"mom_label": mom}, "composite": 3.5}


def test_action_never_points_against_the_rating():
    assert advice.action(_row("BUY", 30.0)) == "BUY"
    assert advice.action(_row("SELL / AVOID", 30.0)) == "REDUCE"
    assert advice.action(_row("HOLD", 30.0)) == "HOLD"
    assert advice.action(_row("HOLD / Accumulate", 30.0)) == "ACCUMULATE"
    dear = dict(_row("BUY", -60.0, spread=-3.0), composite=2.0)      # rules say REDUCE
    assert advice._decide(dict(dear, recommendation=None))[1] == "REDUCE"
    assert advice.action(dear) == "HOLD"
    line = advice.build(_row("SELL / AVOID", 30.0))
    assert "DEEP SELL / AVOID" in line and "REDUCE" in line, line
    assert advice.action(_row(None, 30.0)) == "BUY"          # no rating: Action as before
    print("P2-7 Action aligned to DEEP OK")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("ALL P2 2026-11 TESTS PASSED")
