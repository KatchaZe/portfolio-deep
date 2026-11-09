"""
Target weights from a risk budget, and the band rebalance around them.

1. Budget. Every holding starts with an equal share of the book's risk (equal risk
   contribution, the risk-parity starting point). The share is tilted by what the
   price offers over the cost of equity (the implied return, P1-4) and scaled down by
   the value's uncertainty (P1-3). A SELL gets no budget; a holding with no fair value
   (a bond or gold fund, a name not yet valued) keeps its current weight, since the
   valuation has no opinion on it.
2. Weights. The weights whose risk contributions match the budget (risk budgeting,
   solved by the standard multiplicative iteration on the covariance), on the
   invested share of the book.
3. Caps. No target above the name's hard cap (single-name, risk-share, pricing-asset,
   sector: position_sizing), and a name whose ADD is blocked keeps at most its current
   weight. What these cut is handed to the names that can still take more, in
   proportion to their risk-budget weights and up to their own caps; only what no
   name can take is cash (user decision 2026-11-07).
4. Bands (Vanguard's 200 / 175). A holding more than BAND_TRIGGER from its target is
   traded back to BAND_DEST from it, on the side it drifted; inside the band, no trade.

Every input past the covariance is [JUDG]; the output is a proposal next to the
cap-based rebalance, not a replacement for it. Pure: no I/O.
"""
import math

BAND_TRIGGER, BAND_DEST = 0.02, 0.0175
TILT_PER_PP, TILT_MIN, TILT_MAX = 0.10, 0.5, 1.5
UNCERTAINTY_SCALE = {"Low": 1.0, "Medium": 0.9, "High": 0.75, "Very High": 0.6,
                     "Extreme": 0.5, "Unrated": 0.75, None: 0.75}
_NO_BUDGET = ("SELL",)


def budget(v):
    """(risk budget before normalising, why) for one holding's value dict; budget
    None = no valuation opinion, the holding keeps its current weight."""
    v = v or {}
    if any(k in str(v.get("rating") or "").upper() for k in _NO_BUDGET):
        return 0.0, f"rating {v.get('rating')} → ไม่มี budget"
    if v.get("net_mos") is None and v.get("excess_pct") is None:
        return None, "ไม่มี fair value → คงน้ำหนักเดิม"
    ex = v.get("excess_pct")
    tilt = 1.0 if ex is None else max(TILT_MIN, min(TILT_MAX, 1 + TILT_PER_PP * ex))
    unc = UNCERTAINTY_SCALE.get(v.get("uncertainty"), UNCERTAINTY_SCALE[None])
    why = (f"excess {ex:+.1f}pp → ×{tilt:.2f}" if ex is not None else "ไม่มี implied return → ×1.00")
    return tilt * unc, why + f" · uncertainty {v.get('uncertainty') or 'Unrated'} ×{unc:.2f}"


def risk_budget_weights(cov, budgets, iters=500, tol=1e-10):
    """Long-only weights (sum 1) whose risk contributions are proportional to
    `budgets`; zero budget -> zero weight."""
    n = len(budgets)
    tot = sum(budgets)
    if n == 0 or tot <= 0:
        return [0.0] * n
    b = [x / tot for x in budgets]
    w = [bi for bi in b]
    for _ in range(iters):
        sw = [sum(cov[i][j] * w[j] for j in range(n)) for i in range(n)]
        var = sum(w[i] * sw[i] for i in range(n))
        if var <= 0:
            break
        new = [w[i] * math.sqrt(b[i] / (w[i] * sw[i] / var)) if b[i] > 0 and w[i] * sw[i] > 0
               else (b[i] if b[i] > 0 else 0.0) for i in range(n)]
        s = sum(new)
        new = [x / s for x in new]
        if max(abs(x - y) for x, y in zip(new, w)) < tol:
            w = new
            break
        w = new
    return w


def _sector_fit(tgt, sec_of, sector_cap, notes):
    """Scale each sector's targets down to the sector cap."""
    for sec in {s for s in sec_of.values() if s}:
        names = [t for t in tgt if sec_of.get(t) == sec]
        limit = sector_cap
        tot = sum(tgt[t] for t in names)
        if tot > limit + 1e-9:
            for t in names:
                tgt[t] *= limit / tot
                notes[t] += f" · sector {sec} ≤ {sector_cap * 100:.0f}%"


