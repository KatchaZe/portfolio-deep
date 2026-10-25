"""
Fundamentals refresh: SEC + FMP + Yahoo (+ optional Finnhub / Alpha Vantage)
-> normalize -> validate -> engine, per ticker, then a free sector peer-median pass.

  fetch_fundamentals(tickers, ...)   NETWORK, run OUTSIDE store.LOCK, in parallel
  commit_fundamentals(s, fetched)    STORE, caller holds store.LOCK

`analyze` is a sequence of enrichment steps over one FinancialFacts. Each FMP
step returns the number of FMP calls it spent, for the quota counter.
"""
import logging
import os

import config
from sources import sec_edgar, sec_gap, yahoo, fmp, finnhub, alphavantage, damodaran
from domain import one_time, pricing
from domain.facts import FinancialFacts
from domain.engine import get_engine
from pipeline import (normalize, validate, rev_track, consensus, surprise_backfill,
                      dataquality, batch)
from pipeline import lastgood, peers, quarterly
from pipeline.cik import resolve_cik
import store as store_mod

log = logging.getLogger("portfolio.fundamentals")

# fmp.fetch calls every endpoint in fmp.ENDPOINTS (8), not 4: the old figure under-
# reserved and under-counted the day's FMP use (live 2026-10-24: the app read 242/250
# while FMP itself had already refused)
STALE_FALLBACK_CALLS = len(fmp.ENDPOINTS)
FALLBACK_CORE = ("income_annual", "balance_annual", "cashflow_annual")


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


def analyze(ticker, rf, fmp_key="", rf_live=True, roc_table=None, quota_left=None, prev=None):
    """Fetch -> normalize -> validate -> engine. Returns (facts, valuation, fmp_calls).
    NETWORK ONLY, never touches the store, safe to run outside store.LOCK.

    `quota_left` is a batch.Budget for the OPTIONAL FMP spend (the stale-financials
    fallback); None = unlimited (single-ticker paths). `prev` is the ticker's facts
    dict from the last refresh (read-only), for the 30-day last-good consensus."""
    t = ticker.upper().strip()
    cik, name = resolve_cik(t)
    sec_cf = sec_edgar.fetch_companyfacts(
        cik, config.SEC_USER_AGENT, cache_dir=config.CACHE_DIR,
        ttl_hours=config.SEC_CACHE_TTL_HOURS, min_interval=config.SEC_MIN_INTERVAL) if cik else None
    sec_cf = sec_gap.complete(cik, sec_cf, config.SEC_USER_AGENT, cache_dir=config.CACHE_DIR,
                              throttle=lambda: sec_edgar._throttle(config.SEC_MIN_INTERVAL))
    fx, sec_ccy = _sec_fx(t, sec_cf)
    profile, fmp_calls = _fmp_profile(t, fmp_key)
    splits = _split_history(t) if sec_cf else None
    from sources.industries import securities_are_operating
    operating_sec = securities_are_operating(t, fmp.parse_profile(profile).get("industry"))

    ff = normalize.build(t, sec_cf, profile, yahoo.fetch_consensus(t), fx_rate=fx, company=name,
                         splits=splits, cash_excludes_securities=operating_sec)
    _t1_price(ff, t)
    if operating_sec:
        ff.flags.append("health insurer: short-term investments back claim reserves - "
                        "cash is cash and equivalents only")
    if splits is None and sec_cf:
        ff.flags.append("split history unavailable (Yahoo) - splits found from the share "
                        "count only")
    if not rf_live:
        ff.flags.append(f"Rf fallback {round(rf*100,2)}% — live 10Y Treasury yield unavailable")
    ff.sec_gap = (sec_cf or {}).get("_sec_gap")
    ff.sic = (sec_cf or {}).get("_sic")
    if isinstance(sec_cf, dict) and sec_cf.get("_stale_cache"):
        ff.flags.append("SEC via stale cache (network failed) — figures may lag the latest filing")

    fmp_calls += _stale_financials_fallback(ff, t, name, fmp_key, sec_ccy, fx, quota_left)
    fmp_calls += _quote_fallback(ff, t, fmp_key)
    quarterly.run(ff, t, sec_ccy, fx, config.ALPHAVANTAGE_API_KEY, config.CACHE_DIR,
                  yahoo.fetch_fx_to_usd)
    fmp_calls += _earnings_surprises(ff, t, fmp_key)
    estimates, calls = _estimate_path(ff, t, fmp_key, prev)
    fmp_calls += calls
    fmp_calls += _surprise_backfill(ff, t, fmp_key)
    _blend_forward_eps(ff, t, estimates, prev)
    _own_pe_percentile(ff, t)
    _industry(ff, t)
    _terminal_roic(ff, t, roc_table)
    _industry_beta(ff, t)
    _industry_operating(ff)
    _country_risk(ff, t, cik)
    one_time.normalize_operating_income(ff)

    validate.validate(ff, rf=rf)
    normalize.resolve_growth(ff)
    val = get_engine().evaluate(ff, rf=rf)
    return ff, val, fmp_calls


