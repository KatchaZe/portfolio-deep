"""
FastAPI app — DEEP v7.3 portfolio dashboard (v2.1, hardened).

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
from pipeline import refresh

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("portfolio")

app = FastAPI(title="Portfolio DEEP v7.3")
BASE = os.path.dirname(os.path.abspath(__file__))
QUOTA_CAP = 250
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
    # first successful visit with ?token=... -> remember it in a cookie
    if APP_TOKEN and auth_ok(request.query_params.get("token")):
        resp.set_cookie("app_token", APP_TOKEN, httponly=True,
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
    daily = refresh.fetch_daily(tickers)
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
    return {"status": "ok", "version": config.DEEP_VERSION}


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
    daily = refresh.fetch_daily(tickers)            # network OUTSIDE the lock
    with st.LOCK:
        s = st.load()
        refresh.commit_daily(s, daily)
        st.save(s)
    return JSONResponse(refresh.portfolio_view(s))


@app.get("/api/watchlist")
def api_watchlist():
    s = st.load()
    return JSONResponse({"names": s.get("watchlist", [])})


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
