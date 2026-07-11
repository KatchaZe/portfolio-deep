"""
Risk engine for the Allocation tab — PURE FUNCTIONS, no I/O, no network.

Everything here takes plain numbers/lists/dicts and returns numbers/dicts, so it
is trivially unit-testable offline (see tests/test_risk.py) and safe: it cannot
touch the store, the FMP quota, or the Portfolio/Watchlist tabs.

Deliberately NO numpy/pandas — the app ships with neither (see requirements.txt),
and a portfolio is small (a handful of names), so plain Python loops are clear,
fast enough, and add zero deploy risk.

Sign / tag conventions used across the workflow:
  * weight  wᵢ   = market_valueᵢ / Σ market_value      (capital weight, sums to 1)
  * σ (vol)      = annualized standard deviation of daily returns
  * ρ            = correlation; Σ (cov) = covariance matrix (annualized)
  * σp           = portfolio volatility = √(wᵀ Σ w)
  * MCRᵢ         = marginal risk contribution; RCᵢ = wᵢ·MCRᵢ ; Σ RCᵢ = σp
  * Signed %RC   = RCᵢ / σp           (can be negative for a diversifier)
  * Absolute share qᵢ = |RCᵢ| / Σ|RCⱼ|;  ENB_abs = 1 / Σ qᵢ²
Epistemic tags ([FACT]/[CALC]/[JUDG-PROXY]/[JUDG-SCENARIO]) are attached by the
*caller* (app.py / risk_prices.py) — this module just does the math.
"""
import math

TRADING_DAYS = 252


# --------------------------------------------------------------------------- #
#  Small stats helpers (pure)                                                  #
# --------------------------------------------------------------------------- #
def _mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else 0.0


def _stdev(xs):
    """Sample standard deviation (n-1). Returns 0.0 for <2 points."""
    xs = list(xs)
    n = len(xs)
    if n < 2:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))


def daily_returns(closes):
    """Simple daily returns from a price series (ascending by date).
    r_t = close_t / close_{t-1} - 1. Skips non-positive prices."""
    closes = [c for c in (closes or []) if c and c > 0]
    out = []
    for i in range(1, len(closes)):
        prev = closes[i - 1]
        if prev > 0:
            out.append(closes[i] / prev - 1.0)
    return out


def annualized_vol(returns):
    """Annualized volatility from daily returns."""
    return _stdev(returns) * math.sqrt(TRADING_DAYS)


def _covariance(a, b):
    """Sample covariance of two equal-length daily-return series (annualized)."""
    n = min(len(a), len(b))
    if n < 2:
        return 0.0
    a, b = a[-n:], b[-n:]
    ma, mb = _mean(a), _mean(b)
    cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n)) / (n - 1)
    return cov * TRADING_DAYS


def align_returns(returns_by_ticker, tickers):
    """Trim every ticker's return series to the SAME length (the shortest), so the
    covariance matrix is computed over a common window. Returns a new dict."""
    series = [returns_by_ticker.get(t, []) for t in tickers]
    n = min((len(s) for s in series if s), default=0)
    if n < 2:
        return {t: [] for t in tickers}
    return {t: (returns_by_ticker.get(t, [])[-n:] if returns_by_ticker.get(t) else []) for t in tickers}


# --------------------------------------------------------------------------- #
#  Phase 0/1 — weights, concentration, grouping, one-factor (beta) risk        #
# --------------------------------------------------------------------------- #
def capital_weights(positions):
    """positions: {ticker: market_value}. Returns {ticker: weight} summing to 1.0.
    Non-positive / missing values are dropped."""
    mv = {t: float(v) for t, v in (positions or {}).items() if v and v > 0}
    total = sum(mv.values())
    if total <= 0:
        return {}
    return {t: v / total for t, v in mv.items()}