def _t1_price(ff, t):
    """Value against the last completed session's close (the project's T-1 rule), not
    the live quote FMP and Yahoo hand back mid-session. Market cap moves with it, so
    the share count derived from mcap / price is unchanged."""
    try:
        ch = yahoo.fetch_chart(t, rng="5d", interval="1d")
        close, day = pricing.t1_close(ch.get("closes"), ch.get("dates"))
    except Exception as e:
        log.warning("%s T-1 close failed: %s", t, e)
        close = day = None
    if not close:
        if ff.price:
            ff.flags.append("price is the live quote: the previous close was unavailable")
        return
    if ff.price and ff.market_cap:
        ff.market_cap = ff.market_cap * close / ff.price
    ff.set("price", round(close, 4), f"yahoo close {day} (T-1)")


def _split_history(t):
    """[(ex-date, ratio)] from Yahoo, [] when the stock never split, None on failure."""
    try:
        return yahoo.fetch_splits(t)
    except Exception as e:
        log.warning("%s split history failed: %s", t, e)
        return None


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
    if not (fmp_key and any(d["code"] == "stale_financials" for d in dataquality.assess(ff))):
        return 0
    bundle = _cached_fallback_bundle(t)
    calls = 0
    if bundle is None:
        if not batch.quota_take(quota_left, STALE_FALLBACK_CALLS):
            # live 2026-10-24: TSM silently fell back to its FY2024 SEC base when the
            # day's FMP budget ran short
            ff.flags.append("งบ SEC เก่าและวันนี้โควตา FMP เหลือไม่พอเรียกแหล่งสำรอง — "
                            f"ใช้งบเดิมไปก่อน {batch.RETRY_MARK}")
            return 0
        bundle = fmp.fetch(t, fmp_key)
        calls = bundle.get("_calls", STALE_FALLBACK_CALLS)
        _store_fallback_bundle(t, bundle)
    try:
        alt = FinancialFacts(t, company=name)
        fmp.parse(bundle, alt)
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


FALLBACK_TTL_DAYS = 7


def _bundle_ok(bundle):
    """The three statements the fallback is for answered with data. The free tier
    refuses some of the other endpoints (TTM, key metrics) for some symbols; requiring
    those too meant TSM's bundle was never cached and was bought again every refresh."""
    return isinstance(bundle, dict) and all(
        isinstance(bundle.get(k), list) and bundle[k] for k in FALLBACK_CORE)


def _cached_fallback_bundle(t):
    """The FMP statements bundle fetched within FALLBACK_TTL_DAYS, or None. Annual
    statements change once a year: re-buying them on every refresh spent 4 calls a
    ticker a day."""
    import json
    import time
    try:
        with open(os.path.join(config.CACHE_DIR, f"fmp_fallback_{t}.json"), encoding="utf-8") as fh:
            c = json.load(fh)
        if time.time() - c.get("at", 0) < FALLBACK_TTL_DAYS * 86400 and _bundle_ok(c.get("data")):
            return c["data"]
    except (OSError, ValueError):
        pass
    return None


