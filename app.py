"""
FastAPI app — DEEP portfolio dashboard (v2.1, hardened; engine version = config.DEEP_VERSION).

Run:
    set FMP_API_KEY (optional, for sector/beta via FMP profile)
    set APP_TOKEN   (optional — protects the app when deployed publicly)
    pip install -r requirements.txt
    uvicorn app:app --port 8000
    open http://localhost:8000

Concurrency model (this fixes the v2 server-freeze):
  * ALL endpoints are plain sync `def` -> FastAPI runs them in its threadpool,
    so the asyncio event loop is NEVER blocked (the old async what-if endpoint
    could freeze the whole server, including /healthz).
  * Slow network work (SEC / FMP / Yahoo) runs OUTSIDE store.LOCK via the
    fetch_* functions; the lock is held only for a fast load -> merge -> save
    (commit_*). The UI stays responsive even during a long fundamental refresh.
  * Trade-off (single-user app): two overlapping refreshes could both pass the
    FMP quota pre-check; the counter is still committed correctly afterwards.
  * DEPLOY (H5): run exactly ONE worker process (uvicorn default). store.LOCK,
    the background Drive-push worker and the pull-state guard are per-process;
    multiple workers would silently break all of them.

Optional auth (set env APP_TOKEN):
  open  https://your-app/?token=YOUR_TOKEN  once — a cookie is stored, after
  that the plain URL works. /healthz stays open for Render health checks.
"""
import os
import hmac
import logging
from typing import List, Optional

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from pydantic import BaseModel

import config
import store as st
from pipeline import refresh, risk_prices
from pipeline import screen as screen_mod
from domain.engine import risk as riskeng
from domain import diversification

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("portfolio")

app = FastAPI(title=f"Portfolio DEEP v{config.DEEP_VERSION}")
BASE = os.path.dirname(os.path.abspath(__file__))
QUOTA_CAP = config.QUOTA_CAP
APP_TOKEN = os.environ.get("APP_TOKEN", "")


# --------------------------------------------------------------------------- #
#  Auth (optional — active only when APP_TOKEN env var is set)                 #
# --------------------------------------------------------------------------- #
def auth_ok(supplied: Optional[str], expected: Optional[str] = None) -> bool:
    """Constant-time token check. Empty expected token == auth disabled."""
    exp = APP_TOKEN if expected is None else expected
    if not exp:
        return True
    return bool(supplied) and hmac.compare_digest(str(supplied), exp)


@app.middleware("http")
async def _auth_middleware(request: Request, call_next):
    if APP_TOKEN and request.url.path != "/healthz":
        supplied = (request.query_params.get("token")
                    or request.headers.get("x-app-token")
                    or request.cookies.get("app_token"))
        if not auth_ok(supplied):
            return JSONResponse(
                {"error": "unauthorized — open /?token=YOUR_APP_TOKEN once"},
                status_code=401)
    resp = await call_next(request)
    # first successful visit with ?token=... -> remember it in a cookie.
    # secure=True: never send the token over plain HTTP (Render serves HTTPS).
    # Local http://localhost dev still works — the browser just skips the cookie
    # and ?token= / X-App-Token per request keep working.
    if APP_TOKEN and auth_ok(request.query_params.get("token")):
        resp.set_cookie("app_token", APP_TOKEN, httponly=True, secure=True,
                        samesite="lax", max_age=30 * 86400)
    return resp


# --------------------------------------------------------------------------- #
#  Helpers                                                                     #
# --------------------------------------------------------------------------- #
def _quota(s):
    used = st.fmp_used_today(s)
    return {"used": used, "cap": QUOTA_CAP, "pct": round(used / QUOTA_CAP * 100),
            "warn": used >= 0.9 * QUOTA_CAP,
            "holdings": len(s.get("holdings", {})),
            "headroom_tickers": max(0, QUOTA_CAP - used)}


def _fetch_and_commit(tickers):
    """Fundamentals + daily momentum for `tickers`.
    Phase 1 (slow, NO lock): SEC + FMP + Yahoo fetch & analysis.
    Phase 2 (fast, locked):  reload fresh store, merge, atomic save.
    Returns (result_meta, fresh_store)."""
    s0 = st.load()
    used = st.fmp_used_today(s0)
    fetched, errors, calls, rf_pct, rf_live = refresh.fetch_fundamentals(
        tickers, config.FMP_API_KEY, used, QUOTA_CAP)
    daily = refresh.fetch_daily(tickers, config.FMP_API_KEY)
    with st.LOCK:
        s = st.load()
        refresh.commit_fundamentals(s, fetched, calls)
        refresh.commit_daily(s, daily)
        st.save(s)
    meta = {"refreshed": sorted(fetched.keys()), "errors": errors,
            "fmp_calls": calls, "fmp_used_today": st.fmp_used_today(s),
            "rf_pct": rf_pct, "rf_live": rf_live}
    return meta, s


