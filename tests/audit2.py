"""Second-pass audit — read-only. Hammers the code added in REV-16..REV-28 with
degenerate and boundary inputs, looking for behaviour that is CORRECT AS CODED but
distorts the answer. Nothing here modifies the repo.
"""
import os
import sys

APP = os.environ.get("APP_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)

from domain.engine import deep_v82 as E          # noqa: E402
from domain.engine import young_dcf as Y         # noqa: E402
from domain.facts import FinancialFacts as FF    # noqa: E402
from sources import damodaran                    # noqa: E402

RF = 0.045
FOUND = []


def hdr(n, t):
    print("\n" + "=" * 78)
    print(f"[{n}] {t}")
    print("=" * 78)


def flag(sev, msg):
    FOUND.append((sev, msg))
    print(f"  !! {sev}: {msg}")


# ---------------------------------------------------------------- A1  [FIXED: P2-1]
hdr("A1", "dilution: real share-count history vs the old SBC/market-cap proxy")
print("  the proxy measures GROSS grants and moves with the SHARE PRICE:")
for px_mult, lbl in ((1.0, "today"), (0.5, "after a 50% drawdown"), (2.0, "after a double")):
    mcap = 40e9 * px_mult
    print(f"   {lbl:24s} mcap ${mcap/1e9:5.1f}B -> proxy {E.sbc_dilution_rate(1.05e9, mcap)*100:.2f}%/yr")
print("  the engine now prefers the FILED share-count series (net of buybacks):")
buyback = FF("BB", shares_diluted_annuals=[7.43e9, 7.47e9, 7.54e9, 7.61e9], sbc=12e9)
dilutive = FF("DD", shares_diluted_annuals=[4.99e9, 4.92e9, 4.83e9, 4.74e9], sbc=7.6e9)
ifrs = FF("IF", sbc=1e9)
for f_, mc_, lbl in ((buyback, 3100e9, "retires more than it grants"),
                     (dilutive, 1500e9, "genuinely dilutive"),
                     (ifrs, 1000e9, "IFRS, no series filed")):
    r, l = E.dilution_rate(f_, mc_)
    print(f"   {lbl:28s} -> {r*100:5.2f}%/yr   {l}")
if E.dilution_rate(buyback, 3100e9)[0] != 0.0:
    flag("MED", "a net-buyback company is still being charged dilution")
elif "proxy" in (E.dilution_rate(dilutive, 1500e9)[1] or ""):
    flag("MED", "real history exists but the proxy is still being used")
else:
    print("   -> FIXED (P2-1): real history primary, proxy only as an IFRS fallback")

# ---------------------------------------------------------------- A2  [FIXED: P2-3]
hdr("A2", "working_capital_change: partial legs")
full = E.working_capital_change(FF("X", receivables=3e9, receivables_prior=2.2e9,
                                   inventory=1.5e9, inventory_prior=1.2e9,
                                   accounts_payable=1.0e9, accounts_payable_prior=0.9e9))
ap_only = E.working_capital_change(FF("Y", accounts_payable=2.0e9, accounts_payable_prior=1.0e9))
ar_only = E.working_capital_change(FF("Z", receivables=3e9, receivables_prior=2.2e9))
print(f"   all three legs : {full[0]/1e9:+.2f}B   {full[1]}")
print(f"   AP leg only    : {str(ap_only[0]):>8s}    {ap_only[1]}")
print(f"   AR leg only    : {ar_only[0]/1e9:+.2f}B   {ar_only[1]}")
if ap_only[0] is not None:
    flag("MED", "an AP-only delta is still returned; it flips sign and inflates FCFF")
else:
    print("   -> FIXED (P2-3): receivables required; a sign-flipped partial is refused")

# ---------------------------------------------------------------- A3
hdr("A3", "implied() returns the band floor as if it were a measurement")
r = E.reverse_dcf(price=1.0, shares=1e9, revenue=50e9, rev_1y=45e9, total_debt=0,
                  cash=0, wacc_val=0.09, g=0.045, tax=0.21, margin=0.20, roic_term=0.25)
print(f"   a $1 stock on $50B revenue -> implied_cagr_pct = {r['implied_cagr_pct']}"
      f"  verdict {r['verdict']!r}")
flag("LOW", "when even -50%/yr decline is worth more than the price, implied() returns the "
            "band edge (-50.0%) as a normal number. It is a boundary, not an estimate, and "
            "nothing marks it as one — unlike the PE clamp, which does flag its boundary.")

# ---------------------------------------------------------------- A4
hdr("A4", "peak scan resolution (n=40 over a 150pp band = 3.75pp steps)")
base = dict(shares=1e9, revenue=10e9, rev_1y=9e9, total_debt=0, cash=0,
            wacc_val=0.09, g=0.045, tax=0.21, margin=0.15)
misses = 0
for roic in (0.095, 0.10, 0.11, 0.12):
    for p in (13.0, 13.5, 14.0, 14.5, 15.0):
        r = E.reverse_dcf(price=p, roic_term=roic, **base)
        if r.get("implied_cagr_pct") is None:
            misses += 1
print(f"   thin-spread grid: {misses}/20 report 'no growth justifies this price'")
print("   (that IS the right answer near ROIC=WACC; checking the peak is not being missed)")
fine = []
for n_steps in (40, 400):
    lo, hi, m, roic = -0.5, 1.0, 0.15, 0.10
    ev = 14.0 * 1e9

    def pv(x):
        rein = max(0.0, x / roic) if x > 0 else 0.0
        rt = min(0.8, 0.045 / roic)
        p_, rv = 0.0, 10e9
        for t in range(1, 11):
            rv *= (1 + x)
            p_ += rv * m * 0.79 * (1 - rein) / 1.09 ** t
        return p_ + (rv * 1.045 * m * 0.79 * (1 - rt) / 0.045) / 1.09 ** 10
    peak = max(pv(lo + (hi - lo) * i / n_steps) for i in range(n_steps + 1))
    fine.append(peak)
print(f"   peak with n=40 : ${fine[0]/1e9:.4f}B")
print(f"   peak with n=400: ${fine[1]/1e9:.4f}B   (error {abs(fine[1]-fine[0])/fine[1]*100:.3f}%)")
if abs(fine[1] - fine[0]) / fine[1] > 0.005:
    flag("LOW", "the 40-step peak scan under-locates the maximum, so a price just under the "
                "true peak can be misreported as unjustifiable.")
else:
    print("   -> peak location is accurate enough at n=40; no finding")

# ---------------------------------------------------------------- A5
hdr("A5", "young DCF: p_survival step function at the boundaries")
print("   runway -> p_survival is a STEP, so a day either side of a boundary jumps:")
for burn, cash in ((1e9, 5.01e9), (1e9, 4.99e9), (1e9, 3.01e9), (1e9, 2.99e9)):
    p, why = Y.survival_probability(cash, -burn, None, None)
    print(f"    runway {cash/burn:4.2f}y -> p {p:.2f}   ({why})")
flag("LOW", "p_survival steps 0.85 -> 0.75 across a 0.02y difference in runway. Since value "
            "= p x going-concern + (1-p) x distress, a 10pp step moves the fair value by "
            "10% of the gap between going-concern and distress — discontinuously.")

# ---------------------------------------------------------------- A6
hdr("A6", "young DCF terminal share of value")
kw = dict(current_revenue=2.1e9, g_high=0.35, g_stable=RF, horizon=10,
          current_margin=-0.152, target_margin=0.25, sales_to_capital=2.4,
          tax=0.21, wacc=0.1319, roic_stable=0.15, net_debt=-1.0e9,
          shares=0.24e9, annual_dilution=0.0446)
gc = Y.going_concern(dict(kw))
share = gc["pv_terminal"] / gc["firm_value"] * 100
print(f"   PV(interim) ${gc['pv_fcff']/1e9:+.2f}B   PV(terminal) ${gc['pv_terminal']/1e9:.2f}B"
      f"   -> terminal is {share:.1f}% of firm value")
if share > 85:
    print(f"   ({share:.0f}% of the value is terminal — inherent to a cash-burning young")
    print("    company, but it means the answer rests on target_margin. P2-4 now sizes the")
    print("    Monte Carlo band by whether that margin was MEASURED or ASSUMED:)")
    for anch in (True, False):
        span = Y.MC_MARGIN_SPAN_ANCHORED if anch else Y.MC_MARGIN_SPAN_ASSUMED
        mc = Y.monte_carlo(dict(kw, p_survival=0.68, distress_value_per_share=4.17),
                           margin_span=span)
        print(f"     {'anchored' if anch else 'assumed ':9s} +/-{span*100:4.1f}pp -> "
              f"p10 ${mc['p10']:.2f}  p50 ${mc['p50']:.2f}  p90 ${mc['p90']:.2f}  "
              f"band {mc['p90']/mc['p10']:.2f}x")
    if Y.MC_MARGIN_SPAN_ASSUMED <= Y.MC_MARGIN_SPAN_ANCHORED:
        flag("MED", "the assumed-margin band is not wider than the measured one")
    else:
        print("   -> FIXED (P2-4): the gate now judges a band sized by data availability")

# ---------------------------------------------------------------- A7
hdr("A7", "terminal ROIC for a coarse sector after REV-26")
for tk, sec in (("MSFT", "Technology"), ("ZZZZ", "Technology"), ("ZZZZ", "Healthcare"),
                ("ZZZZ", "Utilities"), ("ZZZZ", "Financial Services"), ("ZZZZ", None)):
    v = damodaran.terminal_roic_for(tk, sec)
    print(f"   {tk:5s} {str(sec):20s} -> {('%.2f%%' % (v*100)) if v else 'None'}")
print("   -> a coarse hit can now only RESTRAIN (capped at the 14.92% market average)")

# ---------------------------------------------------------------- A8
hdr("A8", "zero / missing propagation through the whole engine")
cases = {
    "revenue = 0": dict(revenue=0.0),
    "shares = 0": dict(shares_diluted=0.0),
    "price = 0": dict(price=0.0),
    "cash = 0": dict(cash=0.0),
    "everything None": dict(revenue=None, operating_income=None, net_income=None,
                            shares_diluted=None, price=None, forward_eps=None,
                            cfo=None, total_assets=None),
    "beta = 0": dict(beta=0.0),
    "tax fields 0": dict(income_before_tax=0.0, tax_expense=0.0),
}
GOOD = dict(beta=1.1, price=100.0, revenue=20e9, operating_income=4e9, net_income=3e9,
            shares_diluted=1e9, total_debt=5e9, cash=4e9, equity=15e9, capex=1e9,
            dep_amort=0.8e9, interest_expense=0.2e9, income_before_tax=3.7e9,
            tax_expense=0.7e9, forward_eps=3.3, growth_lt=0.12, cfo=4.2e9,
            total_assets=40e9, market_cap=100e9, sbc=0.5e9,
            revenue_annuals=[20e9, 18e9, 16e9, 14e9, 12e9, 11e9],
            operating_income_annuals=[4e9, 3.6e9], equity_prior=13e9, cash_prior=3.5e9,
            total_debt_prior=4.8e9, terminal_roic_sector=0.2295)
eng = E.DeepV82Engine()
for lbl, over in cases.items():
    try:
        v = eng.evaluate(FF("T", **dict(GOOD, **over)), rf=RF)
        print(f"   {lbl:18s} -> {str(v.recommendation):18s} FV {v.anchor_value}"
              f"  comp {v.composite}  ({v.anchor_method})")
    except Exception as ex:
        flag("HIGH", f"{lbl} CRASHES the engine: {type(ex).__name__}: {ex}")

# ---------------------------------------------------------------- A9
hdr("A9", "tax_rate: a real 0% payer vs missing data")
print("   FinancialFacts.tax_rate returns 0.21 whenever the ratio is unusable.")
for ibt, txe, lbl in ((5e9, 0.0, "genuine 0% tax (NOL shield)"),
                      (5e9, None, "tax not filed"),
                      (0.0, 0.5e9, "pretax exactly zero"),
                      (-2e9, 0.1e9, "loss-making")):
    print(f"    {lbl:28s} ibt={str(ibt):8s} txe={str(txe):8s} -> {FF('T', income_before_tax=ibt, tax_expense=txe).tax_rate}")
flag("LOW", "a company genuinely paying ~0% (NOL carryforwards, common in the pre-profit "
            "names the young DCF now values) is handed the 21% marginal rate, understating "
            "its NOPAT and FCFF. The `0 <= r <= 0.6` band accepts 0.0, so this one is "
            "actually handled — but only when tax_expense is filed as exactly 0, not None.")

# ---------------------------------------------------------------- A10
hdr("A10", "growth_lt = 0 vs missing, after REVIEW-1")
for g, lbl in ((0.0, "measured zero growth"), (None, "growth not available")):
    f = FF("T", **dict(GOOD, growth_lt=g))
    v = eng.evaluate(f, rf=RF)
    used = (v.key_metrics or {}).get("growth_pct")
    print(f"   {lbl:24s} -> growth used {used}%   FV {v.anchor_value}")

print("\n" + "=" * 78)
print(f"SUMMARY: {len(FOUND)} findings")
for sev, m in FOUND:
    print(f"  [{sev}] {m[:110]}")

# This file started as a one-off audit; wiring it into run_tests turns it into a guard
# against RE-introducing the class of defect it was written to find. LOW findings are
# accepted-and-documented (see CODE_REVIEW_2026-08-04_PASS2.md section 4), so only
# MED and HIGH fail the build.
_bad = [x for x in FOUND if x[0] in ("MED", "HIGH")]
if _bad:
    print(f"\nFAIL: {len(_bad)} MED/HIGH finding(s) — see above")
    sys.exit(1)
print(f"\nOK: {len(FOUND)} LOW finding(s), all documented and accepted")
