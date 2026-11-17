"""Offline cross-check: the builder's peer panel (real code path, fixture SEC facts)
against verify/reference.py on the same inputs.

    python3 -m tools.panel_xcheck [--all] [--json OUT]

Peers go through pipeline.peers.peer_point (real sec_edgar.extract, one-time add-back,
relative.point, peer_panel.point_fields); the target through normalize.build and
peers.panel_and_moat. Prices are fixed synthetic closes; the frames map is built from
the fixtures' own CY-tagged annual revenue facts. No network, no storage.

Every reference FAIL is then adjudicated with numbers: ALLOWED when a SPEC resolution
explains the whole gap (the residual is re-compared at the same tolerance), DEFECT
otherwise. Extra checks cover what the reference cannot see (X1 EV and P/E where its
debt ladder found nothing, X2 the share change on a constant member set, X3 the
target's price date, X4 restated fiscal years a point-in-time run would leak).
Exit 1 on any DEFECT."""
import datetime as dt
import json
import os
import re
import sys
from types import SimpleNamespace

import config
import store_sync
from domain import peer_panel as PP
from domain.engine import get_engine
from pipeline import normalize, peers
from verify import compare as C
from verify import reference as R
from verify import timing as T

for _n in dir(store_sync):
    _f = getattr(store_sync, _n)
    if not _n.startswith("_") and callable(_f) and getattr(_f, "__module__", None) == "store_sync":
        setattr(store_sync, _n, lambda *a, **k: (_ for _ in ()).throw(AssertionError("storage")))

TODAY = dt.date(2026, 5, 15)
AS_OF = TODAY.isoformat()
PRICE_DATE = "2026-05-14"
PRICES = {"MSFT": 420.0, "ORCL": 160.0, "AVGO": 330.0, "ABBV": 200.0, "NVO": 50.0}
SYMS = list(PRICES)
INDUSTRY = "Fixture industry"
CY = re.compile(r"^CY(\d{4})$")


def load(sym, name="sec_companyfacts"):
    p = os.path.join(config.FIXTURE_DIR, sym, name + ".json")
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None


CF = {s: load(s) for s in SYMS}
CIK = {s: str(int(CF[s]["cik"])).zfill(10) for s in SYMS}
BY_CIK = {v: k for k, v in CIK.items()}


def frames_map():
    """{CY: {cik: [revenue, end]}} from the frame tags, the larger concept per filer."""
    out = {}
    for s, cf in CF.items():
        ns = cf["facts"].get("us-gaap") or {}
        for c in PP.REVENUE_CONCEPTS:
            for e in ((ns.get(c) or {}).get("units") or {}).get("USD") or []:
                m = CY.match(str(e.get("frame") or ""))
                if m and e.get("filed", "") <= AS_OF:
                    row = out.setdefault(int(m.group(1)), {})
                    if e["val"] > row.get(int(CIK[s]), [0])[0]:
                        row[int(CIK[s])] = [e["val"], e["end"]]
    return out


def build(target):
    peers.resolve_cik = lambda t: (CIK.get(t.upper()), t) if t.upper() in CIK else (None, None)
    pts, excluded = [], []
    for s in SYMS:
        if s == target:
            continue
        p, why = peers.peer_point(s, TODAY, fetch_facts=lambda cik: CF[BY_CIK[cik]], complete=None,
                                  fetch_price=lambda sym, d: (PRICES[sym], PRICE_DATE, "fixed"))
        (pts.append(dict(p, source="damodaran")) if p else excluded.append({"symbol": s, "reason": why}))
    ff = normalize.build(target, CF[target], load(target, "profile"), load(target, "yahoo_quotesummary"))
    ff.price = PRICES[target]
    ff.provenance = dict(getattr(ff, "provenance", None) or {}, price="yahoo close %s (T-1)" % PRICE_DATE)
    ff.damodaran_industry = INDUSTRY
    val = get_engine().evaluate(ff, rf=0.045)
    model, ex2, why = peers.fit_points(list(pts), list(excluded), TODAY, getattr(ff, "sic", None))
    fit_note = None
    if model is None:
        # 3 usable fixture peers cannot carry the EV/Sales regression (needs 5), so the real
        # run would store no panel points. Store them as fit_points would have.
        fit_note = "fit_points returned no model (%s): panel_points injected" % why
        model = {"version": peers.MODEL_VERSION, "panel_points": [PP.compact(p) for p in pts]}
        ex2 = excluded
    rows = {s: [INDUSTRY, "0000", "United States" if s != "NVO" else "Denmark"] for s in SYMS}
    fr = frames_map()
    uni = peers.Universe(rows, {}, {}, as_of="2026-01", frames=fr)
    block = {"model": model, "excluded": ex2, "notes": [fit_note] if fit_note else []}
    panel, moat = peers.panel_and_moat(ff, val, block, uni, TODAY)
    return panel, moat, fr, pts, ff, val