def _store_fallback_bundle(t, bundle):
    import json
    import time
    if not _bundle_ok(bundle):
        return
    try:
        os.makedirs(config.CACHE_DIR, exist_ok=True)
        with open(os.path.join(config.CACHE_DIR, f"fmp_fallback_{t}.json"), "w", encoding="utf-8") as fh:
            json.dump({"at": time.time(), "data": bundle}, fh)
    except OSError as e:
        log.warning("%s FMP fallback cache write failed: %s", t, e)


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
    # cached 7 days: uncached, one call per name per refresh (21 holdings + the
    # watchlist) would spend the free 25/day before EARNINGS_ESTIMATES got any
    av = _optional_earnings(t, "AlphaVantage earnings", config.ALPHAVANTAGE_API_KEY,
                            lambda sym, key: alphavantage.cached(
                                "earnings", sym, key, alphavantage.fetch_earnings,
                                config.CACHE_DIR)[0],
                            alphavantage.parse_earnings)
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


def _estimate_path(ff, t, fmp_key, prev=None):
    """Consensus revenue path: FMP analyst-estimates, else Yahoo's revenue estimates,
    else Alpha Vantage EARNINGS_ESTIMATES, else the last live path no older than 30
    days (flagged). Returns (raw FMP estimates, FMP calls); the raw FMP response is
    reused as a forward-EPS candidate."""
    estimates, calls = None, 0
    if fmp_key:
        try:
            estimates, calls = _cached_estimates(t, fmp_key)
            # FMP estimates are in the filer's reporting currency; the actual-year anchor
            # is only comparable for a USD filer (ff money is FX-converted otherwise)
            anchor = (ff.revenue_annuals or [None])[0] if not ff.provenance.get("fx") else None
            path = fmp.parse_estimate_path(estimates, ff.fiscal_year, latest_revenue=anchor)
            for k in ("fwd_growth_near", "fwd_growth_far", "n_analysts", "fwd_growth_y1",
                      "n_analysts_y2", "rev_growth_path"):
                if path.get(k) is not None:
                    ff.set(k, path[k], "fmp/estimates")
        except Exception as e:
            log.warning("%s FMP estimates failed: %s", t, e)
    _yahoo_growth_fallback(ff)
    if not ff.rev_growth_path:
        _alphavantage_path(ff, t)
    if ff.rev_growth_path:
        ff.consensus_as_of = store_mod.today()
    else:
        _last_good_path(ff, prev)
    return estimates, calls


def _av_estimates(t):
    """Alpha Vantage EARNINGS_ESTIMATES through its 7-day cache: (raw, calls)."""
    try:
        return alphavantage.cached("estimates", t, config.ALPHAVANTAGE_API_KEY,
                                   alphavantage.fetch_estimates, config.CACHE_DIR)
    except Exception as e:
        log.warning("%s AlphaVantage estimates failed: %s", t, e)
        return {}, 0


def _alphavantage_path(ff, t):
    """Third consensus source, called only after FMP and Yahoo both came back empty,
    so the free 25-a-day quota goes to the names that need it. The actual-year anchor
    is a USD filer's own filed revenue, the rule FMP's path follows."""
    if not config.ALPHAVANTAGE_API_KEY or ff.provenance.get("fx"):
        return
    raw, _ = _av_estimates(t)
    anchor = (ff.revenue_annuals or [None])[0]
    path = alphavantage.parse_estimate_path(raw, ff.fiscal_year, latest_revenue=anchor)
    if not path:
        return
    ff.set("rev_growth_path", path, "alphavantage/estimates")
    ff.set("fwd_growth_y1", path[0][0], "alphavantage/estimates")
    ff.set("n_analysts", path[0][1], "alphavantage/estimates")
    if len(path) > 1:
        ff.set("fwd_growth_near", path[1][0], "alphavantage/estimates")
        ff.set("n_analysts_y2", path[1][1], "alphavantage/estimates")
    ff.flags.append("consensus revenue path from Alpha Vantage (FMP and Yahoo returned none)")


