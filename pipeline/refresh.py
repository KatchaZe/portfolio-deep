"""
Orchestration — fundamentals refresh, daily momentum, and portfolio view.

  refresh_fundamentals(s, tickers)  SEC+FMP+Yahoo -> normalize -> validate -> engine -> store
  run_daily(s, tickers)             Yahoo chart -> momentum -> action (no FMP quota)
  portfolio_view(s)                 build display rows incl. P/L

Each FMP call is counted for the quota guard. Fundamentals only need refreshing
after earnings; daily momentum is free (Yahoo).
"""
import datetime as dt

import config
from sources import sec_edgar, yahoo, fmp
from pipeline import normalize, validate, rev_track
from domain import indicators
from domain.engine import get_engine
import store as store_mod

_cik_map = None


def resolve_cik(ticker):
    global _cik_map
    t = ticker.upper().strip()
    if t in config.CIKS:
        return config.CIKS[t], None
    if _cik_map is None:
        try:
            import requests
            r = requests.get("https://www.sec.gov/files/company_tickers.json",
                             headers={"User-Agent": config.SEC_USER_AGENT}, timeout=20)
            _cik_map = {row["ticker"].upper(): (str(row["cik_str"]).zfill(10), row.get("title"))
                        for row in r.json().values()}
        except Exception:
            _cik_map = {}
    v = _cik_map.get(t)
    return (v[0], v[1]) if v else (None, None)


def analyze(ticker, rf, fmp_key=""):
    """Fetch -> normalize -> validate -> engine. Returns (facts, valuation, fmp_calls)."""
    t = ticker.upper().strip()
    cik, name = resolve_cik(t)
    fmp_calls = 0
    sec_cf = sec_edgar.fetch_companyfacts(cik, config.SEC_USER_AGENT) if cik else None

    # currency -> FX
    fx = None
    if sec_cf:
        try:
            ccy = sec_edgar.extract(sec_cf).get("currency", "USD")
            if ccy and ccy != "USD":
                fx = yahoo.fetch_fx_to_usd(ccy)
        except Exception:
            pass

    profile = None
    if fmp_key:
        try:
            import requests
            r = requests.get(f"{config.FMP_BASE}/profile", params={"symbol": t, "apikey": fmp_key}, timeout=15)
            profile = r.json(); fmp_calls = 1
        except Exception:
            profile = None

    yq = yahoo.fetch_consensus(t)
    ff = normalize.build(t, sec_cf, profile, yq, fx_rate=fx, company=name)
    validate.validate(ff, rf=rf)
    val = get_engine().evaluate(ff, rf=rf)
    return ff, val, fmp_calls


def refresh_fundamentals(s, tickers, fmp_key="", quota_cap=250):
    rf = yahoo.fetch_treasury_10y()
    done, errors, calls = [], [], 0
    used = store_mod.fmp_used_today(s)
    for t in tickers:
        if used + calls + 1 > quota_cap:
            errors.append(f"{t} (quota)")
            continue
        try:
            ff, val, c = analyze(t, rf, fmp_key)
            calls += c
            s["facts"][t] = ff.to_dict()
            s["results"][t] = val.to_dict()
            s["updated"][t] = store_mod.today()
            # build-forward revenue beat/miss history (persisted, holdings only)
            rev_track.update(s, t, ff.rev_estimate_curq, ff.revenue_quarters, store_mod.today())
            done.append(t)
        except Exception as e:
            errors.append(f"{t}: {str(e)[:60]}")
    store_mod.add_fmp_calls(s, calls)
    return {"refreshed": done, "errors": errors, "fmp_calls": calls,
            "fmp_used_today": store_mod.fmp_used_today(s), "rf_pct": round(rf * 100, 2)}


def analyze_row(ticker, rf, fmp_key=""):
    """Full analysis for ONE ticker incl. momentum, returned as a display row.
    Ephemeral — nothing is stored. Returns (row, fmp_calls)."""
    t = ticker.upper().strip()
    ff, val, calls = analyze(t, rf, fmp_key)
    mom = {}
    try:
        c = yahoo.fetch_chart(t)
        m = indicators.compute(t, c["closes"], c["volumes"], c["dates"])
        if "error" not in m:
            mom = m
    except Exception:
        pass
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


def watchlist_run(s, tickers, fmp_key="", quota_cap=250):
    rf = yahoo.fetch_treasury_10y()
    rows, errors, calls = [], [], 0
    used = store_mod.fmp_used_today(s)
    for t in tickers:
        if used + calls + 1 > quota_cap:
            errors.append(f"{t} (quota)")
            continue
        try:
            r, c = analyze_row(t, rf, fmp_key)
            calls += c
            rows.append(r)
        except Exception as e:
            errors.append(f"{t}: {str(e)[:50]}")
    store_mod.add_fmp_calls(s, calls)
    return {"rows": rows, "errors": errors, "fmp_calls": calls, "rf_pct": round(rf * 100, 2)}


def run_daily(s, tickers):
    out = []
    for t in tickers:
        try:
            c = yahoo.fetch_chart(t)
            m = indicators.compute(t, c["closes"], c["volumes"], c["dates"])
            if "error" not in m:
                s["momentum"][t] = m
            out.append(t)
        except Exception:
            pass
    return out


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
                from sources import fmp
                sec = fmp.parse_profile(r.json()).get("sector")
                nonlocal fmp_calls
                fmp_calls += 1
            except Exception:
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
        for w in whatif:
            t = (w.get("ticker") or "").upper().strip()
            amt = float(w.get("amount") or 0)
            if t and amt > 0:
                after[t] = after.get(t, 0) + amt
                added.append({"ticker": t, "amount": amt})
        result["after"] = pies(after)
        result["added"] = added
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
