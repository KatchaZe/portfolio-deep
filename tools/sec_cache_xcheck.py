"""App extract against verify/reference.py on every cached SEC companyfacts file.

usage: python3 -B tools/sec_cache_xcheck.py CACHE_DIR [TICKER ...]
Compares GAAP lines only (no add-back, no prices): TTM revenue, net income, EBIT margin,
and the balance-sheet legs debt, operating leases, cash. Tolerance 0.5% for money and
0.2pp for the margin. Prints FAIL rows and a count; exit 1 on any FAIL.
"""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sources import sec_edgar  # noqa: E402
from verify import reference as R  # noqa: E402

MONEY = (("revenue", "revenue_ttm"), ("net_income", "net_income_ttm"),
         ("total_debt", "total_debt"), ("operating_leases", "operating_leases"), ("cash", "cash"))


def rows(cache, only):
    for f in sorted(glob.glob(os.path.join(cache, "companyfacts_*.json"))):
        t = os.path.basename(f)[len("companyfacts_"):-len(".json")]
        if only and t not in only:
            continue
        cf = json.load(open(f))
        app = sec_edgar.extract(cf, cash_excludes_securities=t in R.MANAGED_CARE)
        ref = R.company_reference(cf, symbol=t)
        if ref["excluded"]:
            continue
        for a, r in MONEY:
            va, vr = app.get(a), ref.get(r)
            if va is None and vr is None:
                continue
            ok = va is not None and vr is not None and abs(va - vr) <= 0.005 * max(abs(vr), 1)
            yield ok, t, a, va, vr, ref.get("balance_date") if a in ("total_debt", "operating_leases", "cash") else ref.get("period_end")
        ma = app["operating_income"] / app["revenue"] if app.get("operating_income") is not None and app.get("revenue") else None
        mr = ref["op_margin_ttm"]
        if ma is not None or mr is not None:
            ok = ma is not None and mr is not None and abs(ma - mr) <= 0.002
            yield ok, t, "op_margin", ma, mr, ref["ebit_basis"]


if __name__ == "__main__":
    fails = n = 0
    for ok, t, field, va, vr, note in rows(sys.argv[1], set(sys.argv[2:])):
        n += 1
        if not ok:
            fails += 1
            print("FAIL %-6s %-17s app %-16s ref %-16s %s" % (t, field, va, vr, note))
    print("%d checks, %d FAIL" % (n, fails))
    sys.exit(1 if fails else 0)
