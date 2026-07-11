"""
test_skill_parity (P2-8) — drift guard between the app engine and the
ifa-stock-analysis skill scripts (same formulas maintained in two places).
Skips gracefully when the skill folder isn't present (e.g. CI checks out only
portfolio-app-v2). Offline — imports the scripts by file path, no network.
"""
import importlib.util
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from domain.engine import deep_v82 as E

# skill lives NEXT TO the app repo:  <Stock Screening>/ifa-stock-analysis-v8/scripts
_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL_SCRIPTS = os.path.join(os.path.dirname(_APP_ROOT), "ifa-stock-analysis-v8", "scripts")


def _load(name):
    p = os.path.join(SKILL_SCRIPTS, name + ".py")
    if not os.path.exists(p):
        return None
    spec = importlib.util.spec_from_file_location("skillmod_" + name, p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_erp_default_in_sync():
    w = _load("wacc")
    if w is None:
        print("SKIP: skill scripts not found at", SKILL_SCRIPTS)
        return
    assert abs(w.ERP_DEFAULT - config.ERP) < 1e-9, (
        "ERP DRIFT: skill wacc.py ERP_DEFAULT=%s but app config.ERP=%s — "
        "config.py is the SOURCE OF TRUTH; update the skill (or vice versa)"
        % (w.ERP_DEFAULT, config.ERP))
    print("ERP default in sync OK (%.4f)" % config.ERP)


def test_two_stage_pe_parity():
    jp = _load("justified_peg")
    if jp is None:
        print("SKIP: skill scripts not found")
        return
    args = dict(g_h=0.18, n=5, g_st=0.043, ke=0.0895, roic_h=0.20, roic_st=0.15)
    pe_skill, _, _ = jp.two_stage_pe(**args)
    pe_app = E.two_stage_pe(**args)
    assert abs(pe_skill - pe_app) < 1e-9, (pe_skill, pe_app)
    print("two-stage PE parity OK (%.4f)" % pe_app)


def test_reverse_dcf_fullpath_parity():
    rd = _load("reverse_dcf_terminal")
    if rd is None:
        print("SKIP: skill scripts not found")
        return
    price, shares, debt, cash = 150.0, 3.0e9, 5e9, 20e9
    wacc, g, tax, margin, cur = 0.10, 0.043, 0.21, 0.20, 8.0e9
    ev = price * shares + debt - cash
    x_skill = rd.implied_cagr_fullpath(ev, wacc, g, tax, margin,
                                       E.ROIC_TERMINAL, E.REVERSE_HORIZON, cur)
    out_app = E.reverse_dcf(price, shares, cur, 6.0e9, debt, cash, wacc, g, tax, margin)
    assert out_app["triggered"] and x_skill is not None
    assert abs(x_skill * 100 - out_app["implied_cagr_pct"]) < 0.2, (
        "reverse-DCF DRIFT: skill %.2f%% vs app %.2f%%"
        % (x_skill * 100, out_app["implied_cagr_pct"]))
    print("full-path reverse DCF parity OK (%.1f%%)" % out_app["implied_cagr_pct"])


if __name__ == "__main__":
    test_erp_default_in_sync()
    test_two_stage_pe_parity()
    test_reverse_dcf_fullpath_parity()
    print("\nALL test_skill_parity PASSED")
