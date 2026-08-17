"""
pipeline/risk_report.py — the /api/risk payload builder (extracted verbatim from
app.py on 2026-07-19 so it is unit-testable).

Three concerns, now with visible seams:
  * network  — ONLY through the injected `fetch_returns` (default:
               pipeline.prices.fetch_returns);
  * math     — pure calls into domain.engine.risk / domain.diversification;
  * shaping  — the payload dict consumed by index.html (Risk Desk + Correlation).

READ-ONLY on the store dict; the FMP-quota counter is committed by the caller
(app.py) under store.LOCK.
"""
import logging

import config
from pipeline import prices
from domain.engine import risk as riskeng
from domain import diversification

log = logging.getLogger("portfolio.risk_report")

# --------------------------------------------------------------------------- #
#  Risk engine  (Allocation tab — institutional risk-desk view)               #
#  READ-ONLY on the main store. Network (price history) runs OUTSIDE st.LOCK;  #
#  only the fmp_usage counter is committed under the lock (same safe pattern   #
#  as /api/allocation/whatif). All risk data goes to data/risk_cache.json.     #
# --------------------------------------------------------------------------- #
import math as _math


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


def build(s, fmp_key, quota_used, quota_cap, tolerance_pct, horizon_years,
          prefer_fmp, fetch_returns=None):
    """Assemble the full risk payload. Returns (payload, fmp_calls).
    `fetch_returns` is the ONLY network seam — inject a fake provider to
    unit-test the whole payload shaping offline (tests/test_risk_report.py)."""
    fetch_returns = fetch_returns or prices.fetch_returns
    holdings = s.get("holdings", {})
    facts = s.get("facts", {})
    mom = s.get("momentum", {})

    positions, betas, sectors, currencies, asset_class = {}, {}, {}, {}, {}
    beta_check = {}
    for t, h in holdings.items():
        sh = h.get("shares") or 0
        ff = facts.get(t, {}) or {}
        price = (mom.get(t, {}) or {}).get("price") or ff.get("price")
        if sh <= 0 or not price:
            continue
        positions[t] = sh * price
        # P1-6 (S4): vendor beta primary; regression beta (daily run, vs SPY) as
        # fallback + cross-check. Large disagreement is surfaced in meta.
        b_calc = ((mom.get(t, {}) or {}).get("v2") or {}).get("beta_calc")
        b_fmp = ff.get("beta")
        betas[t] = b_fmp if b_fmp is not None else b_calc
        if b_calc is not None:
            beta_check[t] = {"fmp": b_fmp, "calc": b_calc,
                             "diverge": bool(b_fmp is not None and abs(b_fmp - b_calc) > 0.4)}
        sectors[t] = ff.get("sector") or "Unknown"
        currencies[t] = ff.get("currency") or "USD"
        asset_class[t] = config.ASSET_CLASS_MAP.get(t, "equity")   # S3/S38-41 multi-class

    weights = riskeng.capital_weights(positions)
    if not weights:                        # LEVEL 3 — insufficient data
        return ({"status": "insufficient",
                 "message": "ยังไม่มี holding ที่มีราคา/จำนวนหุ้นพอจะวิเคราะห์ — เพิ่ม holding หรือกด Run Daily ก่อน",
                 "snapshot": None}, 0)

    tickers = sorted(weights, key=lambda t: -weights[t])
    wvec = [weights[t] for t in tickers]
    total_value = sum(positions.values())

    # ---- price history -> returns (network, accuracy-first, quota-guarded) ----
    rdata, calls, rmeta = fetch_returns(
        tickers + ["SPY", "QQQ", "GLD", "IBIT"], fmp_key if prefer_fmp else "",
        quota_used, quota_cap, prefer_fmp=prefer_fmp)

    have_realized = all((rdata.get(t, {}).get("n") or 0) >= 60 for t in tickers)
    # FIX (2026-08-16): align by DATE. The old positional trim paired NVDA(t) with
    # AVGO(t-1) whenever one source lagged a bar, turning the correlation matrix
    # into a lag-1 cross-correlation matrix. `align_mode` is surfaced so the card
    # can say which alignment it got.
    aligned, align_mode = riskeng.align_on_dates(rdata, tickers)
    common_n = min((len(aligned[t]) for t in tickers if aligned[t]), default=0)

    def _rd(sym):
        """(returns, dates) for ANY symbol in rdata — holdings and the SPY/QQQ/GLD/IBIT
        references alike. Every correlation below pairs series through this so nothing
        is matched by position; see riskeng.align_pair."""
        e = rdata.get(sym) or {}
        return (e.get("returns") or []), (e.get("dates") or [])

    if have_realized and common_n >= 60:
        # feed the DATE-ALIGNED series, not the raw ones
        cov = riskeng.cov_matrix(aligned, tickers)
        cov_mode, cov_tag, proxy_tickers = "realized", "[CALC]", []
    else:
        # FIX (2026-07-19): per-pair hybrid — realized pairs keep their REAL corr;
        # only thin-history names use the assumed 0.60/0.20 (no more all-or-nothing
        # proxy matrix from a single thin ticker).
        pvols = [config.CLASS_PROXY_VOL.get(asset_class[t], _proxy_vol(sectors[t]))
                 if asset_class[t] != "equity" else _proxy_vol(sectors[t]) for t in tickers]
        # hybrid_cov measures each pair's correlation on the window that pair
        # shares, so it takes the RAW series (a thin name must not truncate the
        # long names' vol estimate) — but the dates now travel with them.
        cov, realized_tk = riskeng.hybrid_cov(
            {t: (aligned.get(t) if align_mode == "dates" and len(aligned.get(t) or []) >= 60
                 else rdata.get(t, {}).get("returns", [])) for t in tickers},
            tickers, pvols, asset_class)
        proxy_tickers = [t for t in tickers if t not in realized_tk]
        if realized_tk:
            cov_mode, cov_tag = "mixed", "[CALC + JUDG-PROXY]"
        else:
            cov_mode, cov_tag = "proxy", "[JUDG-PROXY]"

    vols = [_math.sqrt(cov[i][i]) if cov[i][i] > 0 else 0.0 for i in range(len(tickers))]
    port_vol = riskeng.portfolio_vol(wvec, cov)
    dr_normal = riskeng.diversification_ratio(wvec, vols, port_vol)

    cov_c = riskeng.crisis_cov(cov, tickers, asset_class)
    vols_c = [_math.sqrt(cov_c[i][i]) if cov_c[i][i] > 0 else 0.0 for i in range(len(tickers))]
    port_vol_c = riskeng.portfolio_vol(wvec, cov_c)
    dr_crisis = riskeng.diversification_ratio(wvec, vols_c, port_vol_c)

    rc_rows, rc_sum = riskeng.risk_contributions(tickers, wvec, cov)
    conc = riskeng.concentration(weights)
    gap = riskeng.gap_coverage(weights, asset_class)
    score = riskeng.diversification_score(
        dr_normal, dr_crisis, rc_sum.get("enb_abs"), conc.get("eff_n"), conc.get("n"), gap)

    # ---- exposures ----
    by_sector = riskeng.group_exposure(weights, sectors)
    by_currency = riskeng.group_exposure(weights, currencies)
    by_sleeve = riskeng.group_exposure(weights, {t: _sleeve(sectors[t]) for t in tickers})
    beta_rc = riskeng.beta_risk_contribution(weights, betas)
    port_beta = riskeng.portfolio_beta(weights, betas)

    # ---- downside-risk lens (Damodaran S2/S4) — defensive: never crash the payload ----
    try:
        spy_rets, spy_dates = _rd("SPY")
        port_rets, port_dates = riskeng.portfolio_returns_dated(rdata, tickers, weights)
        # the downside lens regresses the portfolio ON SPY, so the two must share dates
        port_rets, spy_rets = riskeng.align_pair(port_rets, port_dates, spy_rets, spy_dates)
        downside = {
            "vol_pct": round(riskeng.annualized_vol(port_rets) * 100, 1) if len(port_rets) >= 2 else None,
            "semidev_pct": round(riskeng.semideviation(port_rets) * 100, 1) if len(port_rets) >= 2 else None,
            "sortino": riskeng.sortino(port_rets),
            "downside_beta": riskeng.downside_beta(port_rets, spy_rets) if (port_rets and spy_rets) else None,
            "n": len(port_rets),
            "tag": "[CALC]" if cov_mode == "realized" else "[JUDG-PROXY] thin history",
        }
    except Exception as e:
        downside = {"vol_pct": None, "semidev_pct": None, "sortino": None,
                    "downside_beta": None, "n": 0, "tag": f"[error] {type(e).__name__}"}

    # ---- bond rate risk (Damodaran S3) + pricing-asset flag (S40-41) — defensive ----
    try:
        bond_ticks = [t for t in tickers if asset_class.get(t) == "bond"]
        durations = {t: config.DURATION_PROXY.get(t) for t in bond_ticks}
        durations = {t: d for t, d in durations.items() if d}
        dur_src = {t: "proxy" for t in durations}
        # P2-12 (S3): EMPIRICAL effective duration (bond returns vs Δ10Y-yield)
        # overrides the static proxy table when computable — as config promised.
        if bond_ticks:
            try:
                from sources import yahoo as _yh
                tnx = _yh.fetch_chart("^TNX", rng="1y", interval="1d")
                # keep the DATE with every close: the regression below pairs a bond
                # ETF's daily return with that day's yield change, and ^TNX does not
                # trade on exactly the same calendar as an ETF (different holidays,
                # different vendor gaps). Zipping the two by position was the same
                # off-by-one class as the correlation matrix — a shifted pair here
                # biases the regressed duration toward zero.
                _tz = [(d, c) for d, c in zip(tnx.get("dates") or [],
                                              tnx.get("closes") or []) if c]
                tc = [c for _d, c in _tz]
                tnx_dates = [d for d, _c in _tz]
                # ^TNX is quoted in PERCENT (4.25 == 4.25%), the same convention
                # yahoo.fetch_treasury_10y() relies on when it divides by 100.
                # So one ^TNX point == 100bps, not 10.  The old *10 understated
                # every yield change tenfold, which inflated the regressed
                # duration tenfold (a true 1.9y read came out as 19y) and made
                # the +100bp shock print -19% instead of -1.9%.
                dy_bps = [(tc[i] - tc[i - 1]) * 100 for i in range(1, len(tc))]
                dy_dates = tnx_dates[1:]           # one change per gap, dated at its end
                for t in bond_ticks:
                    b_rets, b_dates = _rd(t)
                    b_al, dy_al = riskeng.align_pair(b_rets, b_dates, dy_bps, dy_dates)
                    d_emp = riskeng.effective_duration(b_al, dy_al)
                    if d_emp and 0 < d_emp < 40:            # sanity band
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
    except Exception:
        rate_risk = {"has_bonds": False, "bps": 100, "loss_pct": None, "durations": {}, "tag": "[error]"}
        pricing_assets = []

    # ---- stress / tail ----
    stress = riskeng.stress_test(weights, betas, sectors)
    historical = riskeng.stress_test(weights, betas, sectors, scenarios=riskeng.HISTORICAL)
    var = riskeng.var_cvar(port_vol, horizon_years)
    # P1-7 (S2/S4): HISTORICAL VaR from realized portfolio returns (fat tails as
    # they happened) alongside the parametric-normal estimate. Defensive.
    try:
        _prets, _ = riskeng.portfolio_returns_dated(rdata, tickers, weights)
        var_hist = riskeng.historical_var(_prets, horizon_years)
    except Exception:
        var_hist = {"var95_pct": None, "var99_pct": None, "cvar95_pct": None,
                    "n": 0, "method": "historical (error)"}
    reverse = riskeng.reverse_stress(weights, betas, tolerance_pct)
    severe_dd = min([r["loss_pct"] for r in (stress + historical)], default=None)

    # ---- suitability / sizing / rebalance ----
    suit = riskeng.suitability(stress + historical, var, tolerance_pct)
    # P1-11 (S40-41): pricing assets (crypto/collectible) get a harder cap
    sizing = riskeng.position_sizing(rc_rows, sectors, asset_class=asset_class,
                                     pricing_cap=config.PRICING_ASSET_CAP)
    reb = riskeng.rebalance(weights, sizing)

    # post-trade re-validation: recompute on the proposed weights (same cov)
    after = None
    if not reb["no_trade"]:
        pw = reb["proposed_weights"]
        pvec = [pw.get(t, 0.0) for t in tickers]
        a_vol = riskeng.portfolio_vol(pvec, cov)
        a_rc, a_sum = riskeng.risk_contributions(tickers, pvec, cov)
        a_conc = riskeng.concentration({t: pw.get(t, 0.0) for t in tickers})
        after = {
            "port_vol_pct": round(a_vol * 100, 1),
            "eff_n": a_conc.get("eff_n"),
            "top_risk": (a_rc[0]["ticker"] if a_rc else None),
            "stress_worst_pct": min([r["loss_pct"] for r in riskeng.stress_test(
                {t: pw.get(t, 0.0) for t in tickers}, betas, sectors)], default=None),
        }

    # ---- Correlation Monitor (Correlation tab) — reuse cov/cov_c + benchmark refs ----
    # Defensive: correlation must NEVER break the risk payload.
    try:
        corr = riskeng.corr_from_cov(cov)
        corr_c = riskeng.corr_from_cov(cov_c)
        sc = riskeng.sector_corr(corr, tickers, sectors)
        sc_c = riskeng.sector_corr(corr_c, tickers, sectors)
        _, rc_sum_c = riskeng.risk_contributions(tickers, wvec, cov_c)
        # FIX 2026-08-16: ENB must be the correlation-adjusted count of independent
        # bets (= DR²), not the inverse-HHI of risk contributions. The old proxy ROSE
        # from normal→crisis and could exceed Effective N, which inverted the whole
        # fragility read on the Correlation tab.
        enb = riskeng.effective_bets(dr_normal)
        enb_c = riskeng.effective_bets(dr_crisis)

        # display order: group by sector, then heaviest weight first
        corr_order = sorted(range(len(tickers)),
                            key=lambda i: (sectors.get(tickers[i]) or "zz", -weights[tickers[i]]))
        order_tk = [tickers[i] for i in corr_order]
        m = len(order_tk)
        mtx = [[round(corr[corr_order[a]][corr_order[b]], 3) for b in range(m)] for a in range(m)]
        mtx_c = [[round(corr_c[corr_order[a]][corr_order[b]], 3) for b in range(m)] for a in range(m)]

        # benchmark refs — kept OUT of weights (yardsticks, not holdings)
        REFS = [("SPY", "S&P 500", "equity"), ("QQQ", "Nasdaq-100", "equity"),
                ("GLD", "Gold", "gold"), ("IBIT", "Bitcoin", "crypto")]
        floor = riskeng.CRISIS_EQUITY_CORR
        ac_all = {**{t: asset_class.get(t, "equity") for t in tickers},
                  **{sym: cls for sym, _nm, cls in REFS}}

        def _is_eq(sym):
            a = (ac_all.get(sym) or "equity").lower()
            return not any(k in a for k in ("bond", "gold", "cash", "real"))

        def _crisis(v, a, b):                  # mirror crisis_cov: floor equity↔equity only
            if v is None:
                return None
            return round(max(v, floor), 3) if (_is_eq(a) and _is_eq(b)) else round(v, 3)

        # every cell below is DATE-aligned per pair (2026-08-16). The benchmark table is
        # the most-read number on this tab and it was the last place still pairing by
        # position, which silently turned a one-day source lag into a lag-1 correlation.
        port_rets, port_dates = riskeng.portfolio_returns_dated(rdata, tickers, weights)

        def _pc(a_rets, a_dates, b_rets, b_dates):
            v = riskeng.pair_corr_dated(a_rets, a_dates, b_rets, b_dates)
            return round(v, 3) if v is not None else None

        bench_port = {}
        for sym, _nm, _cls in REFS:
            v = _pc(port_rets, port_dates, *_rd(sym))
            bench_port[sym] = {"normal": v, "crisis": _crisis(v, "PORT", sym)}
        bench_hold = {}
        for t in order_tk:
            row = {}
            for sym, _nm, _cls in REFS:
                v = _pc(*_rd(t), *_rd(sym))
                row[sym] = {"normal": v, "crisis": _crisis(v, t, sym)}
            bench_hold[t] = row
        bench_ref = {}
        for i in range(len(REFS)):
            for j in range(i + 1, len(REFS)):
                a, b = REFS[i][0], REFS[j][0]
                v = _pc(*_rd(a), *_rd(b))
                bench_ref[f"{a}-{b}"] = {"normal": v, "crisis": _crisis(v, a, b)}

        # the downside / rolling lenses regress the portfolio on SPY — align that pair too
        _spy_r, _spy_d = _rd("SPY")
        _pr, _sr = riskeng.align_pair(port_rets, port_dates, _spy_r, _spy_d)
        dcorr = riskeng.downside_corr(_pr, _sr, _sr)
        pearson = _pc(port_rets, port_dates, _spy_r, _spy_d)
        roll = riskeng.rolling_corr(_pr, _sr, 60)

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

        correlation = {
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
            "meta": {"cov_mode": cov_mode, "tag": cov_tag, "n_window": common_n,
                     "proxy_tickers": proxy_tickers,
                     "refs_fetched": {s2: bool(_rd(s2)[0]) for s2, _nm, _cls in REFS}},
        }
    except Exception as _ce:                    # never break the risk payload
        import traceback as _tb
        _tb.print_exc()
        correlation = {"status": "error", "message": f"{type(_ce).__name__}: {_ce}"}

    snapshot = {
        "as_of": s.get("updated", {}).get("_daily") or None,
        "total_value": round(total_value, 2),
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
            "beta_based": beta_rc,                 # Phase-1 one-factor view
            "covariance_based": rc_rows,           # Phase-2 full view (preferred)
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
            "beta_check": beta_check,          # P1-6: fmp vs regression beta (cross-check)
            "pricing_asset_cap_pct": round(config.PRICING_ASSET_CAP * 100, 1),
        },
    }, calls)
