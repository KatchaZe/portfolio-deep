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
    """REV-15: the old version of this test called BOTH sides without ke_st, so it
    compared the app's LEGACY back-compat default against the skill — and passed
    while production diverged 21.8% on the same inputs. A drift guard has to
    exercise the signature production actually calls."""
    jp = _load("justified_peg")
    if jp is None:
        print("SKIP: skill scripts not found at", SKILL_SCRIPTS)
        return
    args = dict(g_h=0.18, n=5, g_st=0.043, ke=0.0895, roic_h=0.20, roic_st=0.15)
    pe_skill, _, _ = jp.two_stage_pe(**args)
    pe_app = E.two_stage_pe(**args)
    assert abs(pe_skill - pe_app) < 1e-9, (pe_skill, pe_app)

    # the PRODUCTION path: a separate stable-phase Ke (P-B2)
    prod = dict(args, ke_st=0.081)
    pe_skill2, _, _ = jp.two_stage_pe(**prod)
    pe_app2 = E.two_stage_pe(**prod)
    assert abs(pe_skill2 - pe_app2) < 1e-9, (
        "two-stage PE DRIFT on the ke_st path: skill %.4f vs app %.4f" % (pe_skill2, pe_app2))
    assert abs(pe_app2 - pe_app) > 1.0, "ke_st is being ignored — the guard would be vacuous"
    print("two-stage PE parity OK (legacy %.4f / production ke_st %.4f)" % (pe_app, pe_app2))


def test_terminal_helpers_parity():
    """terminal_roic / terminal_beta / sustainable_growth_cap exist in BOTH places."""
    jp = _load("justified_peg")
    if jp is None:
        print("SKIP: skill scripts not found")
        return
    for b in (0.24, 0.9, 1.0, 2.21, None):
        assert jp.terminal_beta(b) == E.terminal_beta(b), ("terminal_beta drift", b)
    for roic, wacc, cap in ((0.62, 0.09, 0.2053), (0.05, 0.085, 0.1538),
                            (None, 0.08, 0.7769), (0.22, 0.075, None)):
        a, b = jp.terminal_roic(roic, wacc, cap), E.terminal_roic(roic, wacc, cap)
        assert abs(a - b) < 1e-12, ("terminal_roic drift", roic, wacc, cap, a, b)
    for roic, fb in ((0.137, None), (None, 0.11), (-0.04, 0.09), (0.45, None)):
        a = jp.sustainable_growth_cap(roic, cap=E.GROWTH_CAP, roic_fallback=fb)
        b = E.sustainable_growth_cap(roic, roic_fallback=fb)
        assert abs(a - b) < 1e-12, ("growth cap drift", roic, fb, a, b)
    print("terminal-phase helper parity OK (beta / ROIC / growth cap)")


