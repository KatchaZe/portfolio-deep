"""
Orchestration — fundamentals refresh, daily momentum, and portfolio view.

  refresh_fundamentals(s, tickers)  SEC+FMP+Yahoo -> normalize -> validate -> engine -> store
  run_daily(s, tickers)             Yahoo chart -> momentum -> action (no FMP quota)
  portfolio_view(s)                 build display rows incl. P/L

Each FMP call is counted for the quota guard. Fundamentals only need refreshing
after earnings; daily momentum is free (Yahoo).
"""
import os
import json
import time
import logging
import datetime as dt

import config
from sources import sec_edgar, yahoo, fmp
from pipeline import normalize, validate, rev_track
from domain import indicators
from domain.engine import get_engine
import store as store_mod

log = logging.getLogger("portfolio.refresh")
_cik_map = None


def resolve_cik(ticker):
    global _cik_map
    t = ticker.upper().strip()
    if t in config.CIKS:
        return config.CIKS[t], None
    if _cik_map is None:
        _cik_map = _load_cik_map()
    v = _cik_map.get(t)
    return (v[0], v[1]) if v else (None, None)


def _load_cik_map():
    """SEC ticker->CIK map, cached to disk (changes rarely; refreshed ~monthly)."""
    cache = os.path.join(config.CACHE_DIR, "company_tickers.json")
    try:
        if os.path.exists(cache) and (time.time() - os.path.getmtime(cache)) < 30 * 86400:
            with open(cache, encoding="utf-8") as fh:
                raw = json.load(fh)
            return {row["ticker"].upper(): (str(row["cik_str"]).zfill(10), row.get("title"))
                    for row in raw.values()}
    except Exception as e:
        log.warning("CIK map cache read failed: %s", e)
    try:
        import requests
        r = requests.get("https://www.sec.gov/files/company_tickers.json",
                         headers={"User-Agent": config.SEC_USER_AGENT}, timeout=20)
        raw = r.json()
        try:
            os.makedirs(config.CACHE_DIR, exist_ok=True)
            tmp = f"{cache}.tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(raw, fh)
            os.replace(tmp, cache)
        except Exception as e:
            log.warning("CIK map cache write failed: %s", e)
        return {row["ticker"].upper(): (str(row["cik_str"]).zfill(10), row.get("title"))
                for row in raw.values()}
    except Exception as e:
        log.warning("CIK map fetch failed: %s", e)
        return {}


def analyze(ticker, rf, fmp_key="", rf_live=True):
    """Fetch -> normalize -> validate -> engine. Returns (facts, valuation, fmp_calls).
    NETWORK ONLY — never touches the store, safe to run outside store.LOCK."""
    t = ticker.upper().strip()
    cik, name = resolve_cik(t)
    fmp_calls = 0
    sec_cf = sec_edgar.fetch_companyfacts(
        cik, config.SEC_USER_AGENT, cache_dir=config.CACHE_DIR,
        ttl_hours=config.SEC_CACHE_TTL_HOURS, min_interval=config.SEC_MIN_INTERVAL) if cik else None

    # currency -> FX
    fx = None
    if sec_cf:
        try:
            ccy = sec_edgar.extract(sec_cf).get("currency", "USD")
            if ccy and ccy != "USD":
                fx = yahoo.fetch_fx_to_usd(ccy)
        except Exception as e:
            log.warning("%s FX lookup failed: %s", t, e)

    profile = None
    if fmp_key:
        try:
            import requests
            r = requests.get(f"{config.FMP_BASE}/profile", params={"symbol": t, "apikey": fmp_key}, timeout=15)
            profile = r.json(); fmp_calls = 1
        except Exception as e:
            log.warning("%s FMP profile failed: %s", t, e)
            profile = None

    yq = yahoo.fetch_consensus(t)
    ff = normalize.build(t, sec_cf, profile, yq, fx_rate=fx, company=name)
    if not rf_live:
        ff.flags.append(f"Rf fallback {round(rf*100,2)}% — live 10Y Treasury yield unavailable")
    validate.validate(ff, rf=rf)
    val = get_engine().evaluate(ff, rf=rf)
    return ff, val, fmp_calls


