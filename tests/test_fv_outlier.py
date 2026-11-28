"""test_fv_outlier: a primary FV more than FV_OUTLIER_GAP from the median of the other
methods (PEG, FV/P, relative, cycle cross-checks) is flagged and badged; no number moves (E104)."""
import copy
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from domain import fv_outlier as O                         # noqa: E402
from domain.engine.deep_v82 import DeepV82Engine           # noqa: E402
from domain.facts import FinancialFacts                    # noqa: E402
from tests._harness import check, run_suite                # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "cyclical")
FIELDS = set(FinancialFacts.__dataclass_fields__)


def _value(t, **over):
    d = dict(json.load(open(os.path.join(FIX, t + ".json"))), **over)
    ff = FinancialFacts(**{k: v for k, v in d.items() if k in FIELDS})
    ff.flags = []
    return DeepV82Engine().evaluate(ff, rf=0.045)


def _numbers(v):
    return (v.anchor_value, v.P, v.composite, v.recommendation, v.range_low, v.range_high,
            json.dumps(v.scenarios, sort_keys=True, default=str))


def test_E104_constant_and_text():
    check(O.FV_OUTLIER_GAP == 0.40, "the gap is the named 40%")
    check(O.FV_OUTLIER_FLAG == "ค่าหลักต่างจากวิธีเทียบอื่นมาก ตรวจสมมติฐาน", "the Thai text")


def test_E104_flags_beyond_40pct_both_ways():
    class V:
        pass
    v = V()
    v.anchor_value, v.fv_peg, v.fv_fvp, v.relative, v.scenarios, v.flags, v.key_metrics = 100.0, 50.0, 60.0, {}, {}, [], {}
    O.mark(v)
    check(v.key_metrics["fv_outlier"]["flag"] and any(O.FV_OUTLIER_FLAG in f for f in v.flags),
          "FV 100 vs median 55 (+82%): flagged", str(v.key_metrics))
    v.fv_peg, v.fv_fvp = 140.0, 150.0
    O.mark(v)
    check(not v.key_metrics["fv_outlier"]["flag"] and not any(O.FV_OUTLIER_FLAG in f for f in v.flags),
          "FV 100 vs median 145 (-31%): no flag, and the old flag is gone", str(v.flags))
    v.fv_peg, v.fv_fvp = 180.0, 160.0
    O.mark(v)
    check(v.key_metrics["fv_outlier"]["flag"], "FV 100 vs median 170 (-41%): flagged")
    O.mark(v)
    check(sum(O.FV_OUTLIER_FLAG in f for f in v.flags) == 1, "marking twice keeps one flag")
    v.relative = {"value_regression": 100.0}
    v.fv_peg, v.fv_fvp = 180.0, None
    O.mark(v)
    check(v.key_metrics["fv_outlier"]["methods"] == ["PEG", "relative"], "relative counts",
          str(v.key_metrics["fv_outlier"]))


def test_E104_engine_flags_without_moving_a_number():
    tmdx = _value("TMDX")
    out = tmdx.key_metrics["fv_outlier"]
    check(out["flag"] and out["gap_pct"] < -40, "TMDX FV $19.78 vs median $37.97 of PEG, FV/P", str(out))
    check(any(f.startswith(O.FV_OUTLIER_FLAG) for f in tmdx.flags), "the flag is on the row")
    tsla = _value("TSLA")
    check(tsla.key_metrics["fv_outlier"]["flag"] is False, "TSLA -15.9% vs its median: no flag")
    before = _numbers(tmdx)
    v2 = copy.deepcopy(tmdx)
    v2.relative = {"value_regression": 1000.0}
    O.mark(v2)
    check(_numbers(v2) == before, "re-marking with a relative value changes no number")
    O.FV_OUTLIER_GAP, saved = 10.0, O.FV_OUTLIER_GAP
    try:
        check(_numbers(_value("TMDX")) == before, "turning the flag off changes no number either")
    finally:
        O.FV_OUTLIER_GAP = saved


def test_E104_badge_in_the_ui():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    js = open(os.path.join(root, "static", "js", "card_value.js"), encoding="utf-8").read()
    check("fvOutlierBadge(x)" in js and O.FV_OUTLIER_FLAG in js, "card_value.js badges the Fair Value cell")


if __name__ == "__main__":
    run_suite(globals(), "ALL test_fv_outlier PASSED")