def concentration(weights):
    """HHI, Effective number of holdings, Top-5 / Top-10 shares."""
    ws = sorted([w for w in weights.values() if w > 0], reverse=True)
    if not ws:
        return {"hhi": None, "eff_n": None, "n": 0,
                "top5_pct": None, "top10_pct": None}
    hhi = sum(w * w for w in ws)
    return {
        "hhi": round(hhi, 4),
        "eff_n": round(1.0 / hhi, 2) if hhi > 0 else None,
        "n": len(ws),
        "top5_pct": round(sum(ws[:5]) * 100, 1),
        "top10_pct": round(sum(ws[:10]) * 100, 1),
    }


def group_exposure(weights, attribute):
    """Sum weights by a categorical attribute, e.g. attribute={ticker: sector}.
    Returns a sorted list [{label, value(%)}] (descending)."""
    agg = {}
    for t, w in weights.items():
        key = attribute.get(t) or "Unknown"
        agg[key] = agg.get(key, 0.0) + w
    return sorted(
        [{"label": k, "value": round(v * 100, 1)} for k, v in agg.items()],
        key=lambda x: -x["value"])


def portfolio_beta(weights, betas, default_beta=1.0):
    """Capital-weighted portfolio beta = Σ wᵢ·βᵢ (missing beta -> default)."""
    return round(sum(w * (betas.get(t) if betas.get(t) is not None else default_beta)
                     for t, w in weights.items()), 3)


def beta_risk_contribution(weights, betas, default_beta=1.0):
    """ONE-FACTOR (market-beta) risk attribution — the Phase-1 view used before a
    full covariance matrix is available. Each name's risk share is its share of
    the portfolio's total beta-dollar exposure:  RCᵢ ∝ wᵢ·βᵢ.

    Returns a list of rows with capital % vs signed risk % so the headline
    'small money, big risk' gap is visible immediately."""
    contrib = {t: w * (betas.get(t) if betas.get(t) is not None else default_beta)
               for t, w in weights.items()}
    total = sum(contrib.values())
    rows = []
    for t, w in sorted(weights.items(), key=lambda kv: -kv[1]):
        rc = (contrib[t] / total) if total else None
        rows.append({
            "ticker": t,
            "capital_pct": round(w * 100, 1),
            "risk_pct": round(rc * 100, 1) if rc is not None else None,
            "diff_pp": round((rc - w) * 100, 1) if rc is not None else None,
            "beta": betas.get(t),
        })
    return rows


# --------------------------------------------------------------------------- #
#  Phase 2 — covariance, portfolio vol, DR, full risk contribution, score      #
# --------------------------------------------------------------------------- #
def cov_matrix(returns_by_ticker, tickers):
    """Annualized covariance matrix (list-of-lists) over a common window."""
    aligned = align_returns(returns_by_ticker, tickers)
    n = len(tickers)
    M = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i, n):
            c = _covariance(aligned[tickers[i]], aligned[tickers[j]])
            M[i][j] = M[j][i] = c
    return M


def proxy_cov(tickers, vols, asset_class, equity_corr=0.6, cross_corr=0.2):
    """Covariance matrix built from per-name vols + an ASSUMED correlation, used
    when realized price history is too thin. Equity↔equity pairs use `equity_corr`;
    pairs involving a non-equity (bond/gold/cash) use the lower `cross_corr`.
    [JUDG-PROXY] — caller must tag."""
    n = len(tickers)

    def is_equity(t):
        ac = (asset_class.get(t) or "equity").lower()
        return not any(k in ac for k in ("bond", "gold", "cash", "real"))

    C = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i == j:
                rho = 1.0
            elif is_equity(tickers[i]) and is_equity(tickers[j]):
                rho = equity_corr
            else:
                rho = cross_corr
            C[i][j] = rho * vols[i] * vols[j]
    return C


