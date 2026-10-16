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
from pipeline import prices
from domain import risk as riskeng
from domain import diversification

log = logging.getLogger("portfolio.risk_report")

# benchmark yardsticks: fetched alongside the holdings, kept OUT of the weights
REFS = (("SPY", "S&P 500", "equity"), ("QQQ", "Nasdaq-100", "equity"),
        ("GLD", "Gold", "gold"), ("IBIT", "Bitcoin", "crypto"))
MIN_REALIZED = 60           # returns per name before its realized history is trusted


def _sleeve(sector):
    """Coarse risk-driver sleeve for the allocation donut (not just sector names)."""
    s = (sector or "").lower()
    if "semicond" in s:
        return "Semiconductor"
    if "tech" in s or "information technology" in s:
        return "Growth / Technology"
    if "health" in s or "pharmac" in s:
        return "Defensive / Healthcare"
    if "financ" in s or "bank" in s:
        return "Financial"
    if "consumer" in s:
        return "Consumer"
    if "communication" in s:
        return "Communication / Media"
    return sector or "Unknown"


def _proxy_vol(sector):
    s = (sector or "").lower()
    if "tech" in s or "semicond" in s or "information technology" in s or "communication" in s:
        return riskeng.PROXY_VOL["tech"]
    return riskeng.PROXY_VOL["us_large"]


@dataclass
class _Book:
    """Priced positions and the per-name attributes every section reads."""
    positions: dict
    betas: dict
    beta_check: dict
    sectors: dict
    currencies: dict
    asset_class: dict


def _book(s):
    facts, mom = s.get("facts", {}), s.get("momentum", {})
    b = _Book({}, {}, {}, {}, {}, {})
    for t, h in s.get("holdings", {}).items():
        sh = h.get("shares") or 0
        ff = facts.get(t, {}) or {}
        price = (mom.get(t, {}) or {}).get("price") or ff.get("price")
        if sh <= 0 or not price:
            continue
        b.positions[t] = sh * price
        # vendor beta primary; the daily run's regression beta (vs SPY) is the
        # fallback and a cross-check, with large disagreement surfaced in meta
        b_calc = ((mom.get(t, {}) or {}).get("v2") or {}).get("beta_calc")
        b_fmp = ff.get("beta")
        b.betas[t] = b_fmp if b_fmp is not None else b_calc
        if b_calc is not None:
            b.beta_check[t] = {"fmp": b_fmp, "calc": b_calc,
                               "diverge": bool(b_fmp is not None and abs(b_fmp - b_calc) > 0.4)}
        b.sectors[t] = ff.get("sector") or "Unknown"
        b.currencies[t] = ff.get("currency") or "USD"
        b.asset_class[t] = config.ASSET_CLASS_MAP.get(t, "equity")
    return b


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


def _after_rebalance(reb, tickers, cov, betas, sectors):
    """Post-trade re-validation on the proposed weights (same covariance)."""
    if reb["no_trade"]:
        return None
    pw = reb["proposed_weights"]
    pvec = [pw.get(t, 0.0) for t in tickers]
    proposed = {t: pw.get(t, 0.0) for t in tickers}
    a_vol = riskeng.portfolio_vol(pvec, cov)
    a_rc, _ = riskeng.risk_contributions(tickers, pvec, cov)
    a_conc = riskeng.concentration(proposed)
    return {
        "port_vol_pct": round(a_vol * 100, 1),
        "eff_n": a_conc.get("eff_n"),
        "top_risk": (a_rc[0]["ticker"] if a_rc else None),
        "stress_worst_pct": min([r["loss_pct"] for r in riskeng.stress_test(
            proposed, betas, sectors)], default=None),
    }


