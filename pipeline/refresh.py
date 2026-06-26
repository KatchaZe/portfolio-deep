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
from sources import sec_edgar, yahoo, fmp, stooq, finnhub, alphavantage
from pipeline import normalize, validate, rev_track, margin_track, consensus, surprise_backfill, pricecache
from domain import indicators, momentum
from domain.engine import get_engine
import store as store_mod

log = logging.getLogger("portfolio.refresh")
_cik_map = None


def get_prices(ticker, rng="3mo", interval="1d"):
    """Daily closes/volumes/dates for momentum — Yahoo first, then Stooq fallback.
    Yahoo is often blocked/emptied from datacenter IPs (cloud hosts), so we fall
    back to Stooq (no key, answers from datacenter IPs) to keep momentum alive.
    Returns the series dict; raises only if BOTH sources fail."""
    yahoo_short = None
    try:
        d = yahoo.fetch_chart(ticker, rng=rng, interval=interval)
        if d and len(d.get("closes", [])) >= 30:
            return d
        yahoo_short = d                         # got data, just not enough — keep as last resort
    except Exception as e:
        log.warning("%s yahoo chart failed, trying stooq: %s", ticker, e)
    try:
        d = stooq.fetch_chart(ticker)
        if d and len(d.get("closes", [])) >= 30:
            tail = 180                          # Stooq returns full history; momentum needs only the tail
            return {"closes": d["closes"][-tail:], "volumes": d["volumes"][-tail:],
                    "dates": d["dates"][-tail:]}
    except Exception as e:
        log.warning("%s stooq chart failed: %s", ticker, e)
    if yahoo_short:
        return yahoo_short
    raise RuntimeError(f"no price data for {ticker} (yahoo+stooq)")


