"""Sector scout fields beyond the v1 fetch, built only from the app's own readers and
engine functions (none re-implemented).

Group 1 (free, SEC companyfacts): per fiscal year OCF, capex, FCF, SBC, D&A,
receivables, revenue, diluted shares and dilution, interest expense, EBIT and EBITDA,
plus the engine's derived metrics on the same FinancialFacts the dashboard values:
the ROIC series (deep_v82._trend_roic_map), incremental ROIC (_incremental_roic, E71 /
E75), margin trend, FCF durability, the earnings-quality checks (_earnings_signals),
net debt / EBITDA and interest coverage (wacc_true's EBIT / interest).

Group 2 (price history, Yahoo then Stooq through pipeline.prices, cached per day):
volatility and the composite (momentum.compute), regression beta vs SPY, the short
indicators (indicators.compute), correlation to each holding (risk.correlation), and
the sector-cap headroom from risk.portfolio.position_sizing. Never FMP (fmp_key "").

Every value is {value, source, reason}."""
import collections

from domain import indicators, momentum, one_time, pricing, trend
from domain.engine import deep_v82 as eng
from domain.risk import correlation, portfolio as riskeng, returns
from pipeline import normalize
from sources import sec_edgar

YEARS = 5
SHARES_TAG = (("WeightedAverageNumberOfDilutedSharesOutstanding", sec_edgar.SHARES),)
DA_TAGS = sec_edgar.FIELD_LADDERS["dep_amort"][1]
HISTORY_RANGE, HISTORY_FULL_BARS, HISTORY_MIN_BARS = "2y", 480, 60
CORR_MIN_DAYS = 120
SHORT_TAIL = 63                  # the app's short indicators read a 3mo Yahoo chart (~63 bars)


def fld(value, source, reason=None):
    return {"value": value, "source": source if value is not None else None,
            "reason": None if value is not None else (reason or "not available")}


def _div(a, b):
    return a / b if a is not None and b not in (None, 0) else None


# --------------------------------------------------------------------------- #
#  Group 1                                                                      #
# --------------------------------------------------------------------------- #
def _per_year(cf, x):
    """{field: {FY end: value}} for the flows and balances the extract keeps only as a
    TTM or a current/prior pair, read by the extract's own ladders and live-tag rule."""
    xb = sec_edgar._Xbrl(cf.get("facts", cf))
    xb.cik = cf.get("cik")
    fy_ends = [e for e, _ in (x.get("operating_income_annuals_dated")
                              or x.get("revenue_annuals_dated") or [])][:12]
    at = sec_edgar._Anchor(x.get("latest_period_end"), fy_ends)
    ar, ar_tag = xb.instant_at_dates(sec_edgar.AR_TAGS, fy_ends)
    return {"da": dict(sec_edgar._trend_series(xb, DA_TAGS, at)),
            "interest_expense": dict(sec_edgar._trend_series(xb, tuple(sec_edgar.INT_EXP), at)),
            "receivables": ar,
            "shares_diluted": _shares_dated(xb, x)}, ar_tag


def _shares_dated(xb, x):
    """{FY end: diluted shares} on today's split basis: the extract's split-adjusted
    shares_diluted_annuals carry the dates of the series they were built from."""
    dates = [e for e, _ in xb.annual_series_dated(SHARES_TAG[0][0], SHARES_TAG[0][1])]
    vals = x.get("shares_diluted_annuals") or []
    return dict(zip(dates, vals)) if len(dates) == len(vals) else {}


