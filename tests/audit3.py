"""Second-pass audit, part 2 — read-only. Follows up the leads from audit2."""
import json
import os
import sys

APP = os.environ.get("APP_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)

from domain.engine import deep_v82 as E          # noqa: E402
from domain.engine import young_dcf as Y         # noqa: E402
from domain.facts import FinancialFacts as FF    # noqa: E402
from pipeline import validate                    # noqa: E402
from sources import sec_edgar                    # noqa: E402

RF = 0.045


def hdr(n, t):
    print("\n" + "=" * 78)
    print(f"[{n}] {t}")
    print("=" * 78)


# ---------------------------------------------------------------- B1
hdr("B1", "revenue = 0 (a pre-revenue filer that TAGS zero) walks the profitable path")
f = FF("BIOTECH", beta=1.4, price=100.0, revenue=0.0, operating_income=-0.5e9,
       net_income=-0.5e9, shares_diluted=0.2e9, total_debt=0, cash=1.5e9, equity=1.4e9,
       capex=0.02e9, dep_amort=0.01e9, forward_eps=3.3, growth_lt=0.12, cfo=-0.45e9,
       total_assets=1.6e9, market_cap=20e9, revenue_annuals=[0.0, 0.0])
why = validate.forward_eps_rejection(f.forward_eps, f.revenue, f.shares_diluted, f.price, f.growth_lt)
print(f"   forward_eps_rejection(revenue=0) -> {why!r}")
print("   the revenue-capacity ceiling is guarded by `if revenue and shares` — a filed")
print("   ZERO is falsy, so the ceiling silently disappears and only the P/E gate remains.")
v = E.DeepV82Engine().evaluate(f, rf=RF)
print(f"   engine -> {v.anchor_method} FV {v.anchor_value} reco {v.recommendation}")
print(f"   !! a company with NO revenue and a $0.5B loss gets a ${v.anchor_value} fair value")
print("      from consensus EPS alone, with the capacity sanity-check switched off.")

# ---------------------------------------------------------------- B2
hdr("B2", "is a REAL share-count series available? (would replace the SBC/mcap proxy)")
fx = os.path.join(APP, "tests", "fixtures")
files = [x for x in os.listdir(fx) if x.startswith("sec_")] if os.path.isdir(fx) else []
print("   fixtures:", files[:6])
for fn in files[:5]:
    try:
        cf = json.load(open(os.path.join(fx, fn), encoding="utf-8"))
    except Exception as e:
        print("   ", fn, "unreadable", e)
        continue
    ns = (cf.get("facts") or cf).get("us-gaap") or (cf.get("facts") or cf).get("ifrs-full") or {}
    tag = "WeightedAverageNumberOfDilutedSharesOutstanding"
    node = ns.get(tag) or {}
    units = (node.get("units") or {}).get("shares") or []
    annuals = sorted({e["end"][:4]: e["val"] for e in units
                      if e.get("start") and 350 <= (
                          __import__("datetime").date.fromisoformat(e["end"])
                          - __import__("datetime").date.fromisoformat(e["start"])).days <= 380}.items(),
                     reverse=True)
    if len(annuals) >= 2:
        g = annuals[0][1] / annuals[1][1] - 1
        print(f"   {fn:28s} {annuals[0][0]} {annuals[0][1]/1e9:.3f}B vs "
              f"{annuals[1][0]} {annuals[1][1]/1e9:.3f}B -> actual dilution {g*100:+.2f}%/yr")
    else:
        print(f"   {fn:28s} <2 annual share-count points")
print("   -> if this series exists, actual share-count growth beats the SBC/mcap proxy,")
print("      which moves with the share PRICE rather than with the grant.")

# ---------------------------------------------------------------- B3
hdr("B3", "how much does the young DCF hang on target_margin?")
base = dict(current_revenue=2.1e9, g_high=0.35, g_stable=RF, horizon=10,
            current_margin=-0.152, sales_to_capital=2.4, tax=0.21, wacc=0.1319,
            roic_stable=0.15, net_debt=-1.0e9, shares=0.24e9, annual_dilution=0.0446)
print("   target margin   going-concern/share   vs 25% base")
b25 = Y.going_concern(dict(base, target_margin=0.25))["going_concern_per_share"]
for m in (0.10, 0.15, 0.20, 0.25, 0.30, 0.35):
    gc = Y.going_concern(dict(base, target_margin=m))["going_concern_per_share"]
    print(f"     {m*100:4.0f}%          ${gc:8.2f}            {(gc/b25-1)*100:+7.1f}%")
print("   the Monte Carlo samples +/-6pp, which spans roughly the 19-31% rows above;")
print("   the band gate then rejects anything wider than 4x. So the exposure is BOUNDED")
print("   by the gate — but the gate is measuring the assumption, not the company.")

# ---------------------------------------------------------------- B4
hdr("B4", "AR check: revenue_prior comes from revenue_annuals[1] — is it always there?")
for ann, lbl in (([20e9, 18e9], "2 years filed"), ([20e9], "only 1 year filed"), ([], "none")):
    f2 = FF("T", revenue=20e9, revenue_annuals=ann, receivables=3.4e9,
            receivables_prior=2.0e9, net_income=3e9, cfo=3.5e9, total_assets=40e9)
    rev_1y = ann[1] if len(ann) > 1 else None
    out = E.earnings_quality(f2.net_income, f2.cfo, f2.total_assets, None, f2.revenue,
                             receivables=f2.receivables, receivables_prior=f2.receivables_prior,
                             revenue_prior=rev_1y)
    print(f"   {lbl:18s} -> flags {out[1]}")
print("   -> degrades silently to no-check when history is short; correct, and it means")
print("      a newly-listed company can never trip the channel-stuffing flag.")

# ---------------------------------------------------------------- B5
hdr("B5", "store round-trip: do the new fields survive to_dict / JSON?")
f3 = FF("T", sbc=1e9, operating_leases_prior=2e9, receivables_prior=1e9,
        inventory=0.5e9, inventory_prior=0.4e9, accounts_payable=0.7e9,
        accounts_payable_prior=0.6e9)
d = f3.to_dict()
missing = [k for k in ("sbc", "operating_leases_prior", "receivables_prior", "inventory",
                       "inventory_prior", "accounts_payable", "accounts_payable_prior")
           if k not in d]
print("   FinancialFacts.to_dict missing:", missing or "nothing")
v = E.DeepV82Engine().evaluate(FF("T", beta=1.0, price=50.0, revenue=10e9,
                                  operating_income=2e9, net_income=1.5e9,
                                  shares_diluted=1e9, market_cap=50e9, cash=1e9,
                                  equity=8e9, total_debt=2e9, forward_eps=1.8,
                                  growth_lt=0.1, cfo=1.8e9, total_assets=20e9,
                                  revenue_annuals=[10e9, 9e9]), rf=RF)
try:
    json.dumps(v.to_dict())
    print("   Valuation.to_dict is JSON-serialisable: yes")
except TypeError as e:
    print("   !! Valuation.to_dict NOT serialisable:", e)
print("   young_dcf key present:", "young_dcf" in v.to_dict())

# ---------------------------------------------------------------- B6
hdr("B6", "does an OLD stored facts dict (pre-REV-18/19) still evaluate?")
old = {"ticker": "OLD", "beta": 1.0, "price": 50.0, "revenue": 10e9,
       "operating_income": 2e9, "net_income": 1.5e9, "shares_diluted": 1e9,
       "market_cap": 50e9, "cash": 1e9, "equity": 8e9, "total_debt": 2e9,
       "forward_eps": 1.8, "growth_lt": 0.1, "cfo": 1.8e9, "total_assets": 20e9,
       "revenue_annuals": [10e9, 9e9]}
try:
    f4 = FF(**old)
    v4 = E.DeepV82Engine().evaluate(f4, rf=RF)
    print(f"   old-schema facts evaluate fine -> {v4.recommendation} FV {v4.anchor_value}")
    print(f"   sbc={f4.sbc} inventory={f4.inventory} -> dWC {E.working_capital_change(f4)}")
except Exception as e:
    print("   !! old-schema facts CRASH:", type(e).__name__, e)

# ---------------------------------------------------------------- B7
hdr("B7", "concept_conflicts arithmetic on a NEGATIVE base")
print("   the conflict message divides by `base`; a loss-making filer has base < 0.")
print("   guard is `if alts and base` (falsy-zero safe) and the message only formats,")
print("   so no division happens in the message itself. Checking pick() directly:")
tiny = {"facts": {"us-gaap": {
    "NetIncomeLoss": {"units": {"USD": [
        {"start": "2024-01-01", "end": "2024-12-31", "val": -1e9, "filed": "2025-02-01"}]}},
    "ProfitLoss": {"units": {"USD": [
        {"start": "2024-01-01", "end": "2024-12-31", "val": -2e9, "filed": "2025-02-01"}]}},
}}}
out = sec_edgar.extract(tiny)
print("   net income picked:", out["net_income"], "(list order -> NetIncomeLoss)")
print("   conflicts:", out["concept_conflicts"])
print("   -> under the OLD magnitude tie-break this would have picked -2e9 (bigger |v|).")
