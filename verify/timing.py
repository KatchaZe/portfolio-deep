"""Timing checks for a PeerPanel / MoatTrend against the raw SEC facts behind it.

Every check returns a list of {"check", "status": PASS|FAIL|SKIP, "detail"}.
Imports only verify.reference. Letters match the verification brief:
(a) look-ahead, (b) period lengths, (c) FY0/FY3 three years apart by date,
(d) peer period and price alignment, (e) fiscal year -> frames calendar year,
(f) no TTM value mixed with an FY value.
"""
import datetime as dt

from verify import reference as R

PEER_PERIOD_DAYS = 183
PRICE_DAYS = 5
RATIO_TOL = 0.002           # 0.2 percentage points, as a fraction


def _r(check, ok, detail):
    return {"check": check, "status": "PASS" if ok else "FAIL", "detail": detail}


def _skip(check, detail):
    return {"check": check, "status": "SKIP", "detail": detail}


def check_lookahead(ref, as_of):
    """(a) E22: no fact behind a reference number was filed after as_of."""
    late = [(name, f["concept"], f["end"], f["filed"])
            for name, fs in ref["used"].items() for f in fs if f["filed"] > as_of]
    return [_r("a.lookahead.reference", not late,
               "all %d facts filed <= %s" % (sum(len(v) for v in ref["used"].values()), as_of)
               if not late else "filed after %s: %s" % (as_of, late[:3]))]


def first_filed(companyfacts, end, annual=False):
    """Earliest filing date of any revenue fact whose period ends on `end`."""
    co = R.Company(companyfacts)
    best = None
    for c in R.REV.get(co.taxonomy, []):
        for f in co._facts(c, co.currency):
            if f.get("end") != end or "start" not in f:
                continue
            if annual and not R.FY_DAYS[0] <= R.span_days(f["start"], f["end"]) <= R.FY_DAYS[1]:
                continue
            if best is None or f["filed"] < best:
                best = f["filed"]
    return best


def check_row_lookahead(row, companyfacts, as_of):
    """(a) E22 on the app's own row: the periods it names were public by as_of."""
    out = []
    for field, annual in (("period_end", False), ("fy0_end", True)):
        end = row.get(field)
        if not end:
            out.append(_skip("a.lookahead." + field, "row has no " + field))
            continue
        filed = first_filed(companyfacts, end, annual)
        if filed is None:
            out.append(_r("a.lookahead." + field, False,
                          "%s %s: no %srevenue fact ends on that date" % (field, end, "annual " if annual else "")))
        else:
            out.append(_r("a.lookahead." + field, filed <= as_of,
                          "%s %s first filed %s, as_of %s" % (field, end, filed, as_of)))
    return out


def check_durations(ref):
    """(b) FY values are ~365-day facts; TTM is four contiguous quarters of ~12 months."""
    out = []
    for name in ("revenue_fy0", "revenue_fy1", "revenue_fy3", "op_income_fy0", "op_income_fy3",
                 "gross_profit_fy0", "gross_profit_fy3"):
        for f in ref["used"].get(name, []):
            days = R.span_days(f["start"], f["end"])
            out.append(_r("b.fy_duration." + name, R.FY_DAYS[0] <= days <= R.FY_DAYS[1],
                          "%s %s..%s = %d days" % (f["concept"], f["start"], f["end"], days)))
    w = ref.get("ttm_window")
    if not w:
        out.append(_skip("b.ttm_span", "no TTM (%s)" % ref.get("ttm_basis")))
        return out
    days = R.span_days(*w)
    out.append(_r("b.ttm_span", R.TTM_DAYS[0] <= days <= R.TTM_DAYS[1],
                  "TTM %s..%s = %d days (%s)" % (w[0], w[1], days, ref["ttm_basis"])))
    qs = ref.get("ttm_quarters")
    if qs:
        gaps = [(R.D(b[0]) - R.D(a[1])).days for a, b in zip(qs, qs[1:])]
        lens = [R.span_days(*q) for q in qs]
        ok = len(qs) == 4 and all(g == 1 for g in gaps) and all(R.Q_DAYS[0] <= n <= R.Q_DAYS[1] for n in lens)
        out.append(_r("b.ttm_four_quarters", ok, "quarter lengths %s, gaps %s" % (lens, gaps)))
    return out


def check_fy_gap(ref):
    """(c) E2: FY0 and FY3 are three fiscal years apart by date."""
    a, b = ref.get("fy3_end"), ref.get("fy0_end")
    if not a or not b:
        return [_skip("c.fy0_fy3_gap", "fy0 %s, fy3 %s" % (b, a))]
    days = (R.D(b) - R.D(a)).days
    return [_r("c.fy0_fy3_gap", abs(days - 1096) <= R.YEAR_TOL_DAYS + 1,
               "%s -> %s = %d days (3 years = 1095-1096)" % (a, b, days))]


