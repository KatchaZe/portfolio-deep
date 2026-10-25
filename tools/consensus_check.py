"""How closely do the consensus sources agree? Decides whether one can stand in for
another when FMP is out (over its daily limit, or no estimates for the symbol).

    python -m tools.consensus_check            # holdings + watchlist
    python -m tools.consensus_check NVDA ASML  # some
    python -m tools.consensus_check --av 8     # also spend up to 8 Alpha Vantage calls

Per ticker, on the app's own parsers and the fiscal year in the stored facts:
  forward EPS (NTM)       Yahoo live | FMP cache | Finnhub live | Alpha Vantage cache
  revenue growth y1, y2   Yahoo live | FMP cache | Alpha Vantage cache
FMP is read from its estimates cache only (no FMP call: the day may be used up).
Writes data/probe/consensus_check_<date>.json and prints a table. Changes nothing in
the store.
"""
import datetime as dt
import json
import os
import sys
import time

import config
import store
from sources import alphavantage, finnhub, fmp, yahoo


def _fmp_cached(t):
    try:
        with open(os.path.join(config.CACHE_DIR, f"fmp_estimates_{t}.json"), encoding="utf-8") as fh:
            c = json.load(fh)
        return c.get("data"), dt.date.fromtimestamp(c.get("at", 0)).isoformat()
    except (OSError, ValueError):
        return None, None


def _av(t, spend):
    key = config.ALPHAVANTAGE_API_KEY if spend[0] > 0 else None
    raw, calls = alphavantage.cached("estimates", t, key, alphavantage.fetch_estimates,
                                     config.CACHE_DIR)
    spend[0] -= calls
    return None if (not raw or raw.get("_refused")) else raw


def _g(path, i):
    return path[i][0] if path and len(path) > i else None


def check(t, f, spend):
    fy = f.get("fiscal_year")
    anchor = (f.get("revenue_annuals") or [None])[0] if not (f.get("provenance") or {}).get("fx") else None
    row = {"ticker": t, "fiscal_year": fy, "eps": {}, "g1": {}, "g2": {}, "n1": {}, "notes": []}
    try:
        qs = yahoo.fetch_consensus(t)
        y = yahoo.parse_consensus(qs)
        yp = yahoo.parse_revenue_path(qs, fy)
        row["eps"]["yahoo"] = y.get("forward_eps")
        row["g1"]["yahoo"], row["g2"]["yahoo"] = _g(yp, 0), _g(yp, 1)
        row["n1"]["yahoo"] = yp[0][1] if yp else None
    except Exception as e:
        row["notes"].append(f"yahoo: {type(e).__name__}")
    data, at = _fmp_cached(t)
    if data:
        row["fmp_cache_date"] = at
        p = fmp.parse_estimate_path(data, fy, latest_revenue=anchor).get("rev_growth_path") or []
        row["eps"]["fmp"] = fmp.parse_ntm_eps(data)
        row["g1"]["fmp"], row["g2"]["fmp"] = _g(p, 0), _g(p, 1)
        row["n1"]["fmp"] = p[0][1] if p else None
        row["fmp_years"] = len(p)
    if config.FINNHUB_API_KEY:
        try:
            row["eps"]["finnhub"] = finnhub.parse_eps_estimate(
                finnhub.fetch_eps_estimate(t, config.FINNHUB_API_KEY))
        except Exception as e:
            row["notes"].append(f"finnhub: {type(e).__name__}")
    raw = _av(t, spend)
    if raw:
        row["eps"]["alphavantage"] = alphavantage.parse_ntm_eps(raw, fy)
        p = alphavantage.parse_estimate_path(raw, fy, latest_revenue=anchor)
        row["g1"]["alphavantage"], row["g2"]["alphavantage"] = _g(p, 0), _g(p, 1)
        row["n1"]["alphavantage"] = p[0][1] if p else None
    for k in ("eps", "g1", "g2", "n1"):
        row[k] = {s: v for s, v in row[k].items() if v is not None}
    row["price"] = f.get("price")
    return row


def main(argv):
    spend = [0]
    if "--av" in argv:
        i = argv.index("--av")
        spend[0] = int(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    s = store.load()
    names = argv or list(s.get("holdings", {})) + list(s.get("watchlist", []))
    facts = dict(s.get("facts", {}))
    for t, e in (s.get("watch_cache") or {}).items():
        facts.setdefault(t, (e or {}).get("facts") or {})
    out = []
    for t in names:
        out.append(check(t, facts.get(t) or {}, spend))
        r = out[-1]
        fmt = lambda d: " ".join(f"{k[:2]}={v:.4g}" for k, v in d.items()) or "-"
        print(f"{t:6} EPS {fmt(r['eps']):48} g1 {fmt(r['g1']):36} g2 {fmt(r['g2'])}")
        time.sleep(0.5)
    os.makedirs(os.path.join(config.DATA_DIR, "probe"), exist_ok=True)
    path = os.path.join(config.DATA_DIR, "probe", f"consensus_check_{dt.date.today().isoformat()}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"at": dt.datetime.now().isoformat(timespec="minutes"), "rows": out}, fh, indent=1)
    print(f"\nwrote {path}")


if __name__ == "__main__":
    main(sys.argv[1:])
