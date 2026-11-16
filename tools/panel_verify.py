"""Check the stored peer panel and moat trend against SEC, independently of the app.

    python -m tools.panel_verify                      every holding
    python -m tools.panel_verify MSFT ORCL            named tickers
    python -m tools.panel_verify --fails-only

Needs network (www.sec.gov, data.sec.gov). Reads data/portfolio.json and never writes it,
the cache folder under data/, or Drive: downloads are kept in the system temp folder.
For each holding it reads results[t]["peer_panel"] and results[t]["moat_trend"], fetches
SEC companyfacts for the target and every peer, recomputes each number with
verify/reference.py as of the panel's own as_of date, runs verify/timing.py, and prints
app value, reference value, absolute and relative difference, PASS or FAIL.
Tolerances: 0.5% money and multiples, 0.2 percentage points ratios, exact dates.
Exit code 1 when anything fails.
"""
import argparse
import json
import os
import sys
import tempfile
import time

import config
from verify import compare as C
from verify import reference as R
from verify import timing as T

FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK%010d.json"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FRAMES_URL = "https://data.sec.gov/api/xbrl/frames/us-gaap/%s/USD/CY%s.json"
FRAMES_CONCEPTS = ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax")
INDNAME_CACHE = os.path.join(config.CACHE_DIR, "damodaran_indname.json")
MIN_INTERVAL = 0.2
_last = [0.0]


def fetch(url, cache_dir, name, max_age_h=12):
    """GET JSON with the app's SEC User-Agent (never printed), cached in the temp folder."""
    path = os.path.join(cache_dir, name)
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < max_age_h * 3600:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    import requests
    wait = MIN_INTERVAL - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    r = requests.get(url, headers={"User-Agent": config.SEC_USER_AGENT}, timeout=60)
    _last[0] = time.time()
    if r.status_code == 404:
        return None
    r.raise_for_status()
    data = r.json()
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    return data


def cik_map(cache_dir):
    raw = fetch(TICKERS_URL, cache_dir, "company_tickers.json", max_age_h=168) or {}
    return {str(v["ticker"]).upper(): int(v["cik_str"]) for v in raw.values()}


def fmt(v):
    if v is None:
        return "-"
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, (int, float)):
        a = abs(v)
        return "%.1fM" % (v / 1e6) if a >= 1e6 else ("%.4f" % v if a < 1000 else "%.1f" % v)
    return str(v)[:44]


def line(status, scope, field, app="", ref="", diff="", rel="", note=""):
    print("%-4s %-28s %-44s %-14s %-14s %-11s %-8s %s" % (status, str(scope)[:28], str(field)[:44], app, ref, diff, rel, note))


def industry_revenue(panel, ciks, cache_dir):
    """{CY: {ticker: revenue}} for the Damodaran industry members, from SEC frames.
    Returns (data, target_key, why_not). Membership comes from the app's cached
    Damodaran list, read only."""
    sh = panel.get("industry_share") or {}
    years = [str(y) for y in sh.get("years") or []]
    if not years:
        return None, "no industry_share.years"
    if not os.path.exists(INDNAME_CACHE):
        return None, "no Damodaran list at data/cache/damodaran_indname.json"
    with open(INDNAME_CACHE, encoding="utf-8") as fh:
        rows = (json.load(fh) or {}).get("rows") or {}
    members = [t for t, v in rows.items() if v and v[0] == panel.get("industry")]
    if not members:
        return None, "no member of '%s' in the Damodaran list" % panel.get("industry")
    out = {}
    for y in years:
        by_cik = {}
        for c in FRAMES_CONCEPTS:
            data = fetch(FRAMES_URL % (c, y), cache_dir, "frames_%s_CY%s.json" % (c, y), max_age_h=168) or {}
            for d in data.get("data") or []:
                if isinstance(d.get("val"), (int, float)):
                    by_cik[int(d["cik"])] = max(by_cik.get(int(d["cik"]), 0), d["val"])
        out[y] = {t: by_cik[ciks[t.upper()]] for t in members if ciks.get(t.upper()) in by_cik}
    return out, "%d Damodaran members" % len(members)


