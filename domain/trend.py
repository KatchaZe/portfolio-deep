"""
trend — the 5-year performance strip (T5). Pure; no network, no config, no I/O.

Answers one question per row: *is this getting better year after year, or was last
year a good year?* Every DEEP subscore is a one- or two-period snapshot, so a company
whose free cash flow has fallen four years running scores exactly like one whose FCF is
compounding, as long as the latest ratio looks the same.

Five rows, in the order Damodaran reads a business:

    revenue      -> is demand growing
    gross margin -> pricing power (can it charge more than it costs to make)
    op margin    -> operating leverage (does scale reach the bottom line)
    FCF          -> does the accounting profit become owner cash
    ROIC         -> the moat itself: return on capital vs its cost, year by year

TWO RULES THIS MODULE EXISTS TO ENFORCE
---------------------------------------
1. **Align on the fiscal-year DATE, never on list position.** SEC tags cover different
   spans of history — MSFT files GrossProfit for 18 years and revenue for 10 — so
   `gross_profit_annuals[i] / revenue_annuals[i]` would divide FY2025 profit by FY2017
   revenue and call it a margin. That is the defect class P3-1 was about, one level
   further down. Every series here arrives dated and is joined on the date.

2. **Show only years where BOTH legs are filed.** A ratio built from a half-filed year
   is worse than a missing point, because a gap is visible and a wrong point is not
   (P2-3, same reasoning). Each row therefore reports `n` — how many years it actually
   found — and the UI prints it.

Everything is derived from SEC 10-K figures, so the values are the audited GAAP ones,
not the adjusted numbers a company presents on its earnings call.
"""

# how far apart the first and last point must be before a row is called a trend
CAGR_FLAT_PCT = 2.0        # revenue / FCF: |CAGR| <= 2%/yr reads as flat
MARGIN_FLAT_PP = 1.0       # margins / ROIC: |change| <= 1pp over the window reads as flat
# ΔIC must be this fraction of the starting capital base before incremental ROIC means
# anything. Below it the denominator is noise and the ratio explodes — the same trap
# REV-13 hit with capex - D&A landing near zero.
MIN_IC_DELTA_FRAC = 0.05
MAX_YEARS = 5


def _pairs(dated):
    """[[FY_end, value], …] -> {FY_end: value}, ignoring malformed rows."""
    out = {}
    for item in dated or []:
        try:
            d, v = item[0], item[1]
        except (TypeError, IndexError):
            continue
        if isinstance(d, str) and isinstance(v, (int, float)):
            out[d] = float(v)
    return out


def _window(dates, years=MAX_YEARS):
    """The most recent CONSECUTIVE run of fiscal-year ends, at most `years` long,
    OLDEST first (left-to-right on a chart).

    Contiguity is the point. AVGO files no long-term debt tag for FY2022-24, so those
    years drop out of the invested-capital series and a plain "last five available"
    window returned 2018, 2019, 2020, 2021, 2025 — which a sparkline then draws as one
    smooth line, turning a three-year hole into apparent continuity and a 4-year jump
    into a 1-year step. A trend across a gap is not a trend. Walk back from the newest
    year and stop at the first break; the row then honestly reports how few years it has."""
    ds = sorted(dates, reverse=True)
    if not ds:
        return []
    run = [ds[0]]
    for d in ds[1:]:
        try:
            if int(run[-1][:4]) - int(d[:4]) != 1:
                break
        except (TypeError, ValueError):
            break
        run.append(d)
        if len(run) >= years:
            break
    return list(reversed(run))


def _cagr_pct(points):
    """Compound annual growth across the window, oldest->newest. None unless the
    starting value is positive: a CAGR out of zero or a loss is not a growth rate,
    it is a division by something that has no scale."""
    if len(points) < 2:
        return None
    first, last = points[0]["v"], points[-1]["v"]
    n = len(points) - 1
    if first is None or last is None or first <= 0 or last <= 0:
        return None
    return round(((last / first) ** (1.0 / n) - 1) * 100, 1)


