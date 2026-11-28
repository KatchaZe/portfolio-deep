"""test_cyclical_minors4: the young Monte Carlo brackets the model margin (E109), the young
scenario text names the real primary, the cyclical growth flag names what it replaces, and a
DCF row without a value range cannot be rated (APPS on the verifier's approximate facts,
tests/fixtures/cyclical/APPS_approx.json: the scout part's SEC fields on MU's template)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from domain.engine import deep_v82 as E                   # noqa: E402
from domain.engine import scenarios as S                  # noqa: E402
from tests._harness import check, run_suite                # noqa: E402
from tests.test_cyclical_round4 import _facts, _raw, _value, RANK   # noqa: E402


def test_E109_young_mc_margin_is_two_sided():
    for t in ("HIMS", "RKLB"):
        v = _value(t)
        lo, mode, hi = v.scenarios["story_mc"]["story"]["margin_pct"]
        span = v.young_dcf["inputs"]["target_margin_sampled_pp"]
        check(lo <= mode - span + 1e-6 and hi >= mode + span - 1e-6,
              f"{t}: MC margin [{lo}, {mode}, {hi}] brackets the model by its {span}pp span")
        check("p50 ของ Monte Carlo" not in v.scenarios["basis"].split("ไม่ใช่")[0],
              f"{t}: the basis no longer says the FV is the MC p50", v.scenarios["basis"])
        cases = {c["name"]: c["value"] for c in v.scenarios["cases"]}
        check(abs(cases["Base"] / v.anchor_value - 1) < 0.001, f"{t}: Base is the primary FV (rounding)", str(cases))


def test_m4_tsla_growth_flag_names_consensus():
    v = _value("TSLA")
    fl = [x for x in v.flags if x.startswith("cyclical growth")]
    check(fl and "consensus" in fl[0] and "historical" not in fl[0] and "FCFF DCF keeps" in fl[0],
          "TSLA: replaces the consensus 13.3% for PEG / FV-P; the DCF keeps consensus", str(fl))
    m = _value("MU")
    fl = [x for x in m.flags if x.startswith("cyclical growth")]
    check(fl and "historical" in fl[0], "MU (no consensus) still says historical", str(fl))


def test_m1_apps_no_rating_without_a_range():
    oi = _raw("APPS_approx")["operating_income"]
    rows = []
    for x in (0.3, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0):
        v = _value("APPS_approx", operating_income=oi * x)
        rows.append((x, v.anchor_method, v.uncertainty.get("rating"), v.recommendation))
        if v.recommendation != "NOT RATED":
            check(v.uncertainty.get("rating") not in (None, "Unrated") and v.scenarios.get("cases"),
                  f"APPS x{x}: a rated row has a measured range", str(rows[-1]))
    check(all(r[1] in ("FCFF DCF", "Young-Company DCF (failure-adjusted)") for r in rows),
          "APPS routes FCFF or young only", str(rows))
    v = _value("APPS_approx")
    unc = {"rating": "Unrated", "width": None}
    anc = type("A", (), {"method": "FCFF DCF", "ydcf": {}, "dcf": {"terminal_share_pct": 60}})()
    check(any("no value range" in w for w in E.not_rated_reasons(unc, anc, _facts("APPS_approx"))),
          "an FCFF anchor without scenarios is NOT RATED, not scored on the Unrated bands")
    check(v.company_type == "standard", "APPS on approximate facts: standard (its margin has not come "
          "back to normal in a fiscal year since the drop, E107)", v.company_type)


if __name__ == "__main__":
    run_suite(globals(), "ALL test_cyclical_minors4 PASSED")
