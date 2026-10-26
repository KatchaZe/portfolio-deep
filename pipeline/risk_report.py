"""
pipeline/risk_report.py — the /api/risk payload builder.

  * network  ONLY through the injected `fetch_returns` (default
             pipeline.prices.fetch_returns);
  * math     pure calls into domain.risk / domain.diversification;
  * shaping  one function per payload section, assembled by `build`.

READ-ONLY on the store dict; the FMP-quota counter is committed by the caller
(app.py) under store.LOCK.
"""
import logging
import math
import traceback
from dataclasses import dataclass

import config
import store as store_mod
from pipeline import prices
from domain import risk as riskeng
from domain import diversification
from domain import exposure

log = logging.getLogger("portfolio.risk_report")

# benchmark yardsticks: fetched alongside the holdings, kept OUT of the weights
REFS = (("SPY", "S&P 500", "equity"), ("QQQ", "Nasdaq-100", "equity"),
        ("GLD", "Gold", "gold"), ("IBIT", "Bitcoin", "crypto"))
MIN_REALIZED = 60           # returns per name before its realized history is trusted


# (substrings of the lower-cased sector, sleeve) in priority order; "tech" also
# catches "information technology"
_SLEEVES = ((("semicond",), "Semiconductor"),
            (("tech",), "Growth / Technology"),
            (("health", "pharmac"), "Defensive / Healthcare"),
            (("financ", "bank"), "Financial"),
            (("consumer",), "Consumer"),
            (("communication",), "Communication / Media"))
_TECH_LIKE = ("tech", "semicond", "communication")


def _sleeve(sector):
    """Coarse risk-driver sleeve for the allocation donut (not just sector names)."""
    s = (sector or "").lower()
    return next((sleeve for keys, sleeve in _SLEEVES if any(k in s for k in keys)),
                sector or "Unknown")


def _proxy_vol(sector):
    s = (sector or "").lower()
    return riskeng.PROXY_VOL["tech" if any(k in s for k in _TECH_LIKE) else "us_large"]


@dataclass
class _Book:
    """Priced positions and the per-name attributes every section reads."""
    positions: dict
    betas: dict
    beta_check: dict
    sectors: dict
    currencies: dict
    asset_class: dict
    industries: dict
    missing_sector: list


def _risk_beta(asset_class, km, b_fmp, b_calc):
    """(beta, source) a stress test runs on. An operating company gets the beta its
    valuation discounts at (bottom-up industry beta, re-levered; Damodaran S4: one
    stock's regression beta is too noisy), so the Risk Desk and the fair value price
    the same business risk. Gold, bonds and crypto have no industry beta: their
    measured regression beta is the only one."""
    if asset_class == "equity" and km.get("beta") is not None:
        src = km.get("beta_source") or "valuation"
        if (km.get("beta_regression") is not None and km["beta"] == km["beta_regression"]
                and src.startswith("bottom-up")):
            src = "regression (valuation's early-stage rule: higher than the industry beta)"
        return km["beta"], src
    if b_fmp is not None:
        return b_fmp, "regression (FMP)"
    if b_calc is not None:
        return b_calc, "regression (daily vs SPY)"
    return None, "default 1.0"


def _book(s):
    facts, mom = s.get("facts", {}), s.get("momentum", {})
    results = s.get("results", {}) or {}
    b = _Book({}, {}, {}, {}, {}, {}, {}, [])
    for t, h in s.get("holdings", {}).items():
        sh = h.get("shares") or 0
        ff = facts.get(t, {}) or {}
        price = (mom.get(t, {}) or {}).get("price") or ff.get("price")
        if sh <= 0 or not price:
            continue
        b.positions[t] = sh * price
        b.asset_class[t] = config.ASSET_CLASS_MAP.get(t, "equity")
        b_calc = ((mom.get(t, {}) or {}).get("v2") or {}).get("beta_calc")
        b_fmp = ff.get("beta")
        km = (results.get(t) or {}).get("key_metrics") or {}
        b.betas[t], src = _risk_beta(b.asset_class[t], km, b_fmp, b_calc)
        measured = b_calc if b_calc is not None else b_fmp
        b.beta_check[t] = {
            "used": b.betas[t], "source": src, "fmp": b_fmp, "calc": b_calc,
            "diverge": bool(b.betas[t] is not None and measured is not None
                            and abs(b.betas[t] - measured) > 0.4)}
        b.sectors[t] = ff.get("sector") or "Unknown"
        b.industries[t] = ff.get("industry")
        if not ff.get("sector"):
            b.missing_sector.append(t)
        b.currencies[t] = ff.get("currency") or "USD"
    return b


def _offline_cik(ticker):
    """CIK from config or SEC's cached ticker map, never the network (the Risk Desk
    must not wait on SEC)."""
    if ticker in config.CIKS:
        return config.CIKS[ticker]
    try:
        import json
        import os
        with open(os.path.join(config.CACHE_DIR, "company_tickers.json"), encoding="utf-8") as fh:
            for row in json.load(fh).values():
                if row.get("ticker", "").upper() == ticker:
                    return str(row["cik_str"]).zfill(10)
    except Exception:
        pass
    return None


