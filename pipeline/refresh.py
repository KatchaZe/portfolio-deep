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
import threading
import datetime as dt
from concurrent.futures import ThreadPoolExecutor

import config
from sources import sec_edgar, yahoo, fmp, stooq, finnhub, alphavantage
from pipeline import normalize, validate, rev_track, margin_track, consensus, surprise_backfill, pricecache, market_valuation
from pipeline import prices
from domain import indicators, momentum, costs, pead, philosophy, advice
from pipeline import screen as screen_mod
from domain.engine import get_engine
import store as store_mod

log = logging.getLogger("portfolio.refresh")
_cik_map = None
_cik_lock = threading.Lock()       # P1: parallel workers share the lazy CIK map
MAX_WORKERS = 4                    # P1: parallel ticker fetches (SEC throttle is global)


def get_prices(ticker, rng="3mo", interval="1d"):
    """Thin delegator (2026-07-19) — ladder lives in pipeline/prices.fetch_daily_series
    (yahoo chart -> stooq, >=30 bars). Kept here so callers/tests that
    monkeypatch refresh.get_prices keep working."""
    return prices.fetch_daily_series(ticker, rng=rng, interval=interval)


def get_prices_long(ticker, fmp_key="", rng="2y", full_bars=250, min_bars=60):
    """Thin delegator (2026-07-19) — ladder lives in
    pipeline/prices.fetch_daily_adjusted (yahoo adj -> fmp adj -> stooq,
    +pricecache with the H3/R4 policies unchanged)."""
    return prices.fetch_daily_adjusted(ticker, fmp_key=fmp_key, rng=rng,
                                       full_bars=full_bars, min_bars=min_bars)


def resolve_cik(ticker):
    global _cik_map
    t = ticker.upper().strip()
    if t in config.CIKS:
        return config.CIKS[t], None
    with _cik_lock:                    # P1: don't double-fetch the map from 2 threads
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
            profile = fmp.fetch_profile(t, fmp_key) or None
            fmp_calls = 1
        except Exception as e:
            log.warning("%s FMP profile failed: %s", t, e)
            profile = None

    yq = yahoo.fetch_consensus(t)
    ff = normalize.build(t, sec_cf, profile, yq, fx_rate=fx, company=name)
    if not rf_live:
        ff.flags.append(f"Rf fallback {round(rf*100,2)}% — live 10Y Treasury yield unavailable")
    if isinstance(sec_cf, dict) and sec_cf.get("_stale_cache"):
        ff.flags.append("SEC via stale cache (network failed) — figures may lag the latest filing")

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


def _partition_by_quota(tickers, cost, quota_used, quota_cap):
    """P1: split tickers into (todo, quota_errors) UP FRONT using the worst-case
    cost per ticker, so the quota pre-check stays exact under parallel fetching."""
    todo, errors = [], []
    budget = max(0, quota_cap - quota_used)
    for t in tickers:
        if cost and (len(todo) + 1) * cost > budget:
            errors.append(f"{t} (quota)")
        else:
            todo.append(t)
    return todo, errors


def fetch_fundamentals(tickers, fmp_key="", quota_used=0, quota_cap=250):
    """NETWORK PHASE — no store access, run OUTSIDE store.LOCK.
    P1: tickers are analysed in PARALLEL (ThreadPool, MAX_WORKERS). Safe because
    analyze() is network+pure (no store), the SEC throttle is global/thread-safe,
    and the FMP quota is partitioned up front (worst-case cost per ticker).
    Returns (fetched {ticker: (FinancialFacts, Valuation)}, errors, fmp_calls,
    rf_pct, rf_live)."""
    rf, rf_live = yahoo.fetch_treasury_10y()
    fetched, calls = {}, 0
    cost = 5 if fmp_key else 0          # H2: profile + quote-fallback + earnings + estimates + quarterly-est backfill per ticker (0 without a key)
    todo, errors = _partition_by_quota(tickers, cost, quota_used, quota_cap)
    if todo:
        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(todo))) as ex:
            futs = {t: ex.submit(analyze, t, rf, fmp_key, rf_live) for t in todo}
        for t in todo:                   # collect in submission order (stable errors)
            try:
                ff, val, c = futs[t].result()
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


