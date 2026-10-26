"""
Covariance matrices for the Risk Desk: realized, assumed (proxy), per-pair hybrid, and
the crisis regime. Annualized throughout; matrices are list-of-lists in ticker order.
"""
import math

from .returns import TRADING_DAYS, align_returns, annualized_vol, pair_corr, tail_cov

# Coarse asset-class annual vol for names with no price history. [JUDG-PROXY], caller tags.
PROXY_VOL = {
    "us_large": 0.16, "tech": 0.21, "growth": 0.50, "btc": 0.65,
    "gov_bond": 0.15, "gold": 0.15, "cash": 0.01, "intl": 0.20,
    "defensive": 0.13, "reit": 0.20,
}
CRISIS_EQUITY_CORR = 0.90      # [JUDG-PROXY] equity pairs converge in a crisis


def _is_equity(asset_class, t):
    ac = (asset_class.get(t) or "equity").lower()
    return not any(k in ac for k in ("bond", "gold", "cash", "real"))


def _assumed_corr(asset_class, ti, tj, equity_corr, cross_corr):
    both_equity = _is_equity(asset_class, ti) and _is_equity(asset_class, tj)
    return equity_corr if both_equity else cross_corr


def _identity(n):
    return [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]


def vols_from_cov(cov):
    return [math.sqrt(cov[i][i]) if cov[i][i] > 0 else 0.0 for i in range(len(cov))]


def _covariance(a, b):
    """Annualized sample covariance over the common tail; 0.0 below 2 points."""
    n = min(len(a), len(b))
    if n < 2:
        return 0.0
    return tail_cov(a, b, n) * TRADING_DAYS


def cov_matrix(returns_by_ticker, tickers):
    """Realized covariance over a common (positional) window."""
    aligned = align_returns(returns_by_ticker, tickers)
    n = len(tickers)
    M = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i, n):
            M[i][j] = M[j][i] = _covariance(aligned[tickers[i]], aligned[tickers[j]])
    return M


def proxy_cov(tickers, vols, asset_class, equity_corr=0.6, cross_corr=0.2):
    """Per-name vols + an ASSUMED correlation: `equity_corr` between equities,
    `cross_corr` for any pair touching bond/gold/cash/real. [JUDG-PROXY]."""
    n = len(tickers)
    return [[(1.0 if i == j else
              _assumed_corr(asset_class, tickers[i], tickers[j], equity_corr, cross_corr))
             * vols[i] * vols[j] for j in range(n)] for i in range(n)]


def _corr_common_window(a, b):
    """pair_corr clamped to [-1, 1] to absorb floating-point overshoot."""
    rho = pair_corr(a, b)
    return None if rho is None else max(-1.0, min(1.0, rho))


def hybrid_cov(returns_by_ticker, tickers, proxy_vols, asset_class,
               min_n=60, equity_corr=0.6, cross_corr=0.2):
    """Realized correlation for pairs where BOTH names have >= min_n returns, the
    assumed one for pairs touching a thin name. Returns (cov, realized_tickers).

    Correlation is measured on the window the pair shares, volatility on each name's own
    full history. Mixing the two windows inside one covariance produced rho = 2.59, a
    negative w'Sigma w, and a portfolio vol of 0 that silently blanked VaR, ENB and risk
    contributions. Rebuilding cov = rho*vol_i*vol_j keeps |rho| <= 1, and shrink_to_psd
    keeps the matrix jointly consistent."""
    n = len(tickers)
    rets = {t: (returns_by_ticker.get(t) or []) for t in tickers}
    has = {t: len(rets[t]) >= min_n for t in tickers}
    vols = [annualized_vol(rets[t]) if has[t] else proxy_vols[i]
            for i, t in enumerate(tickers)]
    R = _identity(n)
    for i in range(n):
        for j in range(i + 1, n):
            ti, tj = tickers[i], tickers[j]
            rho = None
            if has[ti] and has[tj] and min(len(rets[ti]), len(rets[tj])) >= min_n:
                rho = _corr_common_window(rets[ti], rets[tj])   # None: flat in the overlap
            if rho is None:
                rho = _assumed_corr(asset_class, ti, tj, equity_corr, cross_corr)
            R[i][j] = R[j][i] = rho
    R = shrink_to_psd(R)
    C = [[R[i][j] * vols[i] * vols[j] for j in range(n)] for i in range(n)]
    return C, [t for t in tickers if has[t]]


def _cholesky_ok(M):
    """True when M is positive definite (pure-python Cholesky; matrices here are <=30x30)."""
    n = len(M)
    L = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1):
            s = sum(L[i][k] * L[j][k] for k in range(j))
            if i == j:
                d = M[i][i] - s
                if d <= 1e-12:
                    return False
                L[i][j] = math.sqrt(d)
            else:
                L[i][j] = (M[i][j] - s) / L[j][j]
    return True


def shrink_to_psd(R, floor=0.0):
    """Shrink OFF-DIAGONALS toward zero, R(w) = w*R + (1-w)*I, until positive definite.
    Pairwise correlations from differing windows need not be jointly consistent;
    shrinking is the mildest repair that keeps every pair's sign and relative size."""
    if len(R) < 2 or _cholesky_ok(R):
        return R
    n = len(R)
    w = 0.99
    while w > floor:
        S = [[R[i][j] * (1.0 if i == j else w) for j in range(n)] for i in range(n)]
        if _cholesky_ok(S):
            return S
        w -= 0.05
    return _identity(n)


