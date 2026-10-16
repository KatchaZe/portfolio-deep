"""
Correlation Monitor (Correlation tab): pairwise, sector, downside, and rolling
correlation, plus the downside-risk lens (Damodaran S2/S4: risk is downside, not
symmetric vol). Consumes the same return series the Risk Desk already fetched.
"""
import math

from .returns import TRADING_DAYS, align_pair, mean, pair_corr


def pair_corr_dated(a_rets, a_dates, b_rets, b_dates):
    return pair_corr(*align_pair(a_rets, a_dates, b_rets, b_dates))


def sector_corr(matrix, tickers, sectors):
    """Average pairwise correlation within each sector, plus across the whole portfolio.
    Returns {'sector_avg': {sector: {avg, n}}, 'avg_pairwise': float|None}."""
    n = len(tickers)
    by, all_pairs = {}, []
    for i in range(n):
        for j in range(i + 1, n):
            v = matrix[i][j]
            all_pairs.append(v)
            si, sj = sectors.get(tickers[i]), sectors.get(tickers[j])
            if si and si == sj:
                by.setdefault(si, []).append(v)
    sector_avg = {s: {"avg": round(sum(vs) / len(vs), 3), "n": len(vs)}
                  for s, vs in by.items()}
    avg_pairwise = round(sum(all_pairs) / len(all_pairs), 3) if all_pairs else None
    return {"sector_avg": sector_avg, "avg_pairwise": avg_pairwise}


def top_pairs(matrix, tickers, k=5):
    """k highest- and k lowest-correlated pairs as [ticker_a, ticker_b, rho]."""
    n = len(tickers)
    pairs = [[tickers[i], tickers[j], round(matrix[i][j], 3)]
             for i in range(n) for j in range(i + 1, n)]
    pairs.sort(key=lambda p: -p[2])
    return {"high": pairs[:k], "low": list(reversed(pairs[-k:]))}


def downside_corr(a, b, market, threshold=0.0):
    """Correlation of a, b on days the MARKET return is below `threshold`. None if <2 such days."""
    n = min(len(a or []), len(b or []), len(market or []))
    if n < 2:
        return None
    a, b, market = a[-n:], b[-n:], market[-n:]
    idx = [i for i in range(n) if market[i] < threshold]
    if len(idx) < 2:
        return None
    return pair_corr([a[i] for i in idx], [b[i] for i in idx])


def rolling_corr(a, b, window=60):
    """Trailing-window correlation, one value per step once the window is full."""
    n = min(len(a or []), len(b or []))
    if n < window or window < 2:
        return []
    a, b = a[-n:], b[-n:]
    return [round(pair_corr(a[e - window:e], b[e - window:e]) or 0.0, 3)
            for e in range(window, n + 1)]


def semideviation(returns, mar=0.0):
    """Annualized downside deviation: sqrt(mean over ALL observations of squared
    shortfalls below `mar`). 0.0 when nothing falls below it."""
    if not returns or len(returns) < 2:
        return 0.0
    downs = [r - mar for r in returns if r < mar]
    if not downs:
        return 0.0
    msq = sum(d * d for d in downs) / len(returns)
    return math.sqrt(msq) * math.sqrt(TRADING_DAYS)


def sortino(returns, mar=0.0):
    """Annualized excess mean / downside deviation. None without downside deviation."""
    if not returns:
        return None
    sd = semideviation(returns, mar)
    if not sd:
        return None
    return round((mean(returns) - mar) * TRADING_DAYS / sd, 2)


def downside_beta(asset_returns, market_returns):
    """cov(a, m | m<0) / var(m | m<0), population moments. None below 10 points or 5 down days."""
    n = min(len(asset_returns), len(market_returns))
    if n < 10:
        return None
    a, m = asset_returns[-n:], market_returns[-n:]
    pairs = [(a[i], m[i]) for i in range(n) if m[i] < 0]
    if len(pairs) < 5:
        return None
    ma = sum(p[0] for p in pairs) / len(pairs)
    mm = sum(p[1] for p in pairs) / len(pairs)
    cov = sum((p[0] - ma) * (p[1] - mm) for p in pairs) / len(pairs)
    var = sum((p[1] - mm) ** 2 for p in pairs) / len(pairs)
    return round(cov / var, 2) if var > 0 else None