# --------------------------------------------------------------------------- #
#  Endpoints                                                                   #
# --------------------------------------------------------------------------- #
@app.get("/healthz")
def healthz():
    return {"status": "ok", "version": config.DEEP_VERSION, "build": config.BUILD}


@app.get("/", response_class=HTMLResponse)
def index():
    return FileResponse(os.path.join(BASE, "index.html"))


@app.get("/api/portfolio")
def api_portfolio():
    s = st.load()
    view = refresh.portfolio_view(s)
    view["quota"] = _quota(s)
    view["version"] = config.DEEP_VERSION
    return JSONResponse(view)


@app.get("/api/quota")
def api_quota():
    return JSONResponse(_quota(st.load()))


@app.get("/api/persist")
def api_persist():
    """Google-Drive persistence health — surfaced as a header badge so a silent
    backup failure (expired token, quota, network) is visible BEFORE data is lost."""
    return JSONResponse(st.persist_status())


@app.post("/api/holding")
def api_set_holding(ticker: str, shares: float = None, avg_cost: float = None):
    t = st.clean_ticker(ticker)
    if not t:
        return JSONResponse({"error": "invalid ticker"}, status_code=422)
    with st.LOCK:                                   # fast: set holding only
        s = st.load()
        st.set_holding(s, t, shares, avg_cost)
        st.save(s)
        need_fetch = t not in s["facts"]
    meta = None
    if need_fetch:                                  # slow fetch OUTSIDE the lock
        meta, s = _fetch_and_commit([t])
    else:
        s = st.load()
    return JSONResponse({"view": refresh.portfolio_view(s),
                         "quota": _quota(s), "result": meta})


@app.post("/api/holding/remove")
def api_remove_holding(ticker: str):
    with st.LOCK:
        s = st.load()
        st.remove_holding(s, ticker)
        st.save(s)
    return JSONResponse(refresh.portfolio_view(s))


@app.post("/api/refresh")
def api_refresh():
    tickers = list(st.load().get("holdings", {}).keys())
    meta, s = _fetch_and_commit(tickers)
    return {"result": meta, "view": refresh.portfolio_view(s), "quota": _quota(s)}


@app.post("/api/daily")
def api_daily():
    tickers = list(st.load().get("holdings", {}).keys())
    daily = refresh.fetch_daily(tickers, config.FMP_API_KEY)   # FMP only if Yahoo blocked
    with st.LOCK:
        s = st.load()
        refresh.commit_daily(s, daily)
        st.save(s)
    return JSONResponse(refresh.portfolio_view(s))


@app.get("/api/watchlist")
def api_watchlist():
    s = st.load()
    return JSONResponse({"names": s.get("watchlist", [])})


@app.get("/api/screen")
def api_screen():
    """Damodaran S13/S19 GARP screen: rank every analysed name (holdings + watchlist
    results) on CHEAP x QUALITY using the stored DEEP subscores. Read-only."""
    s = st.load()
    facts = s.get("facts", {})
    items = []
    for t, v in (s.get("results", {}) or {}).items():
        ff = facts.get(t, {}) or {}
        items.append({"ticker": t, "company": ff.get("company"), "sector": ff.get("sector"),
                      "economics": v.get("E_econ"), "price": v.get("P"), "demand": v.get("D"),
                      "composite": v.get("composite"), "recommendation": v.get("recommendation")})
    return JSONResponse({"items": screen_mod.rank(items)})


@app.post("/api/watchlist/add")
def api_watch_add(ticker: str):
    with st.LOCK:
        s = st.load()
        st.add_watch(s, ticker)
        st.save(s)
    return JSONResponse({"names": s["watchlist"]})


@app.post("/api/watchlist/remove")
def api_watch_remove(ticker: str):
    with st.LOCK:
        s = st.load()
        st.remove_watch(s, ticker)
        st.save(s)
    return JSONResponse({"names": s["watchlist"]})