def _revenue_mixes(s, tickers):
    """({ticker: mix}, {ticker: source}): the mix the stored CRP was priced on
    (facts.crp_mix, saved at refresh), else re-derived offline in the valuation's own
    order (config -> dashboard -> latest 10-K, US-GAAP only -> domicile) for facts
    refreshed before crp_mix existed."""
    from sources import country_risk, revenue_geo
    facts = s.get("facts", {})
    mixes, src = {}, {}
    for t in tickers:
        ff = facts.get(t) or {}
        if isinstance(ff.get("crp_mix"), dict) and ff["crp_mix"]:
            mixes[t], src[t] = ff["crp_mix"], ff.get("crp_mix_source") or "stored"
            continue
        mix, source = country_risk.revenue_mix(t)
        if mix is None and ff.get("accounting_standard", "us-gaap") == "us-gaap":
            cik = _offline_cik(t)
            auto = revenue_geo.cached_mix(cik, config.CACHE_DIR) if cik else None
            mix, source = country_risk.revenue_mix(t, auto)
        if mix is None and ff.get("country"):
            mix, source = {ff["country"]: 1.0}, "domicile"
        if mix:
            mixes[t], src[t] = mix, source
    return mixes, src


def _downside_betas(rdata, tickers, beta_check):
    """Each holding's measured downside beta vs SPY (S2: risk is the down market),
    beside the beta the stress test uses. Date-aligned per pair."""
    spy_r, spy_d = _series(rdata, "SPY")
    for t in tickers:
        a, m = riskeng.align_pair(*_series(rdata, t), spy_r, spy_d)
        beta_check.setdefault(t, {})["downside"] = riskeng.downside_beta(a, m) if a else None


def _series(rdata, sym):
    """(returns, dates) for any symbol in rdata, holdings and refs alike. Every
    correlation pairs series through this so nothing is matched by position."""
    e = rdata.get(sym) or {}
    return (e.get("returns") or []), (e.get("dates") or [])


def _covariance(rdata, tickers, book):
    """Covariance on DATE-aligned returns when every name has enough history;
    otherwise a per-pair hybrid where only thin-history pairs use the assumed
    proxy correlation. Returns (cov, cov_mode, cov_tag, proxy_tickers, n_window)."""
    have_realized = all((rdata.get(t, {}).get("n") or 0) >= MIN_REALIZED for t in tickers)
    aligned, align_mode = riskeng.align_on_dates(rdata, tickers)
    n_window = min((len(aligned[t]) for t in tickers if aligned[t]), default=0)
    if have_realized and n_window >= MIN_REALIZED:
        return riskeng.cov_matrix(aligned, tickers), "realized", "[CALC]", [], n_window
    ac = book.asset_class
    pvols = [config.CLASS_PROXY_VOL.get(ac[t], _proxy_vol(book.sectors[t]))
             if ac[t] != "equity" else _proxy_vol(book.sectors[t]) for t in tickers]
    # each pair is measured on the window it shares, so a thin name gets its raw
    # series and cannot truncate the long names' vol estimate
    cov, realized_tk = riskeng.hybrid_cov(
        {t: (aligned.get(t) if align_mode == "dates" and len(aligned.get(t) or []) >= MIN_REALIZED
             else rdata.get(t, {}).get("returns", [])) for t in tickers},
        tickers, pvols, ac)
    proxy_tickers = [t for t in tickers if t not in realized_tk]
    if realized_tk:
        return cov, "mixed", "[CALC + JUDG-PROXY]", proxy_tickers, n_window
    return cov, "proxy", "[JUDG-PROXY]", proxy_tickers, n_window


def _vol_and_dr(wvec, cov):
    """(portfolio vol, diversification ratio) for one covariance regime."""
    vols = [math.sqrt(cov[i][i]) if cov[i][i] > 0 else 0.0 for i in range(len(wvec))]
    port_vol = riskeng.portfolio_vol(wvec, cov)
    return port_vol, riskeng.diversification_ratio(wvec, vols, port_vol)


def _downside(rdata, tickers, weights, cov_mode):
    """Damodaran S2/S4 downside lens; never breaks the payload."""
    try:
        spy_rets, spy_dates = _series(rdata, "SPY")
        port_rets, port_dates = riskeng.portfolio_returns_dated(rdata, tickers, weights)
        # the lens regresses the portfolio ON SPY, so the two must share dates
        port_rets, spy_rets = riskeng.align_pair(port_rets, port_dates, spy_rets, spy_dates)
        return {
            "vol_pct": round(riskeng.annualized_vol(port_rets) * 100, 1) if len(port_rets) >= 2 else None,
            "semidev_pct": round(riskeng.semideviation(port_rets) * 100, 1) if len(port_rets) >= 2 else None,
            "sortino": riskeng.sortino(port_rets),
            "downside_beta": riskeng.downside_beta(port_rets, spy_rets) if (port_rets and spy_rets) else None,
            "n": len(port_rets),
            "tag": "[CALC]" if cov_mode == "realized" else "[JUDG-PROXY] thin history",
        }
    except Exception as e:
        return {"vol_pct": None, "semidev_pct": None, "sortino": None,
                "downside_beta": None, "n": 0, "tag": f"[error] {type(e).__name__}"}


