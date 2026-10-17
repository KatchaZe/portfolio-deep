"""
Normalize — merge the free stack into one FinancialFacts:

  SEC EDGAR  -> financials (authoritative, all symbols)        [source: sec]
  FMP profile-> sector, beta, price (all symbols on free tier)  [source: fmp]
  Yahoo      -> forward EPS (adjusted consensus), growth, beta/price/shares fallback [source: yahoo]

Handles non-USD filers (NVO/DKK) via an FX rate, records provenance per field,
and assigns a basic confidence score. Deeper cross-checks live in validate.py.
"""
from domain.facts import FinancialFacts
from sources import sec_edgar, fmp, yahoo

# monetary fields converted when the filer reports in a non-USD currency
_MONEY = ("revenue", "operating_income", "net_income", "total_debt", "cash",
          "equity", "capex", "dep_amort", "income_before_tax", "tax_expense",
          "cfo", "total_assets", "receivables", "interest_expense", "rnd_expense",
          "equity_prior", "total_debt_prior", "cash_prior",
          "acquisitions_net", "deferred_revenue", "deferred_revenue_prior",
          "operating_leases", "operating_leases_prior",
          "sbc",
          "receivables_prior", "inventory", "inventory_prior",
          "accounts_payable", "accounts_payable_prior",
          # Scalar eps_gaap is divided into the USD listed price, so both sides of that
          # P/E must be USD (left in TWD/DKK it reads ~7x too cheap). eps_annuals_dated
          # is NOT converted: it pairs with a LOCAL-currency 5y price percentile.
          "eps_gaap")

# [[FY_end, value], …] money series — converted element-wise, keeping the date.
# `eps_annuals_dated` is deliberately NOT here (see eps_gaap above).
_MONEY_SERIES_DATED = ("revenue_annuals_dated", "operating_income_annuals_dated",
                       "cfo_annuals_dated", "capex_annuals_dated", "sbc_annuals_dated",
                       "gross_profit_annuals_dated", "cost_of_revenue_annuals_dated")


def _fx_each(values, fx):
    return [v * fx for v in values]


def _fx_by_period(values, fx):
    return {k: v * fx for k, v in values.items()}


def _fx_dated(values, fx):
    return [[d, v * fx] for d, v in values]


def _fx_ic(values, fx):
    return {d: {k: (v * fx if isinstance(v, (int, float)) else v) for k, v in comp.items()}
            for d, comp in values.items()}


def build(ticker, sec_companyfacts=None, fmp_profile=None, yahoo_qs=None, fx_rate=None,
          company=None):
    ff = FinancialFacts(ticker, company=company)

    # 1) SEC financials (primary)
    if sec_companyfacts:
        sec_edgar.populate(ff, sec_companyfacts)

    # 2) currency normalization -> USD
    if ff.currency and ff.currency != "USD":
        if fx_rate:
            for k in _MONEY:
                v = getattr(ff, k)
                if isinstance(v, (int, float)):
                    setattr(ff, k, v * fx_rate)
            # Series are money too: an unconverted trend strip or invested-capital leg
            # would sit in DKK/EUR beside USD operating income.
            for fields, convert in (
                    (("revenue_annuals", "rnd_annuals", "operating_income_annuals"), _fx_each),
                    (("revenue_quarters", "operating_income_quarters"), _fx_by_period),
                    (_MONEY_SERIES_DATED, _fx_dated),
                    (("ic_components_dated",), _fx_ic)):
                for k in fields:
                    v = getattr(ff, k, None)
                    if v:
                        setattr(ff, k, convert(v, fx_rate))
            # ONE spot rate for every period: ratios (growth, margins) equal the
            # local-currency figures; only absolute USD levels of past years are approximate.
            ff.flags.append(f"converted {ff.currency}->USD @ {round(fx_rate, 4)} "
                            f"(constant spot: growth/margins = local-currency)")
            ff.provenance["fx"] = f"{ff.currency}->USD {round(fx_rate,4)}"
            ff.currency = "USD"
        else:
            ff.flags.append(f"non-USD ({ff.currency}) and no FX rate — values not converted")

    # 3) FMP profile (sector/beta/price)
    if fmp_profile:
        _apply_fmp_profile(ff, fmp_profile)

    # 4) Yahoo consensus + fallbacks
    if yahoo_qs:
        _apply_yahoo(ff, yahoo_qs)
    if ff.growth_lt is None:
        ff.set("growth_lt", _annual_cagr(ff.revenue_annuals), "sec-cagr(min 3y/5y)")

    _resolve_shares(ff)
    _score(ff)
    return ff


def _apply_fmp_profile(ff, fmp_profile):
    p = fmp.parse_profile(fmp_profile)
    ff.set("sector", p.get("sector"), "fmp")
    ff.set("beta", p.get("beta"), "fmp")
    ff.set("price", p.get("price"), "fmp")
    if not ff.company:
        ff.set("company", p.get("company"), "fmp")


