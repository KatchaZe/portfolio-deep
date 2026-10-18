"""
Faithful quant-momentum (QuantInsti framework: time-series + cross-sectional).

PRIMARY readout for the dashboard. The old RSI<30 / DBBMV signals in
`indicators.py` are mean-reversion, not momentum, so they stay only as SECONDARY
confirmation. This module is the momentum source of truth.

All functions are PURE over an *adjusted* daily close series (oldest -> newest),
so they unit-test offline. Use split+dividend adjusted closes (see
`refresh.get_prices_long`); unadjusted prices bias the longer lookbacks.

Composite label is a transparent vote over 4 visible components:
    MOM_12_1 > 0,  ROC_6M > 0,  price > SMA200,  12-1 beats SPY's 12-1 (same dates)
    4/4 Strong · 3/4 Positive · 2/4 Neutral · 1/4 Weak · 0/4 Negative
(fraction-based, so it degrades gracefully when history is short).
"""
import math

TRADING_MONTH = 21
TRADING_YEAR = 252
MIN_BARS = 30          # below this we can't say anything useful


# --------------------------------------------------------------------------- #
#  Core momentum measures (pure)                                              #
# --------------------------------------------------------------------------- #
def roc(closes, lookback):
    """Rate of change over `lookback` bars: P[-1]/P[-1-lookback] - 1. None if short."""
    if not closes or len(closes) <= lookback:
        return None
    base = closes[-1 - lookback]
    if not base:
        return None
    return closes[-1] / base - 1


def mom_12_1(closes):
    """Academic 12-1 momentum: 12-month return SKIPPING the most recent month
    (~21 bars) to avoid short-term reversal. Needs > ~252 bars."""
    if not closes or len(closes) <= TRADING_YEAR:
        return None
    p_recent = closes[-1 - TRADING_MONTH]     # ~1 month ago
    p_year = closes[-1 - TRADING_YEAR]        # ~12 months ago
    if not p_year:
        return None
    return p_recent / p_year - 1


def excess_12_1(closes, dates, mkt_closes, mkt_dates):
    """12-1 momentum minus the market's (SPY) return over the SAME two dates, so a
    +7% name in a +16% market reads as lagging. None when either date is missing
    from the market series (never shifts to a nearby bar)."""
    m = mom_12_1(closes)
    if m is None or not dates or not mkt_closes or not mkt_dates or len(dates) <= TRADING_YEAR:
        return None
    mkt = {str(d)[:10]: c for d, c in zip(mkt_dates, mkt_closes) if c and c > 0}
    p_recent = mkt.get(str(dates[-1 - TRADING_MONTH])[:10])
    p_year = mkt.get(str(dates[-1 - TRADING_YEAR])[:10])
    if not p_recent or not p_year:
        return None
    return m - (p_recent / p_year - 1)


