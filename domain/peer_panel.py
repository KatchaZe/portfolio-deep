"""
peer_panel — the peer table and the industry revenue share beside the relative
valuation (docs/peer-panel/SPEC.md). Display only: nothing here feeds a score, a fair
value or the recommendation (invariant PP1 in tests/test_invariants.py).

Pure: no network, no clock of its own. Every number sits on one declared clock
(domain/contracts.PANEL_CLOCK):
  TTM  revenue, EBIT, net income and the multiples on them (EV and market cap at T-1)
  FY   growth, 3-year CAGR and gross margin, from each company's own fiscal years,
       joined by DATE through trend._window (never by list position)
  CY   industry share, from the SEC frames calendar years
"""
import datetime as dt
import re
import statistics

from domain import trend

FY_GAP = (330, 400)              # one fiscal year apart, in days (relative.FY_GAP)
FY3_GAP = (3 * 330, 3 * 400)     # three fiscal years apart


def _d(s):
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def _days(a, b):
    da, db = _d(a), _d(b)
    return (da - db).days if da and db else None


REVENUE_CONCEPTS = ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax")
_CY = re.compile(r"^CY(\d{4})$")
CY_SHIFT_DAYS = 180


def frame_tags(companyfacts, as_of=None):
    """{FY end: calendar year} from SEC's own `frame` tag on the annual revenue facts
    (CY2025, not CY2025Q4I). Per end date the latest filing with filed <= as_of wins."""
    ns = (companyfacts or {}).get("facts", {}).get("us-gaap") or {}
    best = {}
    for concept in REVENUE_CONCEPTS:
        for e in ((ns.get(concept) or {}).get("units") or {}).get("USD") or []:
            m = _CY.match(str(e.get("frame") or ""))
            filed = str(e.get("filed") or "")
            if not m or not e.get("end") or (as_of and filed > str(as_of)[:10]):
                continue
            if e["end"] not in best or filed > best[e["end"]][0]:
                best[e["end"]] = (filed, int(m.group(1)))
    return {end: cy for end, (_f, cy) in best.items()}


def frames_cy(end, tags=None):
    """The calendar year SEC frames put a fiscal year in: SEC's own tag when there is
    one, else the calendar year of (FY end - 180 days). Not "most days": SEC tags MSFT's
    FY ending 2025-06-30 CY2025 and ORCL's ending 2025-05-31 CY2024."""
    if tags and end in tags:
        return tags[end]
    d = _d(end)
    return (d - dt.timedelta(days=CY_SHIFT_DAYS)).year if d else None


def fy_snapshot(rev_dated, gp_dated=None, cost_dated=None, oi_dated=None, as_of=None, tags=None):
    """{fy0, fy1, fy3} for one filer, each {end, revenue, gross_profit, operating_income}
    or None, plus gp_basis. FY0 is the newest fiscal year ending on or before `as_of`;
    FY1 and FY3 come from the contiguous run of fiscal-year ends behind it (trend._window),
    each checked to be about one / three years before FY0 by date. A leg with no value
    filed AT THAT DATE is None: gross profit from another year never fills it."""
    rev = trend._pairs(rev_dated)
    if as_of:
        rev = {d: v for d, v in rev.items() if _d(d) and _d(d) <= _d(as_of)}
    win = trend._window(rev, 4)
    gp, gp_basis = trend.gross_profit(gp_dated, rev_dated, cost_dated)
    oi = trend._pairs(oi_dated)

    def leg(d):
        return {"end": d, "frames_cy": frames_cy(d, tags), "revenue": rev.get(d),
                "gross_profit": gp.get(d), "operating_income": oi.get(d)}

    out = {"fy0": None, "fy1": None, "fy3": None, "gp_basis": gp_basis}
    if not win:
        return out
    fy0 = win[-1]
    out["fy0"] = leg(fy0)
    if len(win) >= 2 and FY_GAP[0] <= _days(fy0, win[-2]) <= FY_GAP[1]:
        out["fy1"] = leg(win[-2])
    if len(win) == 4 and FY3_GAP[0] <= _days(fy0, win[0]) <= FY3_GAP[1]:
        out["fy3"] = leg(win[0])
    return out