def _empirical_durations(rdata, bond_ticks):
    """Yield (bond, effective duration) regressed on daily 10Y yield changes (S3),
    inside a sanity band. A failure part-way keeps the durations already yielded."""
    from sources import yahoo
    tnx = yahoo.fetch_chart("^TNX", rng="1y", interval="1d")
    # keep the DATE with every close: ^TNX and an ETF trade on different calendars,
    # and a shifted pair biases the regressed duration toward zero
    dated = [(d, c) for d, c in zip(tnx.get("dates") or [], tnx.get("closes") or []) if c]
    tc = [c for _d, c in dated]
    # ^TNX is quoted in PERCENT (4.25 == 4.25%), so one point is 100bps, not 10
    dy_bps = [(tc[i] - tc[i - 1]) * 100 for i in range(1, len(tc))]
    dy_dates = [d for d, _c in dated][1:]          # one change per gap, dated at its end
    for t in bond_ticks:
        b_rets, b_dates = _series(rdata, t)
        b_al, dy_al = riskeng.align_pair(b_rets, b_dates, dy_bps, dy_dates)
        d_emp = riskeng.effective_duration(b_al, dy_al)
        if d_emp and 0 < d_emp < 40:
            yield t, d_emp


def _rate_risk(rdata, tickers, weights, asset_class):
    """(+100bp bond shock, pricing assets S40-41); never breaks the payload."""
    try:
        bond_ticks = [t for t in tickers if asset_class.get(t) == "bond"]
        durations = {t: config.DURATION_PROXY.get(t) for t in bond_ticks}
        durations = {t: d for t, d in durations.items() if d}
        dur_src = {t: "proxy" for t in durations}
        # the empirical duration overrides the static proxy table when computable
        if bond_ticks:
            try:
                for t, d_emp in _empirical_durations(rdata, bond_ticks):
                    durations[t] = d_emp
                    dur_src[t] = "empirical"
            except Exception as _de:
                log.warning("empirical duration failed (proxy kept): %s", _de)
        rate_risk = {
            "has_bonds": bool(durations), "bps": 100,
            "loss_pct": riskeng.rate_stress(weights, durations, 100) if durations else None,
            "durations": durations, "duration_sources": dur_src,
            "tag": ("[CALC] empirical duration (vs ^TNX); +100bps parallel shock"
                    if any(v == "empirical" for v in dur_src.values())
                    else "[JUDG-PROXY] category duration; +100bps parallel shock"),
        }
        pricing_assets = [t for t in tickers if asset_class.get(t) in ("crypto", "collectible")]
        return rate_risk, pricing_assets
    except Exception:
        return ({"has_bonds": False, "bps": 100, "loss_pct": None, "durations": {}, "tag": "[error]"},
                [])


def _historical_var(rdata, tickers, weights, horizon_years):
    """VaR from realized portfolio returns (fat tails as they happened), shown
    beside the parametric-normal estimate."""
    try:
        prets, _ = riskeng.portfolio_returns_dated(rdata, tickers, weights)
        return riskeng.historical_var(prets, horizon_years)
    except Exception:
        return {"var95_pct": None, "var99_pct": None, "cvar95_pct": None,
                "n": 0, "method": "historical (error)"}


def _value_inputs(s, tickers, book):
    """{ticker: rating, gross and net margin of safety, price, average cost, tax rate}
    for the value-aware rebalance. Net MoS is the card's net upside (after the round-trip
    cost and capital-gains tax, domain/costs) put on the MoS scale (V - P)/V."""
    from domain import costs
    res, hold = s.get("results", {}) or {}, s.get("holdings", {}) or {}
    out = {}
    for t in tickers:
        v = res.get(t) or {}
        price = book.positions[t] / (hold.get(t, {}).get("shares") or 1)
        fv = v.get("anchor_value")
        up = (fv - price) / price * 100 if (fv and fv > 0 and price) else None
        net_up = costs.net_upside(up, config.CAPGAINS_TAX_RATE, config.TRADING_COST_BPS)
        out[t] = {"rating": v.get("recommendation"), "net_upside_pct": net_up,
                  "mos": round((fv - price) / fv, 4) if up is not None else None,
                  "net_mos": round(net_up / (100 + net_up), 4) if (net_up is not None and net_up > -100) else None,
                  "price": price, "avg_cost": hold.get(t, {}).get("avg_cost"),
                  "tax_rate": config.CAPGAINS_TAX_RATE}
    return out


