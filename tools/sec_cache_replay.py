"""Re-value the holdings with SEC facts re-extracted from cached companyfacts files.

usage: python3 -B tools/sec_cache_replay.py APP_ROOT STORE.json CACHE_DIR OUT.json

APP_ROOT is the checkout whose code runs, so the same store and cache can be valued
before and after a change to the SEC extract. Per holding with a cached file: the SEC
values are rebuilt through normalize.build (extract + FX at the stored rate), copied
over every stored field whose provenance is SEC (fields another source supplied, such
as the Alpha Vantage quarterly TTM of a 6-K filer, stay), the one-time add-back is
recomputed, and the engine values the row as tests/replay_snapshot does. Offline.
Writes {"scores": {ticker: tracked fields}, "missing": [...], "facts": {...}}.
"""
import json
import os
import re
import sys


def main(root, store_path, cache, out_path):
    sys.path.insert(0, os.path.abspath(root))
    from tests import _harness
    _harness.cut_network()
    from domain import one_time
    from domain.facts import FinancialFacts
    from pipeline import normalize
    from sources import sec_edgar
    from sources.industries import securities_are_operating
    from tests import replay_snapshot

    store = json.load(open(store_path))
    fields = set(FinancialFacts.__dataclass_fields__)
    missing, facts_out = [], {}
    for t in store["holdings"]:
        p = os.path.join(cache, "companyfacts_%s.json" % t)
        fd = store["facts"].get(t)
        if not fd or not os.path.exists(p):
            missing.append(t)
            continue
        prov = fd.get("provenance") or {}
        fx = None
        m = re.match(r"[A-Z]{3}->USD ([0-9.]+)", str(prov.get("fx") or ""))
        if m:
            fx = float(m.group(1))
        fresh = normalize.build(t, json.load(open(p)), fx_rate=fx,
                                cash_excludes_securities=securities_are_operating(t, fd.get("industry")))
        new = dict(fd)
        new_prov = dict(prov)
        for k in sec_edgar.POPULATED + ("fiscal_year",):
            if str(prov.get(k, "sec")).startswith("sec") and k in fields:
                new[k] = getattr(fresh, k, None)
                new_prov[k] = fresh.provenance.get(k, "sec")
        if str(new_prov.get("operating_income", "")).startswith("sec"):
            new["operating_income_gaap"] = None
            ff = FinancialFacts(**{k: v for k, v in new.items() if k in fields})
            ff.provenance = new_prov
            ff.flags = []
            one_time.normalize_operating_income(ff)
            new["operating_income"], new["operating_income_gaap"] = ff.operating_income, ff.operating_income_gaap
        new["provenance"] = new_prov
        new["sec_flags"] = list(fresh.flags)
        store["facts"][t] = new
        facts_out[t] = new
    store["facts"] = {t: f for t, f in store["facts"].items() if t in facts_out}
    tmp = out_path + ".store.json"
    with open(tmp, "w") as fh:
        json.dump(store, fh)
    scores = replay_snapshot.evaluate_all(tmp)
    with open(out_path, "w") as fh:
        json.dump({"scores": scores, "missing": missing, "facts": facts_out}, fh, default=str)
    print(len(scores), "valued,", "not in cache:", missing)


if __name__ == "__main__":
    main(*sys.argv[1:5])