# what a stored peer point carries for the table, beyond relative.point's own keys
POINT_KEYS = ("symbol", "source", "sic", "currency", "accounting_standard", "period_end",
              "price_date", "price", "revenue_ttm", "ebit_ttm", "net_income_ttm", "ev",
              "market_cap", "addback", "fy0", "fy1", "fy3", "gp_basis")


def point_fields(x, as_of=None, tags=None):
    """The panel's fields from a sec_edgar.extract() dict (EBIT already normalized),
    to merge into a relative.point. `tags` = frame_tags(companyfacts) when the raw
    facts are at hand."""
    snap = fy_snapshot(x.get("revenue_annuals_dated"), x.get("gross_profit_annuals_dated"),
                       x.get("cost_of_revenue_annuals_dated"),
                       x.get("operating_income_annuals_dated"), as_of, tags)
    return {"currency": x.get("currency") or "USD",
            "accounting_standard": x.get("accounting_standard"),
            "revenue_ttm": x.get("revenue"), "ebit_ttm": x.get("operating_income"),
            "net_income_ttm": x.get("net_income"), **snap}


def compact(p):
    """A point reduced to POINT_KEYS (the stored form)."""
    return {k: p.get(k) for k in POINT_KEYS}


# --------------------------------------------------------------------------- #
#  The table                                                                  #
# --------------------------------------------------------------------------- #
VERSION = 1
METRICS = ("revenue_ttm", "growth_fy", "cagr3_fy", "gross_margin_fy", "op_margin_ttm",
           "ev_sales", "ev_ebit", "pe")
RATIO_METRICS = ("growth_fy", "cagr3_fy", "gross_margin_fy", "op_margin_ttm")  # vs median in pp
RANK_METRICS = {"scale": "revenue_ttm", "growth": "cagr3_fy", "margin": "op_margin_ttm"}
IQR_K = 1.5
FY_LAG_DAYS = FY_GAP[1]          # a row whose newest FY is more than a year behind is dropped


def _pos(v):
    return isinstance(v, (int, float)) and v > 0


def _num(v):
    return isinstance(v, (int, float))


def _ratio(a, b):
    """a / b, or None unless both legs exist and the base is positive."""
    return a / b if _num(a) and _pos(b) else None


def table_row(p, is_target=False):
    """One company on the panel's clocks. Every ratio takes both legs from one period:
    FY metrics from the same fiscal-year date, TTM ones from the same TTM point."""
    fy0, fy1, fy3 = p.get("fy0") or {}, p.get("fy1") or {}, p.get("fy3") or {}
    r0 = fy0.get("revenue")
    g = _ratio(r0, fy1.get("revenue"))
    c3 = _ratio(r0, fy3.get("revenue"))
    ev, rev = p.get("ev"), p.get("revenue_ttm")
    return {"symbol": p.get("symbol"), "is_target": bool(is_target), "source": p.get("source"),
            "period_end": p.get("period_end"), "fy0_end": fy0.get("end"),
            "frames_cy": fy0.get("frames_cy"), "price_date": p.get("price_date"),
            "addback": p.get("addback"),
            "revenue_ttm": rev if _pos(rev) else None,
            "growth_fy": None if g is None else g - 1,
            "cagr3_fy": c3 ** (1 / 3) - 1 if c3 and c3 > 0 else None,
            "gross_margin_fy": _ratio(fy0.get("gross_profit"), r0),
            "op_margin_ttm": _ratio(p.get("ebit_ttm"), rev),
            "ev_sales": _ratio(ev, rev) if _pos(ev) else None,
            "ev_ebit": _ratio(ev, p.get("ebit_ttm")) if _pos(ev) and _pos(p.get("ebit_ttm")) else None,
            "pe": (_ratio(p.get("market_cap"), p.get("net_income_ttm"))
                   if _pos(p.get("market_cap")) and _pos(p.get("net_income_ttm")) else None),
            "rank3": {k: None for k in RANK_METRICS}, "outlier": []}


def stats(values):
    """{n, max, p75, median, p25, min} of the numbers present (missing are not zeros);
    quartiles by statistics.quantiles(method="inclusive")."""
    v = sorted(x for x in values if _num(x))
    if not v:
        return {"n": 0, "max": None, "p75": None, "median": None, "p25": None, "min": None}
    q1, q2, q3 = statistics.quantiles(v, n=4, method="inclusive") if len(v) > 1 else (v[0],) * 3
    return {"n": len(v), "max": v[-1], "p75": q3, "median": q2, "p25": q1, "min": v[0]}