def _after_rebalance(reb, tickers, cov, betas, sectors, industries=None):
    """Post-trade re-validation on the proposed weights (same covariance). Weights are
    of the whole portfolio, so vol and stress are total-portfolio numbers."""
    if reb["no_trade"]:
        return None
    pw = reb["proposed_weights"]
    pvec = [pw.get(t, 0.0) for t in tickers]
    proposed = {t: pw.get(t, 0.0) for t in tickers}
    a_vol = riskeng.portfolio_vol(pvec, cov)
    a_rc, _ = riskeng.risk_contributions(tickers, pvec, cov)
    inv = sum(proposed.values())
    a_conc = riskeng.concentration({t: w / inv for t, w in proposed.items()} if inv > 0 else proposed)
    return {
        "port_vol_pct": round(a_vol * 100, 1),
        "cash_pct": reb.get("cash_pct_after"),
        "eff_n": a_conc.get("eff_n"),
        "top_risk": (a_rc[0]["ticker"] if a_rc else None),
        "stress_worst_pct": min([r["loss_pct"] for r in
                                 riskeng.stress_test(proposed, betas, sectors, industries=industries)
                                 + riskeng.stress_test(proposed, betas, sectors, scenarios=riskeng.HISTORICAL,
                                                       industries=industries)], default=None),
    }


def _benchmarks(rdata, tickers, weights, order_tk, asset_class, long=None, days=None):
    """Correlation of the portfolio, each holding and each ref pair against the
    refs, every cell DATE-aligned per pair, with the crisis floor applied to
    equity-equity pairs only (mirrors crisis_cov). Returns (portfolio, holdings,
    refref, (port_rets, port_dates))."""
    floor = riskeng.CRISIS_EQUITY_CORR
    ac_all = {**{t: asset_class.get(t, "equity") for t in tickers},
              **{sym: cls for sym, _nm, cls in REFS}}

    def is_eq(sym):
        a = (ac_all.get(sym) or "equity").lower()
        return not any(k in a for k in ("bond", "gold", "cash", "real"))

    long, days = long or {}, days or set()

    def crisis_cell(v, a_series, b_series, a, b):
        """Measured on the market's crisis days when enough are shared (S41); else
        the assumed floor on equity pairs, the normal value on the rest."""
        if days:
            rho, _n = riskeng.crisis_pair_corr(*a_series, *b_series, days)
            if rho is not None:
                return round(rho, 3), True
        if v is None:
            return None, False
        return (round(max(v, floor), 3) if (is_eq(a) and is_eq(b)) else round(v, 3)), False

    def lng(sym):
        return long.get(sym) or _series(rdata, sym)

    def cell(a_series, b_series, a, b, a_long=None, b_long=None):
        v = riskeng.pair_corr_dated(*a_series, *b_series)
        v = round(v, 3) if v is not None else None
        crisis, measured = crisis_cell(v, a_long or a_series, b_long or b_series, a, b)
        return {"normal": v, "crisis": crisis, "crisis_measured": measured}

    port = riskeng.portfolio_returns_dated(rdata, tickers, weights)
    # the crisis episode is usually older than the 400-day window: the portfolio's
    # crisis-period series is rebuilt from the long histories, today's weights
    port_long = None
    if long and all(t in long for t in tickers):
        port_long = riskeng.portfolio_returns_dated(
            {t: {"returns": long[t][0], "dates": long[t][1]} for t in tickers}, tickers, weights)
    portfolio = {sym: cell(port, _series(rdata, sym), "PORT", sym, port_long, lng(sym))
                 for sym, _nm, _cls in REFS}
    holdings = {t: {sym: cell(_series(rdata, t), _series(rdata, sym), t, sym, lng(t), lng(sym))
                    for sym, _nm, _cls in REFS} for t in order_tk}
    refref = {}
    for i in range(len(REFS)):
        for j in range(i + 1, len(REFS)):
            a, b = REFS[i][0], REFS[j][0]
            refref[f"{a}-{b}"] = cell(_series(rdata, a), _series(rdata, b), a, b, lng(a), lng(b))
    return portfolio, holdings, refref, port


def _ballast(cov_c, tickers, weights):
    """(ticker, crisis correlation with the portfolio) of the held name least tied to
    the book in the crisis, the same measure the Gold/BTC yardsticks report:
    corr(i, P) = (Σ_j w_j cov_ij) / (σ_i σ_P). None for a one-name book."""
    n = len(tickers)
    if n < 2:
        return None
    w = [weights[t] for t in tickers]
    sp = riskeng.portfolio_vol(w, cov_c)
    best = None
    for i in range(n):
        si = cov_c[i][i] ** 0.5 if cov_c[i][i] > 0 else 0.0
        if not (si and sp):
            continue
        c = sum(w[j] * cov_c[i][j] for j in range(n)) / (si * sp)
        if best is None or c < best[1]:
            best = (tickers[i], c)
    return (best[0], round(best[1], 2)) if best else None


