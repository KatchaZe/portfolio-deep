"""
Fundamentals refresh: SEC + FMP + Yahoo (+ optional Finnhub / Alpha Vantage)
-> normalize -> validate -> engine, per ticker, then a free sector peer-median pass.

  fetch_fundamentals(tickers, ...)   NETWORK, run OUTSIDE store.LOCK, in parallel
  commit_fundamentals(s, fetched)    STORE, caller holds store.LOCK

`analyze` is a sequence of enrichment steps over one FinancialFacts. Each FMP
step returns the number of FMP calls it spent, for the quota counter.
"""
import logging

import config
from sources import sec_edgar, yahoo, fmp, finnhub, alphavantage, damodaran
from domain.facts import FinancialFacts
from domain.engine import get_engine
from pipeline import (normalize, validate, rev_track, consensus, surprise_backfill,
                      dataquality, batch)
from pipeline.cik import resolve_cik
import store as store_mod

log = logging.getLogger("portfolio.fundamentals")

STALE_FALLBACK_CALLS = 4           # income annual+ttm, balance, cashflow


def fetch_roc_table():
    """Damodaran's industry ROIC table: fetched once per refresh and shared by every
    ticker. Disk-cached 30 days with a bundled snapshot fallback, never fatal."""
    return damodaran.fetch_roc_table(cache_dir=config.CACHE_DIR,
                                     user_agent=config.SEC_USER_AGENT)


def refresh_implied_erp():
    """Read Damodaran's current implied ERP (cached a week) and re-apply the
    assumption overlay so this refresh prices on it. Never fatal."""
    try:
        if damodaran.fetch_implied_erp(cache_dir=config.CACHE_DIR, user_agent=config.SEC_USER_AGENT):
            store_mod.reapply_assumptions()
    except Exception as e:
        log.warning("implied ERP refresh failed: %s", e)


def analyze(ticker, rf, fmp_key="", rf_live=True, roc_table=None, quota_left=None):
    """Fetch -> normalize -> validate -> engine. Returns (facts, valuation, fmp_calls).
    NETWORK ONLY, never touches the store, safe to run outside store.LOCK.

    `quota_left` is a batch.Budget for the OPTIONAL FMP spend (the stale-financials
    fallback); None = unlimited (single-ticker paths)."""
    t = ticker.upper().strip()
    cik, name = resolve_cik(t)
    sec_cf = sec_edgar.fetch_companyfacts(
        cik, config.SEC_USER_AGENT, cache_dir=config.CACHE_DIR,
        ttl_hours=config.SEC_CACHE_TTL_HOURS, min_interval=config.SEC_MIN_INTERVAL) if cik else None
    fx, sec_ccy = _sec_fx(t, sec_cf)
    profile, fmp_calls = _fmp_profile(t, fmp_key)

    ff = normalize.build(t, sec_cf, profile, yahoo.fetch_consensus(t), fx_rate=fx, company=name)
    if not rf_live:
        ff.flags.append(f"Rf fallback {round(rf*100,2)}% — live 10Y Treasury yield unavailable")
    if isinstance(sec_cf, dict) and sec_cf.get("_stale_cache"):
        ff.flags.append("SEC via stale cache (network failed) — figures may lag the latest filing")

    fmp_calls += _stale_financials_fallback(ff, t, name, fmp_key, sec_ccy, fx, quota_left)
    fmp_calls += _quote_fallback(ff, t, fmp_key)
    fmp_calls += _earnings_surprises(ff, t, fmp_key)
    estimates, calls = _estimate_path(ff, t, fmp_key)
    fmp_calls += calls
    fmp_calls += _surprise_backfill(ff, t, fmp_key)
    _blend_forward_eps(ff, t, estimates)
    _own_pe_percentile(ff, t)
    _terminal_roic(ff, t, roc_table)
    _industry_beta(ff, t)

    validate.validate(ff, rf=rf)
    normalize.resolve_growth(ff)
    val = get_engine().evaluate(ff, rf=rf)
    return ff, val, fmp_calls