def get_prices_long(ticker, fmp_key="", rng="2y", full_bars=250, min_bars=60):
    """Adjusted daily closes (oldest->newest) for the momentum composite.

    3-tier, all dividend+split adjusted EXCEPT Stooq (split-only -> flagged):
        1) Yahoo adjclose   (free, full history; IP-block risk on cloud)
        2) FMP adjClose     (API key -> not IP-blocked; spent only if Yahoo is short)
        3) Stooq            (split-only -> dividend_adjusted=False; no key)

    Returns the FIRST source with >= full_bars (so 12-1 / SMA200 are valid). If none
    reaches that (e.g. a recent listing), returns the source that provided the MOST
    bars, as long as it has >= min_bars, so a *partial* momentum still shows.
    Uses ONLY fetched data — never fabricates or back-fills. Raises only when every
    source is empty/too short, so the caller leaves the cell blank.

    R4: a same-day cached series short-circuits the network (saves FMP quota); every
    successful fetch is cached; if every live source fails a stale cache is served
    (flagged) before giving up — so momentum survives a transient outage."""
    fresh = pricecache.read_fresh(ticker)
    if fresh and len(fresh.get("closes") or []) >= min_bars:
        out = dict(fresh); out["from_cache"] = True
        return out
    best = None                                   # (n_bars, payload) seen so far

    def finish(pay):
        pricecache.write(ticker, pay)             # cache the good series (errors swallowed)
        return pay

    def consider(payload):
        nonlocal best
        # R1 data-quality guard: drop corrupt bars (non-positive / lone spikes)
        cc, vv, dd, q = momentum.clean_series(payload.get("closes"),
                                              payload.get("volumes"), payload.get("dates"))
        payload["closes"], payload["volumes"], payload["dates"], payload["quality"] = cc, vv, dd, q
        n = len(cc)
        if n and (best is None or n > best[0]):
            best = (n, payload)
        return n

    try:
        d = yahoo.fetch_chart(ticker, rng=rng, interval="1d")
        pay = {"closes": d.get("adj_closes") or d.get("closes") or [],
               "volumes": d.get("volumes", []), "dates": d.get("dates", []),
               "dividend_adjusted": "adj_closes" in d, "source": "yahoo"}
        if consider(pay) >= full_bars:
            return finish(pay)
    except Exception as e:
        log.warning("%s yahoo long chart failed: %s", ticker, e)

    if fmp_key:
        try:
            h = fmp.parse_history(fmp.fetch_history(ticker, fmp_key))
            pay = {"closes": h["closes"], "volumes": h["volumes"], "dates": h["dates"],
                   "dividend_adjusted": True, "source": "fmp"}
            if consider(pay) >= full_bars:
                return finish(pay)
        except Exception as e:
            log.warning("%s fmp history failed: %s", ticker, e)

    try:
        d = stooq.fetch_chart(ticker)
        pay = {"closes": d.get("closes", []), "volumes": d.get("volumes", []),
               "dates": d.get("dates", []), "dividend_adjusted": False, "source": "stooq"}
        if consider(pay) >= full_bars:
            return finish(pay)
    except Exception as e:
        log.warning("%s stooq long chart failed: %s", ticker, e)

    if best and best[0] >= min_bars:              # partial history, but usable
        return finish(best[1])
    stale = pricecache.read_any(ticker)           # R4: last good series keeps momentum alive
    if stale and len(stale.get("closes") or []) >= min_bars:
        out = dict(stale); out["from_cache"] = True; out["stale_cache"] = True
        return out
    raise RuntimeError(f"no long price data for {ticker} (yahoo+fmp+stooq)")


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

    # price/shares fallback — foreign filers (e.g. NVO) have no SEC share count, so
    # if Yahoo was degraded the engine has nothing to anchor on. Pull price+shares
    # from FMP quote ON DEMAND (only when actually missing, to spare quota).
    if fmp_key and (ff.shares_diluted is None or ff.price is None):
        try:
            q = fmp.parse_quote(fmp.fetch_quote(t, fmp_key))
            fmp_calls += 1
            filled = []
            if ff.price is None and q.get("price"):
                ff.set("price", q["price"], "fmp/quote"); filled.append("price")
            if ff.shares_diluted is None and q.get("shares"):
                ff.set("shares_diluted", q["shares"], "fmp/quote"); filled.append("shares")
            if filled:
                ff.flags.append(f"{'/'.join(filled)} via FMP quote (sec/yahoo gap)")
        except Exception as e:
            log.warning("%s FMP quote fallback failed: %s", t, e)

    # EPS-surprise cross-check across ALL available free sources: Yahoo (adjusted,
    # from normalize) + FMP (GAAP) + Finnhub + Alpha Vantage (both optional). The
    # reconcile picks a primary track record, tags which sources confirm it, and
    # flags a latest-quarter beat-vs-miss disagreement.
    es_by_source = {}
    if ff.earnings_surprises:
        es_by_source["yahoo"] = ff.earnings_surprises
    if fmp_key:
        try:
            raw_fmp_earn = fmp.fetch_earnings(t, fmp_key)
            fmp_calls += 1
            es_by_source["fmp"] = fmp.parse_earnings(raw_fmp_earn)
            # Phase 3: immediate revenue surprise from the SAME response (no extra call)
            ff.rev_surprises_fmp = fmp.parse_revenue_surprises(raw_fmp_earn)
        except Exception as e:
            log.warning("%s FMP earnings failed: %s", t, e)
    if config.FINNHUB_API_KEY:
        try:
            fh = finnhub.parse_earnings(finnhub.fetch_earnings(t, config.FINNHUB_API_KEY))
            if fh:
                es_by_source["finnhub"] = fh
        except Exception as e:
            log.warning("%s Finnhub earnings failed: %s", t, e)
    if config.ALPHAVANTAGE_API_KEY:
        try:
            av = alphavantage.parse_earnings(alphavantage.fetch_earnings(t, config.ALPHAVANTAGE_API_KEY))
            if av:
                es_by_source["alphavantage"] = av
        except Exception as e:
            log.warning("%s AlphaVantage earnings failed: %s", t, e)
    rec = consensus.reconcile_earnings(es_by_source)
    if rec["list"]:
        ff.earnings_surprises = rec["list"]
        ff.provenance["earnings_surprises"] = rec["provenance"]
        if rec["disagree"]:
            ff.flags.append("earnings beat/miss disagree across sources")
        if "yahoo" not in es_by_source and rec["primary"]:
            ff.flags.append(f"earnings via {rec['primary']} (yahoo earningsHistory empty)")

    # consensus PATH (FMP analyst-estimates) → growth fade + breadth + a fwd-EPS candidate
    fmp_estimates = None
    if fmp_key:
        try:
            fmp_estimates = fmp.fetch_estimates(t, fmp_key)
            fmp_calls += 1
            path = fmp.parse_estimate_path(fmp_estimates, ff.fiscal_year)
            for k in ("fwd_growth_near", "fwd_growth_far", "n_analysts"):
                if path.get(k) is not None:
                    ff.set(k, path[k], "fmp/estimates")
        except Exception as e:
            log.warning("%s FMP estimates failed: %s", t, e)

    # IMMEDIATE estimate-vs-actual backfill — only when the primary paths came up
    # empty (Yahoo blocked / FMP earnings lacked revenue / forward-build not yet
    # matured). Pairs FMP QUARTERLY analyst-estimates with free SEC actuals so the
    # EPS / Rev circles fill on the first refresh. One extra FMP call, gated.
    need_eps = not ff.earnings_surprises
    need_rev = not ff.rev_surprises_fmp
    if fmp_key and (need_eps or need_rev):
        try:
            est_q = fmp.parse_estimates_quarterly(fmp.fetch_estimates_quarter(t, fmp_key))
            fmp_calls += 1
            if est_q:
                if need_eps:
                    bf = surprise_backfill.build_eps(ff.eps_quarters, est_q)
                    if bf:
                        ff.eps_surprises_backfill = bf
                        if not ff.earnings_surprises:
                            ff.earnings_surprises = bf
                            ff.provenance["earnings_surprises"] = "fmp-est x sec-actual"
                            ff.flags.append("EPS beat/miss reconstructed (FMP est × SEC actual)")
                if need_rev:
                    rbf = surprise_backfill.build_rev(ff.revenue_quarters, est_q)
                    if rbf:
                        ff.rev_surprises_fmp = rbf
                        ff.provenance["rev_surprises_fmp"] = "fmp-est x sec-actual"
        except Exception as e:
            log.warning("%s surprise backfill failed: %s", t, e)

    # forward-EPS BLEND — median of Yahoo + FMP + Finnhub (each free), with the
    # min–max dispersion kept for display + a confidence nudge. validate() still
    # applies the revenue-ceiling backstop to the blended value afterwards.
    fwd_candidates = {}
    if ff.forward_eps and ff.forward_eps > 0:
        fwd_candidates["yahoo"] = ff.forward_eps          # Yahoo value set by normalize
    if fmp_estimates:
        fe = fmp.parse_forward_eps(fmp_estimates, ff.fiscal_year)
        if fe:
            fwd_candidates["fmp"] = fe
    if config.FINNHUB_API_KEY:
        try:
            fh_fwd = finnhub.parse_eps_estimate(finnhub.fetch_eps_estimate(t, config.FINNHUB_API_KEY))
            if fh_fwd:
                fwd_candidates["finnhub"] = fh_fwd
        except Exception as e:
            log.warning("%s Finnhub eps-estimate failed: %s", t, e)
    blend = consensus.blend_forward_eps(fwd_candidates)
    if blend:
        ff.set("forward_eps", blend["value"], "consensus-blend(" + "+".join(blend["sources"]) + ")")
        ff.forward_eps_sources = blend["sources"]
        ff.forward_eps_low = blend["low"]
        ff.forward_eps_high = blend["high"]
        ff.forward_eps_spread_pct = blend["spread_pct"]
        ff.forward_eps_n = blend["n"]

    # own 5y P/E percentile (re-rating signal) — Yahoo 5y monthly prices + SEC annual EPS.
    # USD filers only (avoids price-vs-EPS currency mismatch); free, no FMP quota.
    if ff.currency == "USD" and ff.price and ff.eps_annuals_dated:
        try:
            cur_eps = (ff.net_income / ff.shares_diluted) if (ff.net_income and ff.shares_diluted) else ff.eps_gaap
            ch = yahoo.fetch_chart(t, rng="5y", interval="1mo")
            pct = yahoo.pe_percentile_5y(ff.eps_annuals_dated, ch["closes"], ch["dates"], ff.price, cur_eps)
            if pct is not None:
                ff.set("own_pe_pctile", round(pct, 3), "yahoo5y+sec")
        except Exception as e:
            log.warning("%s own-PE percentile failed: %s", t, e)

    validate.validate(ff, rf=rf)
    val = get_engine().evaluate(ff, rf=rf)
    return ff, val, fmp_calls