def _direction(summary, flat_band):
    if summary is None:
        return None
    if summary > flat_band:
        return "up"
    if summary < -flat_band:
        return "down"
    return "flat"


def _row(key, label, unit, points, summary, summary_label, flat_band, note=None,
         summary_unit="pp"):
    """`summary_unit` is the unit of the SUMMARY, which is not the unit of the row.

    B (2026-08-11): the dashboard picked the suffix from the row's own unit — money got
    "%", everything else got "pp" — so the ROIC row printed its INCREMENTAL RETURN as a
    delta. PFE read "ทุนใหม่ −33.0pp", which says "ROIC fell 33 points" when it means
    "the capital added over five years earned −33%". Two different statements about a
    company, and the wrong one was on screen. The backend knows which it computed, so
    the backend says so rather than leaving the front end to infer it."""
    return {"key": key, "label": label, "unit": unit,
            "points": points, "n": len(points),
            "summary": summary, "summary_label": summary_label,
            "summary_unit": summary_unit,
            "direction": _direction(summary, flat_band), "note": note}


def _money_row(key, label, series, years, note=None):
    pts = [{"fy": d[:4], "end": d, "v": series[d]} for d in _window(series, years) if d in series]
    return _row(key, label, "money", pts, _cagr_pct(pts), "CAGR", CAGR_FLAT_PCT, note,
                summary_unit="%")          # a growth RATE


def _margin_row(key, label, num, den, years, note=None):
    """Margin per year = numerator / denominator, joined on the fiscal-year date.
    Years where either side is missing, or revenue is not positive, are dropped."""
    common = [d for d in _window(sorted(set(num) & set(den)), years) if den[d] > 0]
    pts = [{"fy": d[:4], "end": d, "v": round(num[d] / den[d] * 100, 1)} for d in common]
    delta = round(pts[-1]["v"] - pts[0]["v"], 1) if len(pts) >= 2 else None
    return _row(key, label, "pct", pts, delta, "Δ 5y", MARGIN_FLAT_PP, note,
                summary_unit="pp")         # a CHANGE in margin


def free_cash_flow(cfo_dated, capex_dated):
    """FCF = cash from operations - capital expenditure, per fiscal year.

    Both legs required. Capex alone would understate FCF for a year the company did
    not file it, which is the direction that flatters — and the only reason to look at
    FCF at all is that it is harder to flatter than earnings."""
    cfo, capex = _pairs(cfo_dated), _pairs(capex_dated)
    return {d: cfo[d] - capex[d] for d in set(cfo) & set(capex)}


def gross_profit(gross_dated, revenue_dated, cost_dated):
    """Filed gross profit where the company reports the subtotal; revenue - cost of
    revenue where it does not (ABBV and much of pharma file the cost line only).
    Never mixes the two within one series — a switch mid-history would put a
    definitional break in the middle of the trend."""
    filed = _pairs(gross_dated)
    if len(filed) >= 2:
        return filed, "filed GrossProfit"
    rev, cost = _pairs(revenue_dated), _pairs(cost_dated)
    derived = {d: rev[d] - cost[d] for d in set(rev) & set(cost) if rev[d] > 0}
    if len(derived) >= 2:
        return derived, "derived: revenue - cost of revenue"
    return {}, "not filed"