def _last_good_path(ff, prev):
    """Every consensus source failed: the previous refresh's live path, if it is no
    older than lastgood.MAX_AGE_DAYS and still anchored on the same fiscal year."""
    today = lastgood.date_of(store_mod.today())
    got = lastgood.consensus_path(prev, ff, today)
    if not got:
        return
    rows, fields, src, as_of = got
    tag = f"last-good {src} ({as_of})"
    ff.set("rev_growth_path", rows, tag)
    for k, v in fields.items():
        ff.set(k, v, tag)
    ff.consensus_as_of = as_of
    ff.flags.append(f"consensus sources all failed - using the last live revenue path "
                    f"from {src} read {as_of} ({lastgood.age_days(as_of, today)} days old, "
                    f"kept up to {lastgood.MAX_AGE_DAYS})")


ESTIMATES_TTL_DAYS = 7


def _cached_estimates(t, fmp_key):
    """(estimates, calls). Consensus moves over weeks, not hours, so a non-empty answer
    is reused for ESTIMATES_TTL_DAYS instead of spending a call on every refresh."""
    import json
    import time
    path = os.path.join(config.CACHE_DIR, f"fmp_estimates_{t}.json")
    try:
        with open(path, encoding="utf-8") as fh:
            cached = json.load(fh)
        if cached.get("data") and time.time() - cached.get("at", 0) < ESTIMATES_TTL_DAYS * 86400:
            return cached["data"], 0
    except (OSError, ValueError):
        pass
    data = fmp.fetch_estimates(t, fmp_key)
    if data:
        try:
            os.makedirs(config.CACHE_DIR, exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"at": time.time(), "data": data}, fh)
        except OSError as e:
            log.warning("%s estimates cache write failed: %s", t, e)
    return data, 1


def _yahoo_growth_fallback(ff):
    """FMP's free tier refuses analyst-estimates for some symbols (LLY, MELI, AVGO came
    back with a profile and no estimates on 2026-10-18), leaving 13 of 21 names on
    historical growth. When FMP gave no path, Yahoo's does the same job for the fields
    the earnings paths read."""
    if ff.provenance.get("rev_growth_path") != "yahoo" or ff.fwd_growth_y1 is not None:
        return
    path = ff.rev_growth_path
    ff.set("fwd_growth_y1", path[0][0], "yahoo/estimates")
    ff.set("n_analysts", path[0][1], "yahoo/estimates")
    if len(path) > 1:
        ff.set("fwd_growth_near", path[1][0], "yahoo/estimates")
        ff.set("n_analysts_y2", path[1][1], "yahoo/estimates")


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


