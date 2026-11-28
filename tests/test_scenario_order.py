"""test_scenario_order: Bear <= Base <= Bull by construction (E103).

Every Bear input is no better than Base (margin, growth; reinvestment and WACC unchanged)
and every Bull input no worse. Lower growth is worth MORE when ROIC < WACC, so a shifted
growth is kept only when it moves the value the case's way. Scan 2026-11-28 of 175 valued
names (holdings, watchlist, cached filers): Base > Bull on DCO, LFST, LOAR, PRMB; Bear >
Base on GSAT, RDW. The Monte Carlo is not ordered by construction: when the model margin
is the top (NVDA fixture, FV $320 over p90 $318) or the bottom (young AVAV, GILD, HOG, VTRS:
p10 over FV) of its range the draws are one-sided; that is reported, not tested. Fixtures: tests/fixtures/cyclical (MU, NVDA, TMDX, HIMS, RKLB, TSLA)
and tests/fixtures/scenarios (DCO, LOAR, RDW; cached companyfacts at a nominal P/B 3)."""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from domain.engine.deep_v82 import DeepV82Engine           # noqa: E402
from domain.facts import FinancialFacts                    # noqa: E402
from tests._harness import check, run_suite                # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
FIELDS = set(FinancialFacts.__dataclass_fields__)
PATHS = sorted(glob.glob(os.path.join(FIX, "cyclical", "*.json")) + glob.glob(os.path.join(FIX, "scenarios", "*.json")))


def _value(path, **over):
    d = dict(json.load(open(path)), **over)
    ff = FinancialFacts(**{k: v for k, v in d.items() if k in FIELDS})
    ff.flags = []
    return DeepV82Engine().evaluate(ff, rf=0.045)


def order_violations(v, name):
    """[(name, what)] where the three cases or their inputs are out of order."""
    out = []
    cases = {c["name"]: c for c in (v.scenarios or {}).get("cases") or []}
    if len(cases) != 3:
        return out
    b, m, u = cases["Bear"], cases["Base"], cases["Bull"]
    if not (b["value"] <= m["value"] + 1e-9 and m["value"] <= u["value"] + 1e-9):
        out.append((name, f"values {b['value']} / {m['value']} / {u['value']}"))
    for k in ("margin_pct", "growth_y1_pct"):
        xs = [c.get(k) for c in (b, m, u)]
        if None not in xs and not (xs[0] <= xs[1] <= xs[2]):
            out.append((name, f"{k} {xs}"))
    return out


def test_E103_bear_base_bull_ordered_on_every_fixture():
    check(len(PATHS) >= 9, "nine fixtures", str(len(PATHS)))
    bad = []
    for p in PATHS:
        bad += order_violations(_value(p), os.path.basename(p)[:-5])
    check(not bad, "Bear <= Base <= Bull, inputs one way, on every fixture", str(bad))


def test_E103_value_destroying_growth_keeps_base_growth():
    dco = _value(os.path.join(FIX, "scenarios", "DCO.json"))
    cases = {c["name"]: c for c in dco.scenarios["cases"]}
    check(cases["Bull"]["growth_y1_pct"] == cases["Base"]["growth_y1_pct"],
          "DCO (ROIC 7.0% < WACC 8.2%): faster growth lowers value, so Bull keeps Base growth",
          str(cases))
    rdw = _value(os.path.join(FIX, "scenarios", "RDW.json"))
    cases = {c["name"]: c for c in rdw.scenarios["cases"]}
    check(cases["Bear"]["value"] <= cases["Base"]["value"], "RDW young model: Bear <= Base", str(cases))
    check(rdw.scenarios.get("growth_destroys_value") is True, "the young flag still reports it")


def test_E103_bear_label_means_at_or_below_fv():
    """Item 3 and E103 agree: on a cyclical every value called Bear (the Bear case and a
    cross-check) sits at or below the FV; a check above it is the neutral comparison."""
    for t in ("MU", "TSLA"):
        v = _value(os.path.join(FIX, "cyclical", t + ".json"))
        bear = next(c for c in v.scenarios["cases"] if c["name"] == "Bear")
        check(bear["value"] <= v.anchor_value, f"{t} Bear case <= FV")
        for c in v.scenarios.get("cycle_checks") or []:
            check(c["label"].startswith("Bear") == (c["value"] <= v.anchor_value),
                  f"{t} {c['name']}: Bear label iff at or below FV", c["label"][:40])


if __name__ == "__main__":
    run_suite(globals(), "ALL test_scenario_order PASSED")
