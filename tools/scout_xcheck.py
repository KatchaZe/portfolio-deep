"""Check tools/scout_fetch against the app's own readers for a few holdings, offline.

  python -B -m tools.scout_xcheck OUT_DIR FACTS_DIR CACHE_DIR [TICKER ...]

For each ticker: (1) every SEC figure in the scout JSON equals sources.sec_edgar.extract
(+ peers.normalized) on the same companyfacts; (2) market cap and EV from scout's assembly
equal pipeline.peers.peer_point's at one stub close; (3) the CY revenue scout read from the
frames equals the frames panel, and cy_rows_from_facts (the IFRS path) on the filer's own
facts gives the same rows. Exit 1 on any mismatch."""
import datetime as dt
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config                                                     # noqa: E402
from pipeline import peers                                        # noqa: E402
from sources import damodaran_companies as dc, sec_edgar          # noqa: E402
from tools import scout_fetch as sf                               # noqa: E402

DEFAULT = ("NVDA", "AVGO", "TMDX", "MSFT", "ORCL")
STUB = (100.0, "2026-11-21", "stub")


def _same(a, b):
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b))
    return a == b


def check(out_dir, facts_dir, cache, tickers, today):
    config.CACHE_DIR = cache
    recs = {}
    for f in glob.glob(os.path.join(out_dir, "*.json")):
        for r in json.load(open(f, encoding="utf-8")).get("members") or []:
            recs[r["ticker"]] = r
    frames, _ = dc.load_revenue_frames_years(cache, today, config.SEC_USER_AGENT)
    years = dc.panel_years(today)
    rows, bad = [], 0
    for t in tickers:
        r = recs.get(t)
        cf = json.load(open(os.path.join(facts_dir, f"companyfacts_{t}.json"), encoding="utf-8"))
        x = sec_edgar.extract(cf)
        xn, _ = peers.normalized(x)
        cik = r["cik"]["value"]
        pairs = [("ttm.revenue", r["ttm"]["revenue"]["value"], x["revenue"]),
                 ("ttm.ebit_raw", r["ttm"]["ebit_raw"]["value"], x["operating_income"]),
                 ("ttm.ebit(normalized)", r["ttm"]["ebit"]["value"], xn["operating_income"]),
                 ("ttm.net_income", r["ttm"]["net_income"]["value"], x["net_income"]),
                 ("balance.equity", r["balance"]["equity"]["value"], x["equity"]),
                 ("balance.debt", r["balance"]["debt"]["value"], x["total_debt"]),
                 ("balance.cash", r["balance"]["cash"]["value"], x["cash"]),
                 ("balance.operating_leases", r["balance"]["operating_leases"]["value"], x["operating_leases"]),
                 ("market.shares", r["market"]["shares"]["value"], x["shares_diluted"])]
        ic = x.get("ic_components_dated") or {}
        rev_fy, oi_fy = dict(x["revenue_fy_dated"]), dict(x["operating_income_fy_dated"] or [])
        for row in r["fy"]["rows"]:
            e, legs = row["end"], ic.get(row["end"]) or {}
            pairs += [(f"fy[{e}].revenue", row["revenue"], rev_fy.get(e)),
                      (f"fy[{e}].ebit", row["ebit"], oi_fy.get(e)),
                      (f"fy[{e}].equity", row["equity"], legs.get("equity")),
                      (f"fy[{e}].debt", row["debt"], legs.get("debt")),
                      (f"fy[{e}].debt_rule", row["debt_rule"], legs.get("lt_source")),
                      (f"fy[{e}].cash", row["cash"], legs.get("cash"))]
        # market cap / EV: scout's assembly vs the app's peer_point at one stub close
        meta = {"ticker": t, "cik": cik, "cik_how": "ticker", "sic": r["sic"]["value"]}
        price = {"close": STUB[0], "day": STUB[1], "src": STUB[2]}
        mine = sf.assemble(meta, sf.sec_part(t, cik, cf, today.isoformat()), price, frames, years, today)
        app, why = peers.peer_point(t, today, fetch_facts=lambda c: cf, fetch_price=lambda s, d: STUB, complete=None)
        if app:
            pairs += [("market_cap vs peer_point", mine["market"]["market_cap"]["value"], app["market_cap"]),
                      ("ev vs peer_point", mine["market"]["ev"]["value"], app["ev"])]
        else:
            rows.append((t, "peer_point refused", why, "", True))
        fromfacts = sf.cy_rows_from_facts(cf, today.isoformat())
        for y in years:
            fr = (frames.get(y) or {}).get(cik)
            pairs.append((f"revenue_cy[{y}] vs frames panel", r["revenue_cy"][str(y)]["value"], fr and fr[0]))
            ff = fromfacts.get(y)
            pairs.append((f"cy_rows_from_facts[{y}] vs frames panel", ff and ff[:3], fr and fr[:3]))
        for label, a, b in pairs:
            ok = _same(a, b)
            bad += not ok
            rows.append((t, label, a, b, ok))
    return rows, bad


def main(argv):
    out_dir, facts_dir, cache = argv[:3]
    tickers = argv[3:] or DEFAULT
    rows, bad = check(out_dir, facts_dir, cache, tickers, dt.date(2026, 11, 24))
    for t, label, a, b, ok in rows:
        if not ok or label.startswith(("market_cap", "ev", "peer_point")):
            print(f"{'ok  ' if ok else 'DIFF'} {t:<5} {label:<40} scout={a}  app={b}")
    n = sum(1 for r in rows if r[1] != "peer_point refused")
    print(f"{n - bad}/{n} values equal across {len(tickers)} holdings")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