def verify_ticker(t, store, ciks, cache_dir, args):
    res = store.get("results", {}).get(t) or {}
    panel, moat = res.get("peer_panel"), res.get("moat_trend")
    rows_out = []
    if not isinstance(panel, dict):
        return [C._res(t, "peer_panel", None, None, "FAIL", "results[%s] has no peer_panel" % t)], moat
    if not isinstance(moat, dict):
        rows_out.append(C._res(t, "moat_trend", None, None, "FAIL", "results[%s] has no moat_trend" % t))
        moat = None
    as_of = panel.get("as_of")
    table_rows = (panel.get("table") or {}).get("rows") or []
    if not table_rows:
        ok = bool(panel.get("flags"))
        return rows_out + [C._res(t, "table.rows", 0, None, "PASS" if ok else "FAIL",
                                  "no rows; flags: %s" % (panel.get("flags") or "NONE (E13)"))], moat
    facts, refs = {}, {}
    price = (store.get("facts", {}).get(t) or {}).get("price")
    for x in table_rows:
        s = str(x.get("symbol") or "").upper()
        cik = ciks.get(s) or ciks.get(s.replace(".", "-"))
        cf = fetch(FACTS_URL % cik, cache_dir, "companyfacts_%s.json" % s) if cik else None
        if not cf:
            rows_out.append(C._res(s, "sec_companyfacts", None, None, "FAIL" if x.get("is_target") else "SKIP",
                                   "no CIK or no companyfacts at SEC"))
            continue
        facts[x.get("symbol")] = cf
        refs[x.get("symbol")] = R.company_reference(cf, as_of, price=price if x.get("is_target") else None,
                                                    symbol=x.get("symbol"))
    members = target_key = None
    if not args.no_frames:
        members, why = industry_revenue(panel, ciks, cache_dir)
        rows_out.append(C._res(t, "industry revenue recompute", None, None, "PASS" if members else "SKIP", why))
        target_key = next((k for k in (members or {}).get(next(iter(members or {}), ""), {}) if k.upper() == t.upper()), t)
    cmp_rows, scale = C.compare_all(panel, moat, refs, members, target_key)
    rows_out += cmp_rows
    for g in T.run_all(panel, facts, refs, scale):
        rows_out.append({"scope": g.get("symbol", t), "field": g["check"], "app": None, "ref": None,
                         "abs": None, "rel": None, "status": g["status"], "note": g["detail"]})
    for s, r in refs.items():
        for f in r["flags"]:
            rows_out.append(C._res(s, "reference note", None, None, "SKIP", f))
    return rows_out, moat


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("tickers", nargs="*", help="default: every holding")
    ap.add_argument("--store", default=os.path.join(config.DATA_DIR, "portfolio.json"))
    ap.add_argument("--fails-only", action="store_true")
    ap.add_argument("--no-frames", action="store_true", help="skip the industry revenue recompute")
    ap.add_argument("--cache-dir", default=os.path.join(tempfile.gettempdir(), "panel_verify_cache"))
    args = ap.parse_args(argv)
    if "your-email@example.com" in config.SEC_USER_AGENT:
        print("SEC_CONTACT_EMAIL is not set; SEC rejects the default User-Agent. Set it and rerun.")
        return 2
    os.makedirs(args.cache_dir, exist_ok=True)
    with open(args.store, encoding="utf-8") as fh:        # read only
        store = json.load(fh)
    tickers = [x.upper() for x in args.tickers] or list(store.get("holdings") or {})
    ciks = cik_map(args.cache_dir)
    totals = {"PASS": 0, "FAIL": 0, "SKIP": 0}
    failed, moats = [], {}
    line("", "scope", "field", "app", "reference", "abs diff", "rel", "note")
    for t in tickers:
        print("\n== %s ==" % t)
        try:
            rows, moat = verify_ticker(t, store, ciks, args.cache_dir, args)
        except Exception as e:                            # a crash is a failure, never a pass
            rows, moat = [C._res(t, "verifier", None, None, "FAIL", "%s: %s" % (type(e).__name__, e))], None
        moats[t] = moat
        for x in rows:
            totals[x["status"]] += 1
            if x["status"] == "FAIL":
                failed.append((t, x["scope"], x["field"]))
            if args.fails_only and x["status"] != "FAIL":
                continue
            line(x["status"], x["scope"], x["field"], fmt(x["app"]), fmt(x["ref"]), fmt(x["abs"]),
                 "-" if x["rel"] is None else "%+.2f%%" % (x["rel"] * 100), x.get("note") or "")
    print("\n== thresholds across holdings ==")
    for x in C.threshold_consistency(moats):
        totals[x["status"]] += 1
        if x["status"] == "FAIL":
            failed.append(("*", x["scope"], x["field"]))
        line(x["status"], x["scope"], x["field"], fmt(x["app"]), fmt(x["ref"]), "", "", x["note"])
    print("\nPASS %(PASS)d  FAIL %(FAIL)d  SKIP %(SKIP)d" % totals)
    for f in failed[:40]:
        print("  FAIL %s / %s / %s" % f)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