def sma(closes, period):
    """Simple moving average of the last `period` bars. None if short."""
    if not closes or len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def annualized_vol(closes, window=126):
    """Annualised volatility of daily returns over `window` bars (sample stdev).
    Used to risk-adjust momentum (reduces momentum-crash exposure). None if short."""
    if not closes or len(closes) < window + 1:
        return None
    rets = [closes[i] / closes[i - 1] - 1 for i in range(len(closes) - window, len(closes))
            if closes[i - 1]]
    if len(rets) < 2:
        return None
    m = sum(rets) / len(rets)
    var = sum((r - m) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(TRADING_YEAR)


def rsi(closes, period=14):
    """Wilder RSI. Momentum reading: > 50 = bullish momentum (NOT the <30
    contrarian reading used by indicators.py). None if short."""
    if not closes or len(closes) < period + 1:
        return None
    g = l = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        g += d if d > 0 else 0
        l += -d if d < 0 else 0
    ag, al = g / period, l / period
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        ag = (ag * (period - 1) + max(d, 0)) / period
        al = (al * (period - 1) + max(-d, 0)) / period
    return 100.0 if al == 0 else 100 - 100 / (1 + ag / al)


def regression_beta(closes, dates, mkt_closes, mkt_dates, step=5, min_points=40):
    """P1-6 (S4): regression beta vs the market from WEEKLY returns (every `step`
    trading days) over the common date window — a free cross-check of the vendor
    (FMP) beta and the fallback when it is missing. Damodaran prefers bottom-up
    betas; regression betas are noisy, so this is a CROSS-CHECK, not the primary.
    Pure; returns None when data is short/misaligned (never fabricates)."""
    if not closes or not dates or not mkt_closes or not mkt_dates:
        return None
    mkt = {str(d)[:10]: c for d, c in zip(mkt_dates, mkt_closes) if c and c > 0}
    pairs = [(c, mkt[str(d)[:10]]) for d, c in zip(dates, closes)
             if c and c > 0 and str(d)[:10] in mkt]
    if len(pairs) < min_points * step // 2:
        return None
    a = [p[0] for p in pairs][::-1][::step][::-1]      # keep chronological, weekly
    m = [p[1] for p in pairs][::-1][::step][::-1]
    ra = [a[i] / a[i - 1] - 1 for i in range(1, len(a))]
    rm = [m[i] / m[i - 1] - 1 for i in range(1, len(m))]
    n = min(len(ra), len(rm))
    if n < min_points:
        return None
    ra, rm = ra[-n:], rm[-n:]
    mean_a, mean_m = sum(ra) / n, sum(rm) / n
    var_m = sum((x - mean_m) ** 2 for x in rm) / (n - 1)
    if var_m <= 0:
        return None
    cov = sum((ra[i] - mean_a) * (rm[i] - mean_m) for i in range(n)) / (n - 1)
    return round(cov / var_m, 2)


# --------------------------------------------------------------------------- #
#  S9 extensions — long-horizon reversal + market-regime crash guard          #
# --------------------------------------------------------------------------- #
def reversal_flag(closes, window=756, extreme=1.5, top=0.90):
    """Damodaran S9 (Random Walks & Momentum): over 3-5 years price runs tend to
    REVERSE (long-horizon mean reversion). Flags a name whose cumulative run over
    ~`window` bars (default 756 ~= 3y) is extreme (>= `extreme`, default +150%) AND
    that sits near the very top of that window's range (>= `top`). Pure; never
    fabricates -- returns risk=None when there is < `window` bars of history.

    Returns {risk, window_bars, cum_return, range_pos}."""
    n = len(closes) if closes else 0
    if n < window:
        return {"risk": None, "window_bars": n, "cum_return": None, "range_pos": None}
    seg = closes[-window:]
    base, last = seg[0], seg[-1]
    if not base or last <= 0:
        return {"risk": None, "window_bars": len(seg), "cum_return": None, "range_pos": None}
    cum = last / base - 1
    lo, hi = min(seg), max(seg)
    range_pos = (last - lo) / (hi - lo) if hi > lo else None
    risk = bool(cum >= extreme and range_pos is not None and range_pos >= top)
    return {"risk": risk, "window_bars": len(seg), "cum_return": round(cum, 4),
            "range_pos": round(range_pos, 3) if range_pos is not None else None}


def market_state(index_closes, period=200):
    """Damodaran S9: momentum CRASHES when the broad market turns. Reads the trend
    regime from an index series (e.g. SPY adjusted closes):
        risk_on  = index above its own SMA200  -> momentum signals are trustworthy
        risk_off = index below its own SMA200  -> momentum is crash-prone
    Pure; regime=None when there is < `period` bars. Returns
    {regime, above_sma200, dist_pct, n}."""
    n = len(index_closes) if index_closes else 0
    s = sma(index_closes, period)
    if s is None:
        return {"regime": None, "above_sma200": None, "dist_pct": None, "n": n}
    last = index_closes[-1]
    above = last > s
    return {"regime": "risk_on" if above else "risk_off", "above_sma200": above,
            "dist_pct": round((last / s - 1) * 100, 2), "n": n}


def crash_guard(mom_label, market_regime):
    """S9 momentum-crash guard: in a risk_off market, a bullish momentum reading is
    unreliable (momentum tends to crash at market turns). Returns True when caution
    applies -- i.e. the market is risk_off AND the name shows positive momentum."""
    if market_regime != "risk_off":
        return False
    return mom_label in ("Strong", "Positive")


# --------------------------------------------------------------------------- #
#  Data-quality guard (R1) — clean fetched series, never fabricate            #
# --------------------------------------------------------------------------- #
def clean_series(closes, volumes=None, dates=None, max_jump=0.5, revert=0.33):
    """Remove only CLEARLY-corrupt bars from a fetched price series — never
    fabricates, never fills. Drops:
        (a) non-positive / non-numeric closes, and
        (b) lone 1-day spikes: a move > max_jump that reverses by > revert the
            next bar (a data glitch). A real *sustained* jump is kept.
    Keeps closes/volumes/dates aligned. Returns (closes, volumes, dates, flags)
    with flags = {dropped_nonpos, dropped_spikes, max_jump_pct, bars}."""
    closes = list(closes or [])
    n = len(closes)
    volumes = list(volumes or [None] * n)
    dates = list(dates or [None] * n)
    if len(volumes) < n:
        volumes += [None] * (n - len(volumes))
    if len(dates) < n:
        dates += [None] * (n - len(dates))

    # (a) drop non-positive / non-numeric closes
    idx = [i for i in range(n) if isinstance(closes[i], (int, float)) and closes[i] > 0]
    dropped_nonpos = n - len(idx)
    c = [closes[i] for i in idx]
    v = [volumes[i] for i in idx]
    d = [dates[i] for i in idx]

    # (b) drop lone reverting spikes
    oc, ov, od = [], [], []
    spikes = 0
    max_jump_seen = 0.0
    for i in range(len(c)):
        if 0 < i < len(c) - 1 and c[i - 1] > 0 and c[i] > 0:
            r_in = c[i] / c[i - 1] - 1
            r_out = c[i + 1] / c[i] - 1
            if abs(r_in) > max_jump_seen:
                max_jump_seen = abs(r_in)
            if abs(r_in) > max_jump and abs(r_out) > revert and r_in * r_out < 0:
                spikes += 1
                continue                       # skip the glitch bar
        oc.append(c[i]); ov.append(v[i]); od.append(d[i])

    flags = {"dropped_nonpos": dropped_nonpos, "dropped_spikes": spikes,
             "max_jump_pct": round(max_jump_seen * 100, 1), "bars": len(oc)}
    return oc, ov, od, flags


# --------------------------------------------------------------------------- #
#  Composite                                                                  #
# --------------------------------------------------------------------------- #
_LABELS = ((1, "Strong"), (0.75, "Positive"), (0.5, "Neutral"))   # lowest fraction for each


def _label(score, n):
    """Map positive-component count to a label, fraction-based for partial data."""
    if not n:
        return None
    frac = score / n
    return next((label for lo, label in _LABELS if frac >= lo),
                "Weak" if frac > 0 else "Negative")


def compute(ticker, closes, dates=None, dividend_adjusted=True, market=None):
    """Full momentum payload for one ticker from an adjusted close series.
    `market` is the SPY series {"closes", "dates"}; without it the relative
    component is unavailable and the label is a fraction of the other three.

    Returns {ticker, error} when there is too little data. Otherwise returns the
    measures plus the composite (mom_label / mom_score / mom_n / components)."""
    if not closes or len(closes) < MIN_BARS:
        return {"ticker": ticker, "error": "insufficient_data",
                "bars": len(closes) if closes else 0}

    r3, r6, r12 = roc(closes, 63), roc(closes, 126), roc(closes, 252)
    m121 = mom_12_1(closes)
    s200 = sma(closes, 200)
    last = closes[-1]
    above200 = (last > s200) if s200 else None
    dist200 = (last / s200 - 1) if s200 else None
    vol = annualized_vol(closes, 126)
    rv = rsi(closes, 14)
    mk = market or {}
    xs = excess_12_1(closes, dates, mk.get("closes"), mk.get("dates"))

    # 4 transparent components (None when not enough history for that one)
    components = {
        "mom_12_1_pos": (m121 > 0) if m121 is not None else None,
        "roc_6m_pos": (r6 > 0) if r6 is not None else None,
        "above_sma200": above200,
        "beats_mkt_12_1": (xs > 0) if xs is not None else None,
    }
    avail = [v for v in components.values() if v is not None]
    n = len(avail)
    score = sum(1 for v in avail if v)
    label = _label(score, n)
    rev = reversal_flag(closes)                  # S9 long-horizon reversal (None if < 3y)

    def _r(x, nd=4):
        return round(x, nd) if isinstance(x, (int, float)) else x

    return {
        "ticker": ticker,
        "price": _r(last, 2),
        "roc_3m": _r(r3), "roc_6m": _r(r6), "roc_12m": _r(r12),
        "mom_12_1": _r(m121),
        "sma200": _r(s200, 2), "above_sma200": above200, "dist_sma200": _r(dist200),
        "vol_ann": _r(vol), "excess_12_1": _r(xs),
        "rsi": _r(rv, 1),
        "components": components,
        "reversal": rev,                          # S9: long-horizon reversal risk
        "mom_score": score, "mom_n": n, "mom_label": label,
        "dividend_adjusted": bool(dividend_adjusted),
        "as_of": dates[-1] if dates else None,
    }


def div_warn(dividend_adjusted, dividend_ps):
    """R2: should split-only momentum carry a dividend warning?
    Only when the series is NOT dividend-adjusted AND the name pays (or may pay)
    dividends. dividend_ps None = unknown -> warn (conservative); 0 -> no warn.
    A non-payer on a split-only feed is effectively fully adjusted (no warning)."""
    if dividend_adjusted is not False:
        return False
    if dividend_ps is None:
        return True
    return dividend_ps > 0


# --------------------------------------------------------------------------- #
#  Cross-sectional rank across the portfolio                                  #
# --------------------------------------------------------------------------- #
def _vol_adjusted_12_1(r):
    """12-1 / annualised vol: a steady climber outranks a lottery ticket with the
    same run. Read from stored fields, so rows saved before this existed rank too."""
    m, v = r.get("mom_12_1"), r.get("vol_ann")
    return m / v if (m is not None and v) else None


def cross_sectional_rank(results):
    """Rank `compute()` rows against each other on vol-adjusted 12-1 momentum.
    Mutates each row in place, adding:
        mom_rank_pct : 0..1 percentile (higher = stronger)  [None if no value]
        mom_bucket   : 'Top' / 'Mid' / 'Bottom' (terciles)
    Rows without 12-1 or vol (or with an error) are left unranked. Returns `results`."""
    vals = [r for r in results if isinstance(r, dict) and _vol_adjusted_12_1(r) is not None]
    vals.sort(key=_vol_adjusted_12_1)
    n = len(vals)
    for i, r in enumerate(vals):
        p = (i + 0.5) / n
        r["mom_rank_pct"] = round(p, 3)
        r["mom_bucket"] = "Top" if p >= 2 / 3 else "Bottom" if p < 1 / 3 else "Mid"
    for r in results:
        if isinstance(r, dict) and "mom_rank_pct" not in r:
            r["mom_rank_pct"] = None
            r["mom_bucket"] = None
    return results