def roic_series(oi_dated, ic_components, tax_rate):
    """ROIC per fiscal year = NOPAT / invested capital, both measured at the same
    year-end.

    Tax is held at the CURRENT effective rate across the whole window rather than
    recomputed per year. Two reasons: the per-year pretax tag is missing for many
    filers (MSFT files 6 years against 18 of operating income), and a constant rate
    keeps the SHAPE of the series about the business instead of about tax law — the
    same argument the FX conversion already makes for a constant spot rate. Absolute
    levels for older years are therefore approximate; the trend is not.

    Years where invested capital is <= 0 are dropped, not floored: a company whose
    cash exceeds debt plus equity has no meaningful denominator, and inventing one is
    what REV-2 removed from the engine."""
    oi = _pairs(oi_dated)
    out = {}
    for d, comp in (ic_components or {}).items():
        if d not in oi or not isinstance(comp, dict):
            continue
        eq, cash, debt = comp.get("equity"), comp.get("cash"), comp.get("debt") or 0
        if eq is None or cash is None:
            continue
        ic = debt + eq - cash
        if ic > 0:
            out[d] = {"roic": oi[d] * (1 - tax_rate) / ic * 100, "nopat": oi[d] * (1 - tax_rate), "ic": ic}
    return out


def incremental_roic_pct(roic_map, dates):
    """Return on the capital added ACROSS the window: ΔNOPAT / ΔIC.

    Damodaran's point is that average ROIC is a legacy number — it is dominated by
    capital deployed years ago — so a business losing its moat keeps reporting a
    healthy ROIC long after new investment has stopped earning. The incremental figure
    is what the LATEST capital earns, and it turns first.

    Measured across the whole window rather than year by year on purpose: a single
    year's ΔIC can be near zero (or negative, after buybacks), and dividing by it
    produces the four-digit nonsense REV-13 had to clamp. Returns None when the capital
    base barely moved, because then there is no incremental return to speak of."""
    if len(dates) < 2:
        return None, "ต้องมีอย่างน้อย 2 ปี"
    a, b = roic_map.get(dates[0]), roic_map.get(dates[-1])
    if not a or not b:
        return None, "ข้อมูลทุนไม่ครบ"
    d_ic, d_nopat = b["ic"] - a["ic"], b["nopat"] - a["nopat"]
    if d_ic <= a["ic"] * MIN_IC_DELTA_FRAC:
        return None, "ฐานทุนแทบไม่เปลี่ยน — วัดผลตอบแทนของทุนใหม่ไม่ได้"
    # C (2026-08-11): incremental ROIC assumes the EXTRA profit came from the EXTRA
    # capital. That assumption collapses when the company started the window losing
    # money: the numerator is then dominated by fixing the existing business, not by
    # anything the new capital did. HIMS made this visible — NOPAT −99.9M to +91.7M
    # against only +49.5M of capital, printed as "new capital earned 387%". The capital
    # test above passed (capital did grow 19%); the flaw was never in the denominator.
    #
    # `_cagr_pct` in this same file already refuses to compound from a non-positive
    # start, for exactly this reason. The rule simply had not been carried across.
    if a["nopat"] <= 0:
        return None, ("NOPAT ตั้งต้นติดลบ — กำไรที่เพิ่มมาจากการพลิกขาดทุน "
                      "ไม่ใช่ผลตอบแทนของทุนที่ใส่เพิ่ม")
    return round(d_nopat / d_ic * 100, 1), None


def _is_current(points, latest_fy, max_lag_years=1):
    """A row may only appear if its newest point is close to the newest fiscal year in
    the strip. ORCL tags CostOfRevenue for four years ending 2018 — deriving a gross
    margin from it produced a perfectly real row reading "2017: 60.9% · 2018: 61.7%"
    inside a panel headed "5 ปีล่าสุด". Old data presented as current is worse than no
    data, because nothing on screen says it is eight years stale."""
    if not points or not latest_fy:
        return False
    try:
        return int(latest_fy) - int(points[-1]["fy"]) <= max_lag_years
    except (TypeError, ValueError):
        return False


def _get(f, name, default=None):
    """Read a field from either a FinancialFacts object or the plain dict the store
    round-trips. `portfolio_view` reads facts out of JSON while `analyze_row` holds the
    live object, and the two must produce the SAME strip — a watchlist row and a
    holding row differing in what they show is the parity bug this repo has already
    shipped once (`2df2ce2 watchlist parity`)."""
    if isinstance(f, dict):
        v = f.get(name, default)
    else:
        v = getattr(f, name, default)
    return default if v is None else v