def _sec_fx(t, sec_cf):
    """(fx_to_usd or None, SEC's reporting currency). The currency belongs to SEC's
    numbers ONLY: any other source reports in its own, so this rate must never be
    reused for another source's figures."""
    fx, sec_ccy = None, "USD"
    if sec_cf:
        try:
            sec_ccy = (sec_edgar.extract(sec_cf).get("currency") or "USD").upper()
            if sec_ccy != "USD":
                fx = yahoo.fetch_fx_to_usd(sec_ccy)
        except Exception as e:
            log.warning("%s FX lookup failed: %s", t, e)
    return fx, sec_ccy


def _fmp_profile(t, fmp_key):
    if not fmp_key:
        return None, 0
    try:
        return fmp.fetch_profile(t, fmp_key) or None, 1
    except Exception as e:
        log.warning("%s FMP profile failed: %s", t, e)
        return None, 0


def _stale_financials_fallback(ff, t, name, fmp_key, sec_ccy, fx, quota_left):
    """SEC companyfacts can lag per ticker (TSM stops at FY2024 while other foreign
    filers carry FY2025). When the financials are stale, try FMP before degrading
    the row. The +4 calls are optional spend, so they are reserved against the live
    budget at the point of spend: budgeting them up front for every ticker would
    refuse half the portfolio for a branch that fires on almost none of it."""
    if not (fmp_key and batch.quota_take(quota_left, STALE_FALLBACK_CALLS)
            and any(d["code"] == "stale_financials" for d in dataquality.assess(ff))):
        return 0
    calls = 0
    try:
        alt = FinancialFacts(t, company=name)
        fmp.parse(fmp.fetch(t, fmp_key), alt)
        calls = STALE_FALLBACK_CALLS
        # The fallback is converted at ITS OWN currency, not SEC's: TSM's 20-F reads
        # USD at SEC while FMP reports TWD, and the policy refuses the fallback when
        # no rate is found (stale-but-coherent beats fresh-but-mixed).
        _, notes = dataquality.apply_stale_fallback(
            ff, alt, sec_ccy, fx, yahoo.fetch_fx_to_usd)
        ff.flags.extend(notes)
    except Exception as e:
        log.warning("%s stale-financials fallback failed: %s", t, e)
        ff.flags.append("งบเก่าและดึงแหล่งสำรองไม่สำเร็จ — หัก confidence")
    return calls


def _quote_fallback(ff, t, fmp_key):
    """Foreign filers (e.g. NVO) have no SEC share count, so when Yahoo is degraded
    the engine has nothing to anchor on. Spent only when price/shares are missing."""
    if not (fmp_key and (ff.shares_diluted is None or ff.price is None)):
        return 0
    calls = 0
    try:
        q = fmp.parse_quote(fmp.fetch_quote(t, fmp_key))
        calls = 1
        filled = []
        if ff.price is None and q.get("price"):
            ff.set("price", q["price"], "fmp/quote"); filled.append("price")
        if ff.shares_diluted is None and q.get("shares"):
            ff.set("shares_diluted", q["shares"], "fmp/quote"); filled.append("shares")
        if filled:
            ff.flags.append(f"{'/'.join(filled)} via FMP quote (sec/yahoo gap)")
    except Exception as e:
        log.warning("%s FMP quote fallback failed: %s", t, e)
    return calls


def _optional_earnings(t, label, key, fetch, parse):
    """A keyed free source's parsed list, or None (absent key, failure, empty)."""
    if not key:
        return None
    try:
        return parse(fetch(t, key)) or None
    except Exception as e:
        log.warning("%s %s failed: %s", t, label, e)
        return None


def _earnings_surprises(ff, t, fmp_key):
    """EPS-surprise cross-check across every available source. The reconcile picks a
    primary track record, tags which sources confirm it, and flags a latest-quarter
    beat-vs-miss disagreement."""
    calls = 0
    by_source = {}
    if ff.earnings_surprises:
        by_source["yahoo"] = ff.earnings_surprises
    if fmp_key:
        try:
            raw = fmp.fetch_earnings(t, fmp_key)
            calls = 1
            by_source["fmp"] = fmp.parse_earnings(raw)
            # immediate revenue surprise from the SAME response (no extra call)
            ff.rev_surprises_fmp = fmp.parse_revenue_surprises(raw)
        except Exception as e:
            log.warning("%s FMP earnings failed: %s", t, e)
    fh = _optional_earnings(t, "Finnhub earnings", config.FINNHUB_API_KEY,
                            finnhub.fetch_earnings, finnhub.parse_earnings)
    if fh:
        by_source["finnhub"] = fh
    av = _optional_earnings(t, "AlphaVantage earnings", config.ALPHAVANTAGE_API_KEY,
                            alphavantage.fetch_earnings, alphavantage.parse_earnings)
    if av:
        by_source["alphavantage"] = av
    rec = consensus.reconcile_earnings(by_source)
    if rec["list"]:
        ff.earnings_surprises = rec["list"]
        ff.provenance["earnings_surprises"] = rec["provenance"]
        if rec["disagree"]:
            ff.flags.append("earnings beat/miss disagree across sources")
        if "yahoo" not in by_source and rec["primary"]:
            ff.flags.append(f"earnings via {rec['primary']} (yahoo earningsHistory empty)")
    return calls


