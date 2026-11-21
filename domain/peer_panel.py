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


# the frames ladder (sources/damodaran_companies.FRAMES_CONCEPTS): the FY -> CY tag is
# read from whichever of them the filer used
REVENUE_CONCEPTS = ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                    "SalesRevenueNet", "RevenueFromContractWithCustomerIncludingAssessedTax")
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


FY_HIST = 5                      # fiscal years a stored point keeps (FY0..FY-4)
# the balance-sheet legs of EV (domain/relative.point), kept so the row can be checked leg by leg
EV_LEGS = ("total_debt", "operating_leases", "cash", "minority_interest")


def fy_snapshot(rev_dated, gp_dated=None, cost_dated=None, oi_dated=None, as_of=None, tags=None):
    """{fy0, fy1, fy3} for one filer, each {end, revenue, gross_profit, operating_income}
    or None, plus gp_basis and fy_hist (the contiguous run's revenue and gross profit,
    newest first, up to FY_HIST years). FY0 is the newest fiscal year ending on or before
    `as_of`; FY1 and FY3 come from the contiguous run of fiscal-year ends behind it
    (trend._window_by_date: consecutive ends 330-400 days apart, so a 52/53-week year
    counts), each checked to be about one / three years before FY0 by date. A leg with no
    value filed AT THAT DATE is None: gross profit from another year never fills it."""
    rev = trend._pairs(rev_dated)
    if as_of:
        rev = {d: v for d, v in rev.items() if _d(d) and _d(d) <= _d(as_of)}
    win = trend._window_by_date(rev, FY_HIST)
    gp, gp_basis = trend.gross_profit(gp_dated, rev_dated, cost_dated)
    oi = trend._pairs(oi_dated)

    def leg(d):
        return {"end": d, "frames_cy": frames_cy(d, tags), "revenue": rev.get(d),
                "gross_profit": gp.get(d), "operating_income": oi.get(d)}

    out = {"fy0": None, "fy1": None, "fy3": None, "gp_basis": gp_basis,
           "fy_hist": {"revenue": [[d, rev[d]] for d in reversed(win)],
                       "gross_profit": [[d, gp.get(d)] for d in reversed(win)]}}
    if not win:
        return out
    fy0 = win[-1]
    out["fy0"] = leg(fy0)
    if len(win) >= 2 and FY_GAP[0] <= _days(fy0, win[-2]) <= FY_GAP[1]:
        out["fy1"] = leg(win[-2])
    if len(win) >= 4 and FY3_GAP[0] <= _days(fy0, win[-4]) <= FY3_GAP[1]:
        out["fy3"] = leg(win[-4])
    return out


# what a stored peer point carries for the table, beyond relative.point's own keys
POINT_KEYS = ("symbol", "source", "sic", "currency", "accounting_standard", "period_end",
              "price_date", "price", "revenue_ttm", "ebit_ttm", "net_income_ttm", "ev",
              "market_cap", "addback", "fy0", "fy1", "fy3", "gp_basis", "fy_hist") + EV_LEGS
# the panel's fiscal-year inputs: sec_edgar.panel_fy_series (one value per year-end
# date, revenue merged across concepts), else the scored series on facts stored before it
FY_INPUTS = (("revenue_fy_dated", "revenue_annuals_dated"),
             ("gross_profit_fy_dated", "gross_profit_annuals_dated"),
             ("cost_of_revenue_fy_dated", "cost_of_revenue_annuals_dated"),
             ("operating_income_fy_dated", "operating_income_annuals_dated"))


def point_fields(x, as_of=None, tags=None):
    """The panel's fields from a sec_edgar.extract() dict (EBIT already normalized),
    to merge into a relative.point. `tags` = frame_tags(companyfacts) when the raw
    facts are at hand."""
    snap = fy_snapshot(*(x.get(new) or x.get(old) for new, old in FY_INPUTS), as_of=as_of, tags=tags)
    return {"currency": x.get("currency") or "USD",
            "accounting_standard": x.get("accounting_standard"),
            "revenue_ttm": x.get("revenue"), "ebit_ttm": x.get("operating_income"),
            "net_income_ttm": x.get("net_income"), **snap}


def compact(p):
    """A point reduced to POINT_KEYS (the stored form)."""
    return {k: p.get(k) for k in POINT_KEYS}