def _blend_forward_eps(ff, t, fmp_estimates, prev=None):
    """Median of the free forward-EPS candidates, with the min-max dispersion kept for
    display. validate() still applies the revenue-ceiling backstop afterwards. Alpha
    Vantage is the backup when no other source answers; the last live consensus (30
    days, flagged) when none does."""
    candidates = {}
    if ff.forward_eps and ff.forward_eps > 0:
        candidates["yahoo"] = ff.forward_eps          # Yahoo value set by normalize
    if fmp_estimates:
        fe = fmp.parse_ntm_eps(fmp_estimates)          # NTM, the basis the others are put on
        if fe:
            candidates["fmp"] = fe
    if config.FINNHUB_API_KEY:
        try:
            fh_fwd = finnhub.parse_eps_estimate(finnhub.fetch_eps_estimate(t, config.FINNHUB_API_KEY))
            if fh_fwd:
                candidates["finnhub"] = fh_fwd
        except Exception as e:
            log.warning("%s Finnhub eps-estimate failed: %s", t, e)
    if not candidates and config.ALPHAVANTAGE_API_KEY:
        raw, _ = _av_estimates(t)
        av = alphavantage.parse_ntm_eps(raw, ff.fiscal_year)
        if av:
            candidates["alphavantage"] = av
    reused = None
    if not candidates:
        reused = lastgood.forward_eps(prev, lastgood.date_of(store_mod.today()))
        if reused:
            candidates["last-good"] = reused[0]
    # the PRICE lets a candidate quoted in another currency be rejected as a unit
    # mismatch instead of being averaged in and blamed on analyst disagreement. The
    # band only judges 2+ candidates, so a lone backup is tested on its own.
    blend = consensus.blend_forward_eps(candidates, price=ff.price)
    if blend and blend["n"] == 1 and ff.price and set(blend["sources"]) & {"alphavantage", "last-good"}:
        pe = ff.price / blend["value"]
        if not (consensus.PE_MIN <= pe <= consensus.PE_MAX):
            ff.flags.append(f"forward EPS backup {blend['value']} dropped: a {pe:,.1f}x "
                            f"forward P/E reads as a different currency/unit")
            return
    if not blend:
        return
    if reused:
        eps, src, as_of = reused
        ff.set("forward_eps", eps, f"last-good {src} ({as_of})")
        ff.forward_eps_as_of = as_of
        ff.flags.append(f"forward EPS sources all failed - using the last live consensus "
                        f"{eps} from {src} read {as_of} "
                        f"({lastgood.age_days(as_of, lastgood.date_of(store_mod.today()))} days old)")
        return
    for src, why in (blend.get("rejected") or {}).items():
        ff.flags.append(f"forward EPS from {src} dropped before the blend: {why}")
    ff.set("forward_eps", blend["value"], "consensus-blend(" + "+".join(blend["sources"]) + ")")
    ff.forward_eps_as_of = store_mod.today()
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
        from domain.engine.deep_v82 import core_net_income
        core, _ = core_net_income(ff)       # a one-off gain is not a cheaper multiple
        cur_eps = (core / ff.shares_diluted) if (core and ff.shares_diluted) else ff.eps_gaap
        ch = yahoo.fetch_chart(t, rng="5y", interval="1mo")
        pct = yahoo.pe_percentile_5y(ff.eps_annuals_dated, ch["closes"], ch["dates"], ff.price, cur_eps)
        if pct is not None:
            ff.set("own_pe_pctile", round(pct, 3), "yahoo5y+sec")
    except Exception as e:
        log.warning("%s own-PE percentile failed: %s", t, e)


def _industry(ff, t):
    """Resolve the Damodaran industry once; every industry-level input reads it."""
    from sources.industries import industry_for
    ind, basis = industry_for(t, ff.industry, ff.sector)
    if ind:
        ff.set("damodaran_industry", ind, "industries:" + basis)
        ff.set("industry_basis", basis, "industries")
        if basis in ("keyword", "sector"):
            ff.flags.append(f"industry '{ind}' inferred from the {basis} "
                            f"({ff.industry or ff.sector}) - add {t} to INDUSTRY_BY_TICKER if wrong")
    else:
        ff.flags.append("industry unknown (no FMP profile) - industry beta, margin and "
                        "terminal ROIC fall back to firm-level or market values")


def _terminal_roic(ff, t, roc_table):
    """Excess returns fade toward what the INDUSTRY sustains (Damodaran), not a
    flat ceiling."""
    try:
        sec_roic = damodaran.terminal_roic_for(t, ff.sector, roc_table, fmp_industry=ff.industry)
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
        bu, industry = damodaran.industry_beta_for(t, ff.sector, table, fmp_industry=ff.industry)
        if bu:
            ff.set("beta_unlevered_industry", round(bu, 4), "damodaran-betas")
            ff.set("beta_industry", industry, "damodaran-betas")
    except Exception as e:
        log.warning("%s industry beta lookup failed: %s", t, e)