def test_reverse_dcf_fullpath_parity():
    rd = _load("reverse_dcf_terminal")
    if rd is None:
        print("SKIP: skill scripts not found")
        return
    # REV-28: the old fixture priced this firm at 54x sales, which only solved because
    # reinvestment was capped and growth was therefore partly free. With growth
    # properly paid for that price is genuinely unjustifiable, so the fixture now uses
    # a realistic multiple — and the unreachable case is asserted separately below.
    price, shares, debt, cash = 60.0, 3.0e9, 5e9, 20e9
    wacc, g, tax, margin, cur = 0.10, 0.043, 0.21, 0.20, 40.0e9
    ev = price * shares + debt - cash
    x_skill = rd.implied_cagr_fullpath(ev, wacc, g, tax, margin,
                                       E.ROIC_TERMINAL, E.REVERSE_HORIZON, cur)
    out_app = E.reverse_dcf(price, shares, cur, 34.0e9, debt, cash, wacc, g, tax, margin)
    assert out_app["triggered"] and x_skill is not None
    assert abs(x_skill * 100 - out_app["implied_cagr_pct"]) < 0.2, (
        "reverse-DCF DRIFT: skill %.2f%% vs app %.2f%%"
        % (x_skill * 100, out_app["implied_cagr_pct"]))

    # PRODUCTION path: stable-phase WACC on the perpetuity + the firm's own terminal ROIC
    wt, rt = 0.092, 0.23
    x_skill2 = rd.implied_cagr_fullpath(ev, wacc, g, tax, margin, rt, E.REVERSE_HORIZON,
                                        cur, wacc_term=wt)
    out_app2 = E.reverse_dcf(price, shares, cur, 34.0e9, debt, cash, wacc, g, tax, margin,
                             wacc_term=wt, roic_term=rt)
    assert abs(x_skill2 * 100 - out_app2["implied_cagr_pct"]) < 0.2, (
        "reverse-DCF DRIFT on the terminal path: skill %.2f%% vs app %.2f%%"
        % (x_skill2 * 100, out_app2["implied_cagr_pct"]))
    assert abs(out_app2["implied_cagr_pct"] - out_app["implied_cagr_pct"]) > 0.5, (
        "terminal args are being ignored — the guard would be vacuous")
    # REV-28: the two must agree across the whole price range INCLUDING where the
    # price stops being justifiable — an app that solves a case the skill rejects
    # (or vice versa) is drift, whichever way it falls.
    for p in (20.0, 60.0, 150.0, 500.0):
        ev_p = p * shares + debt - cash
        xs = rd.implied_cagr_fullpath(ev_p, wacc, g, tax, margin, E.ROIC_TERMINAL,
                                      E.REVERSE_HORIZON, cur)
        xa = E.reverse_dcf(p, shares, cur, 34.0e9, debt, cash, wacc, g, tax,
                           margin)["implied_cagr_pct"]
        assert (xs is None) == (xa is None), (
            "reverse-DCF DRIFT at price %s: skill %s vs app %s" % (p, xs, xa))
        if xs is not None:
            assert abs(xs * 100 - xa) < 0.2, (p, xs * 100, xa)
    print("full-path reverse DCF parity OK (legacy %.1f%% / production %.1f%% / agree across prices)"
          % (out_app["implied_cagr_pct"], out_app2["implied_cagr_pct"]))


def test_young_company_dcf_parity():
    """REV-16: the app now runs the S20 young-company model, so its going-concern
    build has to stay identical to the skill script's."""
    yc = _load("young_company_dcf")
    if yc is None:
        print("SKIP: skill scripts not found")
        return
    from domain.engine import young_dcf as Y
    d = {"current_revenue": 2.1e9, "g_high": 0.35, "g_stable": 0.045, "horizon": 10,
         "current_margin": -0.152, "target_margin": 0.25, "sales_to_capital": 2.4,
         "tax": 0.21, "wacc": 0.1319, "roic_stable": 0.15, "net_debt": -1.0e9,
         "shares": 0.24e9, "annual_dilution": 0.0446}
    a = yc.going_concern(dict(d))["going_concern_per_share"]
    b = Y.going_concern(dict(d))["going_concern_per_share"]
    assert abs(a - b) < 1e-6, ("young-company DCF DRIFT: skill %.4f vs app %.4f" % (a, b))

    # the failure-risk adjustment must be identical too — and must stay OUT of the
    # discount rate (S20). Same wacc in, different value out only via p_survival.
    for p, dist in ((0.68, 4.17), (1.0, 0.0), (0.3, 2.0)):
        assert abs(yc.failure_adjusted(a, p, dist) - Y.failure_adjusted(b, p, dist)) < 1e-9

    # terminal reinvestment must be capped on BOTH sides (roic_stable < g_stable)
    bad = dict(d, roic_stable=0.02)
    assert yc.going_concern(bad)["going_concern_per_share"] > 0
    assert abs(yc.going_concern(bad)["going_concern_per_share"]
               - Y.going_concern(bad)["going_concern_per_share"]) < 1e-6
    print("young-company DCF parity OK (going concern %.2f/share, failure adj, terminal cap)" % b)


if __name__ == "__main__":
    test_erp_default_in_sync()
    test_two_stage_pe_parity()
    test_terminal_helpers_parity()
    test_reverse_dcf_fullpath_parity()
    test_young_company_dcf_parity()
    print("\nALL test_skill_parity PASSED")
