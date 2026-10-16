"""
The holdings view (/api/portfolio): display rows with P/L, momentum staleness,
the market benchmark and the philosophy assessment. Reads the store only.
"""
import os
import datetime as dt

import config
from domain import momentum, philosophy, advice
from pipeline import rows


def _momentum_meta(view_rows, stale_after=4):
    """Per-row momentum staleness + portfolio 'last updated' banner. Mutates each
    row's momentum_v2 with age_days/stale. Ages come only from the data's own
    as_of; nothing is fabricated."""
    today = dt.date.today()

    def age(a):
        try:
            return (today - dt.date.fromisoformat(str(a)[:10])).days
        except Exception:
            return None

    asofs, stale_count, n = [], 0, 0
    for r in view_rows:
        v = r.get("momentum_v2")
        if not v:
            continue
        n += 1
        ag = age(v.get("as_of"))
        v["age_days"] = ag
        v["stale"] = (ag is not None and ag > stale_after)
        if v.get("as_of"):
            asofs.append(str(v["as_of"])[:10])
        if v["stale"]:
            stale_count += 1
    last = max(asofs) if asofs else None
    overall = age(last) if last else None
    return {"as_of": last, "age_days": overall,
            "stale": (n > 0 and (last is None or (overall is not None and overall > stale_after))),
            "stale_count": stale_count, "n": n}


def weighted_trailing_return(view_rows, key="roc_12m"):
    """S16/S35 benchmark input: MV-weighted trailing return of CURRENT holdings
    (illustrative buy-and-hold, not a realized portfolio return). Pure; uses only
    rows that have both market_value and the momentum return; renormalizes."""
    num = den = 0.0
    for r in view_rows:
        mv = r.get("market_value")
        ret = (r.get("momentum_v2") or {}).get(key)
        if mv and ret is not None:
            num += mv * ret
            den += mv
    return (num / den) if den else None


def _holding_row(s, t, h, regime):
    f = s["facts"].get(t, {})
    mom = s["momentum"].get(t, {})
    v2 = mom.get("v2") or {}
    if v2:
        v2["div_warn"] = momentum.div_warn(v2.get("dividend_adjusted"), f.get("dividend_ps"))
        v2["crash_guard"] = momentum.crash_guard(v2.get("mom_label"), regime)
    stored_rev = s.get("rev_surprises", {})
    row = rows.valuation_row(
        t, f, s["results"].get(t, {}), mom, v2,
        rev_surprises=stored_rev.get(t) or f.get("rev_surprises_fmp"),
        has_new_schema=("operating_income_quarters" in f and "eps_quarters" in f),
        fmp_on=bool(config.FMP_API_KEY))
    price = row["price"]
    shares, avg = h.get("shares", 0), h.get("avg_cost", 0)
    cost = shares * avg if (shares and avg) else None
    mv = shares * price if (shares and price) else None
    pl = (mv - cost) if (mv is not None and cost is not None) else None
    row.update({
        "shares": shares, "avg_cost": avg,
        "cost_basis": round(cost, 2) if cost else None,
        "market_value": round(mv, 2) if mv else None,
        "pl": round(pl, 2) if pl is not None else None,
        "pl_pct": round(pl / cost * 100, 1) if (pl is not None and cost) else None,
        "updated": s["updated"].get(t),
        # the holdings table shows an empty list, never None
        "earnings_surprises": row["earnings_surprises"] or [],
        "rev_surprises": stored_rev.get(t, []) or f.get("rev_surprises_fmp", []),
    })
    row["advice"] = advice.build(row)
    return row


def view(s):
    market = s.get("market") or {}
    view_rows = [_holding_row(s, t, h, market.get("regime"))
                 for t, h in s.get("holdings", {}).items()]
    momentum.cross_sectional_rank([r["momentum_v2"] for r in view_rows if r.get("momentum_v2")])
    mom_meta = _momentum_meta(view_rows)
    tot_cost = sum(r["cost_basis"] or 0 for r in view_rows)
    tot_mv = sum(r["market_value"] or 0 for r in view_rows)
    totals = {"cost_basis": round(tot_cost, 2), "market_value": round(tot_mv, 2),
              "pl": round(tot_mv - tot_cost, 2),
              "pl_pct": round((tot_mv - tot_cost) / tot_cost * 100, 1) if tot_cost else None}
    mkt_ret = market.get("ret_12m")
    port_ret = weighted_trailing_return(view_rows)
    benchmark = {
        "portfolio_12m": round(port_ret * 100, 1) if port_ret is not None else None,
        "market_12m": round(mkt_ret * 100, 1) if mkt_ret is not None else None,
        "excess_pp": round((port_ret - mkt_ret) * 100, 1) if (port_ret is not None and mkt_ret is not None) else None,
    }
    prof = philosophy.load(os.path.join(config.BASE_DIR, "data", "philosophy_profile.json"))
    return {"rows": view_rows, "totals": totals, "momentum_meta": mom_meta,
            "market": market or None, "benchmark": benchmark,
            "philosophy": philosophy.assess(view_rows, prof)}
