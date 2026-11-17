"""Where each peer-panel number came from, for named tickers: read only.

    python -m tools.panel_diag INGN OPCH            stored rows + SEC facts + frames
    python -m tools.panel_diag STAA --no-frames     skip the frames downloads
    python -m tools.panel_diag MSFT --facts-dir tests/fixtures --as-of 2026-05-15 --no-frames

For each ticker it prints
  1. every stored peer_panel row for that symbol in data/portfolio.json (as target or
     peer), with the panel that holds it;
  2. the app's own recompute from SEC companyfacts (sec_edgar.extract, the one-time
     add-back, peer_panel.point_fields): the TTM end and value, which revenue concept
     the TTM matches, and the FY0 / FY1 / FY3 legs with their end dates;
  3. the raw SEC facts behind those numbers: concept, start, end, filed, form, value,
     for the revenue, gross profit, cost, operating income, pretax and interest
     concepts at the FY0/FY1/FY3 ends and inside the TTM window;
  4. SEC frames per panel calendar year and per revenue concept for its CIK.
Needs network (data.sec.gov) unless --facts-dir (and --no-frames) is given. Never
writes data/: downloads go to the system temp folder. Exit 0 always (a diagnostic)."""
import argparse
import datetime as dt
import json
import os
import sys
import tempfile

import config
from domain import peer_panel as PP
from pipeline import peers
from sources import damodaran_companies as dc
from sources import sec_edgar as SE

FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK%010d.json"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FRAMES_URL = "https://data.sec.gov/api/xbrl/frames/us-gaap/%s/USD/CY%s.json"
GROUPS = (("revenue", SE.REV), ("gross profit", SE.GROSS_PROFIT_TAGS), ("cost of revenue", SE.COST_OF_REV_TAGS),
          ("operating income", SE.OP), ("pretax", SE.PRETAX), ("interest", SE.INT_EXP),
          ("net income", SE.NI))


def _fetch(url, cache_dir, name, max_age_h=12):
    from tools.panel_verify import fetch
    return fetch(url, cache_dir, name, max_age_h)


def load_facts(sym, args):
    """(companyfacts, cik) from --facts-dir (companyfacts_<cik>.json or <SYM>/sec_companyfacts.json)
    or SEC."""
    if args.facts_dir:
        p = os.path.join(args.facts_dir, sym, "sec_companyfacts.json")
        if os.path.exists(p):
            cf = json.load(open(p, encoding="utf-8"))
            return cf, int(cf.get("cik") or 0)
        cik = args.ciks.get(sym)
        p = os.path.join(args.facts_dir, "companyfacts_%010d.json" % cik) if cik else None
        if p and os.path.exists(p):
            return json.load(open(p, encoding="utf-8")), cik
        return None, cik
    cik = args.ciks.get(sym)
    return (_fetch(FACTS_URL % cik, args.cache_dir, "companyfacts_%s.json" % sym) if cik else None), cik


def stored_rows(store, sym):
    out = []
    for owner, res in (store.get("results") or {}).items():
        for r in (((res or {}).get("peer_panel") or {}).get("table") or {}).get("rows") or []:
            if str(r.get("symbol")).upper() == sym:
                out.append((owner, r))
    return out


def _fmt(v):
    if isinstance(v, float) and abs(v) >= 1e5:
        return "%.1fM" % (v / 1e6)
    if isinstance(v, float):
        return "%.4f" % v
    return str(v)


def _span(e):
    return (dt.date.fromisoformat(e["end"]) - dt.date.fromisoformat(e["start"])).days + 1 if e.get("start") else 0