def check_peer_alignment(panel):
    """(d) every peer's period_end within 6 months of the target's, every price_date
    within 5 days of the target's and none after as_of."""
    rows = (panel.get("table") or {}).get("rows") or []
    target = next((x for x in rows if x.get("is_target")), None)
    if not target:
        return [_r("d.target_row", False, "no row with is_target")]
    out = [_r("d.one_target", sum(1 for x in rows if x.get("is_target")) == 1, "exactly one target row")]
    as_of = panel.get("as_of")
    for x in rows:
        s = x.get("symbol")
        for field, limit in (("period_end", PEER_PERIOD_DAYS), ("price_date", PRICE_DAYS)):
            v, tv = x.get(field), target.get(field)
            if not v or not tv:
                out.append(_r("d.%s.%s" % (field, s), False, "missing %s (row %s, target %s)" % (field, v, tv)))
                continue
            gap = abs((R.D(v) - R.D(tv)).days)
            out.append(_r("d.%s.%s" % (field, s), gap <= limit and (not as_of or v <= as_of),
                          "%s %s vs target %s: %d days (limit %d), as_of %s" % (field, v, tv, gap, limit, as_of)))
        if x.get("fy0_end") and x.get("period_end"):
            lag = (R.D(x["period_end"]) - R.D(x["fy0_end"])).days
            out.append(_r("d.fy0_not_stale.%s" % s, 0 <= lag <= 380,
                          "period_end is %d days after fy0_end %s" % (lag, x["fy0_end"])))
    return out


def check_frames(companyfacts, ref=None, symbol=""):
    """(e) E21: the calendar year reference.frame_cy gives each fiscal year equals the
    `frame` tag SEC itself put on that fact."""
    co = R.Company(companyfacts)
    ann = R.annuals(co.durations(R.REV.get(co.taxonomy, []), pick="max"))
    tagged = [(e, f) for e, f in sorted(ann.items()) if f.get("frame")]
    if not tagged:
        return [_skip("e.frames_cy." + symbol, "no annual revenue fact carries a frame tag")]
    bad = [(e, f["frame"], R.frame_cy(f["start"], f["end"])) for e, f in tagged
           if f["frame"] != "CY%d" % R.frame_cy(f["start"], f["end"])]
    newest_e, newest = tagged[-1]
    majority = _majority_year(newest["start"], newest["end"])
    out = [_r("e.frames_cy." + symbol, not bad,
              "%d fiscal years agree with SEC tags; newest FY end %s -> %s%s"
              % (len(tagged), newest_e, newest["frame"],
                 "" if "CY%d" % majority == newest["frame"] else
                 " (NOTE: the calendar year holding most days is %d, SEC says otherwise)" % majority)
              if not bad else "disagree: %s" % bad[:3])]
    return out


def _majority_year(start, end):
    s, e = R.D(start), R.D(end)
    if s.year == e.year:
        return e.year
    first = (dt.date(s.year, 12, 31) - s).days + 1
    return s.year if first > (e - dt.date(e.year, 1, 1)).days + 1 else e.year


def check_share_year(panel, ref):
    """(e) the newest industry_share year is a calendar year the target's fiscal year maps to
    and, at as_of, that fiscal year had been filed."""
    sh = panel.get("industry_share") or {}
    years = [str(y) for y in sh.get("years") or []]
    if not years:
        return [_skip("e.share_years", "no industry_share.years")]
    out = [_r("e.share_years_consecutive",
              [int(y) for y in years] == list(range(int(years[0]), int(years[0]) + len(years))) and len(years) == 5,
              "years %s (five consecutive, oldest first)" % years)]
    for y in years:
        if (sh.get("target_share") or {}).get(y, (sh.get("target_share") or {}).get(int(y))) is None:
            continue
        out.append(_r("e.share_year_has_fy.%s" % y, y in ref["frames"],
                      "CY%s <- fiscal year ending %s" % (y, ref["frames"][y]["fy_end"]) if y in ref["frames"]
                      else "target has a share for CY%s but no fiscal year maps to it by as_of" % y))
    return out


def check_no_mixing(row, ref, scale=1.0):
    """(f) E1/E2/E7: a ratio that matches a mixed-clock or by-position variant better
    than the pure one is flagged. `scale` is 100 when the panel stores percent."""
    out = []
    for field, variants in ref["wrong"].items():
        app, pure = row.get(field), ref.get(field)
        if app is None or pure is None:
            out.append(_skip("f.mixing." + field, "app %s, reference %s" % (app, pure)))
            continue
        app = app / scale
        if field == "op_margin_ttm":
            app -= row.get("addback") or 0      # R2: the stored add-back explains that gap
        hit = [name for name, v in variants.items()
               if v is not None and abs(v - pure) > 2 * RATIO_TOL and abs(app - v) <= RATIO_TOL < abs(app - pure)]
        out.append(_r("f.mixing." + field, not hit,
                      "app %.4f, pure %.4f" % (app, pure) if not hit
                      else "app %.4f equals %s (pure value %.4f)" % (app, hit[0], pure)))
    return out


def run_all(panel, facts_by_symbol, refs_by_symbol, scale=1.0):
    """All timing checks for one panel. facts/refs are keyed by row symbol."""
    as_of = panel.get("as_of")
    out = check_peer_alignment(panel)
    for row in (panel.get("table") or {}).get("rows") or []:
        s = row.get("symbol")
        cf, ref = facts_by_symbol.get(s), refs_by_symbol.get(s)
        if not cf or not ref:
            out.append(_skip("facts." + str(s), "no SEC facts for this row"))
            continue
        group = (check_lookahead(ref, as_of) + check_row_lookahead(row, cf, as_of) + check_durations(ref)
                 + check_fy_gap(ref) + check_frames(cf, ref, s) + check_no_mixing(row, ref, scale))
        if row.get("is_target"):
            group += check_share_year(panel, ref)
        for g in group:
            g["symbol"] = s
        out += group
    return out
