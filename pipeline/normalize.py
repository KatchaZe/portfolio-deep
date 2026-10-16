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
        ff.set("growth_lt", _annual_cagr(ff.revenue_annuals), HISTORICAL_GROWTH)

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
    ff.set("growth_lt", g, "yahoo" if y.get("growth_lt") is not None else HISTORICAL_GROWTH)


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


HISTORICAL_GROWTH = "sec-cagr(min 3y/5y)"


def resolve_growth(ff):
    """The growth the engine values on must look FORWARD (Damodaran: price is a bet on
    the future, past growth predicts it weakly). Order: Yahoo analyst long-term growth,
    else the FMP consensus revenue path (geometric mean of its near and far years),
    else the historical revenue CAGR, labelled as such and never above what consensus
    forward EPS implies. Before this, all 21 holdings valued on history: NVO at 19.5%
    while guiding sales -5% to -13% and consensus EPS below trailing."""
    if ff.provenance.get("growth_lt") == "yahoo" and ff.growth_lt is not None:
        return
    near, far = ff.fwd_growth_near, ff.fwd_growth_far
    if near is not None:
        far = near if far is None else far
        g = ((1 + near) * (1 + far)) ** 0.5 - 1 if (1 + near) > 0 and (1 + far) > 0 else min(near, far)
        ff.set("growth_lt", g, "fmp/estimates (consensus revenue path)")
        return
    if ff.growth_lt is None:
        return
    ff.provenance["growth_lt"] = HISTORICAL_GROWTH
    ff.flags.append(f"growth {ff.growth_lt*100:.1f}% is HISTORICAL revenue CAGR - no forward "
                    f"consensus growth available")
    eps0, fwd = ff.eps_gaap, ff.forward_eps
    if eps0 and fwd and eps0 > 0 and fwd > 0:
        implied = fwd / eps0 - 1
        if implied < ff.growth_lt:
            capped = max(0.0, implied)
            ff.flags.append(f"growth capped {ff.growth_lt*100:.1f}% -> {capped*100:.1f}%: consensus "
                            f"forward EPS {fwd:.2f} vs trailing {eps0:.2f} implies {implied*100:+.1f}%")
            ff.set("growth_lt", capped, HISTORICAL_GROWTH + " capped by consensus EPS")


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