def corr_from_cov(cov):
    """Correlation matrix from a covariance matrix."""
    n = len(cov)
    sd = [math.sqrt(cov[i][i]) if cov[i][i] > 0 else 0.0 for i in range(n)]
    C = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            C[i][j] = (cov[i][j] / (sd[i] * sd[j])) if (sd[i] > 0 and sd[j] > 0) else 0.0
    return C


def portfolio_vol(weights_vec, cov):
    """σp = √(wᵀ Σ w). weights_vec aligns with cov's ticker order."""
    n = len(weights_vec)
    var = 0.0
    for i in range(n):
        for j in range(n):
            var += weights_vec[i] * weights_vec[j] * cov[i][j]
    return math.sqrt(var) if var > 0 else 0.0


def diversification_ratio(weights_vec, vols, port_vol):
    """DR = (Σ wᵢσᵢ) / σp. DR≈1 -> almost no diversification benefit."""
    wavg = sum(weights_vec[i] * vols[i] for i in range(len(weights_vec)))
    return (wavg / port_vol) if port_vol > 0 else None


def risk_contributions(tickers, weights_vec, cov):
    """Full covariance-based risk attribution.
    MCRᵢ = (Σⱼ wⱼ·Σᵢⱼ)/σp ; RCᵢ = wᵢ·MCRᵢ ; Σ RCᵢ = σp.
    Returns (rows, summary). Signed %RC can be negative (true diversifier)."""
    n = len(tickers)
    sp = portfolio_vol(weights_vec, cov)
    rows, abs_total = [], 0.0
    rc_list = []
    if sp <= 0:
        for i, t in enumerate(tickers):
            rows.append({"ticker": t, "capital_pct": round(weights_vec[i] * 100, 1),
                         "signed_risk_pct": None, "abs_risk_share_pct": None,
                         "diff_pp": None, "vol_pct": None})
        return rows, {"port_vol_pct": None, "enb_abs": None}
    for i in range(n):
        mcr = sum(weights_vec[j] * cov[i][j] for j in range(n)) / sp
        rc = weights_vec[i] * mcr
        rc_list.append(rc)
        abs_total += abs(rc)
    for i, t in enumerate(tickers):
        signed = rc_list[i] / sp                       # fraction of σp
        rows.append({
            "ticker": t,
            "capital_pct": round(weights_vec[i] * 100, 1),
            "signed_risk_pct": round(signed * 100, 1),
            "abs_risk_share_pct": round(abs(rc_list[i]) / abs_total * 100, 1) if abs_total else None,
            "diff_pp": round((signed - weights_vec[i]) * 100, 1),
            "vol_pct": round(math.sqrt(cov[i][i]) * 100, 1) if cov[i][i] > 0 else None,
        })
    q = [abs(rc) / abs_total for rc in rc_list] if abs_total else []
    enb_abs = (1.0 / sum(x * x for x in q)) if q and sum(x * x for x in q) > 0 else None
    rows.sort(key=lambda r: -(r["abs_risk_share_pct"] or 0))
    return rows, {"port_vol_pct": round(sp * 100, 1), "enb_abs": round(enb_abs, 2) if enb_abs else None}


def crisis_cov(cov, tickers, asset_class, floor=None):
    """Build a CRISIS-regime covariance matrix from a normal one: keep each name's
    volatility, but floor equity↔equity correlations toward the crisis level
    (diversification tends to vanish in a crash). Non-equity pairs (bond/gold/cash)
    keep their realized correlation. [JUDG-PROXY]."""
    floor = CRISIS_EQUITY_CORR if floor is None else floor
    n = len(cov)
    sd = [math.sqrt(cov[i][i]) if cov[i][i] > 0 else 0.0 for i in range(n)]
    corr = corr_from_cov(cov)

    def is_equity(t):
        ac = (asset_class.get(t) or "equity").lower()
        return not any(k in ac for k in ("bond", "gold", "cash", "real"))

    C = [row[:] for row in corr]
    for i in range(n):
        for j in range(n):
            if i != j and is_equity(tickers[i]) and is_equity(tickers[j]):
                C[i][j] = max(corr[i][j], floor)
    return [[C[i][j] * sd[i] * sd[j] for j in range(n)] for i in range(n)]