# --------------------------------------------------------------------------- #
#  Same business: SIC (SPEC R25-R28)                                          #
# --------------------------------------------------------------------------- #
# One SIC source for the target, the peers and the sub-group: the "SIC Code" column of
# Damodaran's company list, the same snapshot that sets industry membership. It covers
# every member offline; SEC submissions SIC would need one request per member and
# disagrees with it on a third of the stored peers (R25).
SIC_SOURCE = "damodaran_indname"
FINANCIAL_SIC = (6000, 6799)     # banks, credit, brokers, insurers, insurance agents, REITs
GM_MIN_COMPARATORS = 3           # a median of fewer firms is one firm's margin
GM_NA_TH = "gross margin ไม่ใช่ตัวชี้วัดของธุรกิจนี้ (ธนาคาร/ประกัน/การเงิน SIC 6000–6799)"


def sic4(raw):
    """A 4-digit SIC string from "6324", 6324, 6324.0, "06324" or " 6324 "; None for a
    missing, non-numeric, zero ("0", "0000") or out-of-range code. A None never matches."""
    if raw is None or isinstance(raw, bool):
        return None
    try:
        n = int(float(str(raw).strip()))
    except (TypeError, ValueError):
        return None
    return f"{n:04d}" if 0 < n <= 9999 else None


def is_financial(code):
    c = sic4(code)
    return c is not None and FINANCIAL_SIC[0] <= int(c) <= FINANCIAL_SIC[1]


def _gm_at(p, legs):
    return all(_ratio((p.get(k) or {}).get("gross_profit"), (p.get(k) or {}).get("revenue")) is not None
               for k in legs)


def gm_scope(target, peers, sics, legs=("fy0",)):
    """Which peers a gross-margin comparison may use (R26). Same 4-digit SIC as the target
    with a gross margin at every leg, at least GM_MIN_COMPARATORS of them; else the same
    3-digit SIC; else none. A financial target (R25) has no GM comparison. `sics`
    {symbol: raw SIC} from SIC_SOURCE. `peers` keeps the comparator points; record()
    drops them for storage. `reason` is Thai and names the counts."""
    sics = sics or {}
    ts = sic4(sics.get((target or {}).get("symbol")))
    out = {"sic_source": SIC_SOURCE, "sic": ts, "applies": True, "level": None, "symbols": [],
           "n": 0, "n4": 0, "n3": 0, "legs": list(legs), "reason": None, "peers": None}
    if ts is None:
        out["reason"] = "ไม่มีรหัส SIC ของหุ้นนี้ในรายชื่อ Damodaran จึงไม่เทียบ gross margin กับคู่แข่ง"
        return out
    if is_financial(ts):
        out.update(applies=False, reason=GM_NA_TH)
        return out
    have = [(p, sic4(sics.get(p.get("symbol")))) for p in peers or [] if _gm_at(p, legs)]
    same = {lv: [p for p, c in have if c is not None and c[:lv] == ts[:lv]] for lv in (4, 3)}
    out["n4"], out["n3"] = len(same[4]), len(same[3])
    for lv in (4, 3):
        if len(same[lv]) >= GM_MIN_COMPARATORS:
            out.update(level=lv, peers=same[lv], n=len(same[lv]),
                       symbols=sorted(p.get("symbol") for p in same[lv]))
            return out
    years = "ทั้ง FY0 และ FY3" if len(legs) > 1 else "ปีงบล่าสุด"
    out["reason"] = (f"คู่แข่งที่มี gross margin {years}: SIC {ts} เดียวกัน {out['n4']} ราย, "
                     f"SIC {ts[:3]}x {out['n3']} ราย (ต้องอย่างน้อย {GM_MIN_COMPARATORS}) จึงไม่เทียบ gross margin")
    return out


def record(scope):
    """The stored form of a gm_scope (no points)."""
    return {k: v for k, v in scope.items() if k != "peers"}


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
            "addback": p.get("addback"), "market_cap": p.get("market_cap"),
            **{k: p.get(k) for k in EV_LEGS},
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


def flag_outliers(rows, within=None):
    """Each row's `outlier` lists the metrics where it sits outside the IQR fence of the
    PEER rows (needs 4 peer values of that metric); the target is judged by that fence.
    `within` {metric: symbols}: that metric's fence and flags use only those peers (and
    the target), as gross margin does (R26)."""
    for m in METRICS:
        keep = (within or {}).get(m)
        use = [r for r in rows if keep is None or r["is_target"] or r["symbol"] in keep]
        st = stats(r[m] for r in use if not r["is_target"])
        if st["n"] < 4:
            continue
        iqr = st["p75"] - st["p25"]
        lo, hi = st["p25"] - IQR_K * iqr, st["p75"] + IQR_K * iqr
        for r in use:
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