def fetch_fundamentals(tickers, fmp_key="", quota_used=0, quota_cap=250):
    """NETWORK PHASE — no store access, run OUTSIDE store.LOCK.
    Returns (fetched {ticker: (FinancialFacts, Valuation)}, errors, fmp_calls,
    rf_pct, rf_live)."""
    rf, rf_live = yahoo.fetch_treasury_10y()
    fetched, errors, calls = {}, [], 0
    cost = 1 if fmp_key else 0          # without a key we make zero FMP calls
    for t in tickers:
        if cost and quota_used + calls + cost > quota_cap:
            errors.append(f"{t} (quota)")
            continue
        try:
            ff, val, c = analyze(t, rf, fmp_key, rf_live=rf_live)
            calls += c
            fetched[t] = (ff, val)
        except Exception as e:
            log.warning("%s fundamentals failed: %s", t, e)
            errors.append(f"{t}: {str(e)[:60]}")
    return fetched, errors, calls, round(rf * 100, 2), rf_live


def commit_fundamentals(s, fetched, fmp_calls):
    """STORE PHASE — fast merge of fetched results. Caller must hold store.LOCK."""
    today = store_mod.today()
    for t, (ff, val) in fetched.items():
        s["facts"][t] = ff.to_dict()
        s["results"][t] = val.to_dict()
        s["updated"][t] = today
        # build-forward revenue beat/miss history (persisted, holdings only)
        rev_track.update(s, t, ff.rev_estimate_curq, ff.revenue_quarters, today)
    store_mod.add_fmp_calls(s, fmp_calls)
    return s


def refresh_fundamentals(s, tickers, fmp_key="", quota_cap=250):
    """Back-compat wrapper (fetch + commit in one call). Prefer the split
    fetch_fundamentals / commit_fundamentals so the lock isn't held during
    network I/O — see app.py."""
    fetched, errors, calls, rf_pct, rf_live = fetch_fundamentals(
        tickers, fmp_key, store_mod.fmp_used_today(s), quota_cap)
    commit_fundamentals(s, fetched, calls)
    return {"refreshed": list(fetched.keys()), "errors": errors, "fmp_calls": calls,
            "fmp_used_today": store_mod.fmp_used_today(s), "rf_pct": rf_pct,
            "rf_live": rf_live}


def analyze_row(ticker, rf, fmp_key="", rf_live=True):
    """Full analysis for ONE ticker incl. momentum, returned as a display row.
    Ephemeral — nothing is stored. NETWORK ONLY. Returns (row, fmp_calls)."""
    t = ticker.upper().strip()
    ff, val, calls = analyze(t, rf, fmp_key, rf_live=rf_live)
    mom = {}
    try:
        c = yahoo.fetch_chart(t)
        m = indicators.compute(t, c["closes"], c["volumes"], c["dates"])
        if "error" not in m:
            mom = m
    except Exception as e:
        log.warning("%s momentum failed: %s", t, e)
    price = mom.get("price") or ff.price
    anchor = val.anchor_value
    upside = ((anchor - price) / price * 100) if (anchor and price) else None
    rd = val.reverse_dcf or {}
    row = {
        "ticker": t, "company": ff.company, "sector": ff.sector,
        "rev_implied_cagr": rd.get("implied_cagr_pct"), "rev_actual_1y": rd.get("actual_1y_pct"),
        "rev_verdict": rd.get("verdict"),
        "price": price, "change": mom.get("change"),
        "composite": val.composite, "stars": val.stars, "recommendation": val.recommendation,
        "momentum_signal": mom.get("momentum_signal"), "rsi": mom.get("rsi"),
        "rsi_signal": mom.get("rsi_signal"), "macd_signal": mom.get("macd_signal"),
        "dbbmv_signal": mom.get("dbbmv_signal"), "momentum_score": mom.get("momentum_score"),
        "action": indicators.action(val.signal, mom.get("momentum_signal")),
        "anchor_method": val.anchor_method, "anchor_value": anchor,
        "range_low": val.range_low, "range_high": val.range_high,
        "upside_pct": round(upside, 1) if upside is not None else None,
        "verdict": val.verdict, "confidence": ff.confidence, "confidence_tier": ff.confidence_tier,
        "currency": ff.currency, "flags": ff.flags,
        "earnings_surprises": ff.earnings_surprises,
    }
    return row, calls