def _earn_status(eps_list, rev_list, mgn_list, provenance, facts_has_new_schema, fmp_on):
    """Why is each earnings track (EPS / Rev / Mgn) filled or empty? Shown as a
    tooltip so a blank row is EXPLAINED instead of silently missing (user ask:
    'ถ้าดึงไม่ได้ ให้บอกว่าดึงไม่ได้ ทั้งจากแหล่งหลักและสำรอง').
    Returns {"eps": {...}, "rev": {...}, "mgn": {...}, "stale_facts": bool}."""
    prov = provenance or {}

    def track(lst, src_key, primary, fallbacks):
        if lst:
            return {"ok": True, "src": prov.get(src_key) or "stored"}
        if not facts_has_new_schema:
            return {"ok": False, "reason": "stale-facts",
                    "detail": "ข้อมูลชุดเก่า (ก่อนอัปเดต schema) — กด Run Fundamental Refresh"}
        fb = ", ".join(fallbacks) if fmp_on else ", ".join(f for f in fallbacks if "FMP" not in f) or "-"
        return {"ok": False, "reason": "unavailable",
                "detail": f"ดึงไม่ได้ทั้งแหล่งหลัก ({primary}) และสำรอง ({fb})"}

    return {
        "eps": track(eps_list, "earnings_surprises", "Yahoo earningsHistory",
                     ["FMP earnings", "FMP est × SEC actual", "Finnhub", "AlphaVantage"]),
        "rev": track(rev_list, "rev_surprises_fmp", "forward-build (Yahoo est × SEC actual)",
                     ["FMP earnings-revenue", "FMP est × SEC actual"]),
        "mgn": track(mgn_list, "operating_income_quarters", "SEC quarterly filings",
                     ["(SEC เท่านั้น — บริษัทต่างชาติ/IFRS บางรายไม่รายงานรายไตรมาส)"]),
        "stale_facts": not facts_has_new_schema,
    }


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
    mgn_trend = margin_track.build(ff.operating_income_quarters, ff.revenue_quarters)  # P3: once
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
        "net_upside_pct": costs.net_upside(round(upside, 1) if upside is not None else None,
                                           config.CAPGAINS_TAX_RATE, config.TRADING_COST_BPS),
        "garp_score": screen_mod.garp_score(val.E_econ, val.P),
        "garp_candidate": screen_mod.is_candidate(val.E_econ, val.P),
        "pead": pead.signal(ff.earnings_surprises or ff.eps_surprises_backfill, ff.rev_surprises_fmp),
        "verdict": val.verdict, "confidence": ff.confidence, "confidence_tier": ff.confidence_tier,
        "currency": ff.currency, "flags": ff.flags,
        "earnings_surprises": ff.earnings_surprises or ff.eps_surprises_backfill,
        "rev_surprises": ff.rev_surprises_fmp,   # watchlist: immediate FMP revenue surprise
        "margin_trend": mgn_trend,
        # v8.3: all fair-value methods + why-empty status for the earnings circles
        "fv_peg": val.fv_peg, "fv_fvp": val.fv_fvp,
        "earn_status": _earn_status(
            ff.earnings_surprises or ff.eps_surprises_backfill, ff.rev_surprises_fmp,
            mgn_trend, ff.provenance, True, bool(fmp_key)),
        "forward_eps": ff.forward_eps, "forward_eps_low": ff.forward_eps_low,
        "forward_eps_high": ff.forward_eps_high, "forward_eps_spread_pct": ff.forward_eps_spread_pct,
        "forward_eps_n": ff.forward_eps_n, "forward_eps_sources": ff.forward_eps_sources,
        # v8.2 detail (for the expandable drawer)
        "eq_verdict": val.eq_verdict, "cost_of_equity": val.cost_of_equity, "eva": val.eva,
        "key_metrics": val.key_metrics, "subscores": val.subscores,
    }
    row["advice"] = advice.build(row)     # v8.3: Damodaran action synthesis (Thai)
    return row, calls


