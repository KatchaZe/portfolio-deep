"""Compare a PeerPanel / MoatTrend JSON with the independent reference.

No import from the app. Each result is
{"scope", "field", "app", "ref", "abs", "rel", "status": PASS|FAIL|SKIP, "note"}.
Tolerances: 0.5% on money and multiples, 0.2 percentage points on ratios, exact dates.
"""
import statistics

from verify import reference as R

MONEY_REL = 0.005
RATIO_PP = 0.2
LABELS = ("improving", "stable", "eroding", None)


def _res(scope, field, app, ref, status, note=""):
    a = (app - ref) if isinstance(app, (int, float)) and isinstance(ref, (int, float)) else None
    rel = (a / abs(ref)) if a is not None and ref else None
    return {"scope": scope, "field": field, "app": app, "ref": ref, "abs": a, "rel": rel,
            "status": status, "note": note}


def cmp_money(scope, field, app, ref, note=""):
    if app is None and ref is None:
        return _res(scope, field, app, ref, "PASS", "both null")
    if app is None or ref is None:
        return _res(scope, field, app, ref, "FAIL", "one side null. " + note)
    ok = abs(app - ref) <= MONEY_REL * abs(ref)
    return _res(scope, field, app, ref, "PASS" if ok else "FAIL", note)


def cmp_ratio(scope, field, app, ref, scale, note=""):
    """app is in panel units (fraction when scale=1, percent when scale=100)."""
    if app is None and ref is None:
        return _res(scope, field, app, ref, "PASS", "both null")
    if app is None or ref is None:
        return _res(scope, field, app, ref, "FAIL", "one side null. " + note)
    ref_u = ref * scale
    ok = abs(app - ref_u) * (100.0 / scale) <= RATIO_PP
    return _res(scope, field, app, ref_u, "PASS" if ok else "FAIL", note)


def cmp_exact(scope, field, app, ref, note=""):
    return _res(scope, field, app, ref, "PASS" if app == ref else "FAIL", note)


def detect_scale(rows):
    """1.0 when ratios are fractions, 100.0 when percent. Margins decide: a gross or
    operating margin above 1.5 in absolute value cannot be a fraction."""
    vals = [abs(x[m]) for x in rows for m in ("gross_margin_fy", "op_margin_ttm")
            if isinstance(x.get(m), (int, float))]
    return 100.0 if vals and statistics.median(vals) > 1.5 else 1.0


def compare_rows(panel, refs):
    rows = (panel.get("table") or {}).get("rows") or []
    scale = detect_scale(rows)
    out = [_res("panel", "ratio_unit", "percent" if scale == 100 else "fraction", None, "PASS",
                "detected from the margins; SPEC does not state the unit")]
    for x in rows:
        s = x.get("symbol")
        ref = refs.get(s)
        if not ref:
            out.append(_res(s, "row", None, None, "SKIP", "no SEC facts for this symbol"))
            continue
        if ref["excluded"]:
            out.append(_res(s, "row", "present", "excluded", "FAIL", "E8: " + ref["excluded"]))
            continue
        out.append(cmp_exact(s, "period_end", x.get("period_end"), ref["period_end"]))
        out.append(cmp_exact(s, "fy0_end", x.get("fy0_end"), ref["fy0_end"]))
        out.append(cmp_money(s, "revenue_ttm", x.get("revenue_ttm"), ref["revenue_ttm"], "TTM"))
        for m in ("growth_fy", "cagr3_fy", "gross_margin_fy"):
            out.append(cmp_ratio(s, m, x.get(m), ref[m], scale, "FY"))
        r = cmp_ratio(s, "op_margin_ttm", x.get("op_margin_ttm"), ref["op_margin_ttm"], scale, "TTM, GAAP")
        if r["status"] == "FAIL" and r["abs"] is not None and r["abs"] > 0:
            r["note"] += ". App is higher: consistent with a one-time add-back the reference does not apply"
        out.append(r)
        out += _multiples(s, x, ref)
    return out, scale