def flag_outliers(rows):
    """Each row's `outlier` lists the metrics where it sits outside the IQR fence of the
    PEER rows (needs 4 peer values of that metric); the target is judged by that fence."""
    for m in METRICS:
        st = stats(r[m] for r in rows if not r["is_target"])
        if st["n"] < 4:
            continue
        iqr = st["p75"] - st["p25"]
        lo, hi = st["p25"] - IQR_K * iqr, st["p75"] + IQR_K * iqr
        for r in rows:
            if _num(r[m]) and not lo <= r[m] <= hi:
                r["outlier"].append(m)


def rank_terciles(rows):
    """rank3 per row: 1 = top third, 3 = bottom third, among the rows that have the
    metric (ties share the better rank); None for a missing value or under 3 values."""
    for key, m in RANK_METRICS.items():
        vals = sorted((r[m] for r in rows if _num(r[m])), reverse=True)
        if len(vals) < 3:
            continue
        for r in rows:
            if _num(r[m]):
                r["rank3"][key] = 1 + (3 * vals.index(r[m])) // len(vals)


def eligible(points, as_of, ref_fy0=None):
    """(kept, excluded[{symbol, reason}]). Filters run before any count or cap: non-USD
    or non-US-GAAP filers, facts dated after as_of, and rows whose newest fiscal year is
    more than a year behind the reference."""
    kept, out = [], []
    for p in points:
        fy0 = (p.get("fy0") or {}).get("end")
        why = None
        if (p.get("currency") or "USD") != "USD":
            why = f"reports in {p.get('currency')}"
        elif p.get("accounting_standard") != "us-gaap":
            why = f"files under {p.get('accounting_standard') or 'an unknown standard'}, not us-gaap"
        elif as_of and any(_d(s) and _d(s) > _d(as_of) for s in (p.get("period_end"), p.get("price_date"), fy0)):
            why = f"facts dated after {as_of}"
        elif ref_fy0 and fy0 and _days(ref_fy0, fy0) > FY_LAG_DAYS:
            why = f"newest fiscal year {fy0} lags {ref_fy0} by more than a year"
        (out.append({"symbol": p.get("symbol"), "reason": why}) if why else kept.append(p))
    return kept, out


def peer_set(target, peers, as_of):
    """eligible() against the target's newest fiscal year (the newest peer FY when the
    target has none). The table and the moat signals read the same set."""
    ref = (target.get("fy0") or {}).get("end") if target else None
    if not ref:
        ref = max(((p.get("fy0") or {}).get("end") for p in peers if (p.get("fy0") or {}).get("end")),
                  default=None)
    return eligible(peers, as_of, ref)


def table(target, peers, as_of):
    """(table, excluded) for the target point and its peer points."""
    kept, excluded = peer_set(target, peers, as_of)
    rows = ([table_row(target, True)] if target else []) + [table_row(p) for p in kept]
    flag_outliers(rows)
    rank_terciles(rows)
    peer_rows = [r for r in rows if not r["is_target"]]
    st = {m: stats(r[m] for r in peer_rows) for m in METRICS}
    tv = {}
    t = rows[0] if target else None
    for m in METRICS:
        med = st[m]["median"]
        if t is None or not _num(t[m]) or med is None:
            tv[m] = None
        else:
            tv[m] = (t[m] - med) * (100 if m in RATIO_METRICS else 1)
    return {"rows": rows, "stats": st, "target_vs_median": tv}, excluded


# --------------------------------------------------------------------------- #
#  Industry revenue share (CY, SEC frames)                                    #
# --------------------------------------------------------------------------- #
GROUP = "damodaran_industry_us_sec_filers"
COVERAGE_MIN = 0.70
TOP_N = 5
SHARE_LOOKBACK = 3