def _clamp01(x):
    return max(0.0, min(1.0, x))


def diversification_score(dr_normal, dr_crisis, enb_abs, eff_n, n_holdings,
                          gap_coverage_frac):
    """Heuristic Diversification Score 0–100  [JUDG].
    Weights: TrueDiv 35% (0.4·normalDR + 0.6·crisisDR) + RiskBalance 30%
    + GapCoverage 20% + Concentration 15%. Mirrors the plan/prompt exactly."""
    def dr_score(dr):
        return _clamp01(((dr or 1.0) - 1.0) / 1.0) * 100

    true_div = 0.4 * dr_score(dr_normal) + 0.6 * dr_score(dr_crisis)
    risk_bal = (_clamp01(((enb_abs or 1.0) - 1.0) / (n_holdings - 1)) * 100
                if n_holdings and n_holdings > 1 else 0.0)
    gap = _clamp01(gap_coverage_frac) * 100
    denom = (min(n_holdings, 10) - 1) if n_holdings and n_holdings > 1 else 1
    conc = _clamp01(((eff_n or 1.0) - 1.0) / denom) * 100 if denom > 0 else 0.0
    total = 0.35 * true_div + 0.30 * risk_bal + 0.20 * gap + 0.15 * conc
    return {
        "score": round(total),
        "sub": {
            "true_diversification": round(true_div),
            "risk_balance": round(risk_bal),
            "gap_coverage": round(gap),
            "concentration": round(conc),
        },
        "band": _score_band(total),
    }


def _score_band(s):
    if s >= 80: return "ความเสี่ยงกระจายกว้าง (broadly diversified)"
    if s >= 60: return "กระจายพอสมควร แต่ยังกระจุกบางด้าน"
    if s >= 40: return "กระจายเชิงจำนวนชื่อ แต่ independent bets จำกัด"
    if s >= 20: return "ความเสี่ยงหลักกระจุกไม่กี่ bet"
    return "outcome ขึ้นกับ risk driver หลักเกือบทั้งหมด"


# A coarse asset-class proxy for vol/correlation when we have no price history.
# Annual vol, as decimals. [JUDG-PROXY] — caller must tag.
PROXY_VOL = {
    "us_large": 0.16, "tech": 0.21, "growth": 0.50, "btc": 0.65,
    "gov_bond": 0.15, "gold": 0.15, "cash": 0.01, "intl": 0.20,
    "defensive": 0.13, "reit": 0.20,
}
CRISIS_EQUITY_CORR = 0.90      # [JUDG-PROXY] equity pairs converge in a crisis


def gap_coverage(weights, asset_class):
    """Fraction (0..1) of desirable diversifiers actually present, for the score's
    Gap sub-component. Looks for ballast that behaves unlike equities."""
    have = set()
    for t, w in weights.items():
        if w <= 0:
            continue
        ac = (asset_class.get(t) or "").lower()
        if "bond" in ac: have.add("bond")
        if "gold" in ac or "real" in ac: have.add("gold")
        if "intl" in ac or "em" in ac: have.add("intl")
        if "defensive" in ac or "low" in ac: have.add("defensive")
        if "cash" in ac: have.add("cash")
    wanted = {"bond", "gold", "intl", "defensive", "cash"}
    return len(have & wanted) / len(wanted)


