"""
FastAPI app — DEEP portfolio dashboard (engine version = config.DEEP_VERSION).

Run:
    set FMP_API_KEY (optional, for sector/beta via FMP profile)
    set APP_TOKEN   (protects the app when deployed publicly)
    pip install -r requirements.txt
    uvicorn app:app --port 8000

Concurrency model:
  * Every endpoint is a plain sync `def`, so FastAPI runs it in its threadpool and
    the event loop is never blocked (an `async` endpoint waiting on store.LOCK once
    froze the whole server, /healthz included).
  * Slow network work (SEC / FMP / Yahoo) runs OUTSIDE store.LOCK; the lock is held
    only for a fast load -> merge -> save (store.transaction).
  * Single-user trade-off: two overlapping refreshes can both pass the FMP quota
    pre-check; the counter is still committed correctly afterwards.
  * Run exactly ONE worker process. store.LOCK, the Drive push worker and the pull
    guard are per-process; multiple workers would silently break all of them.

Auth (env APP_TOKEN): open https://your-app/?token=YOUR_TOKEN once and a cookie is
stored. /healthz stays open for Render health checks.
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
import store_sync
from pipeline import fundamentals, watchlist, portfolio, allocation, risk_report, batch
from pipeline import screen as screen_mod
from pipeline import daily as daily_mod

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("portfolio")

app = FastAPI(title=f"Portfolio DEEP v{config.DEEP_VERSION}")
BASE = os.path.dirname(os.path.abspath(__file__))

if os.path.isdir(os.path.join(BASE, "static")):          # dashboard CSS / JS / fonts, no CDN
    app.mount("/static", StaticFiles(directory=os.path.join(BASE, "static")), name="static")

# Read at import (not from config) so reloading this module re-reads the env.
_workers = os.environ.get("WEB_CONCURRENCY") or os.environ.get("UVICORN_WORKERS")
SINGLE_WORKER_OK = not (_workers and str(_workers).isdigit() and int(_workers) > 1)
if not SINGLE_WORKER_OK:
    log.error("MULTI-WORKER CONFIG DETECTED (%s workers) — store lock/Drive guard "
              "are per-process. Run exactly ONE worker or data loss is possible.", _workers)


# --------------------------------------------------------------------------- #
#  Auth                                                                        #
# --------------------------------------------------------------------------- #
APP_TOKEN = os.environ.get("APP_TOKEN", "")
# No token is fine on localhost, where the socket is the boundary. On a public URL it
# would hand every route (holdings, assumption writes, FMP quota) to the internet, so
# the app fails closed instead. ALLOW_PUBLIC_NO_AUTH=1 is the deliberate opt-out.
PUBLIC_DEPLOY = bool(os.environ.get("RENDER") or os.environ.get("RENDER_EXTERNAL_URL")
                     or os.environ.get("PUBLIC_DEPLOY"))
ALLOW_PUBLIC_NO_AUTH = os.environ.get("ALLOW_PUBLIC_NO_AUTH", "") == "1"
AUTH_REQUIRED_BUT_MISSING = PUBLIC_DEPLOY and not APP_TOKEN and not ALLOW_PUBLIC_NO_AUTH
if AUTH_REQUIRED_BUT_MISSING:
    log.error("APP_TOKEN is not set but this looks like a PUBLIC deployment. "
              "Every route is refused until you set APP_TOKEN (or set "
              "ALLOW_PUBLIC_NO_AUTH=1 to deliberately serve it open).")

_NO_TOKEN_ON_PUBLIC = {
    "error": "APP_TOKEN is not set on a public deployment — refusing to "
             "serve the portfolio. Set APP_TOKEN in the environment and "
             "open /?token=YOUR_APP_TOKEN once, or set "
             "ALLOW_PUBLIC_NO_AUTH=1 to serve it open on purpose."}
_UNAUTHORIZED = {"error": "unauthorized — open /?token=YOUR_APP_TOKEN once"}


def auth_ok(supplied: Optional[str], expected: Optional[str] = None) -> bool:
    """Constant-time token check. Empty expected token == auth disabled."""
    exp = APP_TOKEN if expected is None else expected
    if not exp:
        return True
    return bool(supplied) and hmac.compare_digest(str(supplied), exp)


@app.middleware("http")
async def _auth_middleware(request: Request, call_next):
    # /static holds only code (no portfolio data). Serving it without a token keeps the
    # dashboard able to show its own "open /?token=" message when the cookie is missing.
    is_static = request.url.path.startswith("/static/")
    if request.url.path != "/healthz" and not is_static:
        if AUTH_REQUIRED_BUT_MISSING:
            return JSONResponse(_NO_TOKEN_ON_PUBLIC, status_code=503)
        supplied = (request.query_params.get("token")
                    or request.headers.get("x-app-token")
                    or request.cookies.get("app_token"))
        if not auth_ok(supplied):
            return JSONResponse(_UNAUTHORIZED, status_code=401)
    resp = await call_next(request)
    if is_static:
        # revalidate every load (cheap: ETag -> 304), so a deploy never mixes the new
        # index.html with yesterday's cached JS
        resp.headers["Cache-Control"] = "no-cache"
        return resp
    # Remember a valid ?token= in a cookie. secure=True keeps the token off plain
    # HTTP; on http://localhost the browser skips it and ?token= / X-App-Token still
    # work. Never on /healthz: it is unauthenticated, and minting there would hand
    # out sessions and write the raw token into the platform's access log.
    if (request.url.path != "/healthz" and APP_TOKEN
            and auth_ok(request.query_params.get("token"))):
        resp.set_cookie("app_token", APP_TOKEN, httponly=True, secure=True,
                        samesite="lax", max_age=30 * 86400)
    return resp


# --------------------------------------------------------------------------- #
#  Helpers                                                                     #
# --------------------------------------------------------------------------- #
def _quota(s):
    from sources import fmp
    cap = config.QUOTA_CAP
    used = st.fmp_used_today(s)
    left = batch.usable_quota(used, cap)
    return {"used": used, "cap": cap, "pct": round(used / cap * 100),
            "warn": used >= 0.9 * cap,
            "exhausted": fmp.LEDGER.exhausted(),
            "holdings": len(s.get("holdings", {})),
            "reserved": config.QUOTA_RESERVE,
            # calls, and how many full ticker refreshes they pay for (5 calls each):
            # the banner used to call 18 calls "18 ticker-fetches"
            "headroom_calls": left,
            "headroom_tickers": left // batch.FMP_CALLS_PER_TICKER}


def _holding_tickers():
    return list(st.load().get("holdings", {}).keys())


def _commit_fmp_usage():
    """Persist only the FMP counter (from the request ledger); returns the saved store."""
    with st.transaction() as s:
        st.sync_fmp_usage(s)
    return s


def _fetch_and_commit(tickers):
    """Fundamentals + daily momentum for `tickers`: slow fetch with no lock, then a
    locked merge into a freshly loaded store. Returns (result_meta, saved_store)."""
    from sources import fmp
    before = st.load()
    used = st.fmp_used_today(before)
    sent0 = fmp.LEDGER.used()
    fetched, errors, calls, rf_pct, rf_live = fundamentals.fetch_fundamentals(
        tickers, config.FMP_API_KEY, used, config.QUOTA_CAP, prev_facts=before.get("facts"),
        prev_results=before.get("results"), refreshed_at=before.get("refreshed_at"))
    daily = daily_mod.fetch_daily(tickers, config.FMP_API_KEY, prev_market=before.get("market"))
    with st.transaction() as s:
        fundamentals.commit_fundamentals(s, fetched,
                                         skipped={t: "FMP quota" if f"{t} (quota)" in errors
                                                  else "fetch failed"
                                                  for t in tickers if t not in fetched})
        daily_mod.commit_daily(s, daily)
    if fmp.LEDGER.exhausted():
        errors.append("FMP เกินโควตาวันนี้ — ช่องที่มาจาก FMP ใช้ Yahoo/ข้อมูลรอบก่อนแทน")
    meta = {"refreshed": sorted(fetched.keys()), "errors": errors,
            # what FMP saw this run (the ledger), not the pipeline's own tally
            "fmp_calls": fmp.LEDGER.used() - sent0, "fmp_used_today": st.fmp_used_today(s),
            "rf_pct": rf_pct, "rf_live": rf_live}
    return meta, s


def _assumption_values():
    return {"erp_pct": round(config.ERP * 100, 2), "erp_as_of": config.ERP_AS_OF,
            "market_pe": config.MARKET_PE, "market_pe_as_of": config.MARKET_PE_AS_OF}


def _invalid(message):
    return JSONResponse({"error": message}, status_code=422)


# --------------------------------------------------------------------------- #
#  Read endpoints                                                              #
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
    view = portfolio.view(s)
    view["quota"] = _quota(s)
    view["version"] = config.DEEP_VERSION
    return JSONResponse(view)


@app.get("/api/quota")
def api_quota():
    return JSONResponse(_quota(st.load()))


@app.get("/api/persist")
def api_persist():
    """Drive persistence health, shown as a header badge so a silent backup
    failure is visible BEFORE data is lost."""
    return JSONResponse(store_sync.status())


@app.get("/api/watchlist")
def api_watchlist():
    return JSONResponse({"names": st.load().get("watchlist", [])})


@app.get("/api/screen")
def api_screen():
    """GARP screen: rank every analysed name on CHEAP x QUALITY from the stored
    DEEP subscores. Read-only."""
    s = st.load()
    facts = s.get("facts", {})
    items = []
    for t, v in (s.get("results", {}) or {}).items():
        ff = facts.get(t, {}) or {}
        items.append({"ticker": t, "company": ff.get("company"), "sector": ff.get("sector"),
                      "economics": v.get("E_econ"), "price": v.get("P"), "demand": v.get("D"),
                      "composite": v.get("composite"), "recommendation": v.get("recommendation")})
    return JSONResponse({"items": screen_mod.rank(items)})


@app.get("/api/allocation")
def api_allocation():
    res, _ = allocation.build(st.load())
    return JSONResponse(res)


@app.get("/api/risk")
def api_risk(risk_tolerance_pct: float = 20.0, horizon_years: float = 1.0, enrich: str = "auto"):
    """Risk-desk analysis of the current portfolio. enrich='auto' uses FMP adjusted
    history when quota allows, else free stooq; enrich='free' never spends quota."""
    s = st.load()
    try:
        payload, calls = risk_report.build(
            s, config.FMP_API_KEY, st.fmp_used_today(s), config.QUOTA_CAP,
            risk_tolerance_pct, horizon_years, enrich != "free")
    except Exception as e:      # a bare 500 would crash the frontend's JSON.parse
        log.exception("risk analysis failed")
        return JSONResponse({"status": "error", "snapshot": None,
                             "message": f"Risk analysis failed: {type(e).__name__}: {e}"})
    payload["quota"] = _quota(_commit_fmp_usage())
    return JSONResponse(payload)


# --------------------------------------------------------------------------- #
#  Monthly assumptions (ERP / MARKET_PE). store.load() overlays saved values    #
#  onto config, so the engine, validate band and market overlay read them.     #
# --------------------------------------------------------------------------- #
@app.get("/api/assumptions")
def api_assumptions():
    from domain.engine.deep_v82 import erp_months_old
    a = st.load().get("assumptions") or {}      # load() also applies the override
    return JSONResponse({
        **_assumption_values(),
        "source": {"erp": getattr(config, "ERP_SOURCE", "config-default"),
                   "market_pe": "manual" if a.get("market_pe") else "config-default"},
        "erp_months_old": erp_months_old(),
        "erp_stale": erp_months_old() > config.ERP_STALE_MONTHS,
        "updated": a.get("updated"),
    })


@app.post("/api/assumptions")
def api_set_assumptions(erp_pct: float = None, market_pe: float = None):
    """Save the monthly manual values (Damodaran implied ERP %, S&P trailing P/E)."""
    if erp_pct is None and market_pe is None:
        return _invalid("provide erp_pct and/or market_pe")
    if erp_pct is not None and not (1.0 <= erp_pct <= 10.0):
        return _invalid("erp_pct out of sane band 1-10 (percent, e.g. 4.45)")
    if market_pe is not None and not (5.0 <= market_pe <= 60.0):
        return _invalid("market_pe out of sane band 5-60")
    with st.transaction() as s:
        a = st.set_assumptions(s, erp_pct, market_pe)
    return JSONResponse({
        "ok": True, "assumptions": a, **_assumption_values(),
        "note": "มีผลกับ Ke/WACC/market overlay ทันที — กด Run Fundamental Refresh เพื่อคำนวณ fair value ใหม่ด้วยค่านี้",
    })


# --------------------------------------------------------------------------- #
#  Revenue mix (country risk): where each company earns its revenue             #
# --------------------------------------------------------------------------- #
@app.get("/api/revenue_mix")
def api_revenue_mix():
    """Per holding/watchlist ticker: the mix in force, where it came from (config,
    dashboard, latest 10-K, or the domicile) and the CRP it gave at the last refresh."""
    s = st.load()
    manual = (s.get("assumptions") or {}).get("revenue_mix") or {}
    rows = []
    for t in sorted(set(s.get("holdings") or {}) | set(s.get("watchlist") or [])):
        f = (s.get("facts") or {}).get(t) or {}
        rows.append({"ticker": t, "manual": manual.get(t),
                     "config": (getattr(config, "REVENUE_MIX", {}) or {}).get(t),
                     "crp_pct": round(f["crp"] * 100, 2) if isinstance(f.get("crp"), (int, float)) else None,
                     "basis": f.get("crp_basis")})
    return JSONResponse({"rows": rows})


@app.post("/api/revenue_mix")
def api_set_revenue_mix(ticker: str, mix: str = ""):
    """Save a revenue mix typed as 'US 56, China 20, Western Europe 24'; an empty mix
    clears the ticker's dashboard entry (the 10-K or domicile applies again)."""
    t = st.clean_ticker(ticker)
    if not t:
        return _invalid("invalid ticker")
    parsed = None
    if mix.strip():
        parsed, err = st.parse_revenue_mix(mix)
        if err:
            return _invalid(err)
    with st.transaction() as s:
        st.set_revenue_mix(s, t, parsed)
    return JSONResponse({"ok": True, "ticker": t, "mix": parsed,
                         "note": "มีผลกับ CRP ตอนกด Run Fundamental Refresh ครั้งถัดไป"})


