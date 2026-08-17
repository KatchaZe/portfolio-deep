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
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import config
import store as st
from pipeline import refresh, risk_report
from pipeline import screen as screen_mod

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("portfolio")

app = FastAPI(title=f"Portfolio DEEP v{config.DEEP_VERSION}")
BASE = os.path.dirname(os.path.abspath(__file__))
QUOTA_CAP = config.QUOTA_CAP
APP_TOKEN = os.environ.get("APP_TOKEN", "")

# P2-9: vendored Chart.js (static/chart.umd.js) — no CDN dependency at runtime.
if os.path.isdir(os.path.join(BASE, "static")):
    app.mount("/static", StaticFiles(directory=os.path.join(BASE, "static")), name="static")

# P1-7 (deploy H5, enforced): store.LOCK, the Drive-push worker and the pull-state
# guard are all PER-PROCESS — the app must run as exactly ONE worker. Detect the
# common multi-worker env configs and surface loudly (healthz + error log).
_workers = os.environ.get("WEB_CONCURRENCY") or os.environ.get("UVICORN_WORKERS")
SINGLE_WORKER_OK = not (_workers and str(_workers).isdigit() and int(_workers) > 1)
if not SINGLE_WORKER_OK:
    log.error("MULTI-WORKER CONFIG DETECTED (%s workers) — store lock/Drive guard "
              "are per-process. Run exactly ONE worker or data loss is possible.", _workers)


# --------------------------------------------------------------------------- #
#  Auth (optional — active only when APP_TOKEN env var is set)                 #
# --------------------------------------------------------------------------- #
def auth_ok(supplied: Optional[str], expected: Optional[str] = None) -> bool:
    """Constant-time token check. Empty expected token == auth disabled."""
    exp = APP_TOKEN if expected is None else expected
    if not exp:
        return True
    return bool(supplied) and hmac.compare_digest(str(supplied), exp)


# FIX (2026-08-16) — BROKEN ACCESS CONTROL. Auth was "optional", and unset is the
# default, so a deploy that forgot APP_TOKEN served every route to the whole
# internet. Reproduced in _audit_app/r1_no_auth.py: with APP_TOKEN empty, anonymous
# POSTs to /api/holding, /api/watchlist/add, /api/assumptions, /api/refresh and
# /api/daily all returned 200 and actually mutated the store — a stranger can edit
# the portfolio, rewrite the ERP/market-PE assumptions the whole engine reads, and
# burn the FMP daily quota; GET /api/portfolio hands them the holdings.
#
# Optional is fine on localhost, where the socket is the boundary. It is not fine on
# a public URL. So: when the process looks publicly deployed and no token is set, the
# app FAILS CLOSED with an explanatory 503 rather than serving. `ALLOW_PUBLIC_NO_AUTH=1`
# is the deliberate opt-out for someone who really does want it open.
PUBLIC_DEPLOY = bool(os.environ.get("RENDER") or os.environ.get("RENDER_EXTERNAL_URL")
                     or os.environ.get("PUBLIC_DEPLOY"))
ALLOW_PUBLIC_NO_AUTH = os.environ.get("ALLOW_PUBLIC_NO_AUTH", "") == "1"
AUTH_REQUIRED_BUT_MISSING = PUBLIC_DEPLOY and not APP_TOKEN and not ALLOW_PUBLIC_NO_AUTH
if AUTH_REQUIRED_BUT_MISSING:
    log.error("APP_TOKEN is not set but this looks like a PUBLIC deployment. "
              "Every route is refused until you set APP_TOKEN (or set "
              "ALLOW_PUBLIC_NO_AUTH=1 to deliberately serve it open).")