# --------------------------------------------------------------------------- #
#  Phase 3 — stress test, VaR/CVaR, reverse stress                             #
# --------------------------------------------------------------------------- #
# Scenario sensitivities. Each scenario maps to an equity-market shock and
# optional sector/asset-class overrides. Position loss ≈ overlay applied to beta.
# ALL illustrative — caller tags [JUDG-SCENARIO] "Illustrative, not a forecast".
SCENARIOS = [
    {"key": "broad_selloff", "label": "Broad equity sell-off", "mkt": -0.20},
    {"key": "tech_derate", "label": "Tech / Semiconductor de-rating", "mkt": -0.12,
     "sector_mult": {"Technology": 2.2, "Semiconductors": 2.6, "Information Technology": 2.2}},
    {"key": "rate_shock", "label": "Inflation / Rate shock", "mkt": -0.12, "highbeta_extra": -0.10},
    {"key": "liquidity_crisis", "label": "Liquidity crisis", "mkt": -0.30, "highbeta_extra": -0.08},
]

# Historical replays: equity-market drawdown of each episode (illustrative).
HISTORICAL = [
    {"key": "gfc_2008", "label": "GFC 2008", "mkt": -0.55},
    {"key": "covid_2020", "label": "COVID crash 2020-03", "mkt": -0.34},
    {"key": "rate_2022", "label": "Rate shock 2022", "mkt": -0.25},
    {"key": "carry_2024", "label": "Yen carry unwind 2024-08", "mkt": -0.08},
]


def _position_shock(beta, sector, scn, default_beta=1.0):
    """Estimated single-position loss for a scenario (fraction, negative)."""
    b = beta if beta is not None else default_beta
    loss = b * scn["mkt"]
    mult = (scn.get("sector_mult") or {}).get(sector)
    if mult:
        loss = b * scn["mkt"] * mult
    if scn.get("highbeta_extra") and b >= 1.3:
        loss += scn["highbeta_extra"]
    return max(loss, -0.95)         # floor a single name at -95%


def stress_test(weights, betas, sectors, scenarios=None, default_beta=1.0):
    """Portfolio loss per scenario = Σ wᵢ · position_shockᵢ. Returns a sorted list
    [{key,label,loss_pct,worst}]. [JUDG-SCENARIO]."""
    scenarios = scenarios or SCENARIOS
    out = []
    for scn in scenarios:
        contrib = {t: w * _position_shock(betas.get(t), sectors.get(t), scn, default_beta)
                   for t, w in weights.items()}
        loss = sum(contrib.values())
        worst = min(contrib.items(), key=lambda kv: kv[1])[0] if contrib else None
        out.append({"key": scn["key"], "label": scn["label"],
                    "loss_pct": round(loss * 100, 1), "worst_contributor": worst})
    out.sort(key=lambda x: x["loss_pct"])      # most negative first
    return out


def historical_var(port_returns, horizon_years=1.0, min_n=60):
    """P1-7 (S2/S4): HISTORICAL VaR/CVaR from the realized daily portfolio-return
    series — no normality assumption, so fat tails show up as they happened.
    Scales the daily percentile loss by √(252·horizon). None-fields when history
    is too thin (< min_n days). Complements (does not replace) parametric VaR."""
    rs = [r for r in (port_returns or []) if r is not None]
    n = len(rs)
    if n < min_n:
        return {"var95_pct": None, "var99_pct": None, "cvar95_pct": None,
                "n": n, "method": "historical (insufficient history)"}
    xs = sorted(rs)                                    # worst first
    scale = math.sqrt(TRADING_DAYS * horizon_years)

    def pct_loss(q):
        i = max(0, min(n - 1, int(q * n)))
        return -xs[i]

    v95, v99 = pct_loss(0.05), pct_loss(0.01)
    tail = xs[:max(1, int(0.05 * n))]
    c95 = -(sum(tail) / len(tail))
    return {"var95_pct": round(v95 * scale * 100, 1),
            "var99_pct": round(v99 * scale * 100, 1),
            "cvar95_pct": round(c95 * scale * 100, 1),
            "n": n, "method": "historical"}