def corr_from_cov(cov):
    n = len(cov)
    sd = vols_from_cov(cov)
    return [[(cov[i][j] / (sd[i] * sd[j])) if (sd[i] > 0 and sd[j] > 0) else 0.0
             for j in range(n)] for i in range(n)]


CRISIS_MIN_DRAWDOWN = -0.10    # the episode must be a real sell-off (S&P 500 -10% or worse)
CRISIS_MIN_DAYS = 20           # fewer shared days in the episode -> the pair keeps the assumed floor


def crisis_window(market_rets, market_dates, min_drawdown=CRISIS_MIN_DRAWDOWN):
    """(dates, drawdown) of the market's worst peak-to-trough episode in the history: the
    crisis whose correlation is measured. A contiguous episode keeps up and down days, so
    the common fall is in the co-movement. (Selecting only the down days does not work:
    Pearson removes their shared mean and the 'crisis' correlation came out LOWER than
    normal, 0.14 vs 0.18 on the live book.) Empty set when no episode reaches
    min_drawdown."""
    rets, dates = list(market_rets or []), list(market_dates or [])
    if not rets or len(rets) != len(dates):
        return set(), None
    level, peak_lvl, peak_i, best = 1.0, 1.0, 0, (0.0, 0, 0)
    for i, r in enumerate(rets):
        level *= (1 + r)
        if level > peak_lvl:
            peak_lvl, peak_i = level, i + 1
        dd = level / peak_lvl - 1
        if dd < best[0]:
            best = (dd, peak_i, i + 1)
    dd, a, b = best
    if dd > min_drawdown:
        return set(), round(dd, 4)
    return set(dates[a:b]), round(dd, 4)


def crisis_days(market_rets, market_dates):
    """The crisis episode's dates (see crisis_window)."""
    return crisis_window(market_rets, market_dates)[0]


def crisis_pair_corr(a_rets, a_dates, b_rets, b_dates, days, min_days=CRISIS_MIN_DAYS):
    """(rho, n) of two dated series on the crisis days both have; rho None below min_days."""
    am = dict(zip(a_dates or [], a_rets or []))
    bm = dict(zip(b_dates or [], b_rets or []))
    common = sorted(days & set(am) & set(bm))
    if len(common) < min_days:
        return None, len(common)
    rho = pair_corr([am[d] for d in common], [bm[d] for d in common])
    return (None if rho is None else max(-1.0, min(1.0, rho))), len(common)


def crisis_cov_measured(cov, tickers, asset_class, series, days, floor=None,
                        min_days=CRISIS_MIN_DAYS):
    """Crisis covariance from correlations MEASURED on the market's crisis days (S41:
    test diversification with crisis correlation, not the full sample). `series` is
    {ticker: (returns, dates)} over the long history. A pair with fewer than min_days
    shared crisis days falls back to the assumed regime (equity pairs floored at
    CRISIS_EQUITY_CORR, others keep their normal correlation). Each name keeps its
    normal vol. Returns (cov, info)."""
    floor = CRISIS_EQUITY_CORR if floor is None else floor
    n = len(cov)
    sd = vols_from_cov(cov)
    base = corr_from_cov(cov)
    R = _identity(n)
    measured, assumed, counts = 0, 0, []
    for i in range(n):
        for j in range(i + 1, n):
            ti, tj = tickers[i], tickers[j]
            rho, k = crisis_pair_corr(*(series.get(ti) or ([], [])), *(series.get(tj) or ([], [])), days,
                                      min_days)
            counts.append(k)
            if rho is None:
                both_eq = _is_equity(asset_class, ti) and _is_equity(asset_class, tj)
                rho = max(base[i][j], floor) if both_eq else base[i][j]
                assumed += 1
            else:
                measured += 1
            R[i][j] = R[j][i] = rho
    R = shrink_to_psd(R)
    C = [[R[i][j] * sd[i] * sd[j] for j in range(n)] for i in range(n)]
    pairs = measured + assumed
    info = {"method": ("measured" if pairs and not assumed else "assumed" if not measured else "mixed"),
            "crisis_days": len(days), "measured_pairs": measured, "assumed_pairs": assumed,
            "min_pair_days": min(counts) if counts else 0,
            "window_days": len(days), "min_days": min_days,
            "first_day": min(days) if days else None, "last_day": max(days) if days else None}
    return C, info


def crisis_cov(cov, tickers, asset_class, floor=None):
    """Keep each name's vol, floor equity-equity correlations at the crisis level
    (diversification vanishes in a crash); non-equity pairs keep their realized
    correlation. [JUDG-PROXY]."""
    floor = CRISIS_EQUITY_CORR if floor is None else floor
    n = len(cov)
    sd = vols_from_cov(cov)
    corr = corr_from_cov(cov)
    C = [row[:] for row in corr]
    for i in range(n):
        for j in range(n):
            if i != j and _is_equity(asset_class, tickers[i]) and _is_equity(asset_class, tickers[j]):
                C[i][j] = max(corr[i][j], floor)
    return [[C[i][j] * sd[i] * sd[j] for j in range(n)] for i in range(n)]