def table(target, peers, as_of, sics=None):
    """(table, excluded) for the target point and its peer points. Gross
    margin is compared only within gm_scope (R26) and is not shown at all for a
    financial target or a financial row (R25); every other metric uses all peers."""
    kept, excluded = peer_set(target, peers, as_of)
    rows = ([table_row(target, True)] if target else []) + [table_row(p) for p in kept]
    scope = gm_scope(target, kept, sics)
    ts, comp = scope["sic"], set(scope["symbols"])
    for r in rows:
        r["sic"] = sic4((sics or {}).get(r["symbol"]))
        r["same_business"] = ts is not None and r["sic"] == ts
        r["gm_comparator"] = r["symbol"] in comp and not r["is_target"]
        r["gm_na"] = not scope["applies"] or is_financial(r["sic"])
        if r["gm_na"]:
            r["gross_margin_fy"] = None
    flag_outliers(rows, {"gross_margin_fy": comp})
    rank_terciles(rows)
    peer_rows = [r for r in rows if not r["is_target"]]
    st = {m: stats(r[m] for r in peer_rows if m != "gross_margin_fy" or r["gm_comparator"]) for m in METRICS}
    tv = {}
    t = rows[0] if target else None
    for m in METRICS:
        med = st[m]["median"]
        if t is None or not _num(t[m]) or med is None:
            tv[m] = None
        else:
            tv[m] = (t[m] - med) * (100 if m in RATIO_METRICS else 1)
    return {"rows": rows, "stats": st, "target_vs_median": tv, "gm_compare": record(scope)}, excluded


# --------------------------------------------------------------------------- #
#  Industry revenue share (CY, SEC frames)                                    #
# --------------------------------------------------------------------------- #
GROUP = "damodaran_industry_us_sec_filers"
COVERAGE_MIN = 0.70
TOP_N = 5
SHARE_LOOKBACK = 3


def _in_frames(frames, cik, ys):
    for y in ys:
        yr = (frames or {}).get(y) or (frames or {}).get(str(y)) or {}
        if cik in yr or str(cik) in yr:
            return True
    return False