def compute_peer_medians(fetched):
    """{ticker: median revenue-growth of its SECTOR cohort, excluding itself}. Free
    peer-median from the batch already fetched — no extra API calls. Needs ≥2 names
    in the sector; otherwise the ticker is omitted (engine then skips the peer adj)."""
    by_sector = {}
    growth = {}
    for t, (ff, _v) in fetched.items():
        ann = ff.revenue_annuals or []
        g = (ann[0] / ann[1] - 1) if (len(ann) > 1 and ann[1]) else None
        sec = ff.sector or "Unknown"
        if g is not None:
            growth[t] = g
            by_sector.setdefault(sec, []).append(t)
    out = {}
    for t, (ff, _v) in fetched.items():
        sec = ff.sector or "Unknown"
        peers = [growth[p] for p in by_sector.get(sec, []) if p != t and p in growth]
        if peers:
            peers.sort()
            n = len(peers)
            out[t] = peers[n // 2] if n % 2 else (peers[n // 2 - 1] + peers[n // 2]) / 2
    return out


def _reconcile_earnings(ff, yh, fm):
    """Pick the earnings list to show and tag its provenance.
      • Yahoo present            -> keep Yahoo (adjusted/street, matches forward_eps);
        if FMP also present and the latest quarter disagrees beat-vs-miss, flag it.
      • Yahoo empty, FMP present -> use FMP and flag the substitution.
      • both empty               -> leave as-is."""
    if yh:
        src = "yahoo"
        if fm:
            if {(yh[-1] or {}).get("grade"), (fm[-1] or {}).get("grade")} == {"beat", "miss"}:
                ff.flags.append("earnings beat/miss disagree (yahoo vs FMP)")
            else:
                src = "yahoo+fmp✓"
        ff.provenance["earnings_surprises"] = src
        return yh
    if fm:
        ff.flags.append("earnings via FMP (yahoo earningsHistory empty)")
        ff.provenance["earnings_surprises"] = "fmp"
        return fm
    return yh


def fetch_fundamentals(tickers, fmp_key="", quota_used=0, quota_cap=250):
    """NETWORK PHASE — no store access, run OUTSIDE store.LOCK.
    Returns (fetched {ticker: (FinancialFacts, Valuation)}, errors, fmp_calls,
    rf_pct, rf_live)."""
    rf, rf_live = yahoo.fetch_treasury_10y()
    fetched, errors, calls = {}, [], 0
    cost = 4 if fmp_key else 0          # profile + earnings + estimates + quarterly-est backfill per ticker (0 without a key)
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
    # peer-median (sector cohort, free) → inject + re-score the affected names. The
    # engine is pure, so re-evaluate is cheap and needs no extra network calls.
    medians = compute_peer_medians(fetched)
    for t, med in medians.items():
        ff, _val = fetched[t]
        ff.set("peer_median_growth", round(med, 4), "sector-cohort")
        fetched[t] = (ff, get_engine().evaluate(ff, rf=rf))
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
    mom_v2 = {}
    try:
        c = get_prices(t)
        m = indicators.compute(t, c["closes"], c["volumes"], c["dates"])
        if "error" not in m:
            mom = m
    except Exception as e:
        log.warning("%s momentum failed: %s", t, e)
    try:                                            # faithful composite momentum (v2)
        lc = get_prices_long(t, fmp_key)
        mv = momentum.compute(t, lc["closes"], lc["dates"], lc["dividend_adjusted"])
        if "error" not in mv:
            mv["src"] = lc.get("source")
            mv["quality"] = lc.get("quality")
            mom_v2 = mv
    except Exception as e:
        log.warning("%s momentum_v2 failed: %s", t, e)
    if mom_v2:                                      # R2: warn split-only only for dividend payers
        mom_v2["div_warn"] = momentum.div_warn(mom_v2.get("dividend_adjusted"),
                                               getattr(ff, "dividend_ps", None))
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
        "momentum_v2": mom_v2 or None,           # primary composite (secondary = fields above)
        "action": indicators.action(val.signal, mom.get("momentum_signal")),
        "anchor_method": val.anchor_method, "anchor_value": anchor,
        "range_low": val.range_low, "range_high": val.range_high,
        "upside_pct": round(upside, 1) if upside is not None else None,
        "verdict": val.verdict, "confidence": ff.confidence, "confidence_tier": ff.confidence_tier,
        "currency": ff.currency, "flags": ff.flags,
        "earnings_surprises": ff.earnings_surprises,
        "rev_surprises": ff.rev_surprises_fmp,   # watchlist: immediate FMP revenue surprise
        "margin_trend": margin_track.build(ff.operating_income_quarters, ff.revenue_quarters),
        "forward_eps": ff.forward_eps, "forward_eps_low": ff.forward_eps_low,
        "forward_eps_high": ff.forward_eps_high, "forward_eps_spread_pct": ff.forward_eps_spread_pct,
        "forward_eps_n": ff.forward_eps_n, "forward_eps_sources": ff.forward_eps_sources,
        # v8.2 detail (for the expandable drawer)
        "eq_verdict": val.eq_verdict, "cost_of_equity": val.cost_of_equity, "eva": val.eva,
        "key_metrics": val.key_metrics, "subscores": val.subscores,
    }
    return row, calls