def _multiples(s, x, ref):
    out = []
    es, ee, pe = x.get("ev_sales"), x.get("ev_ebit"), x.get("pe")
    if ref.get("ev_sales") is not None:
        for m, v in (("ev_sales", es), ("ev_ebit", ee), ("pe", pe)):
            out.append(cmp_money(s, m, v, ref[m], "price supplied; debt/cash ladder is the reference's own"))
        return out
    # No price for this symbol: check what the three multiples imply together.
    rev, oi, ni = ref["revenue_ttm"], ref["op_income_ttm"], ref["net_income_ttm"]
    if es is None or rev is None:
        out.append(_res(s, "ev_sales", es, None, "SKIP", "no price and no EV/Sales to back out"))
        return out
    ev = es * rev
    if ee is not None:
        out.append(cmp_money(s, "ebit_ttm implied by ev_sales/ev_ebit", ev / ee, oi,
                             "EV/Sales and EV/EBIT must share one EV; a gap is an add-back or a clock mix"))
    elif oi is not None and oi > 0:
        out.append(_res(s, "ev_ebit", None, None, "FAIL", "null although TTM operating income is positive"))
    if pe is not None and ni and ref["net_debt_and_leases"] is not None:
        out.append(cmp_money(s, "net debt + leases implied by ev_sales and pe", ev - pe * ni,
                             ref["net_debt_and_leases"],
                             "EV - market cap; tolerance 0.5%% of EV = %.0f" % (MONEY_REL * ev)))
        out[-1]["status"] = "PASS" if abs(out[-1]["abs"]) <= MONEY_REL * ev else "FAIL"
    elif pe is None and ni is not None and ni > 0:
        out.append(_res(s, "pe", None, None, "FAIL", "null although TTM net income is positive"))
    if pe is not None and ni is not None and ni <= 0:
        out.append(_res(s, "pe", pe, None, "FAIL", "P/E reported on a TTM loss"))
    return out


def compare_table(panel, scale):
    """stats, target_vs_median, rank3, outliers recomputed from the panel's own rows, so
    a row error is not counted twice."""
    t = panel.get("table") or {}
    rows = t.get("rows") or []
    ref = R.table_reference(rows)
    out = []
    app_stats = t.get("stats") or {}
    n_all = ref["stats"]["revenue_ttm"]["n"]
    n_app = (app_stats.get("revenue_ttm") or {}).get("n")
    basis = "" if n_app == n_all else "_peers_only"
    out.append(_res("table", "stats_basis", "all rows" if not basis else "peers only", None, "PASS",
                    "inferred from stats.revenue_ttm.n=%s; SPEC does not say" % n_app))
    for m in R.METRICS:
        a, b = app_stats.get(m), ref["stats" + basis][m]
        if a is None:
            out.append(_res("table.stats", m, None, b["median"], "FAIL", "metric missing from stats"))
            continue
        out.append(cmp_exact("table.stats." + m, "n", a.get("n"), b["n"]))
        for k in ("max", "p75", "median", "p25", "min"):
            note = "linear-interpolated quantile" if k in ("p75", "p25") else ""
            if m in R.RATIO_METRICS:
                out.append(cmp_ratio("table.stats." + m, k, a.get(k), None if b[k] is None else b[k] / scale, scale, note))
            else:
                out.append(cmp_money("table.stats." + m, k, a.get(k), b[k], note))
        tv = (t.get("target_vs_median") or {}).get(m)
        want = ref["target_vs_median" + basis][m]
        if m in R.RATIO_METRICS and want is not None:
            want_pp = want * (100.0 / scale)
            ok = tv is not None and abs(tv - want_pp) <= RATIO_PP
            out.append(_res("table.target_vs_median", m, tv, want_pp, "PASS" if ok else "FAIL", "percentage points"))
        else:
            out.append(cmp_money("table.target_vs_median", m, tv, want, "target - median"))
    out += _ranks(rows)
    for x in rows:
        want = sorted(m for m in R.METRICS if R.iqr_outlier(x.get(m), ref["stats" + basis][m]))
        got = sorted(x.get("outlier") or [])
        out.append(_res(x.get("symbol"), "outlier", got, want, "PASS" if got == want else "FAIL",
                        "Tukey fence, 1.5 x IQR assumed"))
    return out


RANK_CANDIDATES = {"scale": ("revenue_ttm",), "growth": ("cagr3_fy", "growth_fy"),
                   "margin": ("op_margin_ttm", "gross_margin_fy")}