def _estimate_path(ff, t, fmp_key):
    """FMP analyst-estimates -> growth fade + breadth. Returns (raw estimates, calls);
    the raw response is reused as a forward-EPS candidate."""
    if not fmp_key:
        return None, 0
    estimates, calls = None, 0
    try:
        estimates = fmp.fetch_estimates(t, fmp_key)
        calls = 1
        # FMP estimates are in the filer's reporting currency; the actual-year anchor is
        # only comparable for a USD filer (ff money is FX-converted otherwise)
        anchor = (ff.revenue_annuals or [None])[0] if not ff.provenance.get("fx") else None
        path = fmp.parse_estimate_path(estimates, ff.fiscal_year, latest_revenue=anchor)
        for k in ("fwd_growth_near", "fwd_growth_far", "n_analysts", "fwd_growth_y1", "n_analysts_y2"):
            if path.get(k) is not None:
                ff.set(k, path[k], "fmp/estimates")
    except Exception as e:
        log.warning("%s FMP estimates failed: %s", t, e)
    return estimates, calls


def _surprise_backfill(ff, t, fmp_key):
    """When the primary surprise paths came up empty, pair FMP QUARTERLY estimates
    with free SEC actuals so the EPS / Rev circles fill on the first refresh."""
    need_eps = not ff.earnings_surprises
    need_rev = not ff.rev_surprises_fmp
    if not (fmp_key and (need_eps or need_rev)):
        return 0
    calls = 0
    try:
        est_q = fmp.parse_estimates_quarterly(fmp.fetch_estimates_quarter(t, fmp_key))
        calls = 1
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
    return calls


def _blend_forward_eps(ff, t, fmp_estimates):
    """Median of the free forward-EPS candidates, with the min-max dispersion kept for
    display. validate() still applies the revenue-ceiling backstop afterwards."""
    candidates = {}
    if ff.forward_eps and ff.forward_eps > 0:
        candidates["yahoo"] = ff.forward_eps          # Yahoo value set by normalize
    if fmp_estimates:
        fe = fmp.parse_forward_eps(fmp_estimates, ff.fiscal_year)
        if fe:
            candidates["fmp"] = fe
    if config.FINNHUB_API_KEY:
        try:
            fh_fwd = finnhub.parse_eps_estimate(finnhub.fetch_eps_estimate(t, config.FINNHUB_API_KEY))
            if fh_fwd:
                candidates["finnhub"] = fh_fwd
        except Exception as e:
            log.warning("%s Finnhub eps-estimate failed: %s", t, e)
    # the PRICE lets a candidate quoted in another currency be rejected as a unit
    # mismatch instead of being averaged in and blamed on analyst disagreement
    blend = consensus.blend_forward_eps(candidates, price=ff.price)
    if not blend:
        return
    for src, why in (blend.get("rejected") or {}).items():
        ff.flags.append(f"forward EPS from {src} dropped before the blend: {why}")
    ff.set("forward_eps", blend["value"], "consensus-blend(" + "+".join(blend["sources"]) + ")")
    ff.forward_eps_sources = blend["sources"]
    ff.forward_eps_low = blend["low"]
    ff.forward_eps_high = blend["high"]
    ff.forward_eps_spread_pct = blend["spread_pct"]
    ff.forward_eps_n = blend["n"]


