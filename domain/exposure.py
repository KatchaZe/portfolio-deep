"""
Where the portfolio's cash flows come from (Damodaran S2: cash-flow risk, not price
risk). An ADR is priced in USD while ASML, TSM and NVO earn in EUR, TWD and DKK, so the
listing currency says nothing about currency exposure. Exposure is read off the same
revenue mix the valuation prices country risk on (sources/country_risk), so the Risk Desk
and the fair value describe one business. Pure: the caller resolves the mixes.
"""
from sources.country_risk import ISO2, REGIONS, _country_key

# country (Damodaran-table key, lower case) -> ISO currency
_CCY = {
    "united states": "USD", "china": "CNY", "taiwan": "TWD", "japan": "JPY",
    "united kingdom": "GBP", "switzerland": "CHF", "denmark": "DKK", "sweden": "SEK",
    "norway": "NOK", "canada": "CAD", "brazil": "BRL", "mexico": "MXN", "argentina": "ARS",
    "korea": "KRW", "india": "INR", "singapore": "SGD", "hong kong": "HKD",
    "australia": "AUD", "uruguay": "UYU", "colombia": "COP", "chile": "CLP", "peru": "PEN",
    "israel": "ILS", "poland": "PLN", "turkey": "TRY", "south africa": "ZAR",
    "saudi arabia": "SAR", "united arab emirates": "AED", "indonesia": "IDR",
    "thailand": "THB", "malaysia": "MYR", "philippines": "PHP", "vietnam": "VND",
    "new zealand": "NZD",
}
_EURO = {"germany", "france", "italy", "spain", "netherlands", "belgium", "austria",
         "finland", "ireland", "portugal", "greece", "luxembourg", "slovakia", "slovenia",
         "estonia", "latvia", "lithuania", "malta", "cyprus", "croatia"}
# a region is a basket of currencies, named for what it holds
_REGION_CCY = {
    "western europe": "EUR + other Europe", "emea": "EUR + other EMEA",
    "latin america": "LatAm currencies", "americas ex us": "LatAm / CAD",
    "emerging markets": "EM currencies", "asia pacific": "APAC currencies",
    "middle east": "Middle East currencies", "africa": "African currencies",
    "rest of world": "Other non-USD",
}


def market_name(market):
    """'TW' / 'Taiwan' / 'rest of world' -> one display key (country name or region)."""
    name = ISO2.get((market or "").upper(), market or "")
    key = _country_key(name)
    if key in REGIONS:
        return "EMEA" if key == "emea" else key[0].upper() + key[1:]
    return name.strip() or "Unknown"


def market_currency(market):
    name = ISO2.get((market or "").upper(), market or "")
    key = _country_key(name)
    if key in _EURO:
        return "EUR"
    if key in _CCY:
        return _CCY[key]
    if key in _REGION_CCY:
        return _REGION_CCY[key]
    return "Other"


def _normalized(mix):
    tot = sum(v for v in (mix or {}).values() if v and v > 0)
    return {m: v / tot for m, v in mix.items() if v and v > 0} if tot > 0 else {}


def revenue_exposure(weights, mixes):
    """weights {ticker: w}; mixes {ticker: {market: weight}} (any scale, normalized here;
    a ticker with no mix counts as 'Unknown'). Returns (by_currency, by_region,
    non_us_share {ticker: 0..1 or None}); groups are [{label, value(%)}] descending."""
    ccy, reg, non_us = {}, {}, {}
    for t, w in weights.items():
        mix = _normalized(mixes.get(t))
        if not mix:
            ccy["Unknown"] = ccy.get("Unknown", 0.0) + w
            reg["Unknown"] = reg.get("Unknown", 0.0) + w
            non_us[t] = None
            continue
        us = 0.0
        for m, share in mix.items():
            c, r = market_currency(m), market_name(m)
            ccy[c] = ccy.get(c, 0.0) + w * share
            reg[r] = reg.get(r, 0.0) + w * share
            if c == "USD":
                us += share
        non_us[t] = round(1 - us, 4)

    def rows(d):
        return sorted([{"label": k, "value": round(v * 100, 1)} for k, v in d.items() if v > 0],
                      key=lambda x: -x["value"])
    return rows(ccy), rows(reg), non_us


def portfolio_crp(weights, crps):
    """Σ w·CRP over the names with a CRP on file, as a percent of the whole book, and
    the weight that had none (so a missing CRP never reads as zero risk)."""
    have = {t: w for t, w in weights.items() if crps.get(t) is not None}
    missing = round(max(0.0, 1 - sum(have.values())) * 100, 1) if weights else None
    val = sum(w * crps[t] for t, w in have.items())
    return {"crp_pct": round(val * 100, 2), "weight_without_crp_pct": missing}
