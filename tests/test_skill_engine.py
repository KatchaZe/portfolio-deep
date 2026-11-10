"""
test_skill_engine — the ifa-stock-analysis skill values a stock exactly as the app does.

The skill used to carry its own copy of every formula, kept in step by hand, and fell
behind on every release (v8.3 on claude.ai and v8.4 on disk while the app ran P1/P2).
Now skill/ifa-stock-analysis/scripts/value.py runs the app's own modules, copied in by
tools/build_skill.py. Two guards:
  1. the copy is current (a changed engine file without a rebuild fails here, in CI)
  2. value.py, run as the skill runs it (its own process, its own copy), returns the
     same valuation and facts as the app's pipeline.finish + engine, on each model:
     FCFF DCF, young-company DCF, excess return.
"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tests._harness import check, run_suite                     # noqa: E402
from tools import build_skill                                  # noqa: E402

SKILL = build_skill.SKILL
VALUE_PY = os.path.join(SKILL, "scripts", "value.py")
EXAMPLES = {"standard-firm": "FCFF DCF", "young-firm": "Young-Company DCF (failure-adjusted)",
            "bank": "Excess Return Model"}

# value.py in its own interpreter (-I: no cwd, no user site), network cut like every suite
LAUNCH = ("import sys; sys.path.append(%r); from tests._harness import cut_network; "
          "cut_network(); sys.path.pop(); import runpy; sys.argv = sys.argv[1:]; "
          "runpy.run_path(sys.argv[0], run_name='__main__')" % ROOT)


def _canon(obj):
    return json.loads(json.dumps(obj, sort_keys=True, default=str))


def _skill_run(example):
    path = os.path.join(SKILL, "examples", example + ".json")
    out = subprocess.run([sys.executable, "-I", "-c", LAUNCH, VALUE_PY, path],
                         capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, out.stderr[-3000:]
    return json.loads(out.stdout)


def _app_run(example):
    """The app's own path on the same input, in this process."""
    import config
    from domain.engine import get_engine
    from domain.facts import FinancialFacts
    from pipeline import finish
    with open(os.path.join(SKILL, "examples", example + ".json"), encoding="utf-8") as fh:
        spec = json.load(fh)
    keep = (config.ERP, config.ERP_AS_OF, config.ERP_SOURCE)
    try:
        config.ERP, config.ERP_AS_OF, config.ERP_SOURCE = spec["erp"], spec["erp_as_of"], "manual"
        ff = FinancialFacts(spec["ticker"])
        for k, v in spec["facts"].items():
            ff.set(k, v, (spec.get("sources") or {}).get(k, "chat (no source given)"))
        finish.finish(ff, ff.ticker, spec["rf"], finish.fetch_roc_table())
        return get_engine().evaluate(ff, rf=spec["rf"]).to_dict(), ff.to_dict()
    finally:
        config.ERP, config.ERP_AS_OF, config.ERP_SOURCE = keep


def test_skill_copy_is_current():
    print("1 the skill's engine copy matches the app")
    problems = build_skill.check()
    check(not problems, "scripts/app is the app's current code",
          "; ".join(problems) + " -> python -m tools.build_skill")


def test_value_py_matches_the_app():
    print("2 value.py (skill) = pipeline.finish + engine (app), per model")
    for example, model in EXAMPLES.items():
        skill = _skill_run(example)
        val, facts = _app_run(example)
        check(skill["valuation"].get("anchor_method") == model,
              f"{example}: routed to {model}", str(skill["valuation"].get("anchor_method")))
        check(_canon(skill["valuation"]) == _canon(val), f"{example}: same valuation",
              f"skill FV {skill['valuation'].get('anchor_value')} vs app {val.get('anchor_value')}")
        check(_canon(skill["facts"]) == _canon(facts), f"{example}: same facts after finish()")


def test_a_missing_run_input_is_named_not_guessed():
    print("3 no ERP -> ⛔ MISSING, no valuation")
    sys.path.insert(0, os.path.join(SKILL, "scripts"))
    try:
        import value
        out = value.run({"ticker": "X", "rf": 0.04, "facts": {}})
    finally:
        sys.path.pop(0)
    check(out.get("missing") == ["⛔ MISSING: erp", "⛔ MISSING: erp_as_of"] and "valuation" not in out,
          "erp and erp_as_of reported missing", str(out))


def test_skill_md_names_no_stale_erp():
    """The ERP is a run input now; a number written into SKILL.md would go stale."""
    print("4 SKILL.md asks for the live ERP instead of quoting one")
    text = open(os.path.join(SKILL, "SKILL.md"), encoding="utf-8").read()
    check("%.2f%%" % (100 * 0.0420) not in text and "4.45%" not in text,
          "no hard-coded ERP figure in SKILL.md")
    check("scripts/value.py" in text, "SKILL.md routes the arithmetic to value.py")


if __name__ == "__main__":
    run_suite(globals(), "ALL SKILL ENGINE TESTS PASSED")
