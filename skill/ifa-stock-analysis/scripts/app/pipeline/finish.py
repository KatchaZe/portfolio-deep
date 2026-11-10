"""
The offline half of a fundamentals refresh: everything after the fetches.

Industry and country inputs from Damodaran's tables (live, else the disk cache, else
the bundled snapshot), the one-time add-back, validation, and the growth the valuation
runs on. The app's refresh (pipeline/fundamentals.py) and the ifa-stock-analysis
skill (skill/ifa-stock-analysis/scripts/value.py) both call finish(), so a stock
valued in chat and on the dashboard goes through the same steps.

Imports stay within config / domain / sources.damodaran*, sources.industries,
sources.country_risk: tools/build_skill.py copies this module's import closure into
the skill, and the skill has no API keys.
"""
import logging

import config
from domain import one_time
from pipeline import validate
from sources import damodaran

log = logging.getLogger("portfolio.finish")


def finish(ff, t, rf, roc_table, filing_mix=None):
    """Steps that need no API key, in the order the refresh has always run them."""
    industry(ff, t)
    terminal_roic(ff, t, roc_table)
    industry_beta(ff, t)
    industry_operating(ff)
    country_risk(ff, t, filing_mix)
    one_time.normalize_operating_income(ff)
    validate.validate(ff, rf=rf)
    resolve_growth(ff)


def fetch_roc_table():
    """Damodaran's industry ROIC table: fetched once per refresh and shared by every
    ticker. Disk-cached 30 days with a bundled snapshot fallback, never fatal."""
    return damodaran.fetch_roc_table(cache_dir=config.CACHE_DIR,
                                     user_agent=config.SEC_USER_AGENT)


def industry(ff, t):
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


def terminal_roic(ff, t, roc_table):
    """Excess returns fade toward what the INDUSTRY sustains (Damodaran), not a
    flat ceiling."""
    try:
        sec_roic = damodaran.terminal_roic_for(t, ff.sector, roc_table, fmp_industry=ff.industry)
        if sec_roic:
            ff.set("terminal_roic_sector", round(sec_roic, 4), "damodaran-roc")
    except Exception as e:
        log.warning("%s terminal ROIC lookup failed: %s", t, e)


def industry_beta(ff, t):
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


def industry_operating(ff):
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


def country_risk(ff, t, filing_mix=None):
    """Country risk premium over the US, weighted by where the revenue is earned
    (config / dashboard / latest 10-K), else by the FMP domicile. `filing_mix` is a
    callable returning the 10-K revenue mix (SEC, so the app's refresh supplies it)."""
    from sources import country_risk
    try:
        table = country_risk.fetch_crp_table(cache_dir=config.CACHE_DIR,
                                            user_agent=config.SEC_USER_AGENT)
        mix, source = country_risk.revenue_mix(t)
        if mix is None:
            mix, source = country_risk.revenue_mix(t, filing_mix() if filing_mix else None)
        crp, basis = country_risk.crp_for(t, ff.country, table, mix=mix, source=source)
        if crp is not None:
            ff.set("crp", round(crp, 5), "damodaran-ctryprem")
            ff.set("crp_basis", basis, "damodaran-ctryprem")
            ff.set("crp_mix", dict(mix) if mix else None, "damodaran-ctryprem")
            ff.set("crp_mix_source", source if mix else None, "damodaran-ctryprem")
        if crp is None or "holding jurisdiction" in basis:
            ff.flags.append(f"country risk: {basis}")
    except Exception as e:
        log.warning("%s country risk lookup failed: %s", t, e)


# A consensus revenue path needs this many analysts behind its first year before it
# replaces history as the valuation growth; one or two estimates is an opinion.
MIN_ANALYSTS_FOR_PATH = 3
HISTORICAL_GROWTH = "sec-cagr(min 3y/5y)"


def resolve_growth(ff):
    """The growth the valuation runs on: forward-looking first, history last.

    Damodaran ranks analysts' forecasts and fundamentals ahead of history, and the
    framework's own rule is forward over backward. Measured 2026-08-18: all 21 names
    were valued on historical revenue CAGR, NVO on 19.5% while its 2026 guidance was a
    5-13% sales decline. Ladder:
      1. Yahoo +5y analyst growth (already set by normalize), or FMP's own estimate.
      2. FMP consensus revenue: the mean of next-FY growth (vs the last actual year) and
         the year after, each used only with >= MIN_ANALYSTS_FOR_PATH analysts. The
         final, thinly covered out-year is not used.
      3. Otherwise historical revenue CAGR, CAPPED by the one-year EPS growth analysts
         expect (forward EPS / core trailing EPS - 1, floored at 0). Continuous: a
         few cents of consensus revision moves growth a little, never 0% <-> 19.5%.
         The pair mixes adjusted and GAAP bases, so it may only pull growth down."""
    if ff.provenance.get("growth_lt") not in (None, HISTORICAL_GROWTH):
        return
    path = [g for g, n in ((ff.fwd_growth_y1, ff.n_analysts), (ff.fwd_growth_near, ff.n_analysts_y2))
            if g is not None and (n or 0) >= MIN_ANALYSTS_FOR_PATH]
    if path:
        g = sum(path) / len(path)
        ff.set("growth_lt", round(g, 4),
               "consensus revenue path (fmp, " + " / ".join(f"{x:+.1%}" for x in path)
               + f", {ff.n_analysts} analysts)")
        return
    hist = ff.growth_lt
    fwd, core = ff.forward_eps, _core_eps(ff)
    cap = None
    if (fwd is not None and core and core > 0
            and not str(ff.provenance.get("forward_eps", "")).startswith("sec-derived")):
        cap = max(0.0, fwd / core - 1)
    if hist is not None and cap is not None and cap < hist:
        ff.set("growth_lt", round(cap, 4),
               f"historical CAGR capped by forward/core EPS ({fwd:.2f}/{core:.2f})")
        ff.flags.append(f"growth {cap:.1%}: historical revenue CAGR {hist:.1%} capped by the one-year "
                        f"EPS growth analysts expect (forward {fwd:.2f} vs core trailing {core:.2f})")
        return
    if hist is not None:
        ff.flags.append(f"growth = historical revenue CAGR {hist:.1%} (no forward estimate found)")


def _core_eps(ff):
    """min(net income, NOPAT) per diluted share: trailing EPS without one-off gains."""
    if not ff.shares_diluted or ff.net_income is None:
        return None
    ni = ff.net_income
    if ff.operating_income is not None:
        ni = min(ni, ff.operating_income * (1 - ff.tax_rate))
    return ni / ff.shares_diluted
