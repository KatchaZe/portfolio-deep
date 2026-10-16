"""Fundamental PEG must equal a dividend-discount value of the same story.

two_stage_pe() is Damodaran's P0/E0 and the anchor multiplies NEXT year's EPS, so the
multiple applied is P0/E1 = (P0/E0)/(1+g). Before 2026-10-17 it was not divided, and
every PEG-anchored fair value carried one extra year of growth (x1.20 at g = 20%)."""
import importlib.util
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from domain.engine import deep_v82 as E
from tests._harness import check, run_suite


def _ddm(eps0, g, n, g_st, ke, roic_h, roic_st):
    """Explicit two-stage dividend discount: payout = 1 - g/ROIC each stage."""
    pv, eps = 0.0, eps0
    for t in range(1, n + 1):
        eps *= 1 + g
        pv += eps * (1 - g / roic_h) / (1 + ke) ** t
    tv = eps * (1 + g_st) * (1 - g_st / roic_st) / (ke - g_st)
    return pv + tv / (1 + ke) ** n


def test_fair_value_matches_the_dividend_discount_value():
    eps0, g, n, g_st, ke, rh, rs = 1.0, 0.20, 5, 0.04, 0.10, 0.30, 0.15
    fv, d = E.fundamental_peg_price(g, g_st, ke, rh, rs, forward_eps=eps0 * (1 + g), years=n)
    want = _ddm(eps0, g, n, g_st, ke, rh, rs)
    check(abs(fv - want) / want < 1e-9, "PEG fair value = DDM P0", f"{fv:.4f} vs {want:.4f}")
    check(abs(d["trailing_pe"] - want / eps0) < 0.01, "trailing P/E is reported alongside")


def test_skill_script_gives_the_same_price():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    p = os.path.join(root, "ifa-stock-analysis-v8", "scripts", "justified_peg.py")
    if not os.path.exists(p):
        print("SKIP: skill scripts not found")
        return
    spec = importlib.util.spec_from_file_location("jp_fwd", p)
    jp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(jp)
    r = jp.fundamental_peg(0.15, 5, 0.04, 0.09, 0.25, 0.14, 4.0)
    fv, _ = E.fundamental_peg_price(0.15, 0.04, 0.09, 0.25, 0.14, 4.0)
    check(abs(r["fair_price"] - fv) < 0.01, "skill and app fair price agree", f"{r['fair_price']} vs {fv}")


if __name__ == "__main__":
    run_suite(globals(), "ALL test_peg_forward PASSED")