def _industry_operating(ff):
    """Industry operating margin and sales-to-capital (Damodaran), for the young DCF's
    target margin and the FCFF DCF's reinvestment band."""
    if not ff.damodaran_industry:
        return
    from sources.industries import industry_mix
    mix = industry_mix(ff.ticker)
    try:
        m = damodaran.industry_value(damodaran.fetch_margin_table(cache_dir=config.CACHE_DIR,
                                                                  user_agent=config.SEC_USER_AGENT),
                                     ff.damodaran_industry, mix)
        if m is not None:
            ff.set("industry_margin", round(m, 4), "damodaran-margins")
        s2c = damodaran.industry_value(damodaran.fetch_s2c_table(cache_dir=config.CACHE_DIR,
                                                                 user_agent=config.SEC_USER_AGENT),
                                       ff.damodaran_industry, mix, skip_financial=True)
        if s2c is not None:
            ff.set("industry_sales_to_capital", round(s2c, 3), "damodaran-capex")
    except Exception as e:
        log.warning("%s industry margin / sales-to-capital lookup failed: %s", ff.ticker, e)


def _auto_revenue_mix(ff, t, cik):
    """The latest 10-K's geographic revenue split, for US-GAAP filers without a mix on
    file. None for 20-F filers, on any failure, or when the 10-K has no usable table."""
    if not cik or ff.accounting_standard != "us-gaap":
        return None
    from sources import revenue_geo
    try:
        return revenue_geo.fetch_mix(cik, config.SEC_USER_AGENT, cache_dir=config.CACHE_DIR,
                                     throttle=lambda: sec_edgar._throttle(config.SEC_MIN_INTERVAL))
    except Exception as e:
        log.warning("%s 10-K revenue mix failed: %s", t, e)
        return revenue_geo.cached_mix(cik, config.CACHE_DIR)


def _country_risk(ff, t, cik=None):
    """Country risk premium over the US, weighted by where the revenue is earned
    (config / dashboard / latest 10-K), else by the FMP domicile."""
    from sources import country_risk
    try:
        table = country_risk.fetch_crp_table(cache_dir=config.CACHE_DIR,
                                            user_agent=config.SEC_USER_AGENT)
        mix, source = country_risk.revenue_mix(t)
        if mix is None:
            mix, source = country_risk.revenue_mix(t, _auto_revenue_mix(ff, t, cik))
        crp, basis = country_risk.crp_for(t, ff.country, table, mix=mix, source=source)
        if crp is not None:
            ff.set("crp", round(crp, 5), "damodaran-ctryprem")
            ff.set("crp_basis", basis, "damodaran-ctryprem")
        if crp is None or "holding jurisdiction" in basis:
            ff.flags.append(f"country risk: {basis}")
    except Exception as e:
        log.warning("%s country risk lookup failed: %s", t, e)


