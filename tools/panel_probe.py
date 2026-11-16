"""Live check of the peer panel inputs on every holding, with real network.

    python -m tools.panel_probe            # all holdings in data/portfolio.json
    python -m tools.panel_probe NVDA UNH   # some

For each ticker: its Damodaran industry, the industry's US SEC filers, how many of
them have SEC frames revenue in each calendar year (the 70% coverage rule), the
usable peers with FY0 and FY3, the run time and the FMP calls (must be 0).

Read-only. It reads data/portfolio.json directly (store.load would pull from Google
Drive) and never writes it or Drive. Every cache it fills (Damodaran list, SEC frames,
SEC gap patches, CIK map) goes to a temporary directory seeded from data/cache, so the
app's own cache is not touched either.
"""
import datetime as dt
import json
import os
import shutil
import sys
import tempfile
import time
from types import SimpleNamespace

import config
from domain import peer_panel, pricing
from domain.facts import FinancialFacts
from pipeline import peers
from sources import damodaran_companies as dc, fmp

SEED = ("damodaran_indname.json", "company_tickers.json")


def _isolated_cache():
    """A temp cache dir seeded (copied, read-only) from the app's cache."""
    tmp = tempfile.mkdtemp(prefix="panel_probe_")
    src = config.CACHE_DIR
    for name in os.listdir(src) if os.path.isdir(src) else []:
        if name in SEED or name.startswith("sec_frames_revenue_CY"):
            shutil.copy2(os.path.join(src, name), tmp)
    return tmp


class FmpGuard:
    """Counts every FMP request and refuses it: the feature's budget is zero calls."""

    def __init__(self):
        self.calls = 0

    def __enter__(self):
        self._send = fmp._send

        def refuse(*a, **k):
            self.calls += 1
            raise RuntimeError("panel_probe: FMP call refused")
        fmp._send = refuse
        return self

    def __exit__(self, *exc):
        fmp._send = self._send


def _facts(fd, t):
    fields = set(FinancialFacts.__dataclass_fields__)
    kw = {k: v for k, v in fd.items() if k in fields}
    kw["ticker"], kw["flags"] = t, []
    return FinancialFacts(**kw)


def probe(s, tickers, today, universe, memo=None):
    """{ticker: report} for `tickers` from the loaded store dict `s`."""
    memo = memo or peers.PointMemo()
    out = {}
    for t in tickers:
        fd = (s.get("facts") or {}).get(t)
        if not fd:
            out[t] = {"error": "no stored facts"}
            continue
        f = _facts(fd, t)
        val = SimpleNamespace(key_metrics=((s.get("results") or {}).get(t) or {}).get("key_metrics") or {})
        t0 = time.time()
        with FmpGuard() as g:
            try:
                block, calls = peers.relative_value(f, "", today, None, None, memo, universe)
                panel, moat = peers.panel_and_moat(f, val, block, universe, today)
            except Exception as e:
                out[t] = {"error": f"{type(e).__name__}: {e}", "fmp_calls": g.calls}
                continue
        industry, _basis = universe.industry_of(f)
        members = dc.members(universe.rows, industry) if industry else []
        sh = panel.get("industry_share") or {}
        rows = [r for r in ((panel.get("table") or {}).get("rows") or []) if not r["is_target"]]
        pts = (block.get("model") or {}).get("panel_points") or []
        out[t] = {"industry": industry, "members": len(members),
                  "members_sec_filers": sh.get("n_members"),
                  "with_revenue": sh.get("n_with_revenue"), "coverage": sh.get("coverage"),
                  "target_share": sh.get("target_share"),
                  "usable_peers": len(rows),
                  "peers_fy0": sum(1 for p in pts if p.get("fy0")),
                  "peers_fy3": sum(1 for p in pts if p.get("fy3")),
                  "moat_label": moat.get("label"), "n_signals": moat.get("n_signals"),
                  "secs": round(time.time() - t0, 1), "fmp_calls": calls + g.calls,
                  "flags": panel.get("flags", []) + moat.get("flags", [])}
    return out


def _load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def main(argv, path=None, today=None):
    s = _load(path or os.path.join(config.DATA_DIR, "portfolio.json"))
    tickers = [t.upper() for t in argv] or sorted(s.get("holdings", {}))
    today = today or dt.date.fromisoformat(pricing.ny_today())
    cache = _isolated_cache()
    config.CACHE_DIR = cache                  # SEC gap patches and the CIK map land here too
    universe = peers.Universe.lazy(today, cache_dir=cache)
    t0 = time.time()
    rep = probe(s, tickers, today, universe)
    years = [str(y) for y in dc.panel_years(today)]
    print(f"calendar years {years[0]}..{years[-1]}  (coverage rule {peer_panel.COVERAGE_MIN:.0%})")
    for t, r in rep.items():
        if "error" in r:
            print(f"{t:6} ERROR {r['error']}")
            continue
        cov = " ".join(f"{y[2:]}:{(r['with_revenue'] or {}).get(y, '-')}" for y in years)
        print(f"{t:6} {str(r['industry'])[:28]:28} members {r['members']:4} filers {r['members_sec_filers']} "
              f"with revenue [{cov}] peers {r['usable_peers']} FY0 {r['peers_fy0']} FY3 {r['peers_fy3']} "
              f"moat {r['moat_label']}({r['n_signals']}) {r['secs']}s FMP {r['fmp_calls']}")
    total = sum(r.get("fmp_calls", 0) for r in rep.values())
    print(f"\n{len(rep)} tickers in {time.time() - t0:.0f}s, FMP calls {total} (must be 0); "
          f"caches in {cache}")
    return rep


if __name__ == "__main__":
    main(sys.argv[1:])
