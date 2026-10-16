"""
Daily return series: the sample statistics every risk number is built from, and the
alignment that decides which bars get paired.

Dates are authoritative and positions are not. Three price tiers publish at different
times, so a series one bar short pairs NVDA(t) with AVGO(t-1) when trimmed by position:
that turned rho(NVDA, AVGO) from +0.67 into -0.08 and moved VaR95 from 50.8 to 39.4
without a warning. Every `*_dated` / `align_on_dates` path joins on the date and falls
back to the common tail only for caches written before dates were carried.
"""
import math

TRADING_DAYS = 252


def mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else 0.0


def stdev(xs):
    """Sample standard deviation (n-1). Returns 0.0 for <2 points."""
    xs = list(xs)
    n = len(xs)
    if n < 2:
        return 0.0
    m = mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))


def tail_cov(a, b, n):
    """Sample covariance (n-1) of the last `n` points of two series. Daily, not annualized."""
    a, b = a[-n:], b[-n:]
    ma, mb = mean(a), mean(b)
    return sum((a[i] - ma) * (b[i] - mb) for i in range(n)) / (n - 1)


def daily_returns(closes):
    """r_t = close_t / close_{t-1} - 1 from an ascending price series. Skips non-positive prices."""
    closes = [c for c in (closes or []) if c and c > 0]
    return [closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes))]


def annualized_vol(returns):
    return stdev(returns) * math.sqrt(TRADING_DAYS)


def pair_corr(a, b):
    """Pearson correlation over the common tail window. None if <2 overlapping points
    or a series is flat. POSITIONAL: prefer pair_corr_dated whenever dates exist."""
    n = min(len(a or []), len(b or []))
    if n < 2:
        return None
    a, b = a[-n:], b[-n:]
    sa, sb = stdev(a), stdev(b)
    if sa <= 0 or sb <= 0:
        return None
    return tail_cov(a, b, n) / (sa * sb)


def align_returns(returns_by_ticker, tickers):
    """Trim every series to the shortest one's length. POSITIONAL: only correct when
    every series ends on the same trading day; prefer align_on_dates."""
    n = min((len(returns_by_ticker.get(t, [])) for t in tickers if returns_by_ticker.get(t, [])),
            default=0)
    if n < 2:
        return {t: [] for t in tickers}
    return {t: (returns_by_ticker.get(t, [])[-n:] if returns_by_ticker.get(t) else []) for t in tickers}


def _common_dates(rdata, tickers):
    common = None
    for t in tickers:
        ds = set((rdata.get(t) or {}).get("dates") or [])
        common = ds if common is None else (common & ds)
    return common or set()


def align_on_dates(rdata, tickers):
    """`rdata` is pipeline.prices.fetch_returns' {ticker: {returns, dates, n, ...}}.
    Returns ({ticker: [returns]} on the dates EVERY ticker shares, mode) where mode is
    "dates", or "positional" when any ticker lacks dates."""
    have_dates = all((rdata.get(t) or {}).get("dates") for t in tickers)
    if not tickers or not have_dates:
        return (align_returns({t: (rdata.get(t) or {}).get("returns", []) for t in tickers},
                              tickers),
                "positional")
    common = _common_dates(rdata, tickers)
    if len(common) < 2:
        return ({t: [] for t in tickers}, "dates")
    order = sorted(common)
    aligned = {}
    for t in tickers:
        e = rdata.get(t) or {}
        by_date = dict(zip(e.get("dates") or [], e.get("returns") or []))
        aligned[t] = [by_date[d] for d in order]
    return aligned, "dates"


def align_pair(a_rets, a_dates, b_rets, b_dates):
    """Two return series restricted to the dates they SHARE -> (a, b). Falls back to the
    common tail when either side has no dates (or dates of the wrong length)."""
    a_rets, b_rets = list(a_rets or []), list(b_rets or [])
    if a_dates and b_dates and len(a_dates) == len(a_rets) and len(b_dates) == len(b_rets):
        common = sorted(set(a_dates) & set(b_dates))
        if len(common) < 2:
            return [], []
        am, bm = dict(zip(a_dates, a_rets)), dict(zip(b_dates, b_rets))
        return [am[d] for d in common], [bm[d] for d in common]
    n = min(len(a_rets), len(b_rets))
    return (a_rets[-n:], b_rets[-n:]) if n else ([], [])


def portfolio_returns(returns_by_ticker, tickers, weights):
    """Weighted daily portfolio-return series, positionally aligned. A holding with no
    price history contributes 0 rather than raising, so one failed source cannot take
    the Correlation tab down."""
    aligned = align_returns(returns_by_ticker, tickers)
    have = [t for t in tickers if aligned[t]]
    n = min((len(aligned[t]) for t in have), default=0)
    if n == 0:
        return []
    return [sum(weights.get(t, 0.0) * aligned[t][-n:][i] for t in have) for i in range(n)]


def portfolio_returns_dated(rdata, tickers, weights):
    """(series, dates) for the weighted portfolio return on the dates every covered
    holding shares. Returning the dates lets the portfolio itself be date-aligned
    against SPY instead of pinned to it by position."""
    aligned, mode = align_on_dates(rdata, tickers)
    have = [t for t in tickers if aligned.get(t)]
    if not have:
        return [], []
    n = min(len(aligned[t]) for t in have)
    if n == 0:
        return [], []
    series = [sum(weights.get(t, 0.0) * aligned[t][i] for t in have) for i in range(n)]
    if mode != "dates":
        return series, []
    return series, sorted(_common_dates(rdata, tickers))