def _ranks(rows):
    out = []
    for key, metrics in RANK_CANDIDATES.items():
        got = {x.get("symbol"): (x.get("rank3") or {}).get(key) for x in rows}
        match, tried = None, {}
        for m in metrics:
            ordered = sorted((x for x in rows if x.get(m) is not None), key=lambda x: -x[m])
            want = {x.get("symbol"): None for x in rows}
            for i, x in enumerate(ordered[:3]):
                want[x.get("symbol")] = i + 1
            tried[m] = want
            if want == got:
                match = m
                break
        bad = [v for v in got.values() if v not in (None, 1, 2, 3)]
        out.append(_res("table.rank3", key, {k: v for k, v in got.items() if v},
                        {k: v for k, v in tried[match or metrics[0]].items() if v},
                        "PASS" if match and not bad else "FAIL",
                        "top three by %s, largest first" % (match or " or ".join(metrics))))
    return out


def _get(d, y):
    d = d or {}
    return d.get(str(y), d.get(int(y)) if str(y).isdigit() else None)


def compare_share(panel, target_ref=None, member_revenue_by_year=None, target_key=None, peer_refs=()):
    sh = panel.get("industry_share")
    flags = panel.get("flags") or []
    if not sh:
        return [_res("industry_share", "present", None, None, "PASS" if flags else "FAIL",
                     "absent; flags say why" if flags else "absent with no flag (E13)")]
    out = []
    years = [str(y) for y in sh.get("years") or []]
    out.append(cmp_exact("industry_share", "group", sh.get("group"), "damodaran_industry_us_sec_filers"))
    out.append(_res("industry_share", "years", years, None,
                    "PASS" if years == sorted(years) and len(years) == 5 else "FAIL", "five CY, oldest first"))
    shares = [v for v in (_get(sh.get("target_share"), y) for y in years) if v is not None]
    sscale = 100.0 if shares and max(shares) > 1 else 1.0
    n_mem = sh.get("n_members")
    for y in years:
        ts, t5, hhi = (_get(sh.get(k), y) for k in ("target_share", "top5_share", "hhi"))
        nrev = _get(sh.get("n_with_revenue"), y)
        cov = nrev / n_mem if nrev is not None and n_mem else None
        if cov is not None and cov < 0.70:
            ok = ts is None and t5 is None and any(y in f or "coverage" in f.lower() for f in flags)
            out.append(_res("industry_share.%s" % y, "coverage<70% nulls the year", ts, None,
                            "PASS" if ok else "FAIL", "coverage %.0f%%" % (cov * 100)))
            continue
        if ts is not None and t5 is not None:
            out.append(_res("industry_share.%s" % y, "0 < target_share <= top5_share <= 1",
                            ts, t5, "PASS" if 0 < ts <= t5 + 1e-9 and t5 <= sscale + 1e-9 else "FAIL"))
        if hhi is not None and t5 is not None:
            hs = hhi if hhi <= 1 else hhi / 10000.0
            f5 = t5 / sscale
            ok = f5 ** 2 / 5 - 1e-9 <= hs <= f5 ** 2 + (1 - f5) * (f5 / 5) + 1e-9 if f5 < 1 else hs <= 1
            out.append(_res("industry_share.%s" % y, "hhi within the bounds top5_share allows", hs,
                            None, "PASS" if ok else "FAIL",
                            "between top5^2/5 and top5^2 + (1-top5)*top5/5"))
        if ts and target_ref and y in target_ref["frames"]:
            total = target_ref["frames"][y]["revenue"] / (ts / sscale)
            known = target_ref["frames"][y]["revenue"] + sum(
                p["frames"][y]["revenue"] for p in peer_refs if not p["excluded"] and y in p["frames"])
            out.append(_res("industry_share.%s" % y, "implied industry revenue >= target + panel peers",
                            total, known, "PASS" if total >= known * (1 - MONEY_REL) else "FAIL",
                            "target CY revenue / target_share; fiscal year %s" % target_ref["frames"][y]["fy_end"]))
    for name, src in (("share_change_pp", "target_share"), ("top5_change_pp", "top5_share")):
        new = _get(sh.get(src), years[-1]) if years else None
        old = _get(sh.get(src), int(years[-1]) - 3) if years else None
        want = (new - old) * (100.0 / sscale) if new is not None and old is not None else None
        got = sh.get(name)
        ok = (got is None and want is None) or (got is not None and want is not None and abs(got - want) <= 0.01)
        out.append(_res("industry_share", name, got, want, "PASS" if ok else "FAIL",
                        "newest minus 3 calendar years earlier, null if either is missing"))
    if member_revenue_by_year:
        ref = R.share_reference(member_revenue_by_year, target_key, n_mem)
        for y in years:
            for k in ("target_share", "top5_share"):
                out.append(cmp_ratio("industry_share.%s" % y, k + " (recomputed from frames)",
                                     _get(sh.get(k), y), ref[k].get(y), sscale))
            h = _get(sh.get("hhi"), y)
            hr = ref["hhi"].get(y)
            out.append(cmp_money("industry_share.%s" % y, "hhi (recomputed from frames)",
                                 h if h is None or h <= 1 else h / 10000.0, hr))
            out.append(cmp_exact("industry_share.%s" % y, "n_with_revenue",
                                 _get(sh.get("n_with_revenue"), y), ref["n_with_revenue"].get(y)))
    return out


