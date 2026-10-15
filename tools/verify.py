"""
Phase 1 verify — run the SEC robust extraction on the captured fixtures and check
the result against known-good values. This is the proof that the free data stack
reads CORRECT numbers (the v1 bugs would show up here as CHECK!).

    python verify.py     (after capture.py)
"""
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
from sources import sec_edgar

FIX = config.FIXTURE_DIR


def load(ticker, name):
    p = os.path.join(FIX, ticker, name + ".json")
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def bn(x):
    return f"{x/1e9:.2f}B" if isinstance(x, (int, float)) else "—"


def _raw(node):
    return node.get("raw") if isinstance(node, dict) else node


def main():
    print(f"{'TK':5} {'currency':8} {'revenue':>10} {'opInc':>9} {'netInc':>9} {'epsGAAP':>8} "
          f"{'shares':>9} {'fwdEPS':>7} {'sector':14} check")
    print("-" * 100)
    for t in config.PROBE_TICKERS:
        cf = load(t, "sec_companyfacts")
        if not cf:
            print(f"{t:5} (no SEC fixture — run capture.py)")
            continue
        d = sec_edgar.extract(cf)

        prof = load(t, "fmp_profile") or []
        p0 = prof[0] if isinstance(prof, list) and prof else {}
        sector = p0.get("sector", "—")

        ys = load(t, "yahoo_quotesummary") or {}
        fwd = None
        try:
            res = ys["quoteSummary"]["result"][0]
            fwd = _raw(res.get("defaultKeyStatistics", {}).get("forwardEps"))
        except Exception:
            pass

        kg = config.KNOWN_GOOD.get(t, {})
        checks = []
        if "net_income_ttm_bn" in kg and isinstance(d["net_income"], (int, float)):
            lo, hi = kg["net_income_ttm_bn"]
            checks.append("NI " + ("OK" if lo <= d["net_income"] / 1e9 <= hi else "CHECK!"))
        if "revenue_ttm_bn" in kg and isinstance(d["revenue"], (int, float)):
            lo, hi = kg["revenue_ttm_bn"]
            checks.append("Rev " + ("OK" if lo <= d["revenue"] / 1e9 <= hi else "CHECK!"))

        eps = round(d['eps_gaap'], 2) if isinstance(d['eps_gaap'], (int, float)) else "—"
        print(f"{t:5} {str(d['currency']):8} {bn(d['revenue']):>10} {bn(d['operating_income']):>9} "
              f"{bn(d['net_income']):>9} {str(eps):>8} {bn(d['shares_diluted']):>9} "
              f"{str(round(fwd,2) if fwd else '—'):>7} {str(sector)[:14]:14} {' '.join(checks)}")
        if d.get("revenue_annuals"):
            print(f"      annual revenue series: {[round(x/1e9,1) for x in d['revenue_annuals'][:4]]}")

    print("\nGreen = data layer reads correct numbers. Any CHECK! -> paste it and I'll fix the mapping.")


if __name__ == "__main__":
    main()