def fetch_watchlist(tickers, fmp_key="", quota_used=0, quota_cap=250):
    """NETWORK PHASE — analyse tickers on demand, nothing stored. Run OUTSIDE
    store.LOCK; the caller commits only the FMP quota counter afterwards."""
    rf, rf_live = yahoo.fetch_treasury_10y()
    rows, errors, calls = [], [], 0
    cost = 1 if fmp_key else 0
    for t in tickers:
        if cost and quota_used + calls + cost > quota_cap:
            errors.append(f"{t} (quota)")
            continue
        try:
            r, c = analyze_row(t, rf, fmp_key, rf_live=rf_live)
            calls += c
            rows.append(r)
        except Exception as e:
            errors.append(f"{t}: {str(e)[:50]}")
    return {"rows": rows, "errors": errors, "fmp_calls": calls,
            "rf_pct": round(rf * 100, 2), "rf_live": rf_live}


def watchlist_run(s, tickers, fmp_key="", quota_cap=250):
    """Back-compat wrapper. Prefer fetch_watchlist + commit quota in app.py."""
    res = fetch_watchlist(tickers, fmp_key, store_mod.fmp_used_today(s), quota_cap)
    store_mod.add_fmp_calls(s, res["fmp_calls"])
    return res


def fetch_daily(tickers):
    """NETWORK PHASE — Yahoo chart + momentum per ticker (no FMP quota).
    Returns {ticker: momentum_dict}. Run OUTSIDE store.LOCK."""
    out = {}
    for t in tickers:
        try:
            c = yahoo.fetch_chart(t)
            m = indicators.compute(t, c["closes"], c["volumes"], c["dates"])
            if "error" not in m:
                out[t] = m
        except Exception as e:
            log.warning("%s daily momentum failed: %s", t, e)
    return out


def commit_daily(s, fetched):
    """STORE PHASE — merge momentum. Caller must hold store.LOCK."""
    for t, m in fetched.items():
        s["momentum"][t] = m
    return list(fetched.keys())


def run_daily(s, tickers):
    """Back-compat wrapper (fetch + commit in one call)."""
    return commit_daily(s, fetch_daily(tickers))


def allocation(s, whatif=None, fmp_key=""):
    """Cost-basis allocation pies (by ticker + by sector). `whatif` is a list of
    {ticker, amount} buys -> also returns the 'after' allocation. Returns (result, fmp_calls)."""
    holdings = s.get("holdings", {})
    facts = s.get("facts", {})
    fmp_calls = 0
    sector_cache = {t: (facts.get(t, {}) or {}).get("sector") or "Unknown" for t in holdings}

    def resolve_sector(t):
        if t in sector_cache and sector_cache[t] != "Unknown":
            return sector_cache[t]
        if t in sector_cache:
            return sector_cache[t]
        sec = (facts.get(t, {}) or {}).get("sector")
        if not sec and fmp_key:
            try:
                import requests
                r = requests.get(f"{config.FMP_BASE}/profile", params={"symbol": t, "apikey": fmp_key}, timeout=12)
                sec = fmp.parse_profile(r.json()).get("sector")
                nonlocal fmp_calls
                fmp_calls += 1
            except Exception as e:
                log.warning("%s sector lookup failed: %s", t, e)
                sec = None
        sector_cache[t] = sec or "Unknown"
        return sector_cache[t]

    def pies(costmap):
        bt = sorted([{"label": t, "value": round(v, 2)} for t, v in costmap.items() if v > 0],
                    key=lambda x: -x["value"])
        sect = {}
        for t, v in costmap.items():
            if v > 0:
                k = resolve_sector(t)
                sect[k] = sect.get(k, 0) + v
        bs = sorted([{"label": k, "value": round(v, 2)} for k, v in sect.items()], key=lambda x: -x["value"])
        return {"by_ticker": bt, "by_sector": bs, "total": round(sum(v for v in costmap.values() if v > 0), 2)}

    base = {t: (h.get("shares", 0) * h.get("avg_cost", 0)) for t, h in holdings.items()
            if h.get("shares") and h.get("avg_cost")}
    result = {"before": pies(base)}

    if whatif:
        after = dict(base)
        added = []
        skipped = []
        for w in whatif:
            t = store_mod.clean_ticker((w or {}).get("ticker"))
            try:
                amt = float((w or {}).get("amount") or 0)
            except (TypeError, ValueError):
                skipped.append(str((w or {}).get("ticker") or "?"))
                continue
            if t and amt > 0:
                after[t] = after.get(t, 0) + amt
                added.append({"ticker": t, "amount": amt})
        result["after"] = pies(after)
        result["added"] = added
        if skipped:
            result["skipped"] = skipped
    return result, fmp_calls