def industry_share(members, frames, target_cik, years, as_of):
    """Share of revenue among the US SEC filers in one Damodaran industry, per calendar
    year. `members` {ticker: cik or None} is the industry's own member list (no other
    peer source); share classes of one filer count once. `frames` {CY: {cik: [revenue,
    end]}}. A year with fewer than COVERAGE_MIN of the filers reporting revenue is
    flagged and its shares are null. Calendar years not complete by as_of are not used."""
    flags = []
    ciks = {int(c) for c in members.values() if c}
    no_cik = sorted(t for t, c in members.items() if not c)
    if no_cik:
        flags.append(f"{len(no_cik)} industry members have no SEC CIK (not counted)")
    tc = int(target_cik) if target_cik else None
    ys = sorted(y for y in years if not as_of or int(y) < _d(as_of).year)
    out = {"group": GROUP, "years": ys, "n_members": len(ciks), "n_with_revenue": {},
           "coverage": {}, "target_share": {}, "top5_share": {}, "hhi": {},
           "target_frames_end": {}, "share_change_pp": None, "top5_change_pp": None,
           "matched_n": None, "matched_share_then": None, "matched_share_now": None,
           "matched_top5_then": None, "matched_top5_now": None, "flags": flags}
    shown = {}                   # CY -> {cik: revenue} for the years that pass coverage
    if not ciks:
        flags.append("no industry members with an SEC CIK")
        return out
    if tc is None:
        flags.append("target has no SEC CIK: no share")
    for y in ys:
        k = str(y)
        yr = (frames or {}).get(y) or (frames or {}).get(k)
        if not yr:
            flags.append(f"CY{y}: no SEC revenue frames")
            out["n_with_revenue"][k] = 0
            for m in ("coverage", "target_share", "top5_share", "hhi"):
                out[m][k] = None
            continue
        vals = {}
        for c in ciks:
            row = yr.get(c) or yr.get(str(c))
            if row and _pos(row[0]):
                vals[c] = row[0]
        cov = len(vals) / len(ciks)
        out["n_with_revenue"][k], out["coverage"][k] = len(vals), cov
        trow = (yr.get(tc) or yr.get(str(tc))) if tc else None
        out["target_frames_end"][k] = trow[1] if trow and len(trow) > 1 else None
        if cov < COVERAGE_MIN:
            flags.append(f"CY{y}: {len(vals)} of {len(ciks)} members report revenue "
                         f"({cov:.0%} < {COVERAGE_MIN:.0%}): share not shown")
            for m in ("target_share", "top5_share", "hhi"):
                out[m][k] = None
            continue
        shown[y] = vals
        total = sum(vals.values())
        out["top5_share"][k] = sum(sorted(vals.values(), reverse=True)[:TOP_N]) / total
        out["hhi"][k] = sum((v / total) ** 2 for v in vals.values())
        if tc in vals:
            out["target_share"][k] = vals[tc] / total
        else:
            out["target_share"][k] = None
            if tc is not None:
                flags.append(f"CY{y}: no revenue frame for the target")
    if ys:
        new = ys[-1]
        matched_change(out, shown.get(new), shown.get(new - SHARE_LOOKBACK), tc, new - SHARE_LOOKBACK, new)
    return out


def matched_change(out, now, then, tc, old, new):
    """SPEC R13: share_change_pp and top5_change_pp on the filers with revenue in BOTH
    years, so a filer missing from one year (a May fiscal year not yet in the newest CY)
    cannot move the share. Null when either year failed its own coverage (its flag
    already says so) or the matched set is under COVERAGE_MIN of the older year's filers."""
    if now is None or then is None:
        return
    both = sorted(set(now) & set(then))
    out["matched_n"] = len(both)
    window = f"CY{old}..CY{new}"
    if len(both) < COVERAGE_MIN * len(then):
        out["flags"].append(f"{window}: {len(both)} of the {len(then)} CY{old} filers report in CY{new} "
                            f"({len(both) / len(then):.0%} < {COVERAGE_MIN:.0%}): no share change")
        return
    if len(both) < max(len(now), len(then)):
        out["flags"].append(f"{window}: share change over the {len(both)} filers with revenue in both years "
                            f"({len(then) - len(both)} only in CY{old}, {len(now) - len(both)} only in CY{new})")
    tn, tt = sum(now[c] for c in both), sum(then[c] for c in both)
    top = lambda v, total: sum(sorted((v[c] for c in both), reverse=True)[:TOP_N]) / total
    out["matched_top5_then"], out["matched_top5_now"] = top(then, tt), top(now, tn)
    out["top5_change_pp"] = (out["matched_top5_now"] - out["matched_top5_then"]) * 100
    if tc in both:
        out["matched_share_then"], out["matched_share_now"] = then[tc] / tt, now[tc] / tn
        out["share_change_pp"] = (out["matched_share_now"] - out["matched_share_then"]) * 100


