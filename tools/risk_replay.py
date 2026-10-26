"""
tools/risk_replay.py — rebuild the /api/risk payload OFFLINE from the stored portfolio
and the saved risk cache, and print (or save) the numbers the Allocation and
Correlation tabs show. No network: fetch_returns is replaced by the cache.

    python -m tools.risk_replay                  print the summary
    python -m tools.risk_replay --save out.json  also write it
    python -m tools.risk_replay --diff old.json  print what moved against an earlier save
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from pipeline import prices, risk_report


def _cache_provider():
    data = (prices.load_cache().get("data") or {})

    def fetch_returns(tickers, *a, **kw):
        return {t: data[t] for t in tickers if t in data}, 0, {"cache": "replay"}
    return fetch_returns


def summary(payload, store):
    rec = {t: (store.get("results", {}).get(t) or {}).get("recommendation")
           for t in store.get("holdings", {})}
    co = payload.get("correlation") or {}
    st = payload.get("stress") or {}
    dv = payload.get("diversification") or {}
    return {
        "snapshot": payload.get("snapshot"),
        "diversification": {k: dv.get(k) for k in ("dr_normal", "dr_crisis", "enb", "enb_crisis")},
        "score": dv.get("score"),
        "concentration": payload.get("concentration"),
        "allocation": payload.get("allocation"),
        "stress": {r["label"]: r["loss_pct"] for r in (st.get("hypothetical") or []) + (st.get("historical") or [])},
        "var": st.get("var"), "var_hist": st.get("var_hist"), "reverse": st.get("reverse"),
        "suitability": payload.get("suitability"),
        "risk_rows": {r["ticker"]: [r["capital_pct"], r["signed_risk_pct"], r["abs_risk_share_pct"]]
                      for r in (payload.get("capital_vs_risk") or {}).get("covariance_based") or []},
        "sizing": {r["ticker"]: [r["action"], r["hard_max_pct"], rec.get(r["ticker"])]
                   for r in payload.get("position_sizing") or []},
        "trades": (payload.get("rebalance") or {}).get("trades"),
        "corr_avg": co.get("avg_pairwise"),
        "corr_meta": co.get("meta"),
        "gauge": (co.get("philosophy") or {}).get("gauge"),
        "pillars": (co.get("philosophy") or {}).get("pillars"),
        "story": [(co.get("philosophy") or {}).get("story_normal"),
                  (co.get("philosophy") or {}).get("story_crisis")],
        "betas": {t: v for t, v in sorted(((payload.get("meta") or {}).get("beta_check") or {}).items())},
        "rolling_n": len(((co.get("rolling") or {}).get("port_vs_spy")) or []),
        "marginal": co.get("marginal"),
    }


def _diff(a, b, path=""):
    out = []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b), key=str):
            out += _diff(a.get(k), b.get(k), f"{path}.{k}" if path else str(k))
    elif a != b:
        out.append(f"{path}: {json.dumps(a, ensure_ascii=False)[:160]} -> {json.dumps(b, ensure_ascii=False)[:160]}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--save")
    ap.add_argument("--diff")
    a = ap.parse_args()
    with open(os.path.join(config.DATA_DIR, "portfolio.json"), encoding="utf-8") as fh:
        store = json.load(fh)
    payload, _ = risk_report.build(store, "", 0, 250, 20, 1, False, fetch_returns=_cache_provider(),
                                   fetch_long=lambda t: prices.fetch_long_returns(t, allow_fetch=False))
    s = summary(payload, store)
    if a.save:
        with open(a.save, "w", encoding="utf-8") as fh:
            json.dump(s, fh, ensure_ascii=False, indent=1, default=str)
    if a.diff:
        with open(a.diff, encoding="utf-8") as fh:
            old = json.load(fh)
        lines = _diff(old, json.loads(json.dumps(s, default=str)))
        print("\n".join(lines) or "no change")
    elif not a.save:
        print(json.dumps(s, ensure_ascii=False, indent=1, default=str)[:6000])


if __name__ == "__main__":
    main()
