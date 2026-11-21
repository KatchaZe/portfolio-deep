"""Rebuild moat_trend for every stored ticker from the store alone, offline.

    python -B -m tools.moat_replay [path/to/portfolio.json] [--json out.json]

Uses the stored facts, the stored peer panel points (relative.model.panel_points) and
the stored industry_share; no network. The target point is rebuilt from the facts the
way pipeline.peers.panel_and_moat builds it, on the panel's own as-of date. Prints per
ticker the stored label and hints next to the rebuilt ones, so a change to
domain/moat_trend.py can be measured on real data before it ships. Read-only.
"""
import datetime as dt
import json
import os
import sys

import config
from domain import moat_trend, peer_panel
from pipeline import peers
from tools.panel_probe import _facts


def rebuild(s):
    """{ticker: (stored moat_trend, rebuilt moat_trend)} for every result with a panel."""
    out = {}
    for t, r in sorted((s.get("results") or {}).items()):
        panel, fd = r.get("peer_panel") or {}, (s.get("facts") or {}).get(t)
        if not panel.get("as_of") or not fd:
            continue
        today = dt.date.fromisoformat(panel["as_of"])
        f = _facts(fd, t)
        target, _why = peers._target_panel_point(f, today)
        pts = (((r.get("relative") or {}).get("model") or {}).get("panel_points"))
        kept = peer_panel.peer_set(target, pts, panel["as_of"])[0] if pts else []
        km = r.get("key_metrics") or {}
        out[t] = (r.get("moat_trend") or {}, moat_trend.build(f, target, kept, panel.get("industry_share"),
                                                              km.get("wacc_pct"), km.get("spread_pct")))
    return out


def _kinds(m):
    return [(h["type"], h["strength"]) for h in m.get("source_hints") or []]


def _hints(m):
    return ",".join(f"{h['type']}:{h['strength']}" + (f"({h['trend']:+d})" if h.get("trend") is not None else "")
                    for h in m.get("source_hints") or [])


def main(argv):
    path = next((a for a in argv if not a.startswith("--") and a.endswith(".json")),
                os.path.join(config.DATA_DIR, "portfolio.json"))
    with open(path, encoding="utf-8") as fh:
        s = json.load(fh)
    rep = rebuild(s)
    for t, (old, new) in rep.items():
        mark = "" if (old.get("label"), _kinds(old)) == (new.get("label"), _kinds(new)) else "  CHANGED"
        print(f"{t:6} label {old.get('label')!s:9} -> {new.get('label')!s:9} "
              f"spread {((s['results'][t].get('key_metrics') or {}).get('spread_pct'))} hints [{_hints(old)}] -> [{_hints(new)}] watch {new.get('watch')}{mark}")
    if "--json" in argv:
        with open(argv[argv.index("--json") + 1], "w", encoding="utf-8") as fh:
            json.dump({t: n for t, (_o, n) in rep.items()}, fh, ensure_ascii=False, indent=1, sort_keys=True)
    return rep


if __name__ == "__main__":
    main(sys.argv[1:])