def industry_share(members, frames, target_cik, years, as_of, foreign=None, assets=None):
    """Share of revenue among the US SEC filers in one Damodaran industry, per calendar
    year. `members` {ticker: cik or None} is the industry's own US-domiciled list (no
    other peer source); `foreign` the US-listed members domiciled elsewhere, counted
    when SEC's frames hold their revenue in one of the years (a 10-K filer in USD like
    MELI; a 20-F filer is never in frames). Share classes of one filer count once.
    `frames` {CY: {cik: [revenue, end]}}. A year with fewer than COVERAGE_MIN of the
    filers reporting is flagged and its shares are null. A member reports when it has a
    revenue frame that year or, R20, a year-end balance sheet in `assets` {CY: {cik}}
    (it filed and booked no revenue: revenue 0, so it moves coverage, not the shares).
    Calendar years not complete by as_of are not used."""
    flags = []
    ciks = {int(c) for c in members.values() if c}
    no_cik = sorted(t for t, c in members.items() if not c)
    if no_cik:
        flags.append(f"{len(no_cik)} industry members have no SEC CIK (not counted)")
    tc = int(target_cik) if target_cik else None
    ys = sorted(y for y in years if not as_of or int(y) < _d(as_of).year)
    filers = {int(c) for c in (foreign or {}).values() if c} - ciks
    filers = {c for c in filers if _in_frames(frames, c, ys)}
    if filers:
        ciks |= filers
        flags.append(f"{len(filers)} members not US-domiciled file us-gaap revenue with the SEC (counted)")
    out = {"group": GROUP, "years": ys, "n_members": len(ciks), "n_with_revenue": {}, "n_covered": {},
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
            out["n_with_revenue"][k] = out["n_covered"][k] = 0
            for m in ("coverage", "target_share", "top5_share", "hhi"):
                out[m][k] = None
            continue
        vals, covered = {}, set()
        bs = (assets or {}).get(y) or (assets or {}).get(k) or set()
        for c in ciks:
            row = yr.get(c) or yr.get(str(c))
            if row and _pos(row[0]):
                vals[c] = row[0]
            if row is not None or c in bs:
                covered.add(c)
        cov = len(covered) / len(ciks)
        out["n_with_revenue"][k], out["n_covered"][k], out["coverage"][k] = len(vals), len(covered), cov
        trow = (yr.get(tc) or yr.get(str(tc))) if tc else None
        out["target_frames_end"][k] = trow[1] if trow and len(trow) > 1 else None
        if cov < COVERAGE_MIN:
            basis = "report revenue or a balance sheet" if assets else "report revenue"
            flags.append(f"CY{y}: {len(covered)} of {len(ciks)} members {basis} "
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


SUB_GROUP = "damodaran_industry_same_sic4"


def sub_group(members, frames, target_cik, years, as_of, foreign=None, assets=None, sics=None,
              target_symbol=None):
    """industry_share over the industry members whose 4-digit SIC equals the target's
    (R28): same matched set (R13), coverage (R20) and calendar years (R10) as the full
    industry, plus `members`, one row per filer in the newest calendar year, revenue and
    share of the sub-group (null when that year failed coverage). Share classes of one
    filer are one row. No SIC for the target: an empty group with a Thai reason."""
    sics = sics or {}
    ts = sic4(sics.get(target_symbol))
    if ts is None:
        return {"group": SUB_GROUP, "sic": None, "sic_source": SIC_SOURCE, "n_members": 0, "members": [],
                "reason": "ไม่มีรหัส SIC ของหุ้นนี้ในรายชื่อ Damodaran จึงไม่มีกลุ่มธุรกิจเดียวกัน", "flags": []}
    pick = lambda d: {t: c for t, c in (d or {}).items() if sic4(sics.get(t)) == ts}
    sm, sf = pick(members), pick(foreign)
    out = industry_share(sm, frames, target_cik, years, as_of, sf, assets)
    out.update(group=SUB_GROUP, sic=ts, sic_source=SIC_SOURCE, reason=None,
               flags=[f"sub-group SIC {ts}: {f}" for f in out["flags"]])
    ys = out["years"]
    new = ys[-1] if ys else None
    yr = ((frames or {}).get(new) or (frames or {}).get(str(new)) or {}) if new else {}
    passed = new is not None and out["top5_share"].get(str(new)) is not None
    us = {int(c) for c in sm.values() if c}
    counted = {}                 # the filers industry_share counted (R16), share classes once
    for t, c in list(sm.items()) + list(sf.items()):
        if c and (int(c) in us or _in_frames(frames, int(c), ys)):
            counted.setdefault(int(c), []).append(t)
    rev = {c: (yr.get(c) or yr.get(str(c)) or [None])[0] for c in counted}
    total = sum(v for v in rev.values() if _pos(v))
    out["members"] = sorted(({"symbols": sorted(t), "cik": c, "revenue": rev[c] if _pos(rev[c]) else None,
                              "share": rev[c] / total if passed and _pos(rev[c]) and total else None,
                              "cy": new, "in_panel": False}
                             for c, t in counted.items()),
                            key=lambda m: (-(m["revenue"] or 0), m["symbols"]))
    if out["n_members"] < 2:
        out["reason"] = f"กลุ่มธุรกิจเดียวกัน (SIC {ts}) มีแค่หุ้นนี้ในอุตสาหกรรม จึงไม่มีส่วนแบ่งให้เทียบ"
    return out


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
        out += [f"{r['symbol']} {m}: {why_null(p, m)}" for m in METRICS
                if r[m] is None and not (m == "gross_margin_fy" and r.get("gm_na"))]
    return out


def build(target, peers, as_of, industry=None, basis=None, share=None, excluded=(), notes=(),
          target_why=None, sics=None):
    """results[ticker]["peer_panel"]. `peers` None = no stored peer points (a fit from
    before the panel existed, or no fit this week): the table is absent with a flag.
    `sics` {symbol: raw SIC} from SIC_SOURCE (R25-R28)."""
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
        tbl, excl = table(target, peers, as_of, sics)
        if tbl["gm_compare"]["level"] is None:
            flags.append(f"gross margin: {tbl['gm_compare']['reason']}")
        if not tbl["rows"] or all(r["is_target"] for r in tbl["rows"]):
            flags.append("every peer was excluded: no peer statistics")
        flags += null_flags(tbl, target, peers)
    for e in list(excluded) + excl:
        flags.append(f"{e.get('symbol')}: {e.get('reason')}")
    if share:
        flags += share.get("flags") or []
        sub = share.get("sub_group")
        if sub and tbl:
            listed = {r["symbol"] for r in tbl["rows"]}
            for m in sub.get("members") or []:
                m["in_panel"] = any(x in listed for x in m["symbols"])
    if target is None:
        flags.append("target not on the common clock: no target row"
                     + (f" ({target_why})" if target_why else ""))
    return {"as_of": as_of, "version": VERSION, "industry": industry, "industry_basis": basis,
            "table": tbl, "industry_share": share, "flags": flags}
