"""
Relative valuation against the firm's own peers (Damodaran, "Relative valuation":
regress the multiple on its fundamentals instead of comparing raw medians).

    EV/Sales_i = a + b * operating margin_i  (+ c * revenue growth_i when n >= 8)

Every peer and the target are measured on ONE clock: EV at the T-1 close, TTM revenue
and TTM operating income from SEC, growth = the last two reported fiscal years. A peer
whose numbers cannot be put on that clock is excluded with the reason, never estimated.

Pure: no network, no clock of its own (`today` is passed in). A cross-check only: it
never moves the fair value or the Price score.
"""
import datetime as dt

MIN_PEERS = 5               # fewer points than this and a 2-3 parameter fit is noise
GROWTH_TERM_MIN = 8         # the growth term needs more points than the margin-only fit
WEAK_R2 = 0.30              # below this the regression explains too little to quote
MARGIN_FLOOR = -0.50        # deep loss-makers price on a story, not on margin
STALE_BASE_DAYS = 183       # TTM older than ~6 months is a different clock
PRICE_LAG_DAYS = 5          # a peer price more than 5 days from the target's is stale
FY_GAP = (330, 400)         # two fiscal years must be about one year apart
IQR_K = 1.5


def _date(s):
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def _days(a, b):
    da, db = _date(a), _date(b)
    return abs((da - db).days) if da and db else None


def fy_growth(annuals_dated):
    """(growth, fy_end) from [[end, revenue], ...] newest first, else (None, reason)."""
    a = [r for r in (annuals_dated or []) if r and r[1]]
    if len(a) < 2:
        return None, "fewer than two fiscal years"
    gap = _days(a[0][0], a[1][0])
    if gap is None or not FY_GAP[0] <= gap <= FY_GAP[1]:
        return None, "fiscal years not one year apart"
    if a[1][1] <= 0:
        return None, "prior-year revenue not positive"
    return a[0][1] / a[1][1] - 1, str(a[0][0])[:10]


def point(sym, x, price, price_date, today, market_cap=None, shares=None, check_base=True):
    """One peer (or the target) on the common clock, or (None, reason).

    `x` is a sec_edgar.extract() dict (or the same keys from FinancialFacts).
    `market_cap` overrides price x diluted shares (an ADR's listing already embeds its
    depositary ratio). `check_base=False` for the target, whose base-period age the
    data-quality audit already flags."""
    if (x.get("currency") or "USD") != "USD":
        return None, f"reports in {x.get('currency')}"
    rev, oi = x.get("revenue"), x.get("operating_income")
    if not rev or rev <= 0:
        return None, "no TTM revenue"
    if oi is None:
        return None, "no operating income"
    end = x.get("latest_period_end")
    age = _days(end, today)
    if check_base and (age is None or age > STALE_BASE_DAYS):
        return None, f"latest filing {end or 'unknown'} older than 6 months"
    if not price or price <= 0 or not price_date:
        return None, "no T-1 close"
    shares = shares or x.get("shares_diluted")
    mcap = market_cap or (price * shares if shares else None)
    if not mcap or mcap <= 0:
        return None, "no share count"
    debt = (x.get("total_debt") or 0) + (x.get("operating_leases") or 0)
    ev = mcap + debt - (x.get("cash") or 0)
    if ev <= 0:
        return None, "enterprise value not positive (cash above market cap + debt)"
    margin = oi / rev
    if margin < MARGIN_FLOOR:
        return None, f"operating margin {margin:.0%} below {MARGIN_FLOOR:.0%}"
    growth, fy = fy_growth(x.get("revenue_annuals_dated"))
    if growth is None:
        return None, fy
    return {"symbol": sym, "ev_sales": ev / rev, "margin": margin, "growth": growth,
            "revenue": rev, "ev": ev, "market_cap": mcap, "period_end": str(end)[:10] if end else None,
            "fy_end": fy, "price": price, "price_date": str(price_date)[:10]}, None


