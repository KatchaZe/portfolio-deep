"""Live check of the peer panel inputs on every holding, with real network.

    python -m tools.panel_probe            # all holdings in data/portfolio.json
    python -m tools.panel_probe NVDA UNH   # some
    python -B -m tools.panel_probe --offline [--json out.json] [TICKER...]

--offline rebuilds peer_panel and moat_trend for each holding from the store and the
caches alone (data/cache: damodaran_indname.json, sec_frames_revenue_CY*.json and, when
present, sec_frames_assets_CY*.json) under tests._harness.cut_network: the stored peer
points of the last fit, no fetch. It prints the SIC each GM comparison used (4 / 3 /
skip and the comparator count), S3, the source hints, the label, the votes, and the
same-SIC sub-group (size and the target's share), so a change to the panel rules can
be measured before and after on the same store (SPEC R25-R28).

For each ticker: its Damodaran industry, the industry's US SEC filers, how many of
them have SEC frames revenue in each calendar year and how many have revenue or a
year-end balance sheet (Assets frame; the 70% coverage rule, SPEC R20), the
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

SEED = ("damodaran_indname.json", "damodaran_global_sic.json", "company_tickers.json")


def _isolated_cache():
    """A temp cache dir seeded (copied, read-only) from the app's cache."""
    tmp = tempfile.mkdtemp(prefix="panel_probe_")
    src = config.CACHE_DIR
    for name in os.listdir(src) if os.path.isdir(src) else []:
        if name in SEED or name.startswith(("sec_frames_revenue_CY", "sec_frames_assets_CY")):
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
                  "with_revenue": sh.get("n_with_revenue"), "covered": sh.get("n_covered"),
                  "coverage": sh.get("coverage"),
                  "target_share": sh.get("target_share"),
                  "usable_peers": len(rows),
                  "peers_fy0": sum(1 for p in pts if p.get("fy0")),
                  "peers_fy3": sum(1 for p in pts if p.get("fy3")),
                  "moat_label": moat.get("label"), "n_signals": moat.get("n_signals"),
                  "secs": round(time.time() - t0, 1), "fmp_calls": calls + g.calls,
                  "flags": panel.get("flags", []) + moat.get("flags", [])}
    return out


def _offline_universe(cache):
    """A Universe from the cache files alone (no loader, so nothing is fetched)."""
    rows = (_load(os.path.join(cache, "damodaran_indname.json")) or {}).get("rows") or {}
    frames, assets = {}, {}
    for name in sorted(os.listdir(cache)):
        if name.startswith("sec_frames_revenue_CY"):
            c = _load(os.path.join(cache, name))
            end = c.get("end") or {}
            frames[int(name[21:25])] = {int(k): [v, end.get(k)] for k, v in c["rev"].items()}
        elif name.startswith("sec_frames_assets_CY"):
            assets[int(name[20:24])] = set(_load(os.path.join(cache, name))["ciks"])
    glob = (_load(os.path.join(cache, "damodaran_global_sic.json")) or {}).get("rows") or []
    return peers.Universe(rows=rows, frames=frames, assets=assets, global_rows=glob)


def offline(s, tickers, universe):
    """{ticker: summary} rebuilt from the stored relative block (its panel points) on
    each panel's own as-of date. Reads only; a field the code does not produce is None."""
    out = {}
    for t in tickers:
        r, fd = (s.get("results") or {}).get(t) or {}, (s.get("facts") or {}).get(t)
        as_of = (r.get("peer_panel") or {}).get("as_of")
        if not fd or not as_of:
            out[t] = {"error": "no stored facts or panel"}
            continue
        val = SimpleNamespace(key_metrics=r.get("key_metrics") or {})
        panel, moat = peers.panel_and_moat(_facts(fd, t), val, r.get("relative") or {}, universe,
                                           dt.date.fromisoformat(as_of))
        gm = (panel.get("table") or {}).get("gm_compare") or {}
        sh = panel.get("industry_share") or {}
        sub = sh.get("sub_group") or {}
        sig = {x["name"]: x for x in moat.get("signals") or []}
        s3, s2 = sig.get("gm_gap_change"), sig.get("share_change")
        new = str(max(sub.get("years") or [0]))
        out[t] = {"industry": panel.get("industry"), "sic": (universe.rows.get(t) or [None, None])[1],
                  "gm_level": gm.get("level"), "gm_n": gm.get("n"), "gm_symbols": gm.get("symbols"),
                  "gm_reason": gm.get("reason"), "gm_applies": gm.get("applies"),
                  "s3": None if not s3 else [s3["value"], s3["direction"], (s3.get("comparators") or {}).get("level"),
                                             (s3.get("comparators") or {}).get("n")],
                  "s3_why": next((f for f in moat.get("flags") or [] if str(f).startswith("S3")), None),
                  "s2": None if not s2 else [s2["value"], s2["direction"], s2.get("basis")],
                  "hints": [f"{h['type']}:{h['strength']}" for h in moat.get("source_hints") or []],
                  "label": moat.get("label"), "n_signals": moat.get("n_signals"),
                  "votes": moat.get("votes"),
                  "sub_n": sub.get("n_members"), "sub_matched": sub.get("matched_n"),
                  "sub_share": (sub.get("target_share") or {}).get(new),
                  "sub_reason": sub.get("reason")}
    return out


def _load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def main(argv, path=None, today=None):
    s = _load(path or os.path.join(config.DATA_DIR, "portfolio.json"))
    if "--offline" in argv:
        from tests import _harness
        _harness.cut_network()
        js = argv[argv.index("--json") + 1] if "--json" in argv else None
        names = [a.upper() for a in argv if not a.startswith("--") and a != js]
        rep = offline(s, names or sorted(s.get("holdings", {})), _offline_universe(config.CACHE_DIR))
        for t, r in rep.items():
            print(t, json.dumps(r, ensure_ascii=False, sort_keys=True))
        if js:
            with open(js, "w", encoding="utf-8") as fh:
                json.dump(rep, fh, ensure_ascii=False, indent=1, sort_keys=True)
        return rep
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
        # R20: revenue filers / revenue-or-balance-sheet filers per CY; the share needs the
        # second over `filers` >= 70%. ABBV and LLY were under 70% on revenue alone.
        cov = " ".join(f"{y[2:]}:{(r['with_revenue'] or {}).get(y, '-')}/{(r['covered'] or {}).get(y, '-')}"
                       f"={(r['coverage'] or {}).get(y) or 0:.0%}" for y in years)
        print(f"{t:6} {str(r['industry'])[:28]:28} members {r['members']:4} filers {r['members_sec_filers']} "
              f"revenue/covered [{cov}] peers {r['usable_peers']} FY0 {r['peers_fy0']} FY3 {r['peers_fy3']} "
              f"moat {r['moat_label']}({r['n_signals']}) {r['secs']}s FMP {r['fmp_calls']}")
    total = sum(r.get("fmp_calls", 0) for r in rep.values())
    print(f"\n{len(rep)} tickers in {time.time() - t0:.0f}s, FMP calls {total} (must be 0); "
          f"caches in {cache}")
    return rep


if __name__ == "__main__":
    main(sys.argv[1:])