def group1(ticker, cf, x, cash_excludes_securities=False):
    """The group-1 block for one member from completed companyfacts `cf` and their
    sec_edgar.extract `x`. Pure: no I/O."""
    ff = normalize.build(ticker, cf, cash_excludes_securities=cash_excludes_securities)
    one_time.normalize_operating_income(ff)
    tax = ff.tax_rate
    flags_before = len(ff.flags)
    rmap = eng._trend_roic_map(ff, tax)
    notes = []
    acq = max(0.0, ff.acquisitions_net) if ff.acquisitions_net is not None else 0.0
    inc, inc_src, declined = eng._incremental_roic(ff, rmap, acq, tax, notes)
    ann = ff.revenue_annuals or []
    rev_g = (ann[0] / ann[1] - 1) if len(ann) > 1 and ann[1] else None
    eq_verdict = eng._earnings_signals(ff, rev_g)
    eq_flags = [f[4:] for f in ff.flags[flags_before:] if f.startswith("EQ: ")]
    rows = trend.build(ff).get("rows") or []
    dur_adj, dur_note = eng.fcf_durability(rows)
    dil, dil_label = eng.share_count_growth(ff.shares_diluted_annuals)

    cfo, capex, sbc = (trend._pairs(x.get(k)) for k in
                       ("cfo_annuals_dated", "capex_annuals_dated", "sbc_annuals_dated"))
    fcf = trend.free_cash_flow(x.get("cfo_annuals_dated"), x.get("capex_annuals_dated"),
                               x.get("sbc_annuals_dated"))
    rev = trend._pairs(x.get("revenue_annuals_dated"))
    oi = trend._pairs(x.get("operating_income_annuals_dated"))
    per, ar_tag = _per_year(cf, x)
    ends = sorted(set(rev) | set(oi), reverse=True)[:YEARS]
    sh = per["shares_diluted"]
    fy = []
    for e in ends:
        prev = next((d for d in sorted(sh, reverse=True) if d < e and 300 <= (_days(d, e)) <= 430), None)
        ebit, da = oi.get(e), per["da"].get(e)
        fy.append({"end": e, "revenue": rev.get(e), "ocf": cfo.get(e), "capex": capex.get(e),
                   "sbc": sbc.get(e), "fcf": fcf.get(e), "da": da, "receivables": per["receivables"].get(e),
                   "interest_expense": per["interest_expense"].get(e), "ebit": ebit,
                   "ebitda": ebit + da if ebit is not None and da is not None else None,
                   "shares_diluted": sh.get(e),
                   "dilution_pct": round((sh[e] / sh[prev] - 1) * 100, 2) if prev and sh.get(e) and sh[prev] else None,
                   "nopat": ebit * (1 - tax) if ebit is not None else None,
                   "roic_pct": round(rmap[e]["roic"], 2) if e in rmap else None})
    src_fy = {
        "revenue": "sec_edgar.extract revenue_annuals_dated", "ocf": "extract cfo_annuals_dated (CFO_TAGS)",
        "capex": "extract capex_annuals_dated (CAPEX_TAGS)", "sbc": "extract sbc_annuals_dated (SBC_TAGS)",
        "fcf": "trend.free_cash_flow = CFO - capex - SBC", "da": "sec_edgar._trend_series(dep_amort ladder)",
        "receivables": f"_Xbrl.instant_at_dates(AR_TAGS) at FY ends ({ar_tag})",
        "interest_expense": "sec_edgar._trend_series(INT_EXP)", "ebit": "extract operating_income_annuals_dated (raw, no add-back)",
        "ebitda": "EBIT + D&A per FY", "shares_diluted": "extract shares_diluted_annuals (split-adjusted) at its FY ends",
        "dilution_pct": "diluted shares FY / prior FY - 1", "nopat": f"EBIT x (1 - tax {tax:.3f}, FinancialFacts.tax_rate)",
        "roic_pct": "deep_v82._trend_roic_map (raw ROIC at FY end)"}

    debt = ff.total_debt
    ebit_ttm, da_ttm, int_ttm = ff.operating_income, ff.dep_amort, ff.interest_expense
    ebitda_ttm = ebit_ttm + da_ttm if ebit_ttm is not None and da_ttm is not None else None
    net_debt = (debt or 0) - ff.cash if ff.cash is not None else None
    f0 = fy[0] if fy else {}
    fcf_conv = _div(f0.get("fcf"), f0.get("nopat")) if (f0.get("nopat") or 0) > 0 else None
    sbc_pct = _div(ff.sbc, ff.revenue)
    fcf_row = next((r for r in rows if r.get("key") == "fcf"), None)
    fcf_fy = (fcf_row.get("points") or [{}])[-1] if fcf_row else {}
    cap_win = [{"end": d, "roic_pct": round(rmap[d]["roic"], 2)} for d in trend._window(rmap, years=YEARS)]
    S = "normalize.build -> FinancialFacts (dashboard path), one_time add-back"
    derived = {
        "tax_rate": fld(tax, "FinancialFacts.tax_rate"),
        "roic_series": fld([{"end": d, "roic_pct": round(v["roic"], 2), "nopat": v["nopat"], "ic": v["ic"]}
                            for d, v in sorted(rmap.items())][-8:] or None,
                           "deep_v82._trend_roic_map (EBIT x (1 - t) / (debt + equity - cash) at FY end)",
                           "no FY with operating income and positive invested capital"),
        "incremental_roic": dict(fld(round(inc * 100, 1) if inc is not None else None,
                                     f"deep_v82._incremental_roic ({inc_src})" if inc_src else "deep_v82._incremental_roic",
                                     "; ".join(notes) or "not measurable"), declined=declined),
        "margin_trend": fld(eng._margin_trend(ff), "deep_v82._margin_trend (op margin FY0 vs FY1, +/-2%)",
                            "fewer than two FY of revenue and operating income"),
        "fcf_durability": dict(fld(dur_adj if dur_note else None, "deep_v82.fcf_durability (trend strip FCF)",
                                   "fewer than 3 consecutive filed FCF years"), note=dur_note),
        "eq": dict(fld(eq_verdict, "deep_v82._earnings_signals (cash conversion, accruals, AR vs revenue)",
                       "earnings quality untested"), flags=eq_flags),
        "receivables_flag": fld(any(f.startswith("receivables") for f in eq_flags) if eq_verdict else None,
                                "deep_v82.earnings_quality receivables test (AR growth > 10% and > revenue growth + 15pp)",
                                "earnings quality untested"),
        "dilution_cagr_pct": fld(round(dil * 100, 2) if dil is not None else None,
                                 f"deep_v82.share_count_growth ({dil_label})" if dil_label else "deep_v82.share_count_growth",
                                 "fewer than 2 years of diluted share counts"),
        "fcf_conversion": fld(round(fcf_conv, 3) if fcf_conv is not None else None,
                              f"FY{str(f0.get('end'))[:4]} FCF (CFO - capex - SBC) / NOPAT",
                              "no FY FCF, or NOPAT not positive"),
        "sbc_pct_revenue": fld(round(sbc_pct * 100, 2) if sbc_pct is not None else None,
                               "TTM SBC / TTM revenue (key_metrics.sbc_pct_of_rev)", "SBC not tagged"),
        "cap_window": fld(cap_win if len(cap_win) >= eng.CAP_MIN_YEARS else None,
                          f"trend._window(roic map, {YEARS}) as deep_v82.competitive_advantage_period",
                          f"fewer than {eng.CAP_MIN_YEARS} contiguous FY with ROIC"),
        "fcf_latest_fy": dict(fld(fcf_fy.get("v"), "trend strip FCF row, latest point (deep_v82.fcf_yield_adj's FCF)",
                                  "fewer than 2 FY of CFO and capex"), end=fcf_fy.get("end")),
        "ttm": {"ocf": fld(ff.cfo, S), "capex": fld(ff.capex, S), "sbc": fld(ff.sbc, S),
                "fcf": fld(ff.cfo - ff.capex - (ff.sbc or 0) if ff.cfo is not None and ff.capex is not None else None,
                           "TTM CFO - capex - SBC", "CFO or capex TTM not filed"),
                "da": fld(da_ttm, S), "interest_expense": fld(int_ttm, S, "no interest expense filed"),
                "ebit": fld(ebit_ttm, S), "ebitda": fld(ebitda_ttm, "TTM EBIT (after add-back) + D&A", "no D&A TTM"),
                "net_debt": fld(net_debt, "total debt (0 when none filed, as relative.point) - cash", "no cash"),
                "net_debt_ebitda": fld(round(net_debt / ebitda_ttm, 2) if net_debt is not None and ebitda_ttm and ebitda_ttm > 0 else None,
                                       "net debt / TTM EBITDA", "EBITDA not positive or not filed"),
                "interest_coverage": fld(round(ebit_ttm / int_ttm, 2) if ebit_ttm is not None and int_ttm and int_ttm > 0 else None,
                                         "TTM EBIT / interest expense (deep_v82.wacc_true's coverage)",
                                         "no interest expense filed (no debt cost to cover)")},
        "currency": ff.currency,
    }
    return {"fy": fy, "fy_source": src_fy, "derived": derived,
            "latest_filed": sec_edgar_newest(cf)}