# --------------------------------------------------------------------------- #
#  The panel                                                                  #
# --------------------------------------------------------------------------- #
def why_null(p, m):
    """Why table_row left metric `m` empty for point `p`: its first missing leg."""
    fy0, fy1, fy3 = (p.get(k) or {} for k in ("fy0", "fy1", "fy3"))
    end, r0 = fy0.get("end"), fy0.get("revenue")
    rev, ev = p.get("revenue_ttm"), p.get("ev")
    gp = ("no GrossProfit or CostOfRevenue" if p.get("gp_basis") in (None, "not filed")
          else f"no {p['gp_basis']} value")
    fy = [(end, "no fiscal year on or before as-of"), (_pos(r0), f"no positive revenue for FY0 ({end})")]
    legs = {
        "revenue_ttm": [(_pos(rev), "no positive TTM revenue")],
        "growth_fy": fy + [(fy1, f"no fiscal year about one year before FY0 ({end})"),
                           (_pos(fy1.get("revenue")), f"FY-1 ({fy1.get('end')}) revenue not positive")],
        "cagr3_fy": fy + [(fy3, f"no contiguous fiscal year three years before FY0 ({end})"),
                          (_pos(fy3.get("revenue")), f"FY3 ({fy3.get('end')}) revenue not positive")],
        "gross_margin_fy": fy + [(_num(fy0.get("gross_profit")), f"{gp} for FY0 ({end})")],
        "op_margin_ttm": [(_pos(rev), "no positive TTM revenue"),
                          (_num(p.get("ebit_ttm")), "no TTM operating income")],
        "ev_sales": [(_pos(ev), "enterprise value missing or not positive"),
                     (_pos(rev), "no positive TTM revenue")],
        "ev_ebit": [(_pos(ev), "enterprise value missing or not positive"),
                    (_pos(p.get("ebit_ttm")), "TTM EBIT not positive")],
        "pe": [(_pos(p.get("market_cap")), "no market cap"),
               (_pos(p.get("net_income_ttm")), "TTM net income not positive")],
    }
    return next((why for ok, why in legs[m] if not ok), "not computable from its legs")


def null_flags(tbl, target, peers):
    """One flag per empty metric on each row: row, metric and the missing leg."""
    by = {p.get("symbol"): p for p in peers}
    out = []
    for r in tbl["rows"]:
        p = target if r["is_target"] else by.get(r["symbol"]) or {}
        out += [f"{r['symbol']} {m}: {why_null(p, m)}" for m in METRICS if r[m] is None]
    return out


def build(target, peers, as_of, industry=None, basis=None, share=None, excluded=(), notes=(),
          target_why=None):
    """results[ticker]["peer_panel"]. `peers` None = no stored peer points (a fit from
    before the panel existed, or no fit this week): the table is absent with a flag."""
    flags = [n for n in notes if n]
    if not industry:
        flags.append("no Damodaran industry for this ticker: no industry share")
    if peers is None:
        tbl, excl = None, []
        flags.append("no stored peer points (peer fit predates the panel or failed): no table")
    elif not peers:
        tbl, excl = None, []
        flags.append("no usable peers: no table")
    else:
        tbl, excl = table(target, peers, as_of)
        if not tbl["rows"] or all(r["is_target"] for r in tbl["rows"]):
            flags.append("every peer was excluded: no peer statistics")
        flags += null_flags(tbl, target, peers)
    for e in list(excluded) + excl:
        flags.append(f"{e.get('symbol')}: {e.get('reason')}")
    if share:
        flags += share.get("flags") or []
    if target is None:
        flags.append("target not on the common clock: no target row"
                     + (f" ({target_why})" if target_why else ""))
    return {"as_of": as_of, "version": VERSION, "industry": industry, "industry_basis": basis,
            "table": tbl, "industry_share": share, "flags": flags}