def var_cvar(port_vol, horizon_years=1.0):
    """Parametric (normal) VaR/CVaR as % of portfolio value over the horizon.
    port_vol is the ANNUAL σp. z95=1.645, z99=2.326; CVaR95 factor=2.063."""
    if not port_vol or port_vol <= 0:
        return {"var95_pct": None, "var99_pct": None, "cvar95_pct": None}
    s = port_vol * math.sqrt(horizon_years)
    return {
        "var95_pct": round(1.645 * s * 100, 1),
        "var99_pct": round(2.326 * s * 100, 1),
        "cvar95_pct": round(2.063 * s * 100, 1),
        "method": "parametric-normal",
    }


def reverse_stress(weights, betas, tolerance_pct, default_beta=1.0):
    """What broad equity move breaches the user's max-loss tolerance?
    Solve Σ wᵢβᵢ · m = -tolerance  ->  m = -tolerance / Σwβ."""
    wb = sum(w * (betas.get(t) if betas.get(t) is not None else default_beta)
             for t, w in weights.items())
    if wb <= 0 or not tolerance_pct:
        return {"market_move_pct": None, "port_beta": round(wb, 3)}
    m = -(tolerance_pct / 100.0) / wb
    return {"market_move_pct": round(m * 100, 1), "port_beta": round(wb, 3),
            "note": f"ตลาด (equity beta) ต้องลง ~{round(abs(m)*100,1)}% จึงแตะขีดรับขาดทุน {tolerance_pct}%"}


# --------------------------------------------------------------------------- #
#  Downside-risk lens (Damodaran S2/S4: total vs downside, standalone vs market) #
# --------------------------------------------------------------------------- #
def portfolio_returns(returns_by_ticker, tickers, weights):
    """Weighted daily portfolio-return series from aligned per-ticker returns. Pure."""
    aligned = align_returns(returns_by_ticker, tickers)
    n = min((len(aligned[t]) for t in tickers if aligned[t]), default=0)
    if n == 0:
        return []
    out = []
    for i in range(n):
        out.append(sum(weights.get(t, 0.0) * aligned[t][-n:][i] for t in tickers))
    return out


# --------------------------------------------------------------------------- #
#  Correlation Monitor (Correlation tab) — pairwise / sector / downside / roll  #
#  All pure; consume the same return series the risk desk already fetched.      #
# --------------------------------------------------------------------------- #
def pair_corr(a, b):
    """Pearson correlation of two daily-return series over their common tail
    window. Returns None if <2 overlapping points or a series is flat."""
    n = min(len(a or []), len(b or []))
    if n < 2:
        return None
    a, b = a[-n:], b[-n:]
    sa, sb = _stdev(a), _stdev(b)
    if sa <= 0 or sb <= 0:
        return None
    ma, mb = _mean(a), _mean(b)
    cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n)) / (n - 1)
    return cov / (sa * sb)