def _tax_rate(f):
    """Effective tax rate from either shape, with FinancialFacts' own 0-60% guard."""
    if not isinstance(f, dict):
        return f.tax_rate
    pre, tax = f.get("income_before_tax"), f.get("tax_expense")
    if pre and tax is not None and pre != 0:
        r = tax / pre
        if 0 <= r <= 0.6:
            return r
    return f.get("tax_rate") if isinstance(f.get("tax_rate"), (int, float)) else 0.21


def build(f, years=MAX_YEARS):
    """Facts (object or stored dict) -> the trend strip. Returns {} when nothing at
    all could be built, so a caller can simply not render the section."""
    rev = _pairs(_get(f, "revenue_annuals_dated"))
    oi = _pairs(_get(f, "operating_income_annuals_dated"))
    # the most recent fiscal year anywhere on the income statement — every row is
    # measured for staleness against this
    _latest = max((d[:4] for d in list(rev) + list(oi)), default=None)
    rows = []

    def add(row):
        """Keep a row only if it has 2+ points AND its newest point is current."""
        if row["n"] >= 2 and _is_current(row["points"], _latest):
            rows.append(row)

    if len(rev) >= 2:
        add(_money_row("revenue", "รายได้", rev, years))

    gp, gp_src = gross_profit(_get(f, "gross_profit_annuals_dated"),
                              _get(f, "revenue_annuals_dated"),
                              _get(f, "cost_of_revenue_annuals_dated"))
    if gp and rev:
        add(_margin_row("gross_margin", "Gross margin", gp, rev, years, note=gp_src))

    if oi and rev:
        add(_margin_row("op_margin", "Op margin", oi, rev, years,
                        note="EBIT ตามงบ (GAAP) — บริษัทอาจประกาศเป็น adjusted"))

    fcf = free_cash_flow(_get(f, "cfo_annuals_dated"), _get(f, "capex_annuals_dated"))
    if len(fcf) >= 2:
        add(_money_row("fcf", "Free cash flow", fcf, years, note="CFO − capex (งบกระแสเงินสด)"))

    rmap = roic_series(_get(f, "operating_income_annuals_dated"),
                       _get(f, "ic_components_dated", {}), _tax_rate(f))
    if len(rmap) >= 2:
        win = _window(rmap, years)
        pts = [{"fy": d[:4], "end": d, "v": round(rmap[d]["roic"], 1)} for d in win]
        inc, why = incremental_roic_pct(rmap, win)
        # The headline for this row is the INCREMENTAL return when it can be measured,
        # because that is the moat signal; the sparkline still shows the level. When it
        # cannot, fall back to the change in level so the row is never blank.
        if inc is not None:
            row = _row("roic", "ROIC", "pct", pts, inc, "ทุนใหม่", MARGIN_FLAT_PP,
                       note=f"เส้น = ROIC รายปี · ตัวเลข = ผลตอบแทนของทุนที่ใส่เพิ่ม {len(win)} ปีนี้",
                       summary_unit="%")   # a RETURN on the capital added, not a delta
            # the comparison Damodaran cares about: new capital vs the legacy average
            row["vs_average_pp"] = round(inc - pts[-1]["v"], 1)
        else:
            row = _row("roic", "ROIC", "pct", pts,
                       round(pts[-1]["v"] - pts[0]["v"], 1) if len(pts) >= 2 else None,
                       "Δ 5y", MARGIN_FLAT_PP, note=f"incremental ROIC: {why}",
                       summary_unit="pp")  # fell back to the CHANGE in level
        add(row)

    if not rows:
        return {}
    n_max = max(r["n"] for r in rows)
    return {"rows": rows, "years": n_max,
            "source": "SEC 10-K (GAAP)",
            "partial": any(r["n"] < min(years, n_max) for r in rows)}
