"""
Factor exposures of the book: each holding's daily excess return regressed on the
Fama-French five factors plus momentum (Ken French data library), the portfolio's
exposure the value-weighted sum, and its variance split into what the factors
explain and what is specific to the names (the Barra decomposition, with academic
factors in place of Barra's).

Factors: MKT (market minus T-bill), SMB (small minus big), HML (value minus growth),
RMW (robust minus weak profitability), CMA (conservative minus aggressive
investment), MOM (winners minus losers). Pure: no I/O.
"""
FACTORS = ("MKT", "SMB", "HML", "RMW", "CMA", "MOM")
WINDOW = 504          # two years of trading days
MIN_OBS = 120


def _solve(a, b):
    """x with a @ x = b (Gaussian elimination, partial pivoting); None if singular."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(m[r][c]))
        if abs(m[p][c]) < 1e-14:
            return None
        m[c], m[p] = m[p], m[c]
        for r in range(n):
            if r != c:
                f = m[r][c] / m[c][c]
                for k in range(c, n + 1):
                    m[r][k] -= f * m[c][k]
    return [m[i][n] / m[i][i] for i in range(n)]


def regress(rets, dates, factors, window=WINDOW, min_obs=MIN_OBS):
    """{betas, alpha_ann, r2, resid_var, n} for one holding, or None. `factors` is
    {date: {MKT, SMB, HML, RMW, CMA, MOM, RF}} in decimals."""
    rows = [(r - factors[d]["RF"], [1.0] + [factors[d][k] for k in FACTORS])
            for r, d in zip(rets, dates) if r is not None and d in factors][-window:]
    n = len(rows)
    if n < min_obs:
        return None
    k = len(FACTORS) + 1
    xtx = [[sum(x[i] * x[j] for _, x in rows) for j in range(k)] for i in range(k)]
    xty = [sum(x[i] * y for y, x in rows) for i in range(k)]
    coef = _solve(xtx, xty)
    if coef is None:
        return None
    resid = [y - sum(c * xi for c, xi in zip(coef, x)) for y, x in rows]
    ybar = sum(y for y, _ in rows) / n
    sst = sum((y - ybar) ** 2 for y, _ in rows)
    sse = sum(e * e for e in resid)
    return {"betas": dict(zip(FACTORS, coef[1:])), "alpha_ann": coef[0] * 252,
            "r2": 1 - sse / sst if sst > 0 else None,
            "resid_var": sse / max(1, n - k), "n": n}


def factor_cov(factors, dates):
    """Daily covariance matrix of the factors over `dates`."""
    rows = [[factors[d][f] for f in FACTORS] for d in dates if d in factors]
    n = len(rows)
    mu = [sum(r[i] for r in rows) / n for i in range(len(FACTORS))]
    return [[sum((r[i] - mu[i]) * (r[j] - mu[j]) for r in rows) / (n - 1)
             for j in range(len(FACTORS))] for i in range(len(FACTORS))]


def decompose(weights, series, factors):
    """Portfolio factor exposures and variance split. `series` {ticker: (rets, dates)}.
    Names without enough overlapping history are listed, not guessed."""
    fits, missing = {}, []
    for t in weights:
        rets, dates = series.get(t) or ([], [])
        fit = regress(rets, dates, factors)
        if fit:
            fits[t] = fit
        else:
            missing.append(t)
    covered = sum(weights[t] for t in fits)
    if not fits or covered <= 0:
        return {"status": "insufficient", "missing": missing,
                "note": "ไม่มีหุ้นที่มีประวัติราคาทับกับข้อมูล factor พอ (≥ 120 วัน)"}
    w = {t: weights[t] / covered for t in fits}            # re-weighted over the covered book
    expo = {f: sum(w[t] * fits[t]["betas"][f] for t in fits) for f in FACTORS}
    dates = sorted(factors)[-WINDOW:]
    cov = factor_cov(factors, dates)
    b = [expo[f] for f in FACTORS]
    fb = [sum(cov[i][j] * b[j] for j in range(len(b))) for i in range(len(b))]
    var_f = sum(b[i] * fb[i] for i in range(len(b)))
    var_s = sum(w[t] ** 2 * fits[t]["resid_var"] for t in fits)
    total = var_f + var_s
    return {"status": "ok",
            "exposures": {f: round(v, 2) for f, v in expo.items()},
            "risk_share_pct": {f: round(b[i] * fb[i] / total * 100, 1) for i, f in enumerate(FACTORS)},
            "factor_share_pct": round(var_f / total * 100, 1),
            "specific_share_pct": round(var_s / total * 100, 1),
            "vol_pct": round((total * 252) ** 0.5 * 100, 1),
            "names": {t: {"betas": {f: round(v, 2) for f, v in fits[t]["betas"].items()},
                          "r2": round(fits[t]["r2"], 2) if fits[t]["r2"] is not None else None,
                          "n": fits[t]["n"]} for t in fits},
            "covered_weight_pct": round(covered * 100, 1), "missing": missing,
            "factors_through": dates[-1] if dates else None,
            "tag": "[CALC] Fama-French 5 + Momentum (Ken French data library), 2y daily OLS"}