def _own_pe_percentile(ff, t):
    """Own 5y P/E percentile (re-rating signal). USD filers only, so price and EPS
    share a currency."""
    if not (ff.currency == "USD" and ff.price and ff.eps_annuals_dated):
        return
    try:
        cur_eps = (ff.net_income / ff.shares_diluted) if (ff.net_income and ff.shares_diluted) else ff.eps_gaap
        ch = yahoo.fetch_chart(t, rng="5y", interval="1mo")
        pct = yahoo.pe_percentile_5y(ff.eps_annuals_dated, ch["closes"], ch["dates"], ff.price, cur_eps)
        if pct is not None:
            ff.set("own_pe_pctile", round(pct, 3), "yahoo5y+sec")
    except Exception as e:
        log.warning("%s own-PE percentile failed: %s", t, e)


def _terminal_roic(ff, t, roc_table):
    """Excess returns fade toward what the INDUSTRY sustains (Damodaran), not a
    flat ceiling."""
    try:
        sec_roic = damodaran.terminal_roic_for(t, ff.sector, roc_table)
        if sec_roic:
            ff.set("terminal_roic_sector", round(sec_roic, 4), "damodaran-roc")
    except Exception as e:
        log.warning("%s terminal ROIC lookup failed: %s", t, e)


def _industry_beta(ff, t):
    """Damodaran bottom-up beta input: the industry's unlevered beta (corrected for
    cash). The engine re-levers it at the firm's market D/E; the regression beta in
    ff.beta stays as the cross-check."""
    try:
        table = damodaran.fetch_beta_table(cache_dir=config.CACHE_DIR,
                                           user_agent=config.SEC_USER_AGENT)
        bu, industry = damodaran.industry_beta_for(t, ff.sector, table)
        if bu:
            ff.set("beta_unlevered_industry", round(bu, 4), "damodaran-betas")
            ff.set("beta_industry", industry, "damodaran-betas")
    except Exception as e:
        log.warning("%s industry beta lookup failed: %s", t, e)


def compute_peer_medians(fetched):
    """{ticker: median revenue-growth of its SECTOR cohort, excluding itself}, from
    the batch already fetched. Needs >=2 names in the sector; otherwise the ticker
    is omitted (the engine then skips the peer adjustment)."""
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


def fetch_fundamentals(tickers, fmp_key="", quota_used=0, quota_cap=250):
    """NETWORK PHASE, run OUTSIDE store.LOCK. Tickers are analysed in parallel:
    analyze() is network+pure, the SEC throttle is global, and the FMP quota is
    partitioned up front. Returns (fetched {ticker: (FinancialFacts, Valuation)},
    errors, fmp_calls, rf_pct, rf_live)."""
    rf, rf_live = yahoo.fetch_treasury_10y()
    refresh_implied_erp()
    roc = fetch_roc_table()
    fetched, calls = {}, 0
    cost = batch.guaranteed_cost(fmp_key)
    todo, errors = batch.partition_by_quota(tickers, cost, quota_used, quota_cap)
    # whatever the guaranteed spend does not claim, the optional fallback may draw on
    spare = batch.Budget(max(0, batch.usable_quota(quota_used, quota_cap) - len(todo) * cost)) if fmp_key else None
    futs = batch.run_parallel(lambda t: analyze(t, rf, fmp_key, rf_live, roc, spare), todo)
    for t in todo:
        try:
            ff, val, c = futs[t].result()
            calls += c
            fetched[t] = (ff, val)
        except Exception as e:
            log.warning("%s fundamentals failed: %s", t, e)
            errors.append(f"{t}: {str(e)[:60]}")
    # peer-median injection re-scores the affected names; the engine is pure
    for t, med in compute_peer_medians(fetched).items():
        ff, _val = fetched[t]
        ff.set("peer_median_growth", round(med, 4), "sector-cohort")
        fetched[t] = (ff, get_engine().evaluate(ff, rf=rf))
    return fetched, errors, calls, round(rf * 100, 2), rf_live


def commit_fundamentals(s, fetched, fmp_calls):
    """STORE PHASE, fast merge of fetched results. Caller must hold store.LOCK."""
    today = store_mod.today()
    for t, (ff, val) in fetched.items():
        s["facts"][t] = ff.to_dict()
        s["results"][t] = val.to_dict()
        s["updated"][t] = today
        # build-forward revenue beat/miss history (persisted, holdings only)
        rev_track.update(s, t, ff.rev_estimate_curq, ff.revenue_quarters, today)
    store_mod.add_fmp_calls(s, fmp_calls)
    return s