def sec_edgar_newest(cf):
    from sources import sec_gap
    return sec_gap.newest_filing(cf)


def _days(a, b):
    import datetime as dt
    return (dt.date.fromisoformat(str(b)[:10]) - dt.date.fromisoformat(str(a)[:10])).days


def cap_years(cap_window, wacc):
    """(FY with ROIC > WACC, FY in the window): deep_v82.competitive_advantage_period's
    count, at the industry WACC instead of the company's."""
    if not cap_window or wacc is None:
        return None, len(cap_window or [])
    return sum(1 for r in cap_window if r["roic_pct"] > wacc * 100), len(cap_window)


# --------------------------------------------------------------------------- #
#  Group 2                                                                      #
# --------------------------------------------------------------------------- #
def history(ticker, offline=False):
    """(series {closes, volumes, dates, source, dividend_adjusted}, reason). The app's long
    adjusted ladder with no FMP key (Yahoo, then Stooq), cached under config.CACHE_DIR."""
    from pipeline import pricecache, prices
    try:
        if offline:
            s = pricecache.read_any(ticker)
            if not s:
                return None, "offline run: no cached price history"
        else:
            s = prices.fetch_daily_adjusted(ticker, "", rng=HISTORY_RANGE,
                                            full_bars=HISTORY_FULL_BARS, min_bars=HISTORY_MIN_BARS)
    except Exception as e:
        return None, f"no price history ({str(e)[:80]})"
    c, d, v = pricing.completed_bars(s.get("closes"), s.get("dates"), None, s.get("volumes"))
    return {"closes": c, "dates": [str(x)[:10] for x in d], "volumes": v, "source": s.get("source"),
            "dividend_adjusted": s.get("dividend_adjusted"), "stale_cache": s.get("stale_cache")}, None