def _sig(moat, name):
    return next((s for s in moat.get("signals") or [] if s.get("name") == name), None)


def _dir(s):
    try:
        return int(str(s.get("direction")).replace("+", ""))
    except (TypeError, ValueError):
        return None


def compare_moat(moat, panel=None, target_ref=None, peer_refs=None):
    out = []
    sigs = moat.get("signals") or []
    live = [s for s in sigs if s.get("value") is not None and _dir(s) is not None]
    out.append(cmp_exact("moat", "n_signals", moat.get("n_signals"), len(live),
                         "signals with a value; a missing signal is not a 0 (E5)"))
    for s in sigs:
        if s.get("value") is None and _dir(s) == 0:
            out.append(_res("moat." + str(s.get("name")), "direction", 0, None, "FAIL",
                            "E5: missing signal carries direction 0"))
    total = sum(_dir(s) for s in live)
    want = None if len(live) < 2 else "improving" if total >= 2 else "eroding" if total <= -2 else "stable"
    out.append(cmp_exact("moat", "label", moat.get("label"), want, "sum of directions = %+d" % total))
    out.append(cmp_exact("moat", "thresholds", moat.get("thresholds"), "provisional"))
    for s in live:
        v, d = s["value"], _dir(s)
        ok = d in (-1, 0, 1) and not (d == 1 and v <= 0) and not (d == -1 and v >= 0)
        out.append(_res("moat." + s["name"], "direction sign", d, None, "PASS" if ok else "FAIL",
                        "value %s" % v))
    for h in moat.get("source_hints") or []:
        ok = "consistent with" in str(h.get("evidence", "")).lower() and h.get("strength") in ("weak", "moderate")
        out.append(_res("moat.hint." + str(h.get("type")), "wording", h.get("evidence"), None,
                        "PASS" if ok else "FAIL", 'must say "consistent with"; strength weak|moderate'))
    if target_ref is not None:
        ref = R.moat_reference(target_ref, peer_refs or [])
        fy = "FY%s..FY%s" % ((ref["window_fy"][0] or "?")[:4], (ref["window_fy"][1] or "?")[:4])
        s3, s4 = _sig(moat, "gm_gap_change"), _sig(moat, "incremental_margin")
        out.append(_pp("moat.gm_gap_change", s3, ref["gm_gap_change"], "peers = rows with facts, target excluded"))
        inc = ref["incremental_margin"]
        if s4 and s4.get("value") is not None and inc:
            hit = min((inc["value"], inc["incremental"]), key=lambda w: abs(s4["value"] - w))
            out.append(_pp("moat.incremental_margin", s4, hit,
                           "incremental %.2f%%, average %.2f%%; value may be the gap or the incremental"
                           % (inc["incremental"], inc["average"])))
        else:
            out.append(_pp("moat.incremental_margin", s4, inc and inc["value"], "only when revenue grew"))
        for name, s in (("gm_gap_change", s3), ("incremental_margin", s4)):
            if s and s.get("value") is not None:
                out.append(cmp_exact("moat." + name, "window", s.get("window"), fy, "by fiscal-year date"))
    s2 = _sig(moat, "share_change")
    sh = (panel or {}).get("industry_share") or {}
    if s2 and s2.get("value") is not None:
        years = [str(y) for y in sh.get("years") or []]
        new = _get(sh.get("target_share"), years[-1]) if years else None
        old = _get(sh.get("target_share"), int(years[-1]) - 3) if years else None
        if new is None or old is None or old <= 0:
            out.append(_res("moat.share_change", "value", s2["value"], None, "FAIL",
                            "E6: a share change reported on a missing or non-positive base"))
        else:
            rel = new / old - 1
            ok = min(abs(s2["value"] - rel), abs(s2["value"] - rel * 100)) <= 0.002 * (100 if abs(s2["value"]) > 1.5 else 1) + 1e-9 \
                or abs(s2["value"] - rel * 100) <= RATIO_PP
            out.append(_res("moat.share_change", "value", s2["value"], rel, "PASS" if ok else "FAIL",
                            "relative change = share_newest/share_3y_earlier - 1 (fraction or percent)"))
            out.append(cmp_exact("moat.share_change", "window", s2.get("window"),
                                 "CY%s..CY%s" % (int(years[-1]) - 3, years[-1])))
    elif s2 is None and sh.get("share_change_pp") is not None:
        out.append(_res("moat.share_change", "present", None, sh.get("share_change_pp"), "FAIL",
                        "panel has share_change_pp but the signal is absent"))
    return out


