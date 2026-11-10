"""
Value one stock with the portfolio-deep app's own engine.

    python scripts/value.py input.json            -> JSON result on stdout
    python scripts/value.py input.json --brief    -> the numbers Stage 2 needs
    python scripts/value.py --fields              -> every facts field the engine reads

input.json:
    {
      "ticker": "MSFT",
      "rf": 0.0415,                  US 10-year Treasury yield, decimal (required)
      "erp": 0.0420,                 Damodaran implied ERP, decimal (required)
      "erp_as_of": "2026-10",        year-month of that ERP (required)
      "facts": { <FinancialFacts field>: value, ... },
      "sources": { <field>: "[SEC] 10-K FY2026", ... }     provenance per field
    }

The engine and the offline refresh steps (Damodaran industry beta / margin / sales-to-
capital / terminal ROIC, country risk, one-time add-back, validation, growth choice)
are copied from the app into scripts/app/ by tools/build_skill.py. Damodaran tables
are read live when the network allows, else from the snapshot bundled with the app.
"""
import dataclasses
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "app"))

import config                                               # noqa: E402
from domain.engine import get_engine, young_dcf             # noqa: E402
from domain.facts import FinancialFacts                     # noqa: E402
from pipeline import finish                                 # noqa: E402

# Without these the engine cannot produce a fair value; each one missing is reported
# as "⛔ MISSING: <field>" rather than guessed.
REQUIRED = ("price", "revenue", "operating_income", "net_income", "shares_diluted",
            "total_debt", "cash", "equity", "revenue_annuals")
REQUIRED_RUN = ("rf", "erp", "erp_as_of")


def field_names():
    return [f.name for f in dataclasses.fields(FinancialFacts)]


def build_facts(spec):
    """FinancialFacts from the input, every field tagged with its source."""
    facts, sources = spec.get("facts") or {}, spec.get("sources") or {}
    unknown = sorted(set(facts) - set(field_names()))
    if unknown:
        raise SystemExit("unknown facts field(s): %s  (python scripts/value.py --fields)"
                         % ", ".join(unknown))
    ff = FinancialFacts(spec["ticker"].upper())
    for name, value in facts.items():
        if name == "ticker":
            continue
        ff.set(name, value, sources.get(name, "chat (no source given)"))
    return ff


def run(spec):
    missing = [k for k in REQUIRED_RUN if spec.get(k) in (None, "")]
    if missing:
        return {"error": "missing run inputs", "missing": ["⛔ MISSING: " + k for k in missing]}
    config.ERP, config.ERP_AS_OF, config.ERP_SOURCE = float(spec["erp"]), str(spec["erp_as_of"]), "manual"
    config.CACHE_DIR = os.path.join(tempfile.gettempdir(), "ifa-skill-cache")
    os.makedirs(config.CACHE_DIR, exist_ok=True)

    ff = build_facts(spec)
    rf = float(spec["rf"])
    finish.finish(ff, ff.ticker, rf, finish.fetch_roc_table())
    val = get_engine().evaluate(ff, rf=rf)
    return {
        "ticker": ff.ticker,
        "missing": ["⛔ MISSING: " + k for k in REQUIRED
                    if getattr(ff, k) in (None, [], {})],
        "valuation": val.to_dict(),
        "facts": ff.to_dict(),
    }


def _young(y):
    """The young-company value, plus the same formula at p_survival ± 0.20: the survival
    probability is an assumption, so the reader sees how far the value moves on it."""
    if not y or y.get("going_concern_per_share") is None or y.get("p_survival") is None:
        return y or None
    p = y["p_survival"]
    y = dict(y)
    y["value_at_p_survival"] = {
        round(q, 2): round(young_dcf.failure_adjusted(y["going_concern_per_share"], q,
                                                      y.get("distress_per_share")), 2)
        for q in (max(0.0, p - 0.20), p, min(1.0, p + 0.20))}
    return y


def brief(out):
    """The figures the Stage 2 output reads, in one small block."""
    if "valuation" not in out:
        return out
    v, f = out["valuation"], out["facts"]
    km = v.get("key_metrics") or {}
    return {
        "ticker": out["ticker"], "missing": out["missing"],
        "price": f.get("price"), "company_type": v.get("company_type"),
        "model": v.get("anchor_method"), "fair_value": v.get("anchor_value"),
        "range": [v.get("range_low"), v.get("range_high")],
        "scores": {k: v.get(k) for k in ("D", "E_exec", "E_econ", "P", "composite")},
        "recommendation": v.get("recommendation"), "stars": v.get("stars"),
        "wacc_pct": km.get("wacc_pct"), "ke_pct": km.get("ke_pct"), "roic_pct": km.get("roic_pct"),
        "growth_pct": km.get("growth_pct"),
        "uncertainty": v.get("uncertainty"), "expected_return": v.get("expected_return"),
        "scenarios": v.get("scenarios"), "reverse_dcf": v.get("reverse_dcf"),
        "young_dcf": _young(v.get("young_dcf")),
        "confidence": f.get("confidence"), "verdict": v.get("verdict"),
        "flags": v.get("flags"), "sources": f.get("provenance"),
    }


def main(argv):
    if "--fields" in argv:
        print("\n".join(field_names()))
        return 0
    paths = [a for a in argv if not a.startswith("--")]
    if len(paths) != 1:
        print(__doc__)
        return 2
    with open(paths[0], encoding="utf-8") as fh:
        out = run(json.load(fh))
    if "--brief" in argv:
        out = brief(out)
    json.dump(out, sys.stdout, indent=1, ensure_ascii=False, default=str)
    print()
    return 1 if "error" in out else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
