"""
Engine test — runs the active DEEP engine on the captured fixtures via the
contract and asserts the Valuation is well-formed. Locks the engine's behavior
and proves the contract works regardless of which version is active.

    python -m tests.test_engine
"""
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from pipeline import normalize, validate
from domain.engine import get_engine, available_versions

FIX = config.FIXTURE_DIR
FX = {"NVO": 0.145}


def load(t, n):
    p = os.path.join(FIX, t, n + ".json")
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None


def run():
    eng = get_engine()
    print(f"engine version {eng.version} | available {available_versions()}\n")
    failures = []
    for t in config.PROBE_TICKERS:
        sec = load(t, "sec_companyfacts")
        if not sec:
            continue
        ff = normalize.build(t, sec, load(t, "fmp_profile"), load(t, "yahoo_quotesummary"), fx_rate=FX.get(t))
        validate.validate(ff)
        v = eng.evaluate(ff, rf=0.045)
        print(f"{t:5} {v.recommendation:18} {v.stars}  anchor {v.anchor_method} ${v.anchor_value}")

        # contract well-formedness
        if v.version != "7.3":
            failures.append(f"{t}: wrong version {v.version}")
        for s in (v.D, v.E_exec, v.E_econ, v.P):
            if s is not None and not (0 <= s <= 5):
                failures.append(f"{t}: score {s} out of 0-5")
        if v.composite is None or not (0 <= v.composite <= 5):
            failures.append(f"{t}: composite {v.composite} invalid")
        if not v.recommendation or not v.verdict:
            failures.append(f"{t}: missing recommendation/verdict")
        if v.signal not in ("BUY", "HOLD", "SELL", None):
            failures.append(f"{t}: bad signal {v.signal}")
        # profitable names must yield a fair value; pre-profit uses reverse DCF
        if v.anchor_method != "Terminal-Anchored Reverse DCF" and v.anchor_value is None:
            failures.append(f"{t}: no anchor value despite {v.anchor_method}")

    print()
    if failures:
        print("FAILURES:")
        for f in failures:
            print("  -", f)
        raise SystemExit(1)
    print("ALL ENGINE CONTRACT CHECKS PASSED ✅")


if __name__ == "__main__":
    run()