def _correlation(rdata, tickers, weights, book, cov, cov_c, cov_info,
                         dr_normal, dr_crisis, conc, rc_rows, by_sector, crisis=None):
    cov_mode, cov_tag, proxy_tickers, n_window = cov_info
    sectors = book.sectors
    corr, corr_c = riskeng.corr_from_cov(cov), riskeng.corr_from_cov(cov_c)
    sc = riskeng.sector_corr(corr, tickers, sectors)
    sc_c = riskeng.sector_corr(corr_c, tickers, sectors)
    # ENB is the correlation-adjusted count of independent bets (= DR^2), not the
    # inverse-HHI of risk contributions, which RISES in a crisis
    enb, enb_c = riskeng.effective_bets(dr_normal), riskeng.effective_bets(dr_crisis)

    # display order: group by sector, then heaviest weight first
    corr_order = sorted(range(len(tickers)),
                        key=lambda i: (sectors.get(tickers[i]) or "zz", -weights[tickers[i]]))
    order_tk = [tickers[i] for i in corr_order]
    def ordered(c):
        return [[round(c[i][j], 3) for j in corr_order] for i in corr_order]

    crisis_info, long_series, crisis_set = crisis or ({}, {}, set())
    bench_port, bench_hold, bench_ref, (port_rets, port_dates) = _benchmarks(
        rdata, tickers, weights, order_tk, book.asset_class, long_series, crisis_set)

    spy_r, spy_d = _series(rdata, "SPY")
    pr, sr = riskeng.align_pair(port_rets, port_dates, spy_r, spy_d)
    dcorr = riskeng.downside_corr(pr, sr, sr)
    pearson = riskeng.pair_corr_dated(port_rets, port_dates, spy_r, spy_d)
    pearson = round(pearson, 3) if pearson is not None else None
    roll = riskeng.rolling_corr(pr, sr, 60)

    marginal = [{"ticker": r["ticker"], "cap_pct": r["capital_pct"],
                 "risk_pct": r.get("signed_risk_pct"), "diff_pp": r.get("diff_pp")}
                for r in rc_rows]

    top_sec = by_sector[0]["label"] if by_sector else None
    top_sec_wt = by_sector[0]["value"] if by_sector else None
    top_sec_corr = (sc["sector_avg"].get(top_sec, {}) or {}).get("avg") if top_sec else None

    philosophy = diversification.diversification_philosophy(
        n_holdings=len(tickers), enb=enb, enb_crisis=enb_c, eff_n=conc.get("eff_n"),
        top_sector=top_sec, top_sector_wt=top_sec_wt, top_sector_corr=top_sec_corr,
        avg_pairwise=sc["avg_pairwise"], avg_pairwise_crisis=sc_c["avg_pairwise"],
        bench_nasdaq_corr=bench_port.get("QQQ", {}).get("normal"), downside_corr=dcorr,
        top_risk_driver=(rc_rows[0]["ticker"] if rc_rows else None),
        crisis_info=crisis_info, ballast=_ballast(cov_c, tickers, weights),
        refs_crisis={sym: (bench_port.get(sym) or {}).get("crisis") for sym, _nm, _cls in REFS
                     if (bench_port.get(sym) or {}).get("crisis_measured")})

    return {
        "order": order_tk,
        "sectors": {t: sectors.get(t) for t in tickers},
        "matrix": ordered(corr), "crisis_matrix": ordered(corr_c),
        "sector_avg": {"normal": sc["sector_avg"], "crisis": sc_c["sector_avg"]},
        "avg_pairwise": {"normal": sc["avg_pairwise"], "crisis": sc_c["avg_pairwise"]},
        "pairs": {"normal": riskeng.top_pairs(corr, tickers),
                  "crisis": riskeng.top_pairs(corr_c, tickers)},
        "concentration": {"enb": enb, "enb_crisis": enb_c,
                          "eff_n": conc.get("eff_n"), "n": conc.get("n")},
        "benchmark": {"refs": [{"sym": s2, "name": nm, "cls": cl} for s2, nm, cl in REFS],
                      "portfolio": bench_port, "holdings": bench_hold, "refref": bench_ref,
                      "downside": {"pearson": pearson, "downside": dcorr}},
        "rolling": {"port_vs_spy": roll},
        "marginal": marginal,
        "philosophy": philosophy,
        "meta": {"cov_mode": cov_mode, "tag": cov_tag, "n_window": n_window,
                 "proxy_tickers": proxy_tickers, "crisis": crisis_info,
                 "refs_fetched": {s2: bool(_series(rdata, s2)[0]) for s2, _nm, _cls in REFS}},
    }