def _rets(s):
    return returns.daily_returns(s["closes"]), s["dates"][1:]


def group2(ticker, s, why, spy, holdings):
    """The group-2 block. `s` this member's history (or None with `why`), `spy` and
    `holdings` {ticker: history} the market and the book's series."""
    if not s or len(s["closes"]) < HISTORY_MIN_BARS:
        r = why or f"only {len(s['closes']) if s else 0} bars"
        return {"bars": 0, "reason": r}
    src = f"pipeline.prices.fetch_daily_adjusted ({s['source']}, {HISTORY_RANGE}, no FMP)"
    mv = momentum.compute(ticker, s["closes"], s["dates"], s.get("dividend_adjusted", True), market=spy)
    n = SHORT_TAIL
    ind = (indicators.compute(ticker, s["closes"][-n:], s["volumes"][-n:], s["dates"][-n:])
           if s.get("volumes") else {"error": "no volumes"})
    from pipeline import daily
    beta, beta_end = daily.beta_vs_spy(s, spy)
    a, ad = _rets(s)
    corr = {}
    for h, hs in sorted(holdings.items()):
        if h == ticker or not hs:
            continue
        b, bd = _rets(hs)
        n = len(set(ad) & set(bd))
        if n >= CORR_MIN_DAYS:
            c = correlation.pair_corr_dated(a, ad, b, bd)
            if c is not None:
                corr[h] = round(c, 3)
    top = max(corr.items(), key=lambda kv: kv[1]) if corr else None
    return {
        "bars": len(s["closes"]), "first": s["dates"][0], "last": s["dates"][-1], "source": src,
        "stale_cache": bool(s.get("stale_cache")),
        "vol_ann": fld(mv.get("vol_ann"), "momentum.annualized_vol (126 trading days, annualised)", mv.get("error")),
        "beta_spy": dict(fld(beta, "pipeline.daily.beta_vs_spy (the app's beta: weekly returns vs SPY 2y, completed bars)",
                             "SPY history missing or overlap short"), window_end=beta_end),
        "corr": fld(corr or None, f"risk.correlation.pair_corr_dated on shared dates (>= {CORR_MIN_DAYS} days)",
                    "no holding with enough shared days"),
        "top_corr": fld({"ticker": top[0], "value": top[1]} if top else None, "max of corr", "no correlation measured"),
        "momentum": fld({k: mv.get(k) for k in ("mom_label", "mom_score", "mom_n", "roc_6m", "mom_12_1", "above_sma200")}
                        if "error" not in mv else None, "momentum.compute (momentum_v2 composite)", mv.get("error")),
        "indicators": fld({k: ind.get(k) for k in ("rsi", "rsi_signal", "macd_signal", "dbbmv_signal")}
                          if "error" not in ind else None, f"indicators.compute (RSI, MACD, DBBMV) on the last {SHORT_TAIL} bars, the app's 3mo window", ind.get("error")),
    }