def _sector_room(t, tgt, sec_of, sector_cap):
    sec = sec_of.get(t)
    if not sec or not sector_cap:
        return 1.0
    return max(0.0, sector_cap - sum(w for x, w in tgt.items() if sec_of.get(x) == sec))


def targets(tickers, cov, weights, value, sizing_rows, cash_frac=0.0, total_value=None,
            sector_cap=None):
    """Per holding: target, current, drift and the band trade; plus the cash the caps
    leave. Weights in and out are of the whole portfolio (cash included)."""
    caps = {r["ticker"]: (r.get("hard_max_pct") or 100.0) / 100 for r in sizing_rows}
    blocked = {r["ticker"]: r.get("add_blocker") for r in sizing_rows}
    sec_of = {r["ticker"]: r.get("sector") for r in sizing_rows}
    invest = 1.0 - (cash_frac or 0.0)
    cur = {t: weights.get(t, 0.0) * invest for t in tickers}
    bud, why = zip(*[budget((value or {}).get(t)) for t in tickers]) if tickers else ((), ())
    held = {t for i, t in enumerate(tickers) if bud[i] is None}
    free = [i for i, t in enumerate(tickers) if t not in held]
    sub = [[cov[i][j] for j in free] for i in free]
    raw = risk_budget_weights(sub, [bud[i] for i in free])
    room = invest - sum(cur[t] for t in held)
    tgt = {t: cur[t] for t in held}
    tgt.update({tickers[i]: raw[k] * room for k, i in enumerate(free)})
    notes = {t: why[i] for i, t in enumerate(tickers)}
    ceiling = {t: (min(caps.get(t, 1.0), cur[t]) if blocked.get(t) else caps.get(t, 1.0))
               for t in tickers}
    for t in tickers:
        if tgt[t] > ceiling[t]:
            tgt[t] = ceiling[t]
            notes[t] += (f" · ADD blocked ({blocked[t]})" if blocked.get(t) and ceiling[t] == cur[t]
                         and cur[t] < caps.get(t, 1.0) else f" · cap {ceiling[t] * 100:.1f}%")
    if sector_cap:
        _sector_fit(tgt, sec_of, sector_cap, notes)
    share = {tickers[i]: raw[k] for k, i in enumerate(free)}
    for _ in range(len(tickers)):                 # hand what was cut to who can take it
        left = invest - sum(tgt.values())
        open_ = [t for t in share if share[t] > 0 and tgt[t] < ceiling[t] - 1e-12
                 and _sector_room(t, tgt, sec_of, sector_cap) > 1e-12]
        tot = sum(share[t] for t in open_)
        if left <= 1e-9 or tot <= 0:
            break
        for t in open_:
            add = min(ceiling[t] - tgt[t], left * share[t] / tot,
                      _sector_room(t, tgt, sec_of, sector_cap))
            if add > 1e-12:
                tgt[t] += add
                if "+ ส่วนที่ตัวอื่นรับไม่ได้" not in notes[t]:
                    notes[t] += " + ส่วนที่ตัวอื่นรับไม่ได้"
    rows = []
    for t in tickers:
        drift = cur[t] - tgt[t]
        if tgt[t] == 0 and cur[t] > 0 and bud[tickers.index(t)] == 0.0:
            new, act = 0.0, "SELL"                # a SELL exits whole, band or not
        elif abs(drift) > BAND_TRIGGER:
            new = tgt[t] + math.copysign(BAND_DEST, drift)
            act = "SELL" if drift > 0 else "BUY"
        else:
            new, act = cur[t], "HOLD"
        trade = new - cur[t]
        rows.append({"ticker": t, "target_pct": round(tgt[t] * 100, 1),
                     "current_pct": round(cur[t] * 100, 1), "drift_pp": round(drift * 100, 1),
                     "action": act, "trade_pct": round(trade * 100, 2),
                     "trade_usd": round(trade * total_value, 0) if total_value else None,
                     "basis": notes[t]})
    rows.sort(key=lambda r: -abs(r["drift_pp"]))
    return {"rows": rows,
            "target_cash_pct": round((1 - sum(tgt.values())) * 100, 1),
            "band_pp": [round(BAND_TRIGGER * 100, 2), round(BAND_DEST * 100, 2)],
            "tag": "[JUDG] risk budget = equal risk × tilt(implied excess return) × uncertainty; "
                   "band 2 / 1.75pp (Vanguard 200/175)"}