def _crisis(cov, tickers, asset_class, fetch_long):
    """Measured crisis regime; the assumed floor when the long history is missing."""
    try:
        long = fetch_long(list(dict.fromkeys(tickers + [sym for sym, _nm, _cls in REFS])))
        days, dd = riskeng.crisis_window(*(long.get("SPY") or ([], [])))
        if days:
            c, info = riskeng.crisis_cov_measured(cov, tickers, asset_class, long, days)
            info["market_drawdown_pct"] = round(dd * 100, 1)
            info["history_from"] = (long.get("SPY") or ([], [None]))[1][0]
            return (c, info), long, days
    except Exception as e:
        log.warning("measured crisis correlation failed (assumed floor used): %s", e)
    return (riskeng.crisis_cov(cov, tickers, asset_class),
            {"method": "assumed", "crisis_days": 0, "measured_pairs": 0,
             "assumed_pairs": len(tickers) * (len(tickers) - 1) // 2}), {}, set()


def build(s, fmp_key, quota_used, quota_cap, tolerance_pct, horizon_years,
          prefer_fmp, fetch_returns=None, fetch_long=None):
    """Assemble the full risk payload. Returns (payload, fmp_calls).
    `fetch_returns` is the ONLY network seam: inject a fake provider to unit-test
    the whole payload shaping offline (tests/test_risk_report.py)."""
    fetch_returns = fetch_returns or prices.fetch_returns
    fetch_long = fetch_long or (lambda tks: prices.fetch_long_returns(tks, fmp_key if prefer_fmp else ""))
    book = _book(s)
    weights = riskeng.capital_weights(book.positions)
    if not weights:
        return ({"status": "insufficient",
                 "message": "ยังไม่มี holding ที่มีราคา/จำนวนหุ้นพอจะวิเคราะห์ — เพิ่ม holding หรือกด Run Daily ก่อน",
                 "snapshot": None}, 0)

    tickers = sorted(weights, key=lambda t: -weights[t])
    wvec = [weights[t] for t in tickers]
    betas, sectors, asset_class = book.betas, book.sectors, book.asset_class
    # Cash has no volatility and no correlation, so on the WHOLE portfolio every
    # invested weight shrinks by (1 - cash share). Loss, vol, VaR and beta are measured
    # on these; composition (sector, correlation, risk shares) stays on the invested book.
    invested = sum(book.positions.values())
    cash = store_mod.cash_usd(s)
    cash_frac = cash / (invested + cash) if (invested + cash) > 0 else 0.0
    w_total = {t: w * (1 - cash_frac) for t, w in weights.items()}

    rdata, calls, rmeta = fetch_returns(
        tickers + [sym for sym, _nm, _cls in REFS], fmp_key if prefer_fmp else "",
        quota_used, quota_cap, prefer_fmp=prefer_fmp)

    _downside_betas(rdata, tickers, book.beta_check)
    cov, cov_mode, cov_tag, proxy_tickers, n_window = _covariance(rdata, tickers, book)
    port_vol, dr_normal = _vol_and_dr(wvec, cov)
    (cov_c, crisis_info), long_series, crisis_set = _crisis(cov, tickers, asset_class, fetch_long)
    _port_vol_c, dr_crisis = _vol_and_dr(wvec, cov_c)

    rc_rows, rc_sum = riskeng.risk_contributions(tickers, wvec, cov)
    conc = riskeng.concentration(weights)
    by_sector = riskeng.group_exposure(weights, sectors)
    by_listing_currency = riskeng.group_exposure(weights, book.currencies)
    mixes, mix_src = _revenue_mixes(s, tickers)
    by_currency, by_region, non_us = exposure.revenue_exposure(weights, mixes)
    gap = riskeng.gap_coverage(weights, asset_class, non_us)
    score = riskeng.diversification_score(
        dr_normal, dr_crisis, rc_sum.get("enb_abs"), conc.get("eff_n"), conc.get("n"), gap)
    crps = {t: (s.get("facts", {}).get(t) or {}).get("crp") for t in tickers}
    country_risk = {**exposure.portfolio_crp(weights, crps),
                    "rows": sorted([{"ticker": t, "crp_pct": round(crps[t] * 100, 2) if crps[t] is not None else None,
                                     "non_us_pct": round(non_us[t] * 100) if non_us.get(t) is not None else None,
                                     "mix_source": mix_src.get(t),
                                     "basis": (s.get("facts", {}).get(t) or {}).get("crp_basis")}
                                    for t in tickers], key=lambda r: -(r["crp_pct"] or 0)),
                    "tag": "[STORED] CRP the valuation used (Damodaran ctryprem, revenue-weighted)"}
    by_sleeve = riskeng.group_exposure(weights, {t: _sleeve(sectors[t]) for t in tickers})
    beta_rc = riskeng.beta_risk_contribution(weights, betas)
    port_beta = riskeng.portfolio_beta(w_total, betas)

    downside = _downside(rdata, tickers, w_total, cov_mode)
    rate_risk, pricing_assets = _rate_risk(rdata, tickers, w_total, asset_class)

    stress = riskeng.stress_test(w_total, betas, sectors, industries=book.industries)
    historical = riskeng.stress_test(w_total, betas, sectors, scenarios=riskeng.HISTORICAL,
                                     industries=book.industries)
    port_vol_total = port_vol * (1 - cash_frac)
    var = riskeng.var_cvar(port_vol_total, horizon_years)
    var_hist = _historical_var(rdata, tickers, w_total, horizon_years)
    reverse = riskeng.reverse_stress(w_total, betas, tolerance_pct)
    severe_dd = min([r["loss_pct"] for r in (stress + historical)], default=None)

    suit = riskeng.suitability(stress + historical, var, tolerance_pct)
    value = _value_inputs(s, tickers, book)
    # sizing caps and the trades share one basis: weights of the WHOLE portfolio
    rc_total = [{**r, "capital_pct": round((r["capital_pct"] or 0) * (1 - cash_frac), 4)} for r in rc_rows]
    sizing = riskeng.position_sizing(rc_total, sectors, asset_class=asset_class,
                                     pricing_cap=config.PRICING_ASSET_CAP, value=value)
    reb = riskeng.rebalance(weights, sizing, value=value, cash_frac=cash_frac,
                            total_value=invested + cash, cost_bps=config.TRADING_COST_BPS)
    reb["vol_now_pct"] = round(port_vol_total * 100, 1) if port_vol else None
    after = _after_rebalance(reb, tickers, cov, betas, sectors, book.industries)

    try:                                # the Correlation tab must NEVER break the payload
        correlation = _correlation(rdata, tickers, weights, book, cov, cov_c,
                                   (cov_mode, cov_tag, proxy_tickers, n_window),
                                   dr_normal, dr_crisis, conc, rc_rows, by_sector,
                                   (crisis_info, long_series, crisis_set))
    except Exception as e:
        traceback.print_exc()
        correlation = {"status": "error", "message": f"{type(e).__name__}: {e}"}

    snapshot = {
        "as_of": s.get("updated", {}).get("_daily") or None,
        "total_value": round(invested + cash, 2),
        "invested_value": round(invested, 2),
        "cash_value": round(cash, 2),
        "n_positions": len(weights),
        "cash_pct": round(cash_frac * 100, 1),
        "equity_pct": round((1 - cash_frac) * 100, 1),
        "port_vol_pct": round(port_vol_total * 100, 1) if port_vol else None,
        "invested_vol_pct": round(port_vol * 100, 1) if port_vol else None,
        "severe_drawdown_pct": severe_dd,
        "diversification_score": score["score"],
        "port_beta": port_beta,
        "scope": (f"Total Portfolio (รวม cash {cash_frac * 100:.1f}%) — loss/vol/VaR/β คิดทั้งพอร์ต, "
                  "sector/correlation/risk share คิดเฉพาะส่วนที่ลงทุน" if cash > 0 else
                  "Invested Portfolio (ยังไม่ได้กรอก cash ใน My Portfolio)"),
    }

    return ({
        "status": "ok",
        "snapshot": snapshot,
        "allocation": {"by_sector": by_sector, "by_currency": by_currency, "by_sleeve": by_sleeve,
                       "by_region": by_region, "by_listing_currency": by_listing_currency,
                       "currency_basis": "revenue mix (where cash flows are earned), not the listing currency",
                       "country_risk": country_risk, "non_us_revenue": non_us},
        "correlation": correlation,
        "concentration": conc,
        "capital_vs_risk": {
            "beta_based": beta_rc,                 # one-factor view
            "covariance_based": rc_rows,           # full view (preferred)
            "port_vol_pct": rc_sum.get("port_vol_pct"),
            # how evenly risk is spread across names (inverse-HHI of |RC|).
            # NOT the Effective Number of Bets — see risk.effective_bets().
            "risk_balance_n": rc_sum.get("risk_balance_n"),
            "enb_abs": rc_sum.get("enb_abs"),   # deprecated alias
        },
        "diversification": {
            "dr_normal": round(dr_normal, 2) if dr_normal else None,
            "dr_crisis": round(dr_crisis, 2) if dr_crisis else None,
            "enb": riskeng.effective_bets(dr_normal),
            "enb_crisis": riskeng.effective_bets(dr_crisis),
            "crisis": crisis_info,
            "score": score,
        },
        "downside": downside,
        "rate_risk": rate_risk,
        "stress": {"hypothetical": stress, "historical": historical,
                   "var": var, "var_hist": var_hist, "reverse": reverse,
                   "tag": "[JUDG-SCENARIO] Illustrative, not a forecast"},
        "suitability": suit,
        "position_sizing": sizing,
        "rebalance": {**reb, "after": after},
        "meta": {
            "cov_mode": cov_mode, "cov_tag": cov_tag, "cache": rmeta.get("cache"),
            "quota_degraded": rmeta.get("quota_degraded"),
            "sources": {t: rdata.get(t, {}).get("source") for t in tickers},
            "as_of": {t: rdata.get(t, {}).get("as_of") for t in tickers},
            "tags": {"history": cov_tag, "stress": "[JUDG-SCENARIO]",
                     "beta_sector": "[STORED]", "weights": "[CALC]",
                     "beta": "[STORED] the beta the valuation discounts at (bottom-up; regression for early-stage names)"},
            "pricing_assets": pricing_assets,
            "beta_check": book.beta_check,     # used (bottom-up) vs regression vs downside
            "missing_sector": book.missing_sector,
            "pricing_asset_cap_pct": round(config.PRICING_ASSET_CAP * 100, 1),
        },
    }, calls)


# --------------------------------------------------------------------------- #
#  What-if: the risk a purchase ADDS to the portfolio (S2 marginal, not standalone)
# --------------------------------------------------------------------------- #
def _quote(s, t):
    """(price, facts) for a what-if name: a holding, else a watchlist name's cached facts."""
    mom = (s.get("momentum", {}).get(t) or {})
    ff = (s.get("facts", {}).get(t) or {})
    if not ff:
        ff = ((s.get("watch_cache") or {}).get(t) or {}).get("facts") or {}
    price = mom.get("price") or ff.get("price")
    if not price:                        # the last COMPLETED close Run Daily cached (T-1)
        from domain import pricing
        from pipeline import pricecache
        pc = pricecache.read_any(t) or {}
        price = pricing.t1_close(pc.get("closes") or [], pc.get("dates") or [])[0]
        if price:
            ff = {**ff, "price": price}
    return price, ff


def _headline(p):
    if not p or p.get("status") != "ok":
        return None
    sn, dv, st = p["snapshot"], p["diversification"], p["stress"]
    worst = min([r["loss_pct"] for r in st["hypothetical"] + st["historical"]], default=None)
    return {"port_vol_pct": sn["port_vol_pct"], "port_beta": sn["port_beta"], "cash_pct": sn["cash_pct"],
            "enb": dv["enb"], "enb_crisis": dv["enb_crisis"], "worst_stress_pct": worst,
            "var95_pct": st["var"].get("var95_pct"), "score": sn["diversification_score"]}


def whatif(s, buys, tolerance_pct=20.0, horizon_years=1.0, fetch_returns=None, fetch_long=None):
    """tolerance and horizon: the Risk Desk's inputs, so 'before' is the desk's own run."""
    """Re-run the Risk Desk with the purchases added at today's price (market value;
    the money comes out of the recorded cash first). Returns {before, after, delta,
    names, unpriced}. Only the names not already fetched cost a (free-tier) fetch."""
    import copy
    fetch_returns = fetch_returns or prices.fetch_returns
    seen = {}

    def recording(tks, *a, **kw):
        data, calls, meta = fetch_returns(tks, *a, **kw)
        seen.update(data)
        return data, calls, meta

    before, _ = build(s, "", 0, 0, tolerance_pct, horizon_years, False,
                      fetch_returns=recording, fetch_long=fetch_long)
    s2 = copy.deepcopy(s)
    spent, unpriced, added = 0.0, [], []
    for b in buys or []:
        t, amt = b["ticker"], float(b["amount"])
        price, ff = _quote(s, t)
        if not price or amt <= 0:
            unpriced.append(t)
            continue
        h = s2.setdefault("holdings", {}).setdefault(t, {"shares": 0.0, "avg_cost": price})
        old_sh = h.get("shares") or 0.0
        h["shares"] = old_sh + amt / price
        h["avg_cost"] = ((h.get("avg_cost") or price) * old_sh + amt) / h["shares"]
        if t not in s2.setdefault("facts", {}):
            s2["facts"][t] = dict(ff)
        spent += amt
        added.append(t)
    cash = store_mod.cash_usd(s)
    if cash:
        s2["cash"] = {"usd": max(0.0, cash - spent)}

    def after_provider(tks, *a, **kw):
        have = {t: seen[t] for t in tks if t in seen}
        miss = [t for t in tks if t not in seen]
        extra = prices._fetch_missing(miss, "", 0, 0, prices.DEFAULT_DAYS, False)[0] if miss else {}
        return {**have, **extra}, 0, {"cache": "whatif"}

    after, _ = build(s2, "", 0, 0, tolerance_pct, horizon_years, False,
                     fetch_returns=after_provider, fetch_long=fetch_long)
    hb, ha = _headline(before), _headline(after)
    delta = ({k: (round(ha[k] - hb[k], 2) if (ha.get(k) is not None and hb.get(k) is not None) else None)
              for k in ha} if (hb and ha) else None)
    names = []
    if after and after.get("status") == "ok":
        rc = {r["ticker"]: r for r in after["capital_vs_risk"]["covariance_based"]}
        size = {r["ticker"]: r for r in after["position_sizing"]}
        val = _value_inputs(s2, [t for t in added if t in rc], _book(s2))
        for t in dict.fromkeys(added):
            r, v = rc.get(t) or {}, val.get(t) or {}
            ac = config.ASSET_CLASS_MAP.get(t, "equity")
            names.append({
                "ticker": t, "capital_pct": r.get("capital_pct"), "risk_pct": r.get("signed_risk_pct"),
                "rating": v.get("rating"),
                "net_mos_pct": round(v["net_mos"] * 100, 1) if v.get("net_mos") is not None else None,
                "add_blocker": riskeng.add_blocker(v) if v else "ไม่มี fair value",
                "pricing_asset": ac in ("crypto", "collectible"),
                "over_pricing_cap": bool(ac in ("crypto", "collectible") and (r.get("capital_pct") or 0)
                                         > config.PRICING_ASSET_CAP * 100),
                "proxy_history": t in ((after.get("correlation") or {}).get("meta") or {}).get("proxy_tickers", []),
                "hard_max_pct": (size.get(t) or {}).get("hard_max_pct"),
            })
    return {"before": hb, "after": ha, "delta": delta, "names": names, "unpriced": unpriced,
            "spent_usd": round(spent, 2),
            "basis": "market value (today's price), the Risk Desk's own engine; cash spent first"}