def sector_corr(matrix, tickers, sectors):
    """Average pairwise correlation WITHIN each sector (intra) + the whole-portfolio
    average pairwise correlation. `matrix` is a ticker×ticker correlation matrix.
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
    """Correlation of a,b restricted to days when the MARKET return < threshold
    (Damodaran S2/S4: risk is downside, not symmetric vol). None if <2 down-days."""
    n = min(len(a or []), len(b or []), len(market or []))
    if n < 2:
        return None
    a, b, market = a[-n:], b[-n:], market[-n:]
    idx = [i for i in range(n) if market[i] < threshold]
    if len(idx) < 2:
        return None
    return pair_corr([a[i] for i in idx], [b[i] for i in idx])


def rolling_corr(a, b, window=60):
    """Trailing-window Pearson correlation series (one value per step once the
    window is full) — for a sparkline / 'is correlation trending up now?' read."""
    n = min(len(a or []), len(b or []))
    if n < window or window < 2:
        return []
    a, b = a[-n:], b[-n:]
    return [round(pair_corr(a[e - window:e], b[e - window:e]) or 0.0, 3)
            for e in range(window, n + 1)]


def semideviation(returns, mar=0.0):
    """Annualized DOWNSIDE deviation: only returns below the minimum-acceptable
    return (MAR=0) count. sqrt(mean over ALL obs of squared shortfalls) annualized.
    Damodaran S4: downside risk, not symmetric volatility. 0.0 if no downside."""
    if not returns or len(returns) < 2:
        return 0.0
    downs = [r - mar for r in returns if r < mar]
    if not downs:
        return 0.0
    msq = sum(d * d for d in downs) / len(returns)
    return math.sqrt(msq) * math.sqrt(TRADING_DAYS)


def sortino(returns, mar=0.0):
    """Annualized Sortino-style ratio = annualized excess mean / downside deviation.
    None when there is no downside deviation (can't divide)."""
    if not returns:
        return None
    sd = semideviation(returns, mar)
    if not sd:
        return None
    return round((_mean(returns) - mar) * TRADING_DAYS / sd, 2)


def downside_beta(asset_returns, market_returns):
    """Beta measured ONLY on days the market is down (m<0): cov(a,m|m<0)/var(m|m<0).
    Damodaran S4: risk added in bad times matters more. None if too few down days."""
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


# --------------------------------------------------------------------------- #
#  Phase 4 — suitability, position sizing, rebalancing engine                  #
# --------------------------------------------------------------------------- #
def effective_duration(bond_returns, yield_changes_bps):
    """Empirical effective duration (Damodaran S3) from regression of bond daily
    returns on daily yield CHANGES in bps: r ~ -(D/10000)*dy  ->  D = -slope*10000.
    Reuses data already fetchable (bond-ETF prices + ^TNX). None if too few aligned
    points or the yield never moves. Pure."""
    n = min(len(bond_returns), len(yield_changes_bps))
    if n < 20:
        return None
    r, dy = bond_returns[-n:], yield_changes_bps[-n:]
    mr = sum(r) / n
    mdy = sum(dy) / n
    cov = sum((r[i] - mr) * (dy[i] - mdy) for i in range(n)) / n
    var = sum((dy[i] - mdy) ** 2 for i in range(n)) / n
    if var <= 0:
        return None
    return round(-(cov / var) * 10000, 1)


def rate_stress(weights, durations, bps):
    """Portfolio loss % from a parallel rate move of `bps` bps, applied to rate-
    sensitive (bond) holdings only: loss_i = -duration_i * (bps/10000). Pure."""
    loss = 0.0
    for t, w in weights.items():
        d = durations.get(t)
        if d:
            loss += w * (-d * (bps / 10000.0))
    return round(loss * 100, 2)


def suitability(stress_rows, var_block, tolerance_pct):
    """Compare the worst plausible drawdown to the user's stated tolerance.
    Returns a verdict + the binding number, surfaced as a HEADLINE finding."""
    worst = min((r["loss_pct"] for r in stress_rows), default=None) if stress_rows else None
    var99 = var_block.get("var99_pct")
    # the binding drawdown estimate = the more severe of worst-scenario / VaR99
    candidates = [x for x in (worst, (-var99 if var99 else None)) if x is not None]
    binding = min(candidates) if candidates else None
    ok = (binding is not None and tolerance_pct is not None and binding >= -tolerance_pct)
    return {
        "tolerance_pct": tolerance_pct,
        "binding_drawdown_pct": binding,
        "within_tolerance": ok,
        "verdict": ("ภายในระดับที่รับได้" if ok else
                    "เกินระดับที่ผู้ใช้รับได้ — FINDING หลัก" if binding is not None else
                    "ข้อมูลไม่พอประเมิน"),
    }


def position_sizing(rc_rows, sectors, single_name_cap=0.20, sector_cap=0.40,
                    risk_share_cap=0.30, asset_class=None, pricing_cap=0.05):
    """For each holding, derive a soft/hard target band from the binding cap
    among: single-name cap, sector cap (shared), risk-contribution budget, and
    (P1-11, S40-41) a HARDER cap for pricing assets (crypto/collectible — no
    cash flow, pure pricing game). Outputs current vs target with an action verdict."""
    asset_class = asset_class or {}
    # sector totals (capital)
    sec_tot = {}
    for r in rc_rows:
        s = sectors.get(r["ticker"]) or "Unknown"
        sec_tot[s] = sec_tot.get(s, 0.0) + (r["capital_pct"] or 0) / 100.0
    out = []
    for r in rc_rows:
        w = (r["capital_pct"] or 0) / 100.0
        arc = (r.get("abs_risk_share_pct") or r.get("risk_pct") or 0) / 100.0
        hard = single_name_cap
        binding = "single-name cap"
        ac = (asset_class.get(r["ticker"]) or "equity").lower()
        if ac in ("crypto", "collectible") and pricing_cap and pricing_cap < hard:
            hard = pricing_cap
            binding = "pricing-asset cap (S40-41: no cash flow)"
        if arc and arc > risk_share_cap and arc > 0:
            # scale capital down proportionally if it eats too much risk budget
            implied = w * (risk_share_cap / arc)
            if implied < hard:
                hard, binding = implied, "risk-contribution budget"
        soft = hard * 0.8
        if w > hard * 1.001:
            action = "TRIM"
        elif w < soft * 0.5:
            action = "ADD ได้ (ถ้า thesis ยังดี)"
        else:
            action = "HOLD"
        out.append({
            "ticker": r["ticker"],
            "current_pct": round(w * 100, 1),
            "soft_max_pct": round(soft * 100, 1),
            "hard_max_pct": round(hard * 100, 1),
            "binding_cap": binding,
            "abs_risk_share_pct": r.get("abs_risk_share_pct"),
            "action": action,
        })
    return out


def rebalance(weights, sizing_rows, min_cash_pct=0.0):
    """Propose a target weight set that respects the hard caps, then build a
    trade list (TRIM the over-cap names, redistribute to under-weight names).
    Returns proposed weights + trade list + a NO-TRADE flag when nothing breaches.
    A capital-only first pass — Step 7 (post-trade re-validation) is recomputed by
    the caller with the full risk engine on the proposed weights."""
    proposed = dict(weights)
    trades = []
    freed = 0.0
    # 1) trim over-cap
    for s in sizing_rows:
        t = s["ticker"]
        cur = weights.get(t, 0.0)
        cap = (s["hard_max_pct"] or 100) / 100.0
        if cur > cap + 1e-6:
            cut = cur - cap
            proposed[t] = cap
            freed += cut
            trades.append({"ticker": t, "action": "TRIM",
                           "from_pct": round(cur * 100, 1), "to_pct": round(cap * 100, 1),
                           "amount_pct": round(cut * 100, 1), "priority": "Must"})
    # 2) redistribute freed weight to the most under-weight names (below soft max)
    if freed > 1e-6:
        room = [(s["ticker"], (s["soft_max_pct"] or 0) / 100.0 - proposed.get(s["ticker"], 0))
                for s in sizing_rows]
        room = [(t, r) for t, r in room if r > 0]
        room_tot = sum(r for _, r in room)
        for t, r in room:
            add = freed * (r / room_tot) if room_tot else 0
            if add > 1e-6:
                proposed[t] = proposed.get(t, 0) + add
                trades.append({"ticker": t, "action": "ADD",
                               "from_pct": round(weights.get(t, 0) * 100, 1),
                               "to_pct": round(proposed[t] * 100, 1),
                               "amount_pct": round(add * 100, 1), "priority": "Should"})
    # normalize (guard rounding)
    tot = sum(proposed.values())
    if tot > 0:
        proposed = {t: w / tot for t, w in proposed.items()}
    return {
        "no_trade": len(trades) == 0,
        "proposed_weights": {t: round(w, 4) for t, w in proposed.items()},
        "trades": trades,
    }
