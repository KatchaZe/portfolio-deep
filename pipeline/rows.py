"""
The display row shared by the watchlist and holdings tables. Pure.

Both paths build through `valuation_row` from the STORED shape (facts and
valuation as plain dicts), so a field added for one tab cannot go missing from
the other.
"""
import config
from domain import indicators, costs, pead, trend
from pipeline import margin_track
from pipeline import screen as screen_mod


def earn_status(eps_list, rev_list, mgn_list, provenance, facts_has_new_schema, fmp_on):
    """Why is each earnings track (EPS / Rev / Mgn) filled or empty? Shown as a
    tooltip so a blank row is EXPLAINED instead of silently missing.
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


def valuation_row(t, f, v, mom, v2, *, rev_surprises, has_new_schema, fmp_on):
    """One display row from stored facts `f`, valuation `v`, short momentum `mom`
    and composite momentum `v2`. `rev_surprises` is the revenue beat/miss list the
    PEAD signal and earnings status read. The caller adds `advice` last, after any
    path-specific fields, because advice reads the finished row."""
    price = mom.get("price") or f.get("price")
    anchor = v.get("anchor_value")
    upside = ((anchor - price) / price * 100) if (anchor and price) else None
    upside = round(upside, 1) if upside is not None else None
    # Margin of safety (V - P) / V: the ONE definition the P-score, the gauge, the
    # advice line and the Risk Desk all use. Upside (V - P) / P is the return if price
    # converges and stays on the card as the secondary number.
    mos = round((anchor - price) / anchor * 100, 1) if (anchor and anchor > 0 and price) else None
    rd = v.get("reverse_dcf") or {}
    eps_surprises = f.get("earnings_surprises") or f.get("eps_surprises_backfill")
    mgn_trend = margin_track.build(f.get("operating_income_quarters"), f.get("revenue_quarters"))
    return {
        "ticker": t, "company": f.get("company"), "sector": f.get("sector"),
        "price": price, "change": mom.get("change"),
        "composite": v.get("composite"), "stars": v.get("stars"),
        "recommendation": v.get("recommendation"),
        "anchor_method": v.get("anchor_method"), "anchor_value": anchor,
        "range_low": v.get("range_low"), "range_high": v.get("range_high"),
        "upside_pct": upside, "mos_pct": mos,
        "net_upside_pct": costs.net_upside(upside, config.CAPGAINS_TAX_RATE,
                                           config.TRADING_COST_BPS),
        "momentum_signal": mom.get("momentum_signal"), "rsi": mom.get("rsi"),
        "rsi_signal": mom.get("rsi_signal"), "macd_signal": mom.get("macd_signal"),
        "dbbmv_signal": mom.get("dbbmv_signal"), "momentum_score": mom.get("momentum_score"),
        "momentum_v2": v2 or None,           # primary composite (secondary = fields above)
        "action": indicators.action(v.get("signal"), mom.get("momentum_signal"),
                                    mom_label=(v2 or {}).get("mom_label")),
        "verdict": v.get("verdict"),
        "rev_implied_cagr": rd.get("implied_cagr_pct"), "rev_actual_1y": rd.get("actual_1y_pct"),
        "rev_verdict": rd.get("verdict"),
        # the implied CAGR is only honest next to its margin sensitivity: for a
        # pre-profit filer the terminal margin is assumed, and a +/-5pp guess moves
        # the answer by ~7pp
        "rev_sensitivity": rd.get("sensitivity"),
        "terminal_margin_anchored": (v.get("key_metrics") or {}).get("terminal_margin_anchored"),
        "terminal_margin_label": next((fl for fl in (f.get("flags") or []) if "terminal margin" in fl), None),
        # lets the advice line say "pre-profit" only when it really is one
        "profitable": bool((f.get("net_income") or 0) > 0),
        "confidence": f.get("confidence"), "confidence_tier": f.get("confidence_tier"),
        "currency": f.get("currency"),
        "flags": f.get("flags", []),
        "earnings_surprises": eps_surprises,
        "rev_surprises": rev_surprises,
        "pead": pead.signal(eps_surprises, rev_surprises),
        "margin_trend": mgn_trend,
        "trend5y": trend.build(f) or None,           # 5-year SEC performance strip
        "fv_peg": v.get("fv_peg"), "fv_fvp": v.get("fv_fvp"),
        # model router: FCFF DCF (operating companies) / excess return (financials)
        "fv_dcf": v.get("fv_dcf"), "fv_excess_return": v.get("fv_excess_return"),
        "company_type": v.get("company_type"),
        "dcf": v.get("dcf") or None, "excess_return": v.get("excess_return") or None,
        # pre-profit forward valuation; {} for profitable names
        "young_dcf": v.get("young_dcf") or None,
        "earn_status": earn_status(eps_surprises, rev_surprises, mgn_trend,
                                   f.get("provenance"), has_new_schema, fmp_on),
        "forward_eps": f.get("forward_eps"), "forward_eps_low": f.get("forward_eps_low"),
        "forward_eps_high": f.get("forward_eps_high"),
        "forward_eps_spread_pct": f.get("forward_eps_spread_pct"),
        "forward_eps_n": f.get("forward_eps_n"), "forward_eps_sources": f.get("forward_eps_sources"),
        "eq_verdict": v.get("eq_verdict"),
        "cost_of_equity": v.get("cost_of_equity"),
        "eva": v.get("eva"),
        "key_metrics": v.get("key_metrics"),
        "subscores": v.get("subscores"),
        "garp_score": screen_mod.garp_score(v.get("E_econ"), v.get("P")),
        "garp_candidate": screen_mod.is_candidate(v.get("E_econ"), v.get("P")),
    }
