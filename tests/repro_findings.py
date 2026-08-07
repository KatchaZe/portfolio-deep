"""Numeric repro for the 2026-08-04 code review. Read-only: imports the engine
and re-runs the SAME formulas with the correct input to show the size of each gap.
Run:  python3 repro_findings.py
"""
import os
import sys

APP = os.environ.get("APP_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)

import config                                    # noqa: E402
from domain.engine import deep_v82 as E          # noqa: E402
from domain.facts import FinancialFacts          # noqa: E402

RF = 0.045
line = lambda: print("-" * 78)


def hdr(n, t):
    print("\n" + "=" * 78)
    print(f"[{n}] {t}")
    print("=" * 78)


# ---------------------------------------------------------------- F1 leases in EV
hdr("F1", "reverse DCF enterprise value ignores capitalized operating leases")
common = dict(price=180.0, shares=1.0e9, revenue=40e9, rev_1y=36e9,
              cash=3e9, wacc_val=0.085, g=RF, tax=0.21, margin=0.12,
              wacc_term=0.088, roic_term=0.13)
bare = E.reverse_dcf(total_debt=8e9, **common)
lease = E.reverse_dcf(total_debt=8e9 + 14e9, **common)     # 14B of lease liability
print(f"  debt as passed today (f.total_debt, no leases) : EV ${bare['enterprise_value']/1e9:,.1f}B"
      f"  implied CAGR {bare['implied_cagr_pct']}%  -> {bare['verdict']}")
print(f"  debt the WACC/ROIC actually used (debt_eff)    : EV ${lease['enterprise_value']/1e9:,.1f}B"
      f"  implied CAGR {lease['implied_cagr_pct']}%  -> {lease['verdict']}")
print(f"  GAP: {lease['implied_cagr_pct'] - bare['implied_cagr_pct']:+.1f}pp of implied growth hidden")


# ------------------------------------------------- F2 reinvestment cap = free growth
hdr("F2", "reverse DCF reinvestment cap min(0.9, x/ROIC) — growth is free above it")


def implied_with_cap(cap, roic_t=0.10, price=45.0):
    """Same pv_at as the engine, with the reinvestment cap made explicit."""
    shares, rev, tax, m, w, g = 1e9, 10e9, 0.21, 0.15, 0.09, RF
    ev = price * shares
    H = E.REVERSE_HORIZON

    def pv(x):
        rr = min(cap, max(0.0, x / roic_t)) if x > 0 else 0.0
        rt = min(0.8, g / roic_t)
        p, r = 0.0, rev
        for t in range(1, H + 1):
            r *= (1 + x)
            p += r * m * (1 - tax) * (1 - rr) / (1 + w) ** t
        return p + (r * (1 + g) * m * (1 - tax) * (1 - rt) / (w - g)) / (1 + w) ** H
    lo, hi = -0.5, 1.0
    if pv(lo) > ev or pv(hi) < ev:
        return None
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if pv(mid) < ev else (lo, mid)
    return (lo + hi) / 2


print("  ROIC_terminal 10% -> any growth above 9% is partly UNFUNDED under the 0.9 cap.")
for cap, lbl in ((0.90, "0.90  (today)"), (1.00, "1.00  (all earnings ploughed back)"),
                 (9.99, "none  (unfundable growth costs outside capital)")):
    x = implied_with_cap(cap)
    print(f"  reinvestment cap {lbl:38s}: implied CAGR "
          + (f"{x*100:.1f}%" if x is not None else "unreachable in band"))
print("  the cap makes FCFF INSENSITIVE to growth above 9%: every x from 9% to 100% is")
print("  charged the same 90% reinvestment, so the solver undercounts what is priced in.")
print("  -> verdicts read too lenient; same max(0, 1-g/ROIC) shape that P-H fixed elsewhere.")


# ------------------------------------------------- F3 negative-ROIC firm gifted 15%
hdr("F3", "roic_high / growth cap gift a value-DESTROYING firm 15% ROIC and 30% growth")
for roic_now, label in ((0.22, "healthy 22% ROIC"), (-0.04, "NEGATIVE -4% ROIC"), (0.05, "weak 5% ROIC")):
    roic_high = roic_now if (roic_now and roic_now > 0) else E.ROIC_TERMINAL
    gcap = E.sustainable_growth_cap(roic_now if roic_now > 0 else None)
    g = min(0.30, gcap)
    pe = E.two_stage_pe(g, 5, 0.042, 0.09, roic_high, 0.11, ke_st=0.09)
    print(f"  {label:22s} -> roic_high used {roic_high*100:5.1f}%  g cap {gcap*100:4.1f}%"
          f"  g used {g*100:4.1f}%  justified PE {pe:5.1f}")
print("  the -4% firm gets a BETTER payout ratio than the 5% firm: 1 - g/0.15 vs 1 - g/0.05")


# ------------------------------------------------- F4 lease-asymmetric spread trend
hdr("F4", "spread_prior uses lease-FREE invested capital while spread uses lease-capitalized IC")
oi, oi_prior, tax = 5.0e9, 4.6e9, 0.21
debt, eq, cash, lease = 10e9, 20e9, 4e9, 12e9
ic_now = debt + lease + eq - cash
ic_prior_today = debt + eq - cash                      # what the code does
ic_prior_fixed = debt + lease + eq - cash              # apples to apples
w = 0.085
sp_now = oi * (1 - tax) / ic_now - w
sp_prior_today = oi_prior * (1 - tax) / ic_prior_today - w
sp_prior_fixed = oi_prior * (1 - tax) / ic_prior_fixed - w
print(f"  spread now                       {sp_now*100:+6.2f}pp   (IC ${ic_now/1e9:.0f}B, leases in)")
print(f"  spread prior AS CODED            {sp_prior_today*100:+6.2f}pp   (IC ${ic_prior_today/1e9:.0f}B, leases OUT)")
print(f"  spread prior LIKE-FOR-LIKE       {sp_prior_fixed*100:+6.2f}pp")
d_today, d_fixed = sp_now - sp_prior_today, sp_now - sp_prior_fixed


def econ_adj(delta, spread):
    if delta > 0.02:
        return +0.5
    if delta < -0.02 and spread < 0.05:
        return -1.0
    if delta < -0.02:
        return -0.5
    return 0.0


print(f"  delta AS CODED {d_today*100:+.2f}pp -> E_econ adj {econ_adj(d_today, sp_now):+.1f}")
print(f"  delta FIXED    {d_fixed*100:+.2f}pp -> E_econ adj {econ_adj(d_fixed, sp_now):+.1f}"
      f"   (E_econ is the 30%-weighted pillar)")


# ------------------------------------------------- F5 margin of safety denominator
hdr("F5", "_r_price scores upside-on-PRICE; Damodaran's MoS is (Value - Price)/VALUE")
print("   band          (V-P)/P  ->  same cut expressed as (V-P)/V")
for thr, sc in ((0.40, 5.0), (0.15, 4.0), (-0.15, 3.0), (-0.40, 2.0)):
    print(f"   score {sc:>3}      {thr*100:+6.0f}%   ->  {thr/(1+thr)*100:+6.1f}%")
V, P = 100.0, 71.5
print(f"\n  worked example V=${V:.0f} P=${P:.1f}: upside {(V-P)/P*100:+.0f}% -> top band (5.0)")
print(f"                                   true MoS {(V-P)/V*100:+.0f}% -> should be the 15% band (4.0)")
print("  the distortion is ASYMMETRIC: the top band is easier to reach, the bottom harder")


# ------------------------------------------------- F6 SBC never reaches the engine
hdr("F6", "SBC: fetched by nothing, diluted into nothing")
import sources.sec_edgar as sec                    # noqa: E402
import pipeline.normalize as nz                    # noqa: E402
keys = sec.extract({"facts": {"us-gaap": {}}}).keys()
print(f"  'sbc' in the SEC extract dict            : {'sbc' in keys}")
print(f"  'sbc' in normalize._MONEY (FX-converted) : {'sbc' in nz._MONEY}")
_cmd = "grep -rn 'fmp[.]parse(' " + APP + "/pipeline " + APP + "/domain 2>/dev/null | wc -l"
_n = os.popen(_cmd).read().strip()
print(f"  fmp.parse() (the only setter) called in pipeline/domain: {_n} times")
print("  -> f.sbc is ALWAYS None in production, so:")
print("     * earnings_quality's 'SBC >10% of revenue' flag can never fire (dead branch)")
print("     * the SKILL contract 'SBC dilution must be applied to forward EPS' is unimplemented")
f = FinancialFacts("X", net_income=10e9, cfo=12e9, total_assets=100e9, revenue=50e9, sbc=None)
print("     earnings_quality(sbc=None) ->", E.earnings_quality(f.net_income, f.cfo, f.total_assets, f.sbc, f.revenue))
print("     earnings_quality(sbc=8e9)  ->", E.earnings_quality(f.net_income, f.cfo, f.total_assets, 8e9, f.revenue))


# ------------------------------------------------- F7 ke floor is one-way
hdr("F7", "ke_eff = max(ke, rf+3.5%) is a FLOOR with no ceiling — it only penalizes low beta")
for beta in (0.24, 0.51, 1.00, 2.21):
    ke = E.cost_of_equity(RF, beta)
    ke_eff = max(ke, RF + 0.035)
    print(f"  beta {beta:4.2f} -> Ke {ke*100:5.2f}%  ->  ke_eff {ke_eff*100:5.2f}%"
          f"   {'FLOOR BINDS (+%.2fpp)' % ((ke_eff-ke)*100) if ke_eff > ke else 'untouched'}")
print("  the high-growth phase is supposed to keep TODAY's risk (P-B2 docstring);")
print("  this floor quietly fades low-beta risk upward there too, double-counting P-K.")


# ------------------------------------------------- F8 parity test is vacuous
hdr("F8", "test_skill_parity compares the LEGACY defaults, not the production call")
args = dict(g_h=0.18, n=5, g_st=0.043, ke=0.0895, roic_h=0.20, roic_st=0.15)
pe_default = E.two_stage_pe(**args)                    # what the test asserts
pe_prod = E.two_stage_pe(**args, ke_st=0.081)          # what evaluate() actually calls
print(f"  two_stage_pe WITHOUT ke_st (test path) : {pe_default:.3f}   <- skill matches this")
print(f"  two_stage_pe WITH   ke_st (prod path)  : {pe_prod:.3f}   <- skill has no such argument")
print(f"  divergence {abs(pe_prod-pe_default)/pe_default*100:.1f}% of fair value, invisible to the guard")
rd_default = E.reverse_dcf(150.0, 3e9, 8e9, 6e9, 5e9, 20e9, 0.10, 0.043, 0.21, 0.20)
rd_prod = E.reverse_dcf(150.0, 3e9, 8e9, 6e9, 5e9, 20e9, 0.10, 0.043, 0.21, 0.20,
                        wacc_term=0.092, roic_term=0.23)
print(f"  reverse_dcf legacy defaults (test path): {rd_default['implied_cagr_pct']}%")
print(f"  reverse_dcf with terminal args (prod)  : {rd_prod['implied_cagr_pct']}%")


# ------------------------------------------------- F9 composite drops price discipline
hdr("F9", "composite() re-weights over AVAILABLE pillars — no fair value = no price test")
full = {"D": 4.0, "E_exec": 4.0, "E_econ": 4.0, "P": 1.0}
noprice = dict(full, P=None)
print(f"  D4 E4 E4 P1 (expensive)      -> composite {E.composite(full):.2f}"
      f"  = {E.recommendation(E.composite(full))}")
print(f"  same names, fair value MISSING-> composite {E.composite(noprice):.2f}"
      f"  = {E.recommendation(E.composite(noprice))}")
print("  a data outage on forward EPS therefore UPGRADES the recommendation.")


# ------------------------------------------------- F10 zero net income reads as missing
hdr("F10", "falsy-vs-None guards still present after REVIEW-1")
for ni in (0.0, None, 5e9):
    got = "net_income branch" if (ni and 1e9) else "eps_gaap fallback"
    print(f"  net_income = {str(ni):>6} -> {got}")
print("  `if f.net_income and f.shares_diluted` treats a genuine break-even year as missing data")
print("  (same shape as the `or 0.08` bug fixed in REVIEW-1).")

print("\n" + "=" * 78)
print("done")