# --------------------------------------------------------------------------- #
#  Holdings                                                                    #
# --------------------------------------------------------------------------- #
@app.post("/api/holding")
def api_set_holding(ticker: str, shares: float = None, avg_cost: float = None):
    t = st.clean_ticker(ticker)
    if not t:
        return _invalid("invalid ticker")
    with st.transaction() as s:
        st.set_holding(s, t, shares, avg_cost)
    meta = None
    if t not in s["facts"]:                     # new name: slow fetch OUTSIDE the lock
        meta, s = _fetch_and_commit([t])
    return JSONResponse({"view": portfolio.view(s),
                         "quota": _quota(s), "result": meta})


@app.post("/api/cash")
def api_set_cash(usd: float):
    """Uninvested cash (USD): the Risk Desk measures loss and volatility on the whole
    portfolio, invested names plus this cash."""
    try:
        with st.transaction() as s:
            st.set_cash(s, usd)
    except ValueError as e:
        return _invalid(str(e))
    return JSONResponse({"view": portfolio.view(s)})


@app.post("/api/holding/remove")
def api_remove_holding(ticker: str):
    with st.transaction() as s:
        st.remove_holding(s, ticker)
    return JSONResponse(portfolio.view(s))


@app.post("/api/refresh")
def api_refresh():
    meta, s = _fetch_and_commit(_holding_tickers())
    return {"result": meta, "view": portfolio.view(s), "quota": _quota(s)}