def raw_facts(cf, ends, ttm_from, ttm_to, as_of):
    """[(group, concept, start, end, filed, form, val)] at the FY ends and inside the TTM."""
    ns = (cf.get("facts") or {}).get("us-gaap") or (cf.get("facts") or {}).get("ifrs-full") or {}
    rows = []
    for group, ladder in GROUPS:
        for c in ladder:
            for unit, es in ((ns.get(c) or {}).get("units") or {}).items():
                for e in es:
                    if "start" not in e or (as_of and e.get("filed", "") > as_of):
                        continue
                    annual = 350 <= _span(e) <= 380 and e["end"] in ends
                    in_ttm = ttm_from and ttm_from <= e["start"] and e["end"] <= ttm_to
                    if annual or in_ttm:
                        rows.append((group, c, unit, e["start"], e["end"], e.get("filed"), e.get("form"), e["val"]))
    return sorted(set(rows), key=lambda r: (r[0], r[1], r[4], r[3], r[5]))


def ttm_parts(xb, c):
    """The facts _Xbrl.ttm sums for concept c: the year itself, FY + YTD - prior YTD, or
    four quarters."""
    es = [e for e in xb.entries(c) if e.get("start")]
    end = max(e["end"] for e in es)
    cur = max((e for e in es if e["end"] == end), key=SE._span)
    show = lambda e: "%s..%s %s filed %s" % (e["start"], e["end"], _fmt(float(e["val"])), e.get("filed"))
    if SE._is_annual(cur):
        return "the fiscal year " + show(cur)
    annual = max((e for e in es if SE._is_annual(e)), key=lambda e: e["end"], default=None)
    prior = SE._year_earlier(es, end, SE._span(cur)) if annual else None
    if prior and annual["end"] > prior["end"]:
        return "FY %s + YTD %s - prior YTD %s" % (show(annual), show(cur), show(prior))
    return "quarters " + ", ".join("%s %s" % (d, _fmt(float(v))) for d, v in list(xb.quarters(c).items())[:4])


def frames_values(cik, years, args):
    out = {}
    for y in years:
        for c in dc.FRAMES_CONCEPTS:
            data = _fetch(FRAMES_URL % (c, y), args.cache_dir, "frames_%s_CY%s.json" % (c, y), 168) or {}
            hit = [d for d in data.get("data") or [] if int(d.get("cik") or 0) == cik]
            out[(y, c)] = hit[0] if hit else None
    return out


