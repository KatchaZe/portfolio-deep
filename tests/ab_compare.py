"""Before/after harness: run the engine over a spread of company shapes and dump
the numbers that matter. Usage:  python3 ab_compare.py before.json
Then apply the patches and:      python3 ab_compare.py after.json
                                 python3 ab_compare.py --diff before.json after.json
"""
import json
import os
import sys

APP = os.environ.get("APP_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)

from domain.facts import FinancialFacts          # noqa: E402
from domain.engine.deep_v82 import DeepV82Engine  # noqa: E402

RF = 0.045

# name -> facts kwargs. Shapes chosen to exercise each finding.
CASES = {
    "HIGHQ_LOWBETA": dict(   # low beta + high ROIC: the ke floor bites hardest
        beta=0.30, price=900.0, revenue=15e9, operating_income=6.5e9, net_income=5.2e9,
        shares_diluted=0.107e9, total_debt=2.7e9, cash=8e9, equity=25e9, capex=0.9e9,
        dep_amort=0.6e9, interest_expense=0.09e9, income_before_tax=6.3e9, tax_expense=1.0e9,
        forward_eps=52.0, growth_lt=0.11, cfo=6.0e9, total_assets=40e9,
        revenue_annuals=[15e9, 13.6e9, 12.1e9, 10.9e9, 9.6e9, 8.4e9],
        operating_income_annuals=[6.5e9, 5.9e9], equity_prior=22e9, cash_prior=7e9,
        total_debt_prior=2.6e9, market_cap=96.3e9, terminal_roic_sector=0.2160,
        sbc=1.1e9),
    "LEASE_HEAVY_RETAIL": dict(   # leases > debt: EV + prior-IC asymmetry
        beta=0.95, price=180.0, revenue=40e9, operating_income=3.2e9, net_income=2.3e9,
        shares_diluted=1.0e9, total_debt=8e9, cash=3e9, equity=12e9, operating_leases=14e9,
        capex=1.6e9, dep_amort=1.4e9, interest_expense=0.45e9, income_before_tax=2.9e9,
        tax_expense=0.6e9, forward_eps=2.55, growth_lt=0.09, cfo=3.4e9, total_assets=45e9,
        revenue_annuals=[40e9, 36e9, 33e9, 30e9, 27e9, 25e9],
        operating_income_annuals=[3.2e9, 3.0e9], equity_prior=11e9, cash_prior=2.7e9,
        total_debt_prior=7.6e9, market_cap=180e9, terminal_roic_sector=0.1518,
        sbc=0.35e9),
    "VALUE_DESTROYER": dict(   # NEGATIVE ROIC but positive forward EPS -> gifted 15%/30%
        beta=1.30, price=22.0, revenue=9e9, operating_income=-0.4e9, net_income=0.15e9,
        shares_diluted=0.9e9, total_debt=6e9, cash=1e9, equity=7e9, capex=0.7e9,
        dep_amort=0.5e9, interest_expense=0.30e9, income_before_tax=0.2e9, tax_expense=0.05e9,
        forward_eps=0.95, growth_lt=0.34, cfo=0.4e9, total_assets=22e9,
        revenue_annuals=[9e9, 7.4e9, 5.6e9, 4.1e9, 3.0e9, 2.4e9],
        operating_income_annuals=[-0.4e9, -0.55e9], equity_prior=6.4e9, cash_prior=1.1e9,
        total_debt_prior=5.4e9, market_cap=19.8e9, terminal_roic_sector=0.0440,
        sbc=1.2e9),
    "SBC_HEAVY_GROWTH": dict(   # SBC 14% of revenue: dilution never charged today
        beta=1.75, price=64.0, revenue=7.5e9, operating_income=0.62e9, net_income=0.40e9,
        shares_diluted=0.62e9, total_debt=0.4e9, cash=2.6e9, equity=4.2e9, capex=0.12e9,
        dep_amort=0.09e9, interest_expense=0.02e9, income_before_tax=0.55e9, tax_expense=0.12e9,
        forward_eps=0.98, growth_lt=0.27, cfo=1.1e9, total_assets=8.8e9,
        revenue_annuals=[7.5e9, 5.6e9, 4.0e9, 2.8e9, 1.9e9, 1.2e9],
        operating_income_annuals=[0.62e9, 0.31e9], equity_prior=3.4e9, cash_prior=2.2e9,
        total_debt_prior=0.4e9, market_cap=39.7e9, terminal_roic_sector=0.2295,
        sbc=1.05e9),
    "NET_CASH_NO_ROIC": dict(   # cash > equity+debt -> IC <= 0 -> ROIC None -> gifted 15%/30%
        beta=1.40, price=48.0, revenue=3.2e9, operating_income=0.30e9, net_income=0.34e9,
        shares_diluted=0.30e9, total_debt=0.2e9, cash=3.9e9, equity=3.4e9, capex=0.05e9,
        dep_amort=0.04e9, interest_expense=0.01e9, income_before_tax=0.40e9, tax_expense=0.07e9,
        forward_eps=1.30, growth_lt=0.31, cfo=0.45e9, total_assets=5.6e9,
        revenue_annuals=[3.2e9, 2.4e9, 1.7e9, 1.2e9, 0.85e9, 0.6e9],
        operating_income_annuals=[0.30e9, 0.12e9], equity_prior=3.0e9, cash_prior=3.5e9,
        total_debt_prior=0.2e9, market_cap=14.4e9, terminal_roic_sector=0.2295,
        sbc=0.5e9),
    "NO_FWD_EPS": dict(   # forward EPS rejected upstream -> P pillar disappears
        beta=1.05, price=140.0, revenue=28e9, operating_income=5.4e9, net_income=4.1e9,
        shares_diluted=1.2e9, total_debt=9e9, cash=6e9, equity=18e9, capex=1.1e9,
        dep_amort=0.9e9, interest_expense=0.35e9, income_before_tax=5.0e9, tax_expense=0.9e9,
        forward_eps=None, growth_lt=0.13, cfo=5.6e9, total_assets=44e9,
        revenue_annuals=[28e9, 25e9, 22e9, 19.5e9, 17e9, 15e9],
        operating_income_annuals=[5.4e9, 4.8e9], equity_prior=16e9, cash_prior=5.4e9,
        total_debt_prior=8.6e9, market_cap=168e9, terminal_roic_sector=0.2352,
        sbc=0.6e9),
    "BREAKEVEN_YEAR": dict(   # net income exactly 0 -> falsy-guard path
        beta=1.20, price=30.0, revenue=6e9, operating_income=0.05e9, net_income=0.0,
        shares_diluted=0.5e9, total_debt=2e9, cash=1e9, equity=4e9, capex=0.4e9,
        dep_amort=0.35e9, interest_expense=0.12e9, income_before_tax=0.02e9, tax_expense=0.004e9,
        forward_eps=0.40, growth_lt=0.06, cfo=0.5e9, total_assets=11e9, eps_gaap=0.0,
        revenue_annuals=[6e9, 5.7e9, 5.4e9, 5.1e9, 4.9e9, 4.6e9],
        operating_income_annuals=[0.05e9, 0.20e9], equity_prior=4.1e9, cash_prior=1.2e9,
        total_debt_prior=2.0e9, market_cap=15e9, terminal_roic_sector=0.1706,
        sbc=0.2e9),
}

KEYS = ("anchor_method", "anchor_value", "fv_peg", "fv_fvp", "composite",
        "recommendation", "D", "E_exec", "E_econ", "P")
KM = ("wacc_pct", "ke_pct", "roic_pct", "terminal_roic_pct", "terminal_ke_pct",
      "growth_pct", "justified_pe", "terminal_margin_pct")


def snapshot():
    eng = DeepV82Engine()
    out = {}
    for name, kw in CASES.items():
        f = FinancialFacts(name, **kw)
        v = eng.evaluate(f, rf=RF)
        rec = {k: getattr(v, k) for k in KEYS}
        rec["km"] = {k: (v.key_metrics or {}).get(k) for k in KM}
        rd = v.reverse_dcf or {}
        rec["rdcf"] = {"implied_cagr_pct": rd.get("implied_cagr_pct"),
                       "verdict": rd.get("verdict"),
                       "ev_bn": round(rd["enterprise_value"] / 1e9, 1) if rd.get("enterprise_value") else None}
        rec["flags"] = sorted(v.flags or [])
        out[name] = rec
    return out


def show_diff(a, b):
    for name in a:
        ra, rb = a[name], b.get(name, {})
        rows = []
        for k in KEYS:
            if ra.get(k) != rb.get(k):
                rows.append((k, ra.get(k), rb.get(k)))
        for k in KM:
            if ra["km"].get(k) != rb.get("km", {}).get(k):
                rows.append(("km." + k, ra["km"].get(k), rb.get("km", {}).get(k)))
        for k in ("implied_cagr_pct", "verdict", "ev_bn"):
            if ra["rdcf"].get(k) != rb.get("rdcf", {}).get(k):
                rows.append(("rdcf." + k, ra["rdcf"].get(k), rb.get("rdcf", {}).get(k)))
        new_flags = [x for x in rb.get("flags", []) if x not in ra.get("flags", [])]
        gone = [x for x in ra.get("flags", []) if x not in rb.get("flags", [])]
        print("\n" + "=" * 78)
        print(name)
        print("=" * 78)
        if not rows and not new_flags and not gone:
            print("  (no change)")
        for k, x, y in rows:
            print(f"  {k:28s} {str(x):>16s}  ->  {str(y):>16s}")
        for fl in new_flags:
            print(f"  + flag  {fl}")
        for fl in gone:
            print(f"  - flag  {fl}")


if __name__ == "__main__":
    if sys.argv[1] == "--diff":
        show_diff(json.load(open(sys.argv[2])), json.load(open(sys.argv[3])))
    else:
        json.dump(snapshot(), open(sys.argv[1], "w"), indent=1, sort_keys=True)
        print("wrote", sys.argv[1])