@app.post("/api/daily")
def api_daily():
    daily = daily_mod.fetch_daily(_holding_tickers(), config.FMP_API_KEY,
                                  prev_market=st.load().get("market"))
    with st.transaction() as s:
        daily_mod.commit_daily(s, daily)
        st.sync_fmp_usage(s)
    return JSONResponse(portfolio.view(s))


# --------------------------------------------------------------------------- #
#  Watchlist                                                                   #
# --------------------------------------------------------------------------- #
@app.post("/api/watchlist/add")
def api_watch_add(ticker: str):
    with st.transaction() as s:
        st.add_watch(s, ticker)
    return JSONResponse({"names": s["watchlist"]})


@app.post("/api/watchlist/remove")
def api_watch_remove(ticker: str):
    with st.transaction() as s:
        st.remove_watch(s, ticker)
    return JSONResponse({"names": s["watchlist"]})


@app.post("/api/watchlist/run")
def api_watch_run(ticker: str = None):
    s0 = st.load()
    names = [st.clean_ticker(ticker)] if ticker else list(s0.get("watchlist", []))
    res = watchlist.fetch_watchlist([n for n in names if n], config.FMP_API_KEY,
                                  st.fmp_used_today(s0), config.QUOTA_CAP,
                                  prev=s0.get("watch_cache"))
    with st.transaction() as s:
        st.sync_fmp_usage(s)
        st.commit_watch_cache(s, res.pop("cache", {}))
    from sources import fmp
    if fmp.LEDGER.exhausted():
        res["errors"] = list(res.get("errors") or []) + [
            "FMP เกินโควตาวันนี้ — ช่องที่มาจาก FMP ใช้ Yahoo/ข้อมูลรอบก่อนแทน"]
    res["names"] = s.get("watchlist", [])
    res["quota"] = _quota(s)
    return JSONResponse(res)