def inst(sym, tag, date):
    """A balance-sheet tag at one date, the latest filing on or before AS_OF."""
    best = None
    for e in ((CF[sym]["facts"]["us-gaap"].get(tag) or {}).get("units") or {}).get("USD") or []:
        if e["end"] == date and "start" not in e and e.get("filed", "") <= AS_OF:
            best = e if best is None or e["filed"] >= best["filed"] else best
    return best["val"] if best else None


def hand_ev(sym, ref):
    """EV on the reference's balance date with the two legs SPEC R5 names and the
    reference ladder lacks: finance leases (debt) and minority interest. Debt falls back
    to notes payable where the reference found no debt tag (ORCL)."""
    d = ref.get("balance_date")
    g = lambda *tags: next((v for v in (inst(sym, t, d) for t in tags) if v is not None), None)
    debt = ref.get("total_debt")
    if debt is None:
        lt, st = g("LongTermNotesAndLoans", "LongTermDebtNoncurrent"), g("NotesPayableCurrent", "LongTermDebtCurrent")
        debt = None if lt is None else lt + (st or 0)
    cash = ref.get("cash")
    shares = ref.get("shares_diluted")
    if None in (debt, cash, shares, d):
        return None, None
    mcap = PRICES[sym] * shares
    fin = g("FinanceLeaseLiability") or 0
    ev = mcap + debt + fin + (ref.get("operating_leases") or 0) - cash + (g("MinorityInterest") or 0)
    return ev, mcap


def _close(a, b, tol=C.MONEY_REL):
    return a is not None and b is not None and abs(a - b) <= tol * abs(b)