def _apply_yahoo(ff, yahoo_qs):
    """Consensus forward EPS, surprises, and beta/price/market-cap/growth fallbacks."""
    # surface a degraded Yahoo response (datacenter IPs are often blocked)
    res = (yahoo_qs.get("quoteSummary") or {}).get("result")
    if yahoo_qs.get("_error") or not res:
        ff.flags.append("yahoo unavailable — consensus (fwd EPS) / momentum may be degraded")
    y = yahoo.parse_consensus(yahoo_qs)
    es = yahoo.parse_earnings_history(yahoo_qs)
    if es:
        ff.set("earnings_surprises", es, "yahoo")
    rq = yahoo.parse_revenue_estimate(yahoo_qs)
    if rq:
        ff.set("rev_estimate_curq", rq, "yahoo")
    ff.set("forward_eps", y.get("forward_eps"), "yahoo")
    if ff.beta is None:
        ff.set("beta", y.get("beta"), "yahoo")
    if ff.price is None:
        ff.set("price", y.get("price"), "yahoo")
    if ff.market_cap is None:
        ff.set("market_cap", y.get("market_cap"), "yahoo")
    # long-term growth: Yahoo estimate, else SEC annual revenue CAGR
    g = y.get("growth_lt")
    if g is None:
        g = _annual_cagr(ff.revenue_annuals)
    ff.set("growth_lt", g, "yahoo" if y.get("growth_lt") is not None else "sec-cagr(min 3y/5y)")


# How far price x shares may sit from the reported market cap before we call it
# a unit mismatch. Genuine causes of a small gap (different share-count vintage,
# buybacks between the filing and the quote) are a few percent; an ADR ratio or an
# unadjusted split is a whole multiple.
MCAP_TOLERANCE = 0.20


def _resolve_shares(ff):
    """Share count in the SAME unit as `price`.

    The SEC reports ORDINARY shares; an ADR quote is per depositary share (TSM: one
    ADR = five ordinary shares, so price x SEC shares overstates market cap 5x and
    corrupts WACC weights and EV). Instead of a hand-written ADR-ratio table, derive
    the count from two numbers already in the same unit: shares = market_cap / price.
    For ordinary US listings this reproduces the reported count exactly."""
    mcap, price = ff.market_cap, ff.price
    derived = (mcap / price) if (mcap and price and price > 0) else None

    if ff.shares_diluted is None:
        if derived:
            ff.set("shares_diluted", round(derived), "derived (market cap / price)")
        return

    if not derived:
        return
    # both available: they must agree, or one of them is in the wrong unit
    gap = abs(ff.shares_diluted / derived - 1)
    if gap > MCAP_TOLERANCE:
        ratio = ff.shares_diluted / derived
        ff.flags.append(
            f"share-unit mismatch: SEC {ff.shares_diluted/1e9:.2f}B shares x price "
            f"{price} = {ff.shares_diluted*price/1e9:.0f}B vs reported market cap "
            f"{mcap/1e9:.0f}B (implied ratio {ratio:.2f}x - ADR or unadjusted split); "
            f"using the market-cap-derived count")
        ff.set("shares_diluted", round(derived), "derived (market cap / price, SEC count rejected)")


def _cagr_over(annuals, yrs):
    """Revenue CAGR over exactly `yrs` years, or None if the history is too short."""
    if not annuals or len(annuals) <= yrs or not annuals[yrs] or annuals[yrs] <= 0:
        return None
    try:
        return (annuals[0] / annuals[yrs]) ** (1 / yrs) - 1
    except Exception:
        return None


def _annual_cagr(annuals):
    """Long-term growth input: the lower of the 3y and 5y revenue CAGR, floored at 0.

    Not the whole-history CAGR, which measures straight through structural breaks
    (PFE +8.5% while shrinking). Past growth predicts future growth weakly and growth
    is where optimism concentrates, so when two readings disagree take the lower.
    Fundamental growth (reinvestment x ROIC) is only a divergence check in the engine,
    because capex is missing for several filers."""
    g3, g5 = _cagr_over(annuals, 3), _cagr_over(annuals, 5)
    cands = [g for g in (g3, g5) if g is not None]
    if not cands:
        # too little history for either window — fall back to the full span
        if not annuals or len(annuals) < 2 or not annuals[-1] or annuals[-1] <= 0:
            return None
        try:
            return max(0.0, (annuals[0] / annuals[-1]) ** (1 / (len(annuals) - 1)) - 1)
        except Exception:
            return None
    return max(0.0, min(cands))


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


def _score(ff):
    """Basic confidence 0-100 + flags (deeper checks in validate.py)."""
    score = 100
    for fld in ("revenue", "net_income", "shares_diluted", "price"):
        if getattr(ff, fld) is None:
            score -= 20
            ff.flags.append(f"missing {fld}")
    if ff.forward_eps is None:
        score -= 10
        ff.flags.append("missing forward_eps")
    if any(f.startswith("converted") for f in ff.flags):
        score -= 15            # FX uncertainty
    # forward EPS plausibility: the SAME gate validate uses to substitute the value,
    # so a rejected consensus can never still score as trustworthy
    from pipeline.validate import forward_eps_rejection      # local: avoids an import cycle
    _why = forward_eps_rejection(ff.forward_eps, ff.revenue, ff.shares_diluted,
                                 ff.price, ff.growth_lt)
    if _why:
        score -= 15
        ff.flags.append(f"forward_eps {round(ff.forward_eps, 2)} implausible: {_why}")
    ff.confidence = max(0, min(100, score))
    return ff