def fetch_watchlist(tickers, fmp_key="", quota_used=0, quota_cap=250):
    """NETWORK PHASE — analyse tickers on demand, nothing stored. Run OUTSIDE
    store.LOCK; the caller commits only the FMP quota counter afterwards."""
    rf, rf_live = yahoo.fetch_treasury_10y()
    rows, errors, calls = [], [], 0
    cost = 4 if fmp_key else 0          # profile + earnings + estimates + quarterly-est backfill per ticker
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
    # cross-sectional momentum rank among the watchlist names (mirrors portfolio_view)
    momentum.cross_sectional_rank([r["momentum_v2"] for r in rows if r.get("momentum_v2")])
    return {"rows": rows, "errors": errors, "fmp_calls": calls,
            "rf_pct": round(rf * 100, 2), "rf_live": rf_live}


def watchlist_run(s, tickers, fmp_key="", quota_cap=250):
    """Back-compat wrapper. Prefer fetch_watchlist + commit quota in app.py."""
    res = fetch_watchlist(tickers, fmp_key, store_mod.fmp_used_today(s), quota_cap)
    store_mod.add_fmp_calls(s, res["fmp_calls"])
    return res


def fetch_daily(tickers, fmp_key=""):
    """NETWORK PHASE — per ticker: short-window indicators (RSI/MACD/DBBMV, secondary)
    plus the faithful composite momentum (v2, primary) under m["v2"].
    `fmp_key` is only spent if Yahoo is blocked (get_prices_long tier 2); leave ""
    to keep the daily run quota-free (Yahoo->Stooq). Run OUTSIDE store.LOCK."""
    out = {}
    for t in tickers:
        m = {}
        try:
            c = get_prices(t)
            mm = indicators.compute(t, c["closes"], c["volumes"], c["dates"])
            if "error" not in mm:
                m = mm
        except Exception as e:
            log.warning("%s daily momentum failed: %s", t, e)
        try:
            lc = get_prices_long(t, fmp_key)
            mv = momentum.compute(t, lc["closes"], lc["dates"], lc["dividend_adjusted"])
            if "error" not in mv:
                mv["src"] = lc.get("source")
                mv["quality"] = lc.get("quality")
                m["v2"] = mv
        except Exception as e:
            log.warning("%s daily momentum_v2 failed: %s", t, e)
        if m:
            out[t] = m
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


