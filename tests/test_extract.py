"""
Regression test — the data-layer safety net.

Runs the full normalize pipeline on the captured real fixtures and asserts the
numbers fall in known-good ranges. The v1 catastrophes (AVGO net 2.99B, ORCL
161B) would fail here instantly. Run:  python -m tests.test_extract
"""
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from pipeline import normalize, validate

FIX = config.FIXTURE_DIR


def load(ticker, name):
    p = os.path.join(FIX, ticker, name + ".json")
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None


# expected ranges (USD billions) + NVO FX rate for the test
CASES = {
    "AVGO": {"net": (20, 28), "rev": (60, 72)},
    "ORCL": {"rev": (54, 72), "annual0": (55, 60)},
    "ABBV": {"net": (3, 6), "eps": (1.8, 2.6)},
    "MSFT": {"rev": (290, 340), "net": (95, 140)},
    "NVO":  {"rev": (38, 52), "fx": 0.145},   # 309B DKK x 0.145 ~ 44.8B USD
}


def _bn(x):
    return x / 1e9 if isinstance(x, (int, float)) else None


def run():
    print(f"{'TK':5}{'cur':5}{'rev$B':>9}{'net$B':>9}{'epsGAAP':>9}{'fwdEPS':>8}{'conf':>6}  flags")
    print("-" * 90)
    failures = []
    for t, exp in CASES.items():
        sec = load(t, "sec_companyfacts")
        if not sec:
            print(f"{t}: no fixture (run capture.py)"); failures.append(t); continue
        ff = normalize.build(t, sec, load(t, "fmp_profile"), load(t, "yahoo_quotesummary"),
                             fx_rate=exp.get("fx"))
        validate.validate(ff)
        rev, net = _bn(ff.revenue), _bn(ff.net_income)
        print(f"{t:5}{ff.currency:5}{(round(rev,1) if rev else '—'):>9}{(round(net,1) if net else '—'):>9}"
              f"{(round(ff.eps_gaap,2) if ff.eps_gaap else '—'):>9}{(round(ff.forward_eps,2) if ff.forward_eps else '—'):>8}"
              f"{ff.confidence:>6}  {'; '.join(ff.flags) if ff.flags else 'clean'}")

        def chk(name, val, rng):
            if val is None or not (rng[0] <= val <= rng[1]):
                failures.append(f"{t}.{name}={val} not in {rng}")
        if "rev" in exp:
            chk("rev", rev, exp["rev"])
        if "net" in exp:
            chk("net", net, exp["net"])
        if "eps" in exp:
            chk("eps", ff.eps_gaap, exp["eps"])
        if "annual0" in exp and ff.revenue_annuals:
            chk("annual0", _bn(ff.revenue_annuals[0]), exp["annual0"])
        # Phase 2 locks: no out-of-band metrics (ORCL debt fix), no red tier,
        # AVGO's unsplit consensus EPS must be corrected to the SEC-derived value
        if any("out of band" in f for f in ff.flags):
            failures.append(f"{t}: sanity out-of-band {[f for f in ff.flags if 'out of band' in f]}")
        if ff.confidence_tier == "red":
            failures.append(f"{t}: confidence tier red")
        if t == "AVGO" and ff.forward_eps and ff.forward_eps > 10:
            failures.append(f"{t}: forward_eps {ff.forward_eps} not corrected to SEC-derived")

    print()
    if failures:
        print("FAILURES:")
        for f in failures:
            print("  -", f)
        raise SystemExit(1)
    print("ALL DATA-LAYER REGRESSION CHECKS PASSED ✅")


if __name__ == "__main__":
    run()