# --------------------------------------------------------------------------- #
#  Ordinary least squares, small k, no numpy                                  #
# --------------------------------------------------------------------------- #
def _solve(a, b):
    """Gaussian elimination with partial pivoting; None when singular."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(m[r][c]))
        if abs(m[p][c]) < 1e-12:
            return None
        m[c], m[p] = m[p], m[c]
        for r in range(n):
            if r != c:
                f = m[r][c] / m[c][c]
                m[r] = [vr - f * vc for vr, vc in zip(m[r], m[c])]
    return [m[i][n] / m[i][i] for i in range(n)]


def ols(xs, ys):
    """Coefficients [intercept, b1, ...] for rows `xs` (lists of regressors)."""
    rows = [[1.0] + list(r) for r in xs]
    k = len(rows[0])
    xtx = [[sum(r[i] * r[j] for r in rows) for j in range(k)] for i in range(k)]
    xty = [sum(r[i] * y for r, y in zip(rows, ys)) for i in range(k)]
    return _solve(xtx, xty)


def _predict(coef, r):
    return coef[0] + sum(c * v for c, v in zip(coef[1:], r))


def r_squared(coef, xs, ys):
    mean = sum(ys) / len(ys)
    ss_tot = sum((y - mean) ** 2 for y in ys)
    ss_res = sum((y - _predict(coef, r)) ** 2 for r, y in zip(xs, ys))
    return 1 - ss_res / ss_tot if ss_tot > 0 else None


def loo_errors(xs, ys):
    """|predicted - actual| / actual for each point, predicted from the others."""
    out = []
    for i in range(len(ys)):
        c = ols(xs[:i] + xs[i + 1:], ys[:i] + ys[i + 1:])
        if c is not None:
            out.append(abs(_predict(c, xs[i]) - ys[i]) / ys[i])
    return out


def _median(v):
    v = sorted(v)
    n = len(v)
    return None if not n else (v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2)


def _quartiles(v):
    v = sorted(v)
    def q(p):
        i = p * (len(v) - 1)
        lo = int(i)
        return v[lo] + (v[min(lo + 1, len(v) - 1)] - v[lo]) * (i - lo)
    return q(0.25), q(0.75)


def drop_outliers(points):
    """(kept, dropped) by the EV/Sales interquartile fence."""
    if len(points) < 4:
        return points, []
    q1, q3 = _quartiles([p["ev_sales"] for p in points])
    lo, hi = q1 - IQR_K * (q3 - q1), q3 + IQR_K * (q3 - q1)
    kept = [p for p in points if lo <= p["ev_sales"] <= hi]
    return kept, [p for p in points if p not in kept]


def fit(peers):
    """The regression on `peers` (points), or (None, reason)."""
    kept, dropped = drop_outliers(peers)
    if len(kept) < MIN_PEERS:
        return None, f"{len(kept)} usable peers (need {MIN_PEERS})"
    terms = ["margin", "growth"] if len(kept) >= GROWTH_TERM_MIN else ["margin"]
    xs = [[p[t] for t in terms] for p in kept]
    ys = [p["ev_sales"] for p in kept]
    coef = ols(xs, ys)
    if coef is None:
        return None, "peers do not vary on margin/growth (singular fit)"
    loo = loo_errors(xs, ys)
    r2 = r_squared(coef, xs, ys)
    return {"terms": terms, "coef": [round(c, 4) for c in coef],
            "r2": None if r2 is None else round(r2, 3), "n": len(kept),
            "loo_median_err": None if not loo else round(_median(loo), 3),
            "weak": r2 is None or r2 < WEAK_R2,
            "peer_median_ev_sales": round(_median(ys), 3),
            "margin_range": [round(min(p["margin"] for p in kept), 4),
                             round(max(p["margin"] for p in kept), 4)],
            "growth_range": [round(min(p["growth"] for p in kept), 4),
                             round(max(p["growth"] for p in kept), 4)],
            "peers": [{k: (round(v, 4) if isinstance(v, float) else v)
                       for k, v in p.items() if k in ("symbol", "ev_sales", "margin", "growth",
                                                      "period_end", "price_date", "sic",
                                                      "addback")} for p in kept],
            "outliers": [p["symbol"] for p in dropped]}, None


def implied(model, target, net_debt, shares):
    """Per-share value of the target at the fitted multiple and at the peer median.

    `target` is the target's own point (same clock); `net_debt` = debt + leases - cash;
    `shares` is the count the price is quoted on."""
    if not model or not target or not shares or shares <= 0:
        return {}
    x = [target[t] for t in model["terms"]]
    mult = _predict(model["coef"], x)
    lo, hi = model["margin_range"]
    flags = []
    if not lo <= target["margin"] <= hi:
        flags.append("target margin outside the peers' range (extrapolated)")
    if "growth" in model["terms"]:
        glo, ghi = model["growth_range"]
        if not glo <= target["growth"] <= ghi:
            flags.append("target growth outside the peers' range (extrapolated)")
    per_share = lambda m: (m * target["revenue"] - net_debt) / shares
    out = {"target_ev_sales": round(target["ev_sales"], 3),
           "target_margin": round(target["margin"], 4), "target_growth": round(target["growth"], 4),
           "implied_ev_sales": round(mult, 3),
           "value_regression": None, "value_median": None, "flags": flags}
    if mult <= 0:
        flags.append("the fit implies a non-positive multiple at this margin")
    else:
        out["value_regression"] = round(per_share(mult), 2)
    out["value_median"] = round(per_share(model["peer_median_ev_sales"]), 2)
    for k in ("value_regression", "value_median"):
        if out[k] is not None and out[k] <= 0:
            flags.append(f"{k.split('_')[1]} value not positive: net debt exceeds the implied EV")
            out[k] = None
    return out