def diag(sym, store, args, say=print):
    say("\n== %s ==" % sym)
    for owner, r in stored_rows(store, sym):
        keep = {k: r.get(k) for k in ("is_target", "period_end", "fy0_end", "frames_cy", "revenue_ttm", "growth_fy",
                                      "cagr3_fy", "gross_margin_fy", "op_margin_ttm", "addback", "ev_sales", "pe")}
        say("stored row in %s's panel: %s" % (owner, {k: _fmt(v) for k, v in keep.items()}))
    cf, cik = load_facts(sym, args)
    if not cf:
        say("no SEC companyfacts (CIK %s)" % cik)
        return
    as_of = args.as_of
    x = SE.extract(cf)
    xn, add = peers.normalized(x)
    pf = PP.point_fields(xn, as_of, PP.frame_tags(cf, as_of))
    xb = SE._Xbrl(cf.get("facts", cf))
    match = [c for c in SE.REV if xb.ttm(c) is not None and x.get("revenue") and abs(xb.ttm(c) - x["revenue"]) < 1]
    num = lambda v: _fmt(float(v)) if isinstance(v, (int, float)) else str(v)
    say("TTM: revenue %s ending %s (concept %s); EBIT %s after add-back %s (GAAP %s); net income %s"
        % (num(x.get("revenue")), x.get("latest_period_end"), ", ".join(match) or "none matches",
           num(xn.get("operating_income")), num(add or 0), num(x.get("operating_income")),
           num(x.get("net_income"))))
    for c in match[:1]:
        say("TTM revenue built from (%s): %s" % (c, ttm_parts(xb, c)))
    other = {c: num(xb.ttm(c)) for c in SE.REV if c not in match and xb.ttm(c) is not None}
    if other:
        say("other revenue concepts' TTM: %s" % other)
    for k in ("fy0", "fy1", "fy3"):
        leg = pf.get(k)
        say("%s: %s" % (k.upper(), {a: _fmt(b) for a, b in leg.items()} if leg else "none"))
    say("gross profit basis: %s" % pf.get("gp_basis"))
    say("fiscal years kept: %s" % [r[0] for r in (pf.get("fy_hist") or {}).get("revenue") or []])
    say("panel revenue series (newest first): %s" % [[d, _fmt(float(v))] for d, v in (x.get("revenue_fy_dated") or [])[:6]])
    say("scored revenue series (newest first): %s" % [[d, _fmt(float(v))] for d, v in (x.get("revenue_annuals_dated") or [])[:6]])
    ends = {leg["end"] for leg in (pf.get(k) for k in ("fy0", "fy1", "fy3")) if leg}
    to = x.get("latest_period_end")
    frm = (dt.date.fromisoformat(to) - dt.timedelta(days=380)).isoformat() if to else None
    say("raw SEC facts (group, concept, unit, start, end, filed, form, value):")
    for r in raw_facts(cf, ends, frm, to, as_of):
        say("  " + "  ".join(_fmt(float(v)) if i == 7 else str(v) for i, v in enumerate(r)))
    if args.no_frames:
        return
    years = dc.panel_years(dt.date.fromisoformat(as_of) if as_of else dt.date.today())
    say("SEC frames for CIK %s (CY, concept: value, end, accn):" % cik)
    for (y, c), d in sorted(frames_values(cik, years, args).items()):
        say("  CY%s %-55s %s" % (y, c, "-" if not d else "%s %s %s" % (_fmt(float(d["val"])), d.get("end"), d.get("accn"))))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("tickers", nargs="+")
    ap.add_argument("--store", default=os.path.join(config.DATA_DIR, "portfolio.json"))
    ap.add_argument("--facts-dir", help="read companyfacts from here instead of SEC")
    ap.add_argument("--as-of", help="point in time (default: the stored panels' as_of, else today)")
    ap.add_argument("--no-frames", action="store_true")
    ap.add_argument("--cache-dir", default=os.path.join(tempfile.gettempdir(), "panel_verify_cache"))
    args = ap.parse_args(argv)
    store = {}
    if os.path.exists(args.store):
        with open(args.store, encoding="utf-8") as fh:          # read only
            store = json.load(fh)
    if not args.as_of:
        args.as_of = next((r["peer_panel"]["as_of"] for r in (store.get("results") or {}).values()
                           if isinstance(r, dict) and isinstance(r.get("peer_panel"), dict)
                           and r["peer_panel"].get("as_of")), None) or dt.date.today().isoformat()
    needs_net = not args.facts_dir or not args.no_frames
    if needs_net and "your-email@example.com" in config.SEC_USER_AGENT:
        print("SEC_CONTACT_EMAIL is not set; SEC rejects the default User-Agent. Set it and rerun.")
        return 2
    os.makedirs(args.cache_dir, exist_ok=True)
    args.ciks = {}
    if not args.facts_dir or any(not os.path.exists(os.path.join(args.facts_dir, t.upper(), "sec_companyfacts.json"))
                                 for t in args.tickers):
        local = os.path.join(args.facts_dir or "", "company_tickers.json")
        if args.facts_dir and os.path.exists(local):        # an app cache folder carries the map
            with open(local, encoding="utf-8") as fh:
                args.ciks = {str(v["ticker"]).upper(): int(v["cik_str"]) for v in json.load(fh).values()}
        else:
            from tools.panel_verify import cik_map
            args.ciks = cik_map(args.cache_dir)
    for t in args.tickers:
        try:
            diag(t.upper(), store, args)
        except Exception as e:                       # one ticker never stops the others
            print("  diagnostic failed: %s: %s" % (type(e).__name__, e))
    return 0


if __name__ == "__main__":
    sys.exit(main())
