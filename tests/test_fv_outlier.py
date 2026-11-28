"""test_fv_outlier: a primary FV more than FV_OUTLIER_RATIO (log terms) from the median of the
methods that can speak to it, with two of them beyond on one side, is flagged and badged; no
number moves (E104, E108)."""
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


class _V:
    def __init__(self, fv, peg=None, fvp=None, rel=None, method="FCFF DCF", checks=()):
        self.anchor_value, self.fv_peg, self.fv_fvp, self.anchor_method = fv, peg, fvp, method
        self.relative, self.flags, self.key_metrics = rel or {}, [], {}
        self.scenarios = {"cycle_checks": [{"name": n, "value": x} for n, x in checks]}


def test_E108_constants_and_text():
    check(O.FV_OUTLIER_RATIO == 1.5 and O.FV_OUTLIER_MAX_LOO == 0.40 and O.FV_OUTLIER_MIN_METHODS == 2,
          "ratio 1.5x, loo 0.40, two methods")
    check(not hasattr(O, "FV_OUTLIER_GAP"), "the asymmetric +/-40% gap is gone")
    check(O.FV_OUTLIER_FLAG == "ค่าหลักต่างจากวิธีเทียบอื่นมาก ตรวจสมมติฐาน", "the Thai text")


def test_E108_symmetric_in_log_terms_and_two_methods_beyond():
    v = O.mark(_V(100.0, 60.0, 64.0))
    check(v.key_metrics["fv_outlier"]["flag"] and any(O.FV_OUTLIER_FLAG in f for f in v.flags),
          "FV 100 vs 60 / 64 (both under 1/1.5): flagged", str(v.key_metrics))
    v = O.mark(_V(100.0, 160.0, 170.0))
    check(v.key_metrics["fv_outlier"]["flag"], "FV 100 vs 160 / 170 (both over 1.5x): flagged the other way")
    v = O.mark(_V(100.0, 145.0, 150.0))
    check(not v.key_metrics["fv_outlier"]["flag"], "FV 100 vs 145 / 150: inside 1.5x both ways, no flag "
          "(the old -40% rule needed 1.67x below, +40% only 1.40x above)")
    v = O.mark(_V(100.0, 145.0, 150.0))
    v.fv_peg, v.fv_fvp = 140.0, 155.0
    O.mark(v)
    check(sum(O.FV_OUTLIER_FLAG in f for f in v.flags) == 0, "re-marking clears the old flag")
    v = O.mark(_V(100.0, 40.0, 95.0, checks=[("trend_revenue", 30.0)]))
    out = v.key_metrics["fv_outlier"]
    check(out["flag"] and set(out["beyond"]) == {"PEG", "trend_revenue"}, "two of three beyond on one side",
          str(out))
    v = O.mark(_V(100.0, 30.0, 95.0, checks=[("trend_revenue", 98.0)]))
    check(not v.key_metrics["fv_outlier"]["flag"], "one stray method is not a consensus",
          str(v.key_metrics["fv_outlier"]))
    O.mark(v)
    check(sum(O.FV_OUTLIER_FLAG in f for f in v.flags) <= 1, "marking twice keeps at most one flag")


def test_E108_weak_relative_and_young_peg_are_not_methods():
    weak = {"value_regression": 170.0, "model": {"weak": True, "loo_median_err": 0.2}}
    v = O.mark(_V(100.0, 160.0, None, rel=weak))
    check(v.key_metrics["fv_outlier"] is None, "AVGO-like weak regression dropped: one method left, no verdict",
          str(v.key_metrics["fv_outlier"]))
    loose = {"value_regression": 170.0, "model": {"weak": False, "loo_median_err": 0.41}}
    check(O.comparators(_V(100.0, 160.0, rel=loose)) == [("PEG", 160.0)], "loo error 0.41 drops relative")
    good = {"value_regression": 170.0, "value_median": 90.0, "model": {"weak": False, "loo_median_err": 0.3}}
    check(O.comparators(_V(100.0, 160.0, rel=good)) == [("PEG", 160.0), ("relative", 170.0)],
          "a good fit's regression counts")
    young = _V(36.9, 4.29, 5.0, rel={"value_median": 5.82, "model": {"weak": False, "loo_median_err": 0.41}},
               method="Young-Company DCF (failure-adjusted)")
    check(O.comparators(young) == [] and O.mark(young).key_metrics["fv_outlier"] is None,
          "HIMS-like young primary: no PEG / FV/P, so nothing to flag")


def test_E104_engine_flags_without_moving_a_number():
    tmdx = _value("TMDX")
    out = tmdx.key_metrics["fv_outlier"]
    check(out["gap_pct"] < -40 and out["beyond"] == ["PEG"] and not out["flag"],
          "TMDX FV $19.78 vs median $37.97: only PEG beyond 1.5x, so no flag (E108)", str(out))
    t2 = copy.deepcopy(tmdx)
    t2.relative = {"value_regression": 40.0, "model": {"weak": False, "loo_median_err": 0.2}}
    O.mark(t2)
    check(t2.key_metrics["fv_outlier"]["flag"] and any(f.startswith(O.FV_OUTLIER_FLAG) for f in t2.flags),
          "with a sound relative $40 too, two methods agree: flagged on the row", str(t2.key_metrics["fv_outlier"]))
    tsla = _value("TSLA")
    check(tsla.key_metrics["fv_outlier"]["flag"] is False, "TSLA -15.9% vs its median: no flag")
    before = _numbers(tmdx)
    v2 = copy.deepcopy(tmdx)
    v2.relative = {"value_regression": 1000.0}
    O.mark(v2)
    check(_numbers(v2) == before, "re-marking with a relative value changes no number")
    O.FV_OUTLIER_RATIO, saved = 1e9, O.FV_OUTLIER_RATIO
    try:
        check(_numbers(_value("TMDX")) == before, "turning the flag off changes no number either")
    finally:
        O.FV_OUTLIER_RATIO = saved


def test_E104_badge_in_the_ui():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    js = open(os.path.join(root, "static", "js", "card_value.js"), encoding="utf-8").read()
    check("fvOutlierBadge(x)" in js and O.FV_OUTLIER_FLAG in js, "card_value.js badges the Fair Value cell")


if __name__ == "__main__":
    run_suite(globals(), "ALL test_fv_outlier PASSED")