def portfolio_view(s):
    rows = []
    for t, h in s.get("holdings", {}).items():
        ff = s["facts"].get(t, {})
        val = s["results"].get(t, {})
        mom = s["momentum"].get(t, {})
        price = mom.get("price") or ff.get("price")
        shares, avg = h.get("shares", 0), h.get("avg_cost", 0)
        cost = shares * avg if (shares and avg) else None
        mv = shares * price if (shares and price) else None
        pl = (mv - cost) if (mv is not None and cost is not None) else None
        signal = val.get("signal")
        act = indicators.action(signal, mom.get("momentum_signal"))
        anchor = val.get("anchor_value")
        rd = val.get("reverse_dcf") or {}
        upside = ((anchor - price) / price * 100) if (anchor and price) else None
        rows.append({
            "ticker": t, "company": ff.get("company"), "sector": ff.get("sector"),
            "price": price, "change": mom.get("change"),
            "shares": shares, "avg_cost": avg,
            "cost_basis": round(cost, 2) if cost else None,
            "market_value": round(mv, 2) if mv else None,
            "pl": round(pl, 2) if pl is not None else None,
            "pl_pct": round(pl / cost * 100, 1) if (pl is not None and cost) else None,
            "composite": val.get("composite"), "stars": val.get("stars"),
            "recommendation": val.get("recommendation"),
            "anchor_method": val.get("anchor_method"), "anchor_value": anchor,
            "range_low": val.get("range_low"), "range_high": val.get("range_high"),
            "upside_pct": round(upside, 1) if upside is not None else None,
            "momentum_signal": mom.get("momentum_signal"), "rsi": mom.get("rsi"),
            "rsi_signal": mom.get("rsi_signal"), "macd_signal": mom.get("macd_signal"),
            "dbbmv_signal": mom.get("dbbmv_signal"), "momentum_score": mom.get("momentum_score"),
            "action": act, "verdict": val.get("verdict"),
            "rev_implied_cagr": rd.get("implied_cagr_pct"), "rev_actual_1y": rd.get("actual_1y_pct"),
            "rev_verdict": rd.get("verdict"),
            "confidence": ff.get("confidence"), "confidence_tier": ff.get("confidence_tier"),
            "currency": ff.get("currency"), "updated": s["updated"].get(t),
            "flags": ff.get("flags", []),
            "earnings_surprises": ff.get("earnings_surprises", []),
            "rev_surprises": s.get("rev_surprises", {}).get(t, []),
        })
    # portfolio totals
    tot_cost = sum(r["cost_basis"] or 0 for r in rows)
    tot_mv = sum(r["market_value"] or 0 for r in rows)
    totals = {"cost_basis": round(tot_cost, 2), "market_value": round(tot_mv, 2),
              "pl": round(tot_mv - tot_cost, 2), "pl_pct": round((tot_mv - tot_cost) / tot_cost * 100, 1) if tot_cost else None}
    return {"rows": rows, "totals": totals}