def fetch_watchlist(tickers, fmp_key="", quota_used=0, quota_cap=250):
    """NETWORK PHASE — analyse tickers on demand, nothing stored. Run OUTSIDE
    store.LOCK; the caller commits only the FMP quota counter afterwards."""
    rf, rf_live = yahoo.fetch_treasury_10y()
    rows, calls = [], 0
    cost = 5 if fmp_key else 0          # H2: profile + quote-fallback + earnings + estimates + quarterly-est backfill per ticker
    todo, errors = _partition_by_quota(tickers, cost, quota_used, quota_cap)
    if todo:                            # P1: parallel, same pattern as fetch_fundamentals
        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(todo))) as ex:
            futs = {t: ex.submit(analyze_row, t, rf, fmp_key, rf_live) for t in todo}
        for t in todo:                  # keep input order for the display rows
            try:
                r, c = futs[t].result()
                calls += c
                rows.append(r)
            except Exception as e:
                errors.append(f"{t}: {str(e)[:50]}")
    # cross-sectional momentum rank among the watchlist names (mirrors portfolio_view)
    momentum.cross_sectional_rank([r["momentum_v2"] for r in rows if r.get("momentum_v2")])
    try:                                            # S9 market regime -> crash guard per row (mirror portfolio)
        sp = get_prices_long("SPY", fmp_key, rng="2y")
        regime = momentum.market_state(sp["closes"]).get("regime")
        for r in rows:
            v2 = r.get("momentum_v2")
            if v2:
                v2["crash_guard"] = momentum.crash_guard(v2.get("mom_label"), regime)
    except Exception as e:
        log.warning("watchlist market_state failed: %s", e)
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
    # P1-6 (S4): fetch SPY FIRST so every ticker can compute a regression beta
    # (weekly, ~2y) as a free cross-check / fallback of the FMP vendor beta.
    sp = None
    try:
        sp = get_prices_long("SPY", fmp_key, rng="2y")     # ~1y+ is enough for SMA200
    except Exception as e:
        log.warning("SPY fetch failed (beta_calc + market_state skipped): %s", e)

    def _daily_one(t):
        """Per-ticker daily payload. Never raises (each part is contained)."""
        m = {}
        try:
            c = get_prices(t)
            mm = indicators.compute(t, c["closes"], c["volumes"], c["dates"])
            if "error" not in mm:
                m = mm
        except Exception as e:
            log.warning("%s daily momentum failed: %s", t, e)
        try:
            lc = get_prices_long(t, fmp_key, rng="5y", full_bars=780)  # H3: S9 reversal needs 756 bars — demand them
            mv = momentum.compute(t, lc["closes"], lc["dates"], lc["dividend_adjusted"])
            if "error" not in mv:
                mv["src"] = lc.get("source")
                mv["quality"] = lc.get("quality")
                if sp:                                    # P1-6: regression beta vs SPY
                    try:
                        b = momentum.regression_beta(lc["closes"], lc["dates"],
                                                     sp["closes"], sp["dates"])
                        if b is not None:
                            mv["beta_calc"] = b
                    except Exception as e:
                        log.warning("%s beta_calc failed: %s", t, e)
                m["v2"] = mv
        except Exception as e:
            log.warning("%s daily momentum_v2 failed: %s", t, e)
        return m

    out = {}
    if tickers:                          # P1: parallel per-ticker daily fetch
        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(tickers))) as ex:
            futs = {t: ex.submit(_daily_one, t) for t in tickers}
        for t in tickers:
            m = futs[t].result()
            if m:
                out[t] = m
    # S9 market regime (once per run): SPY trend gates per-name momentum reliability.
    try:
        if sp is None:
            sp = get_prices_long("SPY", fmp_key, rng="2y")
        mkt = momentum.market_state(sp["closes"])
        mkt["ret_12m"] = momentum.roc(sp["closes"], 252)   # S16/S35 benchmark
        mkt["as_of"] = sp["dates"][-1] if sp.get("dates") else None
        try:
            rf, _ = yahoo.fetch_treasury_10y()
            mkt["valuation"] = market_valuation.overlay(rf, config.ERP, config.MARKET_PE)
            # additive: expose assumption freshness so the UI can flag stale ERP/PE (S32/33)
            mkt["valuation"]["erp_as_of"] = config.ERP_AS_OF
            mkt["valuation"]["market_pe_as_of"] = config.MARKET_PE_AS_OF
            mkt["valuation"]["erp_stale_months"] = config.ERP_STALE_MONTHS
        except Exception as e:
            log.warning("market valuation overlay failed: %s", e)
        out["^MARKET"] = mkt                                # reserved key -> commit routes to s["market"]
    except Exception as e:
        log.warning("market_state SPY failed: %s", e)
    return out


def commit_daily(s, fetched):
    """STORE PHASE — merge momentum. Caller must hold store.LOCK.
    The reserved '^MARKET' key (S9 regime) is routed to s['market'], not a holding."""
    for t, m in fetched.items():
        if t == "^MARKET":
            s["market"] = m
        else:
            s["momentum"][t] = m
    return [t for t in fetched if t != "^MARKET"]


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
                sec = fmp.parse_profile(fmp.fetch_profile(t, fmp_key)).get("sector")
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


def weighted_trailing_return(rows, key="roc_12m"):
    """S16/S35 benchmark input: MV-weighted trailing return of CURRENT holdings
    (illustrative buy-and-hold, not a realized portfolio return). Pure; uses only
    rows that have both market_value and the momentum return; renormalizes."""
    num = den = 0.0
    for r in rows:
        mv = r.get("market_value")
        ret = (r.get("momentum_v2") or {}).get(key)
        if mv and ret is not None:
            num += mv * ret
            den += mv
    return (num / den) if den else None