def _momentum_meta(rows, stale_after=4):
    """Per-row momentum staleness + portfolio 'last updated' (R3 banner).
    Mutates each row's momentum_v2 with age_days/stale. 'today' is the server date;
    ages come only from the data's own as_of — nothing is fabricated."""
    import datetime as _dt
    today = _dt.date.today()

    def age(a):
        try:
            return (today - _dt.date.fromisoformat(str(a)[:10])).days
        except Exception:
            return None

    asofs, stale_count, n = [], 0, 0
    for r in rows:
        v = r.get("momentum_v2")
        if not v:
            continue
        n += 1
        ag = age(v.get("as_of"))
        v["age_days"] = ag
        v["stale"] = (ag is not None and ag > stale_after)
        if v.get("as_of"):
            asofs.append(str(v["as_of"])[:10])
        if v["stale"]:
            stale_count += 1
    last = max(asofs) if asofs else None
    overall = age(last) if last else None
    return {"as_of": last, "age_days": overall,
            "stale": (n > 0 and (last is None or (overall is not None and overall > stale_after))),
            "stale_count": stale_count, "n": n}


def portfolio_view(s):
    rows = []
    for t, h in s.get("holdings", {}).items():
        ff = s["facts"].get(t, {})
        val = s["results"].get(t, {})
        mom = s["momentum"].get(t, {})
        v2 = mom.get("v2") or {}
        if v2:                                       # R2: dividend-aware split-only warning
            v2["div_warn"] = momentum.div_warn(v2.get("dividend_adjusted"), ff.get("dividend_ps"))
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
            "momentum_v2": v2 or None,           # primary composite (secondary = fields above)
            "action": act, "verdict": val.get("verdict"),
            "rev_implied_cagr": rd.get("implied_cagr_pct"), "rev_actual_1y": rd.get("actual_1y_pct"),
            "rev_verdict": rd.get("verdict"),
            "confidence": ff.get("confidence"), "confidence_tier": ff.get("confidence_tier"),
            "currency": ff.get("currency"), "updated": s["updated"].get(t),
            "flags": ff.get("flags", []),
            "earnings_surprises": ff.get("earnings_surprises", []),
            "rev_surprises": s.get("rev_surprises", {}).get(t, []) or ff.get("rev_surprises_fmp", []),
            "margin_trend": margin_track.build(ff.get("operating_income_quarters"),
                                               ff.get("revenue_quarters")),
            "forward_eps": ff.get("forward_eps"), "forward_eps_low": ff.get("forward_eps_low"),
            "forward_eps_high": ff.get("forward_eps_high"),
            "forward_eps_spread_pct": ff.get("forward_eps_spread_pct"),
            "forward_eps_n": ff.get("forward_eps_n"), "forward_eps_sources": ff.get("forward_eps_sources"),
            "eq_verdict": val.get("eq_verdict"),
            "cost_of_equity": val.get("cost_of_equity"),
            "eva": val.get("eva"),
            "key_metrics": val.get("key_metrics"),
            "subscores": val.get("subscores"),
        })
    # cross-sectional momentum rank across the held portfolio (mutates each v2 dict)
    momentum.cross_sectional_rank([r["momentum_v2"] for r in rows if r.get("momentum_v2")])
    mom_meta = _momentum_meta(rows)              # R3: per-row staleness + banner meta
    tot_cost = sum(r["cost_basis"] or 0 for r in rows)
    tot_mv = sum(r["market_value"] or 0 for r in rows)
    totals = {"cost_basis": round(tot_cost, 2), "market_value": round(tot_mv, 2),
              "pl": round(tot_mv - tot_cost, 2), "pl_pct": round((tot_mv - tot_cost) / tot_cost * 100, 1) if tot_cost else None}
    return {"rows": rows, "totals": totals, "momentum_meta": mom_meta}
# end of refresh.py