def _pp(scope, sig, want, note):
    got = sig.get("value") if sig else None
    if got is None and want is None:
        return _res(scope, "value", got, want, "PASS", "both absent. " + note)
    if got is None or want is None:
        return _res(scope, "value", got, want, "FAIL", "one side absent. " + note)
    ok = min(abs(got - want), abs(got * 100 - want)) <= RATIO_PP
    return _res(scope, "value", got, want, "PASS" if ok else "FAIL", "pp. " + note)


def threshold_consistency(moats):
    """E6 across holdings: for each signal one symmetric threshold T must explain every
    direction: +1 above T, -1 below -T, 0 between. Needs no knowledge of T."""
    by = {}
    for sym, m in moats.items():
        for s in (m or {}).get("signals") or []:
            if s.get("value") is not None and _dir(s) is not None:
                by.setdefault(s["name"], []).append((abs(s["value"]), _dir(s), s["value"], sym))
    out = []
    for name, xs in sorted(by.items()):
        flat = [a for a, d, _v, _s in xs if d == 0]
        moved = [a for a, d, _v, _s in xs if d != 0]
        ok = not flat or not moved or max(flat) <= min(moved)
        out.append(_res("moat.thresholds", name, max(flat) if flat else None, min(moved) if moved else None,
                        "PASS" if ok else "FAIL",
                        "largest |value| called 0 must not exceed the smallest called +1/-1 (n=%d)" % len(xs)))
    return out


def compare_flags(panel):
    rows = (panel.get("table") or {}).get("rows") or []
    nulls = [(x.get("symbol"), m) for x in rows for m in R.METRICS if x.get(m) is None]
    flags = panel.get("flags") or []
    silent = [(s, m) for s, m in nulls if not any(str(s) in f for f in flags)]
    return [_res("panel", "flags name every row with a null metric", len(nulls) - len(silent), len(nulls),
                 "PASS" if not silent else "FAIL", "silent: %s" % silent[:6] if silent else "")]


def compare_all(panel, moat, refs, member_revenue_by_year=None, target_key=None):
    rows = (panel.get("table") or {}).get("rows") or []
    target = next((x.get("symbol") for x in rows if x.get("is_target")), None)
    tref = refs.get(target)
    peers = [refs[x["symbol"]] for x in rows if not x.get("is_target") and x.get("symbol") in refs]
    out, scale = compare_rows(panel, refs)
    out += compare_table(panel, scale)
    out += compare_share(panel, tref, member_revenue_by_year, target_key, peers)
    out += compare_flags(panel)
    if moat is not None:
        out += compare_moat(moat, panel, tref, peers)
    return out, scale
