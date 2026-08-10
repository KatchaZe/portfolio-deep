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
          # v8.2 additions
          "cfo", "total_assets", "receivables", "interest_expense", "rnd_expense",
          "equity_prior", "total_debt_prior", "cash_prior",
          # round 2 additions
          "acquisitions_net", "deferred_revenue", "deferred_revenue_prior",
          # P1-5 (+ REV-4 prior-year lease)
          "operating_leases", "operating_leases_prior",
          # REV-1: SBC is a currency amount too — it was absent here as well, so an
          # IFRS filer would have compared DKK SBC against USD revenue.
          "sbc",
          # REV-18/19: working-capital components + prior AR
          "receivables_prior", "inventory", "inventory_prior",
          "accounts_payable", "accounts_payable_prior")

# T5: [[FY_end, value], …] money series — converted element-wise, keeping the date.
# `eps_annuals_dated` is deliberately NOT here: it is a per-share figure paired with a
# per-share PRICE in the own-5y-P/E percentile, and converting one side of that ratio
# without the other would break it.
_MONEY_SERIES_DATED = ("revenue_annuals_dated", "operating_income_annuals_dated",
                       "cfo_annuals_dated", "capex_annuals_dated",
                       "gross_profit_annuals_dated", "cost_of_revenue_annuals_dated")


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
            if ff.revenue_annuals:
                ff.revenue_annuals = [v * fx_rate for v in ff.revenue_annuals]
            if ff.revenue_quarters:
                ff.revenue_quarters = {k: v * fx_rate for k, v in ff.revenue_quarters.items()}
            if ff.operating_income_quarters:
                ff.operating_income_quarters = {k: v * fx_rate for k, v in ff.operating_income_quarters.items()}
            for _ls in ("rnd_annuals", "operating_income_annuals"):
                _v = getattr(ff, _ls)
                if _v:
                    setattr(ff, _ls, [x * fx_rate for x in _v])
            # T5: the dated series are money too. Missing them here would leave an
            # IFRS filer's trend strip in DKK/EUR while the card reads USD — the same
            # unit mismatch REV-1 found on SBC. Ratios (margins, CAGR) are unaffected
            # by a constant spot rate, but the absolute FCF bar would be wrong.
            for _ds in _MONEY_SERIES_DATED:
                _v = getattr(ff, _ds, None)
                if _v:
                    setattr(ff, _ds, [[d, x * fx_rate] for d, x in _v])
            # invested-capital legs are money too. ROIC is a ratio so the rate cancels,
            # but leaving them unconverted would make the legs inconsistent with the
            # operating income they are divided into once THAT has been converted.
            if getattr(ff, "ic_components_dated", None):
                ff.ic_components_dated = {
                    d: {k: (v * fx_rate if isinstance(v, (int, float)) else v)
                        for k, v in comp.items()}
                    for d, comp in ff.ic_components_dated.items()}
            # P1-4 (S5 consistency): ONE spot rate for every period — ratios
            # (growth, margins) equal LOCAL-currency figures (FX cancels out);
            # only absolute USD levels of past years are approximate.
            ff.flags.append(f"converted {ff.currency}->USD @ {round(fx_rate, 4)} "
                            f"(constant spot: growth/margins = local-currency)")
            ff.provenance["fx"] = f"{ff.currency}->USD {round(fx_rate,4)}"
            ff.currency = "USD"
        else:
            ff.flags.append(f"non-USD ({ff.currency}) and no FX rate — values not converted")

    # 3) FMP profile (sector/beta/price)
    if fmp_profile:
        p = fmp.parse_profile(fmp_profile)
        ff.set("sector", p.get("sector"), "fmp")
        ff.set("beta", p.get("beta"), "fmp")
        ff.set("price", p.get("price"), "fmp")
        if not ff.company:
            ff.set("company", p.get("company"), "fmp")

    # 4) Yahoo consensus + fallbacks
    if yahoo_qs:
        # surface a degraded Yahoo response (datacenter IPs are often blocked)
        _res = (yahoo_qs.get("quoteSummary") or {}).get("result")
        if yahoo_qs.get("_error") or not _res:
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

    if ff.growth_lt is None:
        ff.set("growth_lt", _annual_cagr(ff.revenue_annuals), "sec-cagr(min 3y/5y)")

    _resolve_shares(ff)
    _score(ff)
    return ff


# T3: how far price x shares may sit from the reported market cap before we call it
# a unit mismatch. Genuine causes of a small gap (different share-count vintage,
# buybacks between the filing and the quote) are a few percent; an ADR ratio or an
# unadjusted split is a whole multiple.
MCAP_TOLERANCE = 0.20


def _resolve_shares(ff):
    """Share count in the SAME unit as `price` (T2/T3).

    The SEC always reports ORDINARY shares. For a US filer that is also what the
    price refers to, so price x shares == market cap and nothing else is needed.
    For a depositary listing it is not: TSM files 25.9B ordinary shares while the
    quote is per ADR worth five of them, and multiplying the two overstates market
    cap 5x — which would then corrupt the WACC weights and enterprise value.

    Rather than maintain a hand-written ADR-ratio table (a constant that fails
    silently when wrong — the exact failure mode this codebase keeps hitting), the
    count is DERIVED from two numbers that are already in the same unit:

        shares = market_cap / price

    Market cap is quoted for the US-listed security, so the depositary ratio is
    already inside it. Verified against saved fixtures: this reproduces the
    reported share count to 0.000% for ordinary US listings."""
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
    """Long-term growth input (P-E). Was: CAGR over the WHOLE available history,
    whose length varies per ticker and which happily measures straight through a
    structural break — PFE came out at +8.5% (2020 pre-COVID base -> 2025) while
    its 3y CAGR was -14.8% and revenue was shrinking; TSLA 34.6% vs 3y +5.2%.
    The long window also DILUTES genuine accelerations (LLY 7.2% vs 3y +31.7%).

    Now: the more conservative of the 3y and 5y CAGR, floored at 0. Damodaran's
    own research shows past growth predicts future growth weakly, and growth is
    where optimism concentrates — so when two readings disagree, take the lower.
    (The theoretically right input is fundamental growth = reinvestment x ROIC;
    it is computed in the engine as a divergence CHECK only, because capex is
    missing for several filers and the capex-based formula badly understates
    asset-light R&D spenders. See deep_v82.fundamental_growth.)"""
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
    # forward EPS plausibility (R1: the SAME gate validate uses to substitute the
    # value — a second hand-rolled copy here had its own hardcoded 0.65 and no
    # currency/P-E check, so a rejected consensus could still score as trustworthy)
    from pipeline.validate import forward_eps_rejection      # local: avoids an import cycle
    _why = forward_eps_rejection(ff.forward_eps, ff.revenue, ff.shares_diluted,
                                 ff.price, ff.growth_lt)
    if _why:
        score -= 15
        ff.flags.append(f"forward_eps {round(ff.forward_eps, 2)} implausible: {_why}")
    ff.confidence = max(0, min(100, score))
    return ff