@app.post("/api/watchlist/promote")
def api_watch_promote(ticker: str, shares: float = None, avg_cost: float = None):
    """Move a watchlist ticker into the portfolio."""
    t = st.clean_ticker(ticker)
    if not t:
        return _invalid("invalid ticker")
    with st.transaction() as s:
        st.set_holding(s, t, shares, avg_cost)
        st.remove_watch(s, t)
    meta, s = _fetch_and_commit([t])
    return {"view": portfolio.view(s), "names": s.get("watchlist", []),
            "quota": _quota(s), "result": meta}


# --------------------------------------------------------------------------- #
#  What-if allocation                                                          #
# --------------------------------------------------------------------------- #
class WhatIfBuy(BaseModel):
    """One what-if purchase. Pydantic turns a non-numeric amount into a 422."""
    ticker: str = ""
    amount: Optional[float] = None


@app.post("/api/allocation/whatif")
def api_whatif(items: List[WhatIfBuy], risk_tolerance_pct: float = 20.0, horizon_years: float = 1.0):
    body = [{"ticker": i.ticker, "amount": i.amount} for i in items]
    s = st.load()
    res, calls = allocation.build(s, body, config.FMP_API_KEY)  # may hit FMP: no lock
    try:                                   # S2: the risk the buys ADD, at market value
        res["risk"] = (risk_report.whatif(s, res["added"], risk_tolerance_pct, horizon_years)
                       if res.get("added") else None)
    except Exception as e:
        log.exception("what-if risk failed")
        res["risk"] = {"error": f"{type(e).__name__}: {e}"}
    res["quota"] = _quota(_commit_fmp_usage())
    return JSONResponse(res)