def _benchmarks(rdata, tickers, weights, order_tk, asset_class):
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

    def cell(a_series, b_series, a, b):
        v = riskeng.pair_corr_dated(*a_series, *b_series)
        v = round(v, 3) if v is not None else None
        crisis = None if v is None else (round(max(v, floor), 3) if (is_eq(a) and is_eq(b)) else round(v, 3))
        return {"normal": v, "crisis": crisis}

    port = riskeng.portfolio_returns_dated(rdata, tickers, weights)
    portfolio = {sym: cell(port, _series(rdata, sym), "PORT", sym) for sym, _nm, _cls in REFS}
    holdings = {t: {sym: cell(_series(rdata, t), _series(rdata, sym), t, sym)
                    for sym, _nm, _cls in REFS} for t in order_tk}
    refref = {}
    for i in range(len(REFS)):
        for j in range(i + 1, len(REFS)):
            a, b = REFS[i][0], REFS[j][0]
            refref[f"{a}-{b}"] = cell(_series(rdata, a), _series(rdata, b), a, b)
    return portfolio, holdings, refref, port


def _correlation(rdata, tickers, weights, book, cov, cov_c, cov_info,
                         dr_normal, dr_crisis, conc, rc_rows, by_sector):
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
    m = len(order_tk)
    mtx = [[round(corr[corr_order[a]][corr_order[b]], 3) for b in range(m)] for a in range(m)]
    mtx_c = [[round(corr_c[corr_order[a]][corr_order[b]], 3) for b in range(m)] for a in range(m)]

    bench_port, bench_hold, bench_ref, (port_rets, port_dates) = _benchmarks(
        rdata, tickers, weights, order_tk, book.asset_class)

    spy_r, spy_d = _series(rdata, "SPY")
    pr, sr = riskeng.align_pair(port_rets, port_dates, spy_r, spy_d)
    dcorr = riskeng.downside_corr(pr, sr, sr)
    pearson = riskeng.pair_corr_dated(port_rets, port_dates, spy_r, spy_d)
    pearson = round(pearson, 3) if pearson is not None else None
    roll = riskeng.rolling_corr(pr, sr, 60)

    marginal = [{"ticker": r["ticker"], "cap_pct": r["capital_pct"],
                 "risk_pct": r.get("abs_risk_share_pct"), "diff_pp": r.get("diff_pp")}
                for r in rc_rows]

    top_sec = by_sector[0]["label"] if by_sector else None
    top_sec_wt = by_sector[0]["value"] if by_sector else None
    top_sec_corr = (sc["sector_avg"].get(top_sec, {}) or {}).get("avg") if top_sec else None

    philosophy = diversification.diversification_philosophy(
        n_holdings=len(tickers), enb=enb, enb_crisis=enb_c, eff_n=conc.get("eff_n"),
        top_sector=top_sec, top_sector_wt=top_sec_wt, top_sector_corr=top_sec_corr,
        avg_pairwise=sc["avg_pairwise"], avg_pairwise_crisis=sc_c["avg_pairwise"],
        bench_nasdaq_corr=bench_port.get("QQQ", {}).get("normal"), downside_corr=dcorr,
        top_risk_driver=(rc_rows[0]["ticker"] if rc_rows else None))

    return {
        "order": order_tk,
        "sectors": {t: sectors.get(t) for t in tickers},
        "matrix": mtx, "crisis_matrix": mtx_c,
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
                 "proxy_tickers": proxy_tickers,
                 "refs_fetched": {s2: bool(_series(rdata, s2)[0]) for s2, _nm, _cls in REFS}},
    }


