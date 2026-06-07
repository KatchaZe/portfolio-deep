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
          "equity", "capex", "dep_amort", "income_before_tax", "tax_expense")


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
            ff.flags.append(f"converted {ff.currency}->USD @ {round(fx_rate, 4)}")
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
        y = yahoo.parse_consensus(yahoo_qs)
        es = yahoo.parse_earnings_history(yahoo_qs)
        if es:
            ff.set("earnings_surprises", es, "yahoo")
        ff.set("forward_eps", y.get("forward_eps"), "yahoo")
        if ff.beta is None:
            ff.set("beta", y.get("beta"), "yahoo")
        if ff.price is None:
            ff.set("price", y.get("price"), "yahoo")
        if ff.shares_diluted is None:                 # e.g. NVO (SEC shares missing)
            ff.set("shares_diluted", y.get("shares"), "yahoo")
        # long-term growth: Yahoo estimate, else SEC annual revenue CAGR
        g = y.get("growth_lt")
        if g is None:
            g = _annual_cagr(ff.revenue_annuals)
        ff.set("growth_lt", g, "yahoo" if y.get("growth_lt") is not None else "sec-cagr")

    if ff.growth_lt is None:
        ff.set("growth_lt", _annual_cagr(ff.revenue_annuals), "sec-cagr")

    _score(ff)
    return ff


def _annual_cagr(annuals):
    if not annuals or len(annuals) < 2 or not annuals[-1] or annuals[-1] <= 0:
        return None
    yrs = len(annuals) - 1
    try:
        return (annuals[0] / annuals[-1]) ** (1 / yrs) - 1
    except Exception:
        return None


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
    # forward EPS plausibility vs revenue capacity (catches unsplit/bad consensus)
    if ff.forward_eps and ff.revenue and ff.shares_diluted:
        ceiling = ff.revenue / ff.shares_diluted * 0.65
        if ff.forward_eps > ceiling:
            score -= 15
            ff.flags.append(f"forward_eps {round(ff.forward_eps,2)} > rev ceiling {round(ceiling,2)} (likely unsplit/bad)")
    ff.confidence = max(0, min(100, score))
    return ff