def portfolio_view(s):
    rows = []
    market = s.get("market") or {}                   # S9 regime (risk_on / risk_off / None)
    mkt_regime = market.get("regime")
    for t, h in s.get("holdings", {}).items():
        ff = s["facts"].get(t, {})
        val = s["results"].get(t, {})
        mom = s["momentum"].get(t, {})
        v2 = mom.get("v2") or {}
        if v2:                                       # R2: dividend-aware split-only warning
            v2["div_warn"] = momentum.div_warn(v2.get("dividend_adjusted"), ff.get("dividend_ps"))
            v2["crash_guard"] = momentum.crash_guard(v2.get("mom_label"), mkt_regime)  # S9
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
        mgn_trend = margin_track.build(ff.get("operating_income_quarters"),
                                       ff.get("revenue_quarters"))        # P3: once
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
            "net_upside_pct": costs.net_upside(round(upside, 1) if upside is not None else None,
                                               config.CAPGAINS_TAX_RATE, config.TRADING_COST_BPS),
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
            "earnings_surprises": ff.get("earnings_surprises") or ff.get("eps_surprises_backfill") or [],
            "rev_surprises": s.get("rev_surprises", {}).get(t, []) or ff.get("rev_surprises_fmp", []),
            "pead": pead.signal(ff.get("earnings_surprises") or ff.get("eps_surprises_backfill"),
                                s.get("rev_surprises", {}).get(t) or ff.get("rev_surprises_fmp")),
            "margin_trend": mgn_trend,
            # v8.3: all fair-value methods + why-empty status for the earnings circles
            "fv_peg": val.get("fv_peg"), "fv_fvp": val.get("fv_fvp"),
            "earn_status": _earn_status(
                ff.get("earnings_surprises") or ff.get("eps_surprises_backfill"),
                s.get("rev_surprises", {}).get(t) or ff.get("rev_surprises_fmp"),
                mgn_trend,
                ff.get("provenance"),
                ("operating_income_quarters" in ff and "eps_quarters" in ff),
                bool(config.FMP_API_KEY)),
            "forward_eps": ff.get("forward_eps"), "forward_eps_low": ff.get("forward_eps_low"),
            "forward_eps_high": ff.get("forward_eps_high"),
            "forward_eps_spread_pct": ff.get("forward_eps_spread_pct"),
            "forward_eps_n": ff.get("forward_eps_n"), "forward_eps_sources": ff.get("forward_eps_sources"),
            "eq_verdict": val.get("eq_verdict"),
            "cost_of_equity": val.get("cost_of_equity"),
            "eva": val.get("eva"),
            "key_metrics": val.get("key_metrics"),
            "subscores": val.get("subscores"),
            "garp_score": screen_mod.garp_score(val.get("E_econ"), val.get("P")),
            "garp_candidate": screen_mod.is_candidate(val.get("E_econ"), val.get("P")),
        })
        rows[-1]["advice"] = advice.build(rows[-1])   # v8.3: Damodaran action synthesis
    # cross-sectional momentum rank across the held portfolio (mutates each v2 dict)
    momentum.cross_sectional_rank([r["momentum_v2"] for r in rows if r.get("momentum_v2")])
    mom_meta = _momentum_meta(rows)              # R3: per-row staleness + banner meta
    tot_cost = sum(r["cost_basis"] or 0 for r in rows)
    tot_mv = sum(r["market_value"] or 0 for r in rows)
    totals = {"cost_basis": round(tot_cost, 2), "market_value": round(tot_mv, 2),
              "pl": round(tot_mv - tot_cost, 2), "pl_pct": round((tot_mv - tot_cost) / tot_cost * 100, 1) if tot_cost else None}
    mkt_ret = (market or {}).get("ret_12m")
    port_ret = weighted_trailing_return(rows)
    benchmark = {
        "portfolio_12m": round(port_ret * 100, 1) if port_ret is not None else None,
        "market_12m": round(mkt_ret * 100, 1) if mkt_ret is not None else None,
        "excess_pp": round((port_ret - mkt_ret) * 100, 1) if (port_ret is not None and mkt_ret is not None) else None,
    }
    prof = philosophy.load(os.path.join(config.BASE_DIR, "data", "philosophy_profile.json"))
    phil = philosophy.assess(rows, prof)
    return {"rows": rows, "totals": totals, "momentum_meta": mom_meta,
            "market": market or None, "benchmark": benchmark, "philosophy": phil}
# end of refresh.py
