"""Check tools/scout_fetch against the app's own readers for a few holdings, offline.

  python -B -m tools.scout_xcheck OUT_DIR FACTS_DIR CACHE_DIR [--store portfolio.json] [TICKER ...]

For each ticker: (1) every SEC figure in the scout JSON equals sources.sec_edgar.extract
(+ peers.normalized) on the same companyfacts; (2) market cap and EV from scout's assembly
equal pipeline.peers.peer_point's at one stub close; (3) the CY revenue scout read from the
frames equals the frames panel, and cy_rows_from_facts (the IFRS path) on the filer's own
facts gives the same rows; (4) with --store, every group-1 field equals the engine's own
reading of the same companyfacts (incremental ROIC, EQ, margin trend, FCF durability, CAP,
SBC share, FCF) or the extract it came from. Exit 1 on any mismatch."""
import datetime as dt
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config                                                     # noqa: E402
from pipeline import peers                                        # noqa: E402
from domain import frames_revenue                                 # noqa: E402
from sources import damodaran_companies as dc, sec_edgar          # noqa: E402
from tools import scout_fetch as sf                               # noqa: E402

DEFAULT = ("NVDA", "AVGO", "TMDX", "MSFT", "ORCL")
STUB = (100.0, "2026-11-21", "stub")


def _same(a, b):
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b))
    return a == b


def _engine_view(t, cf, store):
    """The dashboard's own valuation of `t` on these companyfacts: the stored facts with
    every SEC field rebuilt (tools/sec_cache_replay's rule), valued by the engine."""
    import re
    from domain import one_time
    from domain.engine.deep_v82 import DeepV82Engine
    from domain.facts import FinancialFacts
    from pipeline import normalize
    from sources.industries import securities_are_operating
    from tests import replay_snapshot
    fd = (store.get("facts") or {}).get(t)
    if not fd:
        return None
    prov = fd.get("provenance") or {}
    m = re.match(r"[A-Z]{3}->USD ([0-9.]+)", str(prov.get("fx") or ""))
    fresh = normalize.build(t, cf, fx_rate=float(m.group(1)) if m else None,
                            cash_excludes_securities=securities_are_operating(t, fd.get("industry")))
    fields = set(FinancialFacts.__dataclass_fields__)
    new = dict(fd)
    for k in sec_edgar.POPULATED + ("fiscal_year",):
        if str(prov.get(k, "sec")).startswith("sec") and k in fields:
            new[k] = getattr(fresh, k, None)
    new["operating_income_gaap"], new["flags"] = None, []
    ff = FinancialFacts(**{k: v for k, v in new.items() if k in fields})
    ff.provenance = dict(prov)
    one_time.normalize_operating_income(ff)
    return DeepV82Engine().evaluate(ff, rf=replay_snapshot.RF)


def _note(breakdown, head):
    import re
    hit = next((b for b in breakdown if b.startswith(head)), None)
    if not hit or "adj=skipped" in hit:
        return None                       # the engine did not measure it
    return re.search(r"\((.*)\)", hit).group(1)


def _yield_text(fcf, v):
    """FCF / EV as the engine's P FCF-yield note writes it."""
    ev = (v.reverse_dcf or {}).get("enterprise_value")
    if fcf is None or not ev:
        return None
    return "ติดลบ" if fcf <= 0 else f"{fcf / ev:.1%}"


def group1_pairs(t, cf, r, store):
    """Every group-1 field against the engine's own reading of the same companyfacts."""
    v = _engine_view(t, cf, store)
    if v is None:
        return [("engine view", "no stored facts", None)]
    g = r["group1"]
    d, km = g["derived"], v.key_metrics or {}
    bd = (v.subscores or {}).get("breakdown") or []
    flags = [f[4:] for f in (v.flags or []) if f.startswith("EQ: ")]
    w = (km.get("wacc_pct") or 0) / 100
    above, n = sf.scout_groups.cap_years(d["cap_window"]["value"], w)
    cap = _note(bd, "E_econ CAP")
    mt = _note(bd, "E_exec margin-trend")
    dur = _note(bd, "E_exec FCF-durability")
    fy_note = _note(bd, "P FCF-yield")
    x = sec_edgar.extract(cf)
    out = [("g1.incremental_roic_pct vs key_metrics", d["incremental_roic"]["value"], km.get("incremental_roic_pct")),
           ("g1.eq verdict vs result.eq_verdict", d["eq"]["value"], v.eq_verdict),
           ("g1.eq flags vs result EQ flags", d["eq"]["flags"], flags),
           ("g1.margin_trend vs E_exec margin-trend", d["margin_trend"]["value"], mt),
           ("g1.fcf_durability note vs E_exec FCF-durability", d["fcf_durability"]["note"], dur),
           ("g1.cap_years at engine WACC vs E_econ CAP", f"{above}/{n}", cap.split(" ")[0] if cap else None),
           ("g1.sbc_pct_revenue vs key_metrics.sbc_pct_of_rev", round(d["sbc_pct_revenue"]["value"], 1)
            if d["sbc_pct_revenue"]["value"] is not None else None, km.get("sbc_pct_of_rev")),
           ("g1.fcf_latest_fy / engine EV vs P FCF-yield", _yield_text(d["fcf_latest_fy"]["value"], v),
            (fy_note or "").split(" ")[2] if fy_note else None)]
    rdate = {e: v_ for e, v_ in x.get("revenue_annuals_dated") or []}
    cfo = dict(x.get("cfo_annuals_dated") or [])
    for row in g["fy"]:
        e = row["end"]
        out += [(f"g1.fy[{e}].revenue vs extract", row["revenue"], rdate.get(e)),
                (f"g1.fy[{e}].ocf vs extract", row["ocf"], cfo.get(e))]
    tt = d["ttm"]
    out += [(f"g1.ttm.{k} vs extract", tt[k]["value"], x.get(k2))
            for k, k2 in (("ocf", "cfo"), ("capex", "capex"), ("sbc", "sbc"), ("da", "dep_amort"),
                          ("interest_expense", "interest_expense"))]
    return out


def check(out_dir, facts_dir, cache, tickers, today, store=None):
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
        fromfacts = frames_revenue.cy_rows_from_facts(cf, today.isoformat())
        for y in years:
            fr = (frames.get(y) or {}).get(cik)
            pairs.append((f"revenue_cy[{y}] vs frames panel", r["revenue_cy"][str(y)]["value"], fr and fr[0]))
            ff = fromfacts.get(y)
            pairs.append((f"cy_rows_from_facts[{y}] vs frames panel", ff and ff[:3], fr and fr[:3]))
        if store:
            pairs += group1_pairs(t, cf, r, store)
        for label, a, b in pairs:
            ok = _same(a, b)
            bad += not ok
            rows.append((t, label, a, b, ok))
    return rows, bad


def main(argv):
    store = None
    if "--store" in argv:
        i = argv.index("--store")
        store = json.load(open(argv[i + 1], encoding="utf-8"))
        argv = argv[:i] + argv[i + 2:]
    out_dir, facts_dir, cache = argv[:3]
    tickers = argv[3:] or DEFAULT
    rows, bad = check(out_dir, facts_dir, cache, tickers, dt.date(2026, 11, 24), store)
    for t, label, a, b, ok in rows:
        if not ok or label.startswith(("market_cap", "ev", "peer_point", "g1.")) and "fy[" not in label:
            print(f"{'ok  ' if ok else 'DIFF'} {t:<5} {label:<40} scout={a}  app={b}")
    n = sum(1 for r in rows if r[1] != "peer_point refused")
    print(f"{n - bad}/{n} values equal across {len(tickers)} holdings")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