@app.middleware("http")
async def _auth_middleware(request: Request, call_next):
    if AUTH_REQUIRED_BUT_MISSING and request.url.path != "/healthz":
        return JSONResponse(
            {"error": "APP_TOKEN is not set on a public deployment — refusing to "
                      "serve the portfolio. Set APP_TOKEN in the environment and "
                      "open /?token=YOUR_APP_TOKEN once, or set "
                      "ALLOW_PUBLIC_NO_AUTH=1 to serve it open on purpose."},
            status_code=503)
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
    # 2026-08-16: only mint the cookie on a real page/API hit. /healthz is exempt from
    # the 401 check, and minting there meant the unauthenticated health endpoint also
    # handed out session cookies — and every /healthz?token=... probe wrote the raw
    # token into Render's access log for no benefit.
    if (APP_TOKEN and request.url.path != "/healthz"
            and auth_ok(request.query_params.get("token"))):
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
    return {"status": "ok", "version": config.DEEP_VERSION, "build": config.BUILD,
            "single_worker_ok": SINGLE_WORKER_OK}


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


# --------------------------------------------------------------------------- #
#  Monthly assumptions (ERP / MARKET_PE) — dashboard form -> store -> Drive.    #
#  store.load() overlays saved values onto config, so the engine (Ke/WACC),     #
#  validate band and the market overlay all pick them up at call time.          #
# --------------------------------------------------------------------------- #
@app.get("/api/assumptions")
def api_assumptions():
    s = st.load()                        # also applies any stored override to config
    a = s.get("assumptions") or {}
    from domain.engine.deep_v82 import erp_months_old
    return JSONResponse({
        "erp_pct": round(config.ERP * 100, 2), "erp_as_of": config.ERP_AS_OF,
        "market_pe": config.MARKET_PE, "market_pe_as_of": config.MARKET_PE_AS_OF,
        "source": {"erp": "manual" if a.get("erp") else "config-default",
                   "market_pe": "manual" if a.get("market_pe") else "config-default"},
        "erp_months_old": erp_months_old(),
        "erp_stale": erp_months_old() > config.ERP_STALE_MONTHS,
        "updated": a.get("updated"),
    })


@app.post("/api/assumptions")
def api_set_assumptions(erp_pct: float = None, market_pe: float = None):
    """Save the monthly manual values (Damodaran implied ERP %, S&P trailing P/E).
    Persisted in portfolio.json -> mirrored to Google Drive by the normal save()
    flow (same guard/worker as holdings), restored automatically on cold start."""
    if erp_pct is None and market_pe is None:
        return JSONResponse({"error": "provide erp_pct and/or market_pe"}, status_code=422)
    if erp_pct is not None and not (1.0 <= erp_pct <= 10.0):
        return JSONResponse({"error": "erp_pct out of sane band 1-10 (percent, e.g. 4.45)"},
                            status_code=422)
    if market_pe is not None and not (5.0 <= market_pe <= 60.0):
        return JSONResponse({"error": "market_pe out of sane band 5-60"}, status_code=422)
    with st.LOCK:
        s = st.load()
        a = st.set_assumptions(s, erp_pct, market_pe)
        st.save(s)                                   # atomic write + Drive mirror
    return JSONResponse({
        "ok": True, "assumptions": a,
        "erp_pct": round(config.ERP * 100, 2), "erp_as_of": config.ERP_AS_OF,
        "market_pe": config.MARKET_PE, "market_pe_as_of": config.MARKET_PE_AS_OF,
        "note": "มีผลกับ Ke/WACC/market overlay ทันที — กด Run Fundamental Refresh เพื่อคำนวณ fair value ใหม่ด้วยค่านี้",
    })


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


@app.get("/api/risk")
def api_risk(risk_tolerance_pct: float = 20.0, horizon_years: float = 1.0, enrich: str = "auto"):
    """Full risk-desk analysis of the current portfolio.
    enrich='auto' (default) uses FMP adjusted history when quota allows, else free
    stooq; enrich='free' never spends FMP quota (used by the regression test)."""
    prefer_fmp = (enrich != "free")
    s = st.load()
    quota_used = st.fmp_used_today(s)
    try:
        payload, calls = risk_report.build(
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
