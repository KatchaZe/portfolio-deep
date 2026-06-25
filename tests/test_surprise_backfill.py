"""Tests for surprise_backfill — immediate EPS/Rev estimate-vs-actual from
SEC actuals x FMP quarterly estimates. Pure, offline."""
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import surprise_backfill as sb
from sources import sec_edgar, fmp

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def test_grade_thresholds():
    assert sb.grade(5.0) == "beat"
    assert sb.grade(-5.0) == "miss"
    assert sb.grade(1.0) == "meet"
    assert sb.grade(0.0) == "meet"
    assert sb.grade(None) is None


def test_build_eps_exact_dates():
    actuals = {"2025-03-31": 2.20, "2025-06-30": 2.50}
    est = {"2025-03-31": {"eps_est": 2.00, "rev_est": None},
           "2025-06-30": {"eps_est": 2.55, "rev_est": None}}
    out = sb.build_eps(actuals, est)
    assert [r["grade"] for r in out] == ["beat", "meet"]
    assert out[0]["eps_actual"] == 2.20 and out[0]["eps_estimate"] == 2.00
    # oldest -> newest
    assert out[0]["quarter"] < out[1]["quarter"]


def test_build_rev_miss():
    actuals = {"2025-03-31": 9.0e9}
    est = {"2025-03-31": {"eps_est": None, "rev_est": 10.0e9}}
    out = sb.build_rev(actuals, est)
    assert len(out) == 1 and out[0]["grade"] == "miss"
    assert out[0]["surprise_pct"] == -10.0


def test_date_tolerance_pairs_nearby():
    # SEC fiscal end 02-01, FMP estimate dated 01-31 -> should pair (1 day)
    actuals = {"2026-02-01": 1.10}
    est = {"2026-01-31": {"eps_est": 1.00, "rev_est": None}}
    out = sb.build_eps(actuals, est)
    assert len(out) == 1 and out[0]["grade"] == "beat"


def test_date_tolerance_rejects_far():
    # 100 days apart -> no pairing -> empty
    actuals = {"2026-02-01": 1.10}
    est = {"2025-10-01": {"eps_est": 1.00, "rev_est": None}}
    assert sb.build_eps(actuals, est) == []


def test_limit_keeps_last_four_newest():
    actuals = {f"2024-{m:02d}-28": 1.0 + i for i, m in enumerate([3, 6, 9, 12])}
    actuals["2025-03-28"] = 9.0
    est = {k: {"eps_est": 1.0, "rev_est": None} for k in actuals}
    out = sb.build_eps(actuals, est)
    assert len(out) == 4
    assert out[-1]["quarter"] == "2025-03-28"


def test_handles_empty_inputs():
    assert sb.build_eps({}, {}) == []
    assert sb.build_eps(None, None) == []
    assert sb.build_rev({"2025-03-31": 1e9}, {}) == []


def test_skips_zero_estimate():
    out = sb.build_eps({"2025-03-31": 1.0}, {"2025-03-31": {"eps_est": 0, "rev_est": None}})
    assert out == []


def test_sec_eps_quarters_extracted_from_fixture():
    # AVGO is a US GAAP filer with quarterly diluted EPS -> eps_quarters populated.
    cf = json.load(open(os.path.join(FIX, "AVGO", "sec_companyfacts.json")))
    d = sec_edgar.extract(cf)
    eq = d.get("eps_quarters") or {}
    assert len(eq) >= 4
    # values are per-share (small magnitude), keyed by ISO end date
    k = sorted(eq)[-1]
    assert len(k) == 10 and isinstance(eq[k], (int, float))


def test_fmp_parse_estimates_quarterly_shapes():
    rows = [
        {"date": "2025-03-31", "epsAvg": 2.0, "revenueAvg": 10e9},
        {"date": "2025-06-30", "estimatedEpsAvg": 2.1, "estimatedRevenueAvg": 11e9},
        {"period": "2025-09-30", "epsAvg": 2.2},
        {"date": "2025-12-31"},          # no est -> skipped
        "garbage",                        # non-dict -> skipped
    ]
    out = fmp.parse_estimates_quarterly(rows)
    assert set(out.keys()) == {"2025-03-31", "2025-06-30", "2025-09-30"}
    assert out["2025-03-31"]["eps_est"] == 2.0
    assert out["2025-06-30"]["rev_est"] == 11e9


def test_end_to_end_backfill_from_fixture():
    # SEC actuals (AVGO) x synthetic FMP quarterly estimates -> graded EPS history.
    cf = json.load(open(os.path.join(FIX, "AVGO", "sec_companyfacts.json")))
    d = sec_edgar.extract(cf)
    eps_q = d["eps_quarters"]
    # estimate = 95% of actual for every quarter -> all "beat"
    est = {k: {"eps_est": round(v * 0.95, 4), "rev_est": None} for k, v in eps_q.items()}
    out = sb.build_eps(eps_q, est)
    assert len(out) == 4
    assert all(r["grade"] == "beat" for r in out)