def build(s, fmp_key, quota_used, quota_cap, tolerance_pct, horizon_years,
          prefer_fmp, fetch_returns=None):
    """Assemble the full risk payload. Returns (payload, fmp_calls).
    `fetch_returns` is the ONLY network seam: inject a fake provider to unit-test
    the whole payload shaping offline (tests/test_risk_report.py)."""
    fetch_returns = fetch_returns or prices.fetch_returns
    book = _book(s)
    weights = riskeng.capital_weights(book.positions)
    if not weights:
        return ({"status": "insufficient",
                 "message": "ยังไม่มี holding ที่มีราคา/จำนวนหุ้นพอจะวิเคราะห์ — เพิ่ม holding หรือกด Run Daily ก่อน",
                 "snapshot": None}, 0)

    tickers = sorted(weights, key=lambda t: -weights[t])
    wvec = [weights[t] for t in tickers]
    betas, sectors, asset_class = book.betas, book.sectors, book.asset_class

    rdata, calls, rmeta = fetch_returns(
        tickers + [sym for sym, _nm, _cls in REFS], fmp_key if prefer_fmp else "",
        quota_used, quota_cap, prefer_fmp=prefer_fmp)

    cov, cov_mode, cov_tag, proxy_tickers, n_window = _covariance(rdata, tickers, book)
    port_vol, dr_normal = _vol_and_dr(wvec, cov)
    cov_c = riskeng.crisis_cov(cov, tickers, asset_class)
    _port_vol_c, dr_crisis = _vol_and_dr(wvec, cov_c)

    rc_rows, rc_sum = riskeng.risk_contributions(tickers, wvec, cov)
    conc = riskeng.concentration(weights)
    gap = riskeng.gap_coverage(weights, asset_class)
    score = riskeng.diversification_score(
        dr_normal, dr_crisis, rc_sum.get("enb_abs"), conc.get("eff_n"), conc.get("n"), gap)

    by_sector = riskeng.group_exposure(weights, sectors)
    by_currency = riskeng.group_exposure(weights, book.currencies)
    by_sleeve = riskeng.group_exposure(weights, {t: _sleeve(sectors[t]) for t in tickers})
    beta_rc = riskeng.beta_risk_contribution(weights, betas)
    port_beta = riskeng.portfolio_beta(weights, betas)

    downside = _downside(rdata, tickers, weights, cov_mode)
    rate_risk, pricing_assets = _rate_risk(rdata, tickers, weights, asset_class)

    stress = riskeng.stress_test(weights, betas, sectors)
    historical = riskeng.stress_test(weights, betas, sectors, scenarios=riskeng.HISTORICAL)
    var = riskeng.var_cvar(port_vol, horizon_years)
    var_hist = _historical_var(rdata, tickers, weights, horizon_years)
    reverse = riskeng.reverse_stress(weights, betas, tolerance_pct)
    severe_dd = min([r["loss_pct"] for r in (stress + historical)], default=None)

    suit = riskeng.suitability(stress + historical, var, tolerance_pct)
    sizing = riskeng.position_sizing(rc_rows, sectors, asset_class=asset_class,
                                     pricing_cap=config.PRICING_ASSET_CAP)
    reb = riskeng.rebalance(weights, sizing)
    after = _after_rebalance(reb, tickers, cov, betas, sectors)

    try:                                # the Correlation tab must NEVER break the payload
        correlation = _correlation(rdata, tickers, weights, book, cov, cov_c,
                                   (cov_mode, cov_tag, proxy_tickers, n_window),
                                   dr_normal, dr_crisis, conc, rc_rows, by_sector)
    except Exception as e:
        traceback.print_exc()
        correlation = {"status": "error", "message": f"{type(e).__name__}: {e}"}

    snapshot = {
        "as_of": s.get("updated", {}).get("_daily") or None,
        "total_value": round(sum(book.positions.values()), 2),
        "n_positions": len(weights),
        "cash_pct": None,                  # not in data model -> "Invested Portfolio"
        "equity_pct": 100.0,
        "port_vol_pct": round(port_vol * 100, 1) if port_vol else None,
        "severe_drawdown_pct": severe_dd,
        "diversification_score": score["score"],
        "port_beta": port_beta,
        "scope": "Invested Portfolio (ไม่รวม Cash — data model ไม่มียอดเงินสด)",
    }

    return ({
        "status": "ok",
        "snapshot": snapshot,
        "allocation": {"by_sector": by_sector, "by_currency": by_currency, "by_sleeve": by_sleeve},
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
                     "beta_sector": "[STORED]", "weights": "[CALC]"},
            "pricing_assets": pricing_assets,
            "beta_check": book.beta_check,     # vendor vs regression beta
            "pricing_asset_cap_pct": round(config.PRICING_ASSET_CAP * 100, 1),
        },
    }, calls)