def adjudicate(o, refs):
    """Turn each reference FAIL into ALLOWED (with the residual) or DEFECT, in place."""
    rows = {r["symbol"]: r for r in o["panel"]["table"]["rows"]}
    moat = o["moat"]
    for x in o["compare"]:
        if x["status"] != "FAIL":
            continue
        sym, f = x["scope"], x["field"]
        row, ref = rows.get(sym), refs.get(sym)
        verdict, why = "DEFECT", "no SPEC resolution explains the gap"
        if row and f == "op_margin_ttm":
            res = row["op_margin_ttm"] - (row["addback"] or 0) - ref["op_margin_ttm"]
            if row["addback"] and abs(res) * 100 <= C.RATIO_PP:
                verdict, why = "ALLOWED", "R2 add-back %.4f of revenue; app - addback - ref = %+.4fpp" % (row["addback"], res * 100)
        elif row and f.startswith("ebit_ttm implied"):
            gaap = x["app"] * (1 - row["addback"] / row["op_margin_ttm"])
            if row["addback"] and _close(gaap, x["ref"]):
                verdict, why = "ALLOWED", "R2 add-back; app EBIT less the add-back = %.0f vs %.0f" % (gaap, x["ref"])
        elif row and f in ("ev_sales", "ev_ebit"):
            ev, _m = hand_ev(sym, ref)
            base = ref["revenue_ttm"] if f == "ev_sales" else ref["op_income_ttm"] + (row["addback"] or 0) * ref["revenue_ttm"]
            want = ev / base if ev else None
            if _close(x["app"], want):
                verdict, why = "ALLOWED", ("R5 EV with finance leases and minority interest%s; hand value %.4f, rel %+.4f"
                                           % (", R2 add-back EBIT" if f == "ev_ebit" and row["addback"] else "", want, x["app"] / want - 1))
        elif sym == "table.rank3":
            m = PP.RANK_METRICS[f]
            vals = sorted((r[m] for r in rows.values() if r[m] is not None), reverse=True)
            want = {s: 1 + 3 * vals.index(r[m]) // len(vals) for s, r in rows.items() if r[m] is not None}
            if want == {s: r["rank3"][f] for s, r in rows.items()}:
                verdict, why = "ALLOWED", "R4 terciles over %d rows (floor(3i/n)+1) recomputed here: %s; the reference ranks ordinally" % (len(vals), want)
        elif sym == "moat.gm_gap_change" and x["app"] is None:
            n = sum(1 for s, r in rows.items() if not r["is_target"]
                    and refs[s].get("gross_margin_fy0") is not None and refs[s].get("gross_margin_fy3") is not None)
            if n < 3 and any(str(fl).startswith("S3:") for fl in moat["flags"]):
                verdict, why = "ALLOWED", "builder needs 3 peers with gross margin at FY0 and FY3 (not in SPEC), has %d, and says so in flags" % n
        elif sym == "moat.incremental_margin":
            t = refs[o["target"]]
            want = ((t["op_income_fy0"] - t["op_income_fy3"]) / (t["revenue_fy0"] - t["revenue_fy3"])
                    - t["op_income_fy0"] / t["revenue_fy0"])
            if abs(x["app"] - want) * 100 <= C.RATIO_PP:
                verdict, why = "ALLOWED", "R8 value = incremental - average = %.6f; the reference compared the incremental alone" % want
        elif f.startswith("flags name every row"):
            why = "SPEC flags: nothing missing is silent. " + x["note"]
        x["status"], x["note"] = verdict, why


def extra_checks(o, refs, fr):
    """Checks the reference does not make. Each {status, check, detail}."""
    out = []
    rows = {r["symbol"]: r for r in o["panel"]["table"]["rows"]}
    add = lambda ok, check, detail: out.append({"status": "PASS" if ok else "DEFECT", "check": check, "detail": detail})
    for s, r in rows.items():
        ref = refs[s]
        ev, mcap = hand_ev(s, ref)
        if ref.get("ev") is None and ev:
            add(_close(r["ev_sales"], ev / ref["revenue_ttm"]), "X1 %s ev_sales (hand ladder)" % s,
                "app %.4f hand %.4f" % (r["ev_sales"], ev / ref["revenue_ttm"]))
            pe = mcap / ref["net_income_ttm"] if (ref.get("net_income_ttm") or 0) > 0 else None
            add((r["pe"] is None and pe is None) or _close(r["pe"], pe), "X1 %s pe (hand)" % s, "app %s hand %s" % (r["pe"], pe))
    sh = o["panel"]["industry_share"]
    tc = int(CIK[o["target"]])
    if sh and sh["years"] and sh.get("share_change_pp") is not None:
        new, old = sh["years"][-1], sh["years"][-1] - PP.SHARE_LOOKBACK
        mem = [c for c in fr[new] if c in fr[old] and BY_CIK[str(c).zfill(10)] != "NVO"]
        gone = sorted(BY_CIK[str(c).zfill(10)] for c in fr[old] if c not in fr[new])
        a = fr[new][tc][0] / sum(fr[new][c][0] for c in mem)
        b = fr[old][tc][0] / sum(fr[old][c][0] for c in mem)
        from domain import moat_trend as MT
        d_same = MT.direction(a / b - 1, MT.SHARE_REL)
        s2 = next((s for s in o["moat"]["signals"] if s["name"] == "share_change"), None)
        add(not gone or (s2 and s2["direction"] == d_same), "X2 share change on a constant member set",
            "CY%d..CY%d: builder %+.4f (vote %s) over %d then %d filers; same %d filers both years %+.4f (vote %+d); absent from CY%d: %s"
            % (old, new, s2["value"] if s2 else float("nan"), s2 and s2["direction"], sh["n_with_revenue"][str(old)],
               sh["n_with_revenue"][str(new)], len(mem), a / b - 1, d_same, new, gone or "none"))
    t = rows[o["target"]]
    add(t["price_date"] == PRICE_DATE, "X3 target price_date is the close's own date",
        "row %s, close supplied for %s, as_of %s" % (t["price_date"], PRICE_DATE, AS_OF))
    return out


def restated(tol=C.MONEY_REL):
    """X4: fiscal-year values extract() serves today that differ from the first filing.
    A point-in-time run (PR5) needs filed <= as_of inside extract() to see the old one."""
    from sources import sec_edgar
    out = []
    for s in SYMS:
        if s == "NVO":
            continue
        x = sec_edgar.extract(CF[s])
        for key, tags in (("revenue_annuals_dated", PP.REVENUE_CONCEPTS),
                          ("operating_income_annuals_dated", ("OperatingIncomeLoss",)),
                          ("gross_profit_annuals_dated", ("GrossProfit",))):
            first = {}
            for tg in tags:
                for e in ((CF[s]["facts"]["us-gaap"].get(tg) or {}).get("units") or {}).get("USD") or []:
                    if e.get("start") and 350 <= (dt.date.fromisoformat(e["end"]) - dt.date.fromisoformat(e["start"])).days <= 380:
                        if e["end"] not in first or e["filed"] < first[e["end"]][0]:
                            first[e["end"]] = (e["filed"], e["val"])
            for end, v in x.get(key) or []:
                if end in first and abs(first[end][1] - v) > tol * abs(v):
                    out.append((s, key, end, first[end][0], first[end][1], v))
    return out


def run(target, verbose=False):
    panel, moat, fr, pts, ff, val = build(target)
    refs = {s: R.company_reference(CF[s], AS_OF, PRICES[s], s) for s in SYMS}
    members = {str(y): {c: v[0] for c, v in row.items() if BY_CIK[str(c).zfill(10)] != "NVO"}
               for y, row in fr.items() if y in (panel["industry_share"] or {}).get("years", [])}
    res, scale = C.compare_all(panel, moat, refs, members, int(CIK[target]))
    tim = T.run_all(panel, CF, refs, scale)
    o = {"target": target, "panel": panel, "moat": moat, "compare": res}
    adjudicate(o, refs)
    return {"target": target, "panel": panel, "moat": moat, "compare": res, "timing": tim,
            "extra": extra_checks(o, refs, fr), "excluded": [f for f in panel["flags"] if f.startswith("NVO")],
            "refs": {s: {k: v for k, v in r.items() if k not in ("used", "wrong")} for s, r in refs.items()},
            "wacc_pct": (val.key_metrics or {}).get("wacc_pct")}


def main(argv):
    out = [run(t) for t in ("MSFT", "ORCL", "AVGO", "ABBV")]
    if "--json" in argv:
        json.dump(out, open(argv[argv.index("--json") + 1], "w"), indent=1, default=str)
    bad = 0
    kinds = ("PASS", "ALLOWED", "DEFECT", "FAIL", "SKIP")
    cnt = lambda xs: {s: n for s in kinds for n in [sum(1 for x in xs if x["status"] == s)] if n}
    for o in out:
        c, t, e = o["compare"], o["timing"], o["extra"]
        print("== target %s  compare %s  timing %s  extra %s  label %s  %s" % (
            o["target"], cnt(c), cnt(t), cnt(e), o["moat"]["label"], o["excluded"]))
        for x in c:
            if x["status"] != "PASS" or "--all" in argv:
                print("  %-7s %-22s %-36s app=%s ref=%s | %s" % (
                    x["status"], x["scope"], x["field"][:36], _short(x["app"]), _short(x["ref"]), x["note"]))
        for x in t:
            if x["status"] != "PASS" or "--all" in argv:
                print("  %-7s T %-6s %-28s %s" % (x["status"], x.get("symbol", ""), x["check"], x["detail"]))
        for x in e:
            print("  %-7s %-44s %s" % (x["status"], x["check"], x["detail"]))
        bad += sum(1 for x in c + t + e if x["status"] in ("FAIL", "DEFECT"))
    r = restated()
    print("== X4 restated fiscal years (extract() has no filed <= as_of): %d values differ from the first filing by > 0.5%%" % len(r))
    for s, key, end, filed, was, now in r:
        print("  INFO    %-5s %-32s %s first filed %s %.3fB, served now %.3fB (%+.1f%%)" % (
            s, key, end, filed, was / 1e9, now / 1e9, (now / was - 1) * 100))
    return 1 if bad else 0


def _short(v):
    return "%.6g" % v if isinstance(v, float) else str(v)[:60]


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
