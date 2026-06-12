"""
FastAPI app — DEEP v7.3 portfolio dashboard (v2).

Run:
    set FMP_API_KEY (optional, for sector/beta via FMP profile)
    pip install -r requirements.txt
    uvicorn app:app --port 8000
    open http://localhost:8000
"""
import os
import logging
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse

import config
import store as st
from pipeline import refresh

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("portfolio")

app = FastAPI(title="Portfolio DEEP v7.3")
_pool = ThreadPoolExecutor(max_workers=4)
BASE = os.path.dirname(os.path.abspath(__file__))
QUOTA_CAP = 250


def _run(fn, *a):
    # Hold the store lock for the whole job (load->mutate->save) so concurrent
    # mutating requests serialize instead of clobbering each other.
    with st.LOCK:
        return _pool.submit(fn, *a).result()


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


def _quota(s):
    used = st.fmp_used_today(s)
    return {"used": used, "cap": QUOTA_CAP, "pct": round(used / QUOTA_CAP * 100),
            "warn": used >= 0.9 * QUOTA_CAP,
            "holdings": len(s.get("holdings", {})),
            "headroom_tickers": max(0, QUOTA_CAP - used)}


@app.post("/api/holding")
def api_set_holding(ticker: str, shares: float = None, avg_cost: float = None):
    def job():
        s = st.load()
        st.set_holding(s, ticker, shares, avg_cost)
        t = ticker.upper().strip()
        if t not in s["facts"]:                     # newly added -> fetch + analyse once
            res = refresh.refresh_fundamentals(s, [t], config.FMP_API_KEY, QUOTA_CAP)
            refresh.run_daily(s, [t])
        st.save(s)
        return refresh.portfolio_view(s)
    return JSONResponse(_run(job))


@app.post("/api/holding/remove")
def api_remove_holding(ticker: str):
    def job():
        s = st.load(); st.remove_holding(s, ticker); st.save(s)
        return refresh.portfolio_view(s)
    return JSONResponse(_run(job))


@app.post("/api/refresh")
def api_refresh():
    def job():
        s = st.load()
        tickers = list(s.get("holdings", {}).keys())
        res = refresh.refresh_fundamentals(s, tickers, config.FMP_API_KEY, QUOTA_CAP)
        refresh.run_daily(s, tickers)
        st.save(s)
        return {"result": res, "view": refresh.portfolio_view(s), "quota": _quota(s)}
    return JSONResponse(_run(job))


@app.get("/api/watchlist")
def api_watchlist():
    s = st.load()
    return JSONResponse({"names": s.get("watchlist", [])})


@app.post("/api/watchlist/add")
def api_watch_add(ticker: str):
    with st.LOCK:
        s = st.load(); st.add_watch(s, ticker); st.save(s)
    return JSONResponse({"names": s["watchlist"]})


@app.post("/api/watchlist/remove")
def api_watch_remove(ticker: str):
    with st.LOCK:
        s = st.load(); st.remove_watch(s, ticker); st.save(s)
    return JSONResponse({"names": s["watchlist"]})


@app.post("/api/watchlist/run")
def api_watch_run(ticker: str = None):
    def job():
        s = st.load()
        names = [ticker.upper().strip()] if ticker else list(s.get("watchlist", []))
        res = refresh.watchlist_run(s, names, config.FMP_API_KEY, QUOTA_CAP)
        st.save(s)                                  # persist only the quota counter
        res["names"] = s.get("watchlist", [])
        res["quota"] = _quota(s)
        return res
    return JSONResponse(_run(job))


@app.post("/api/watchlist/promote")
def api_watch_promote(ticker: str, shares: float = None, avg_cost: float = None):
    """Move a watchlist ticker into the portfolio (then it behaves like a holding)."""
    def job():
        s = st.load()
        st.set_holding(s, ticker, shares, avg_cost)
        st.remove_watch(s, ticker)
        t = ticker.upper().strip()
        refresh.refresh_fundamentals(s, [t], config.FMP_API_KEY, QUOTA_CAP)
        refresh.run_daily(s, [t])
        st.save(s)
        return {"view": refresh.portfolio_view(s), "names": s.get("watchlist", []), "quota": _quota(s)}
    return JSONResponse(_run(job))


@app.get("/api/allocation")
def api_allocation():
    s = st.load()
    res, _ = refresh.allocation(s)
    return JSONResponse(res)


@app.post("/api/allocation/whatif")
async def api_whatif(request: Request):
    body = await request.json()        # [{ticker, amount}, ...]
    def job():
        s = st.load()
        res, calls = refresh.allocation(s, body, config.FMP_API_KEY)
        if calls:
            st.add_fmp_calls(s, calls); st.save(s)
        res["quota"] = _quota(s)
        return res
    return JSONResponse(_run(job))


@app.post("/api/daily")
def api_daily():
    def job():
        s = st.load()
        refresh.run_daily(s, list(s.get("holdings", {}).keys()))
        st.save(s)
        return refresh.portfolio_view(s)
    return JSONResponse(_run(job))