@app.post("/api/watchlist/run")
def api_watch_run(ticker: str = None):
    s0 = st.load()
    names = [st.clean_ticker(ticker)] if ticker else list(s0.get("watchlist", []))
    names = [n for n in names if n]
    res = refresh.fetch_watchlist(names, config.FMP_API_KEY,
                                  st.fmp_used_today(s0), QUOTA_CAP)
    with st.LOCK:                                   # persist only the quota counter
        s = st.load()
        st.add_fmp_calls(s, res["fmp_calls"])
        st.save(s)
    res["names"] = s.get("watchlist", [])
    res["quota"] = _quota(s)
    return JSONResponse(res)


@app.post("/api/watchlist/promote")
def api_watch_promote(ticker: str, shares: float = None, avg_cost: float = None):
    """Move a watchlist ticker into the portfolio (then it behaves like a holding)."""
    t = st.clean_ticker(ticker)
    if not t:
        return JSONResponse({"error": "invalid ticker"}, status_code=422)
    with st.LOCK:
        s = st.load()
        st.set_holding(s, t, shares, avg_cost)
        st.remove_watch(s, t)
        st.save(s)
    meta, s = _fetch_and_commit([t])
    return {"view": refresh.portfolio_view(s), "names": s.get("watchlist", []),
            "quota": _quota(s), "result": meta}


@app.get("/api/allocation")
def api_allocation():
    s = st.load()
    res, _ = refresh.allocation(s)
    return JSONResponse(res)


class WhatIfBuy(BaseModel):
    """One what-if purchase. Pydantic rejects non-numeric amounts with a clear
    422 instead of the old unhandled-500 crash."""
    ticker: str = ""
    amount: Optional[float] = None


@app.post("/api/allocation/whatif")
def api_whatif(items: List[WhatIfBuy]):
    # NOTE: deliberately a sync `def`. The old `async def` version blocked the
    # event loop waiting for the store lock and froze the ENTIRE server.
    body = [{"ticker": i.ticker, "amount": i.amount} for i in items]
    s = st.load()
    res, calls = refresh.allocation(s, body, config.FMP_API_KEY)  # may hit FMP — no lock
    if calls:
        with st.LOCK:
            s = st.load()
            st.add_fmp_calls(s, calls)
            st.save(s)
    res["quota"] = _quota(s)
    return JSONResponse(res)


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