def sector_headroom(store, industry, prices=None):
    """{sector, weight_pct, cap_pct, headroom_pct, holdings} for the sector the book's
    holdings of `industry` sit in, from risk.portfolio.position_sizing's sector pooling."""
    import config
    hold, facts = store.get("holdings") or {}, store.get("facts") or {}
    val = {}
    for t, h in hold.items():
        px = (prices or {}).get(t) or (facts.get(t) or {}).get("price")
        if px and h.get("shares"):
            val[t] = h["shares"] * px
    cash = sum(v for v in (store.get("cash") or {}).values() if isinstance(v, (int, float)))
    tot = sum(val.values()) + cash
    if not tot:
        return fld(None, None, "no holding values in the store")
    sectors = {t: (facts.get(t) or {}).get("sector") for t in hold}
    rc = [{"ticker": t, "capital_pct": v / tot * 100} for t, v in val.items()]
    rows = riskeng.position_sizing(rc, sectors, sector_cap=config.SECTOR_CAP)
    mine = collections.Counter(sectors[t] for t in hold if (facts.get(t) or {}).get("damodaran_industry") == industry and sectors.get(t))
    if not mine:
        return fld(None, None, f"no holding in {industry}, so its sector is unknown")
    sec = mine.most_common(1)[0][0]
    w = sum(r["current_pct"] for r in rows if r["sector"] == sec)
    return dict(fld(round(config.SECTOR_CAP * 100 - w, 1),
                    "risk.portfolio.position_sizing sector pooling (shares x store price / book incl. cash); "
                    f"cap config.SECTOR_CAP {config.SECTOR_CAP:.0%}"),
                sector=sec, weight_pct=round(w, 1), cap_pct=round(config.SECTOR_CAP * 100, 1),
                holdings=sorted(t for t in hold if sectors.get(t) == sec))