MIN_PEERS = 3


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
        # one or two names from the user's own list are an anecdote, not a sector
        if len(peers) >= MIN_PEERS:
            peers.sort()
            n = len(peers)
            out[t] = peers[n // 2] if n % 2 else (peers[n // 2 - 1] + peers[n // 2]) / 2
    return out


def fetch_fundamentals(tickers, fmp_key="", quota_used=0, quota_cap=250, prev_facts=None,
                       prev_results=None, refreshed_at=None):
    """NETWORK PHASE, run OUTSIDE store.LOCK. Tickers are analysed in parallel:
    analyze() is network+pure, the SEC throttle is global, and the FMP quota is
    partitioned up front. Returns (fetched {ticker: (FinancialFacts, Valuation)},
    errors, fmp_calls, rf_pct, rf_live). `prev_facts` {ticker: facts dict} is the
    store's last refresh, read-only, for the 30-day last-good consensus; `prev_results`
    the same for the weekly peer fit."""
    rf, rf_live = yahoo.fetch_treasury_10y()
    refresh_implied_erp()
    roc = fetch_roc_table()
    fetched, calls = {}, 0
    cost = batch.guaranteed_cost(fmp_key)
    tickers = batch.refresh_order(tickers, prev_facts or {}, refreshed_at or {})
    todo, errors = batch.partition_by_quota(tickers, cost, quota_used, quota_cap)
    # whatever the guaranteed spend does not claim, the optional fallback may draw on
    spare = batch.Budget(max(0, batch.usable_quota(quota_used, quota_cap) - len(todo) * cost)) if fmp_key else None
    prev_facts = prev_facts or {}
    futs = batch.run_parallel(
        lambda t: analyze(t, rf, fmp_key, rf_live, roc, spare,
                          **({"prev": prev_facts[t]} if prev_facts.get(t) else {})), todo)
    for t in todo:
        try:
            ff, val, c = futs[t].result()
            calls += c
            fetched[t] = (ff, val)
        except Exception as e:
            log.warning("%s fundamentals failed: %s", t, e)
            errors.append(f"{t}: {str(e)[:60]}")
    if fmp.LEDGER.exhausted():
        # live 2026-10-25: past the limit FMP answers 429 and every FMP field went
        # quietly to its Yahoo or last-good fallback
        for ff, _v in fetched.values():
            ff.flags.append("FMP เกินโควตาวันนี้ระหว่างรอบนี้ — ช่องที่มาจาก FMP ใช้ Yahoo/ข้อมูลรอบก่อน "
                            "(ดูแหล่งในวงเล็บของแต่ละช่อง) " + batch.RETRY_MARK)
    # peer-median injection re-scores the affected names; the engine is pure
    for t, med in compute_peer_medians(fetched).items():
        ff, _val = fetched[t]
        ff.set("peer_median_growth", round(med, 4), "sector-cohort")
        fetched[t] = (ff, get_engine().evaluate(ff, rf=rf))
    calls += _attach_relative(fetched, fmp_key, spare, prev_results or {})
    return fetched, errors, calls, round(rf * 100, 2), rf_live


def _attach_relative(fetched, fmp_key, budget, prev_results, today=None):
    """Peer relative valuation on each fetched ticker (cross-check only). One failure
    never fails the refresh: the ticker gets no block and a log line."""
    today = today or lastgood.date_of(pricing.ny_today())
    memo, calls = peers.PointMemo(), 0
    universe = peers.Universe.lazy(today)
    futs = batch.run_parallel(lambda t: peers.relative_value(
        fetched[t][0], fmp_key, today, (prev_results.get(t) or {}).get("relative"), budget, memo,
        universe), list(fetched))
    for t, fut in futs.items():
        try:
            block, c = fut.result()
        except Exception as e:
            log.warning("%s relative valuation failed: %s", t, e)
            continue
        calls += c
        fetched[t][1].relative = block
    return calls


def commit_fundamentals(s, fetched, skipped=None, now=None):
    """STORE PHASE, fast merge of fetched results. Caller must hold store.LOCK.
    `skipped` {ticker: reason} this run did not reach: their card keeps the
    last run's figures and says so, instead of showing them as today's."""
    today = store_mod.today()
    now = now or store_mod.now_iso()
    at, skip = s.setdefault("refreshed_at", {}), s.setdefault("skipped", {})
    for t, (ff, val) in fetched.items():
        s["facts"][t] = ff.to_dict()
        s["results"][t] = val.to_dict()
        s["updated"][t] = today
        at[t] = now
        skip.pop(t, None)
        # build-forward revenue beat/miss history (persisted, holdings only)
        rev_track.update(s, t, ff.rev_estimate_curq, ff.revenue_quarters, today)
    for t, why in (skipped or {}).items():
        if t in s["facts"]:
            skip[t] = {"at": now, "reason": why}
    store_mod.sync_fmp_usage(s)
    return s