def _build_risk(s, fmp_key, quota_used, quota_cap, tolerance_pct, horizon_years, prefer_fmp):
    """Assemble the full risk payload. Returns (payload, fmp_calls)."""
    holdings = s.get("holdings", {})
    facts = s.get("facts", {})
    mom = s.get("momentum", {})

    positions, betas, sectors, currencies, asset_class = {}, {}, {}, {}, {}
    for t, h in holdings.items():
        sh = h.get("shares") or 0
        ff = facts.get(t, {}) or {}
        price = (mom.get(t, {}) or {}).get("price") or ff.get("price")
        if sh <= 0 or not price:
            continue
        positions[t] = sh * price
        betas[t] = ff.get("beta")
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
    rdata, calls, rmeta = risk_prices.fetch_returns(
        tickers + ["SPY", "QQQ", "GLD", "IBIT"], fmp_key if prefer_fmp else "",
        quota_used, quota_cap, prefer_fmp=prefer_fmp)

    have_realized = all((rdata.get(t, {}).get("n") or 0) >= 60 for t in tickers)
    aligned = riskeng.align_returns({t: rdata.get(t, {}).get("returns", []) for t in tickers}, tickers)
    common_n = min((len(aligned[t]) for t in tickers if aligned[t]), default=0)

    if have_realized and common_n >= 60:
        cov = riskeng.cov_matrix({t: rdata[t]["returns"] for t in tickers}, tickers)
        cov_mode, cov_tag = "realized", "[CALC]"
    else:
        pvols = [config.CLASS_PROXY_VOL.get(asset_class[t], _proxy_vol(sectors[t]))
                 if asset_class[t] != "equity" else _proxy_vol(sectors[t]) for t in tickers]
        cov = riskeng.proxy_cov(tickers, pvols, asset_class)
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
        spy_rets = rdata.get("SPY", {}).get("returns", [])
        port_rets = riskeng.portfolio_returns(
            {t: rdata.get(t, {}).get("returns", []) for t in tickers}, tickers, weights)
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
        durations = {t: config.DURATION_PROXY.get(t) for t in tickers if asset_class.get(t) == "bond"}
        durations = {t: d for t, d in durations.items() if d}
        rate_risk = {
            "has_bonds": bool(durations), "bps": 100,
            "loss_pct": riskeng.rate_stress(weights, durations, 100) if durations else None,
            "durations": durations,
            "tag": "[JUDG-PROXY] category duration; +100bps parallel shock",
        }
        pricing_assets = [t for t in tickers if asset_class.get(t) in ("crypto", "collectible")]
    except Exception:
        rate_risk = {"has_bonds": False, "bps": 100, "loss_pct": None, "durations": {}, "tag": "[error]"}
        pricing_assets = []

    # ---- stress / tail ----
    stress = riskeng.stress_test(weights, betas, sectors)
    historical = riskeng.stress_test(weights, betas, sectors, scenarios=riskeng.HISTORICAL)
    var = riskeng.var_cvar(port_vol, horizon_years)
    reverse = riskeng.reverse_stress(weights, betas, tolerance_pct)
    severe_dd = min([r["loss_pct"] for r in (stress + historical)], default=None)

    # ---- suitability / sizing / rebalance ----
    suit = riskeng.suitability(stress + historical, var, tolerance_pct)
    sizing = riskeng.position_sizing(rc_rows, sectors)
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
        enb = rc_sum.get("enb_abs")
        enb_c = rc_sum_c.get("enb_abs")

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

        rets = {t: rdata.get(t, {}).get("returns", []) for t in tickers}
        ref_rets = {sym: rdata.get(sym, {}).get("returns", []) for sym, _nm, _cls in REFS}
        spy_rets = ref_rets.get("SPY", [])
        port_rets = riskeng.portfolio_returns(rets, tickers, weights)

        def _pc(a, b):
            v = riskeng.pair_corr(a, b)
            return round(v, 3) if v is not None else None

        bench_port = {}
        for sym, _nm, _cls in REFS:
            v = _pc(port_rets, ref_rets.get(sym, []))
            bench_port[sym] = {"normal": v, "crisis": _crisis(v, "PORT", sym)}
        bench_hold = {}
        for t in order_tk:
            bench_hold[t] = {sym: {"normal": _pc(rets.get(t, []), ref_rets.get(sym, [])),
                                   "crisis": _crisis(_pc(rets.get(t, []), ref_rets.get(sym, [])), t, sym)}
                             for sym, _nm, _cls in REFS}
        bench_ref = {}
        for i in range(len(REFS)):
            for j in range(i + 1, len(REFS)):
                a, b = REFS[i][0], REFS[j][0]
                v = _pc(ref_rets.get(a, []), ref_rets.get(b, []))
                bench_ref[f"{a}-{b}"] = {"normal": v, "crisis": _crisis(v, a, b)}

        dcorr = riskeng.downside_corr(port_rets, spy_rets, spy_rets)
        pearson = _pc(port_rets, spy_rets)
        roll = riskeng.rolling_corr(port_rets, spy_rets, 60)

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
                     "refs_fetched": {s2: bool(ref_rets.get(s2)) for s2, _nm, _cls in REFS}},
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
            "enb_abs": rc_sum.get("enb_abs"),
        },
        "diversification": {
            "dr_normal": round(dr_normal, 2) if dr_normal else None,
            "dr_crisis": round(dr_crisis, 2) if dr_crisis else None,
            "score": score,
        },
        "downside": downside,
        "rate_risk": rate_risk,
        "stress": {"hypothetical": stress, "historical": historical,
                   "var": var, "reverse": reverse, "tag": "[JUDG-SCENARIO] Illustrative, not a forecast"},
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
        },
    }, calls)


@app.get("/api/risk")
def api_risk(risk_tolerance_pct: float = 20.0, horizon_years: float = 1.0, enrich: str = "auto"):
    """Full risk-desk analysis of the current portfolio.
    enrich='auto' (default) uses FMP adjusted history when quota allows, else free
    stooq; enrich='free' never spends FMP quota (used by the regression test)."""
    prefer_fmp = (enrich != "free")
    s = st.load()
    quota_used = st.fmp_used_today(s)
    try:
        payload, calls = _build_risk(
            s, config.FMP_API_KEY, quota_used, QUOTA_CAP,
            risk_tolerance_pct, horizon_years, prefer_fmp)
    except Exception as e:                              # never return a bare 500 (frontend JSON.parse crashes)
        import traceback as _tb
        _tb.print_exc()
        return JSONResponse({"status": "error", "snapshot": None,
                             "message": f"Risk analysis failed: {type(e).__name__}: {e}"})
    if calls:                               # commit ONLY the quota counter, safely
        with st.LOCK:
            s2 = st.load()
            st.add_fmp_calls(s2, calls)
            st.save(s2)
        payload["quota"] = _quota(st.load())
    else:
        payload["quota"] = _quota(s)
    return JSONResponse(payload)
