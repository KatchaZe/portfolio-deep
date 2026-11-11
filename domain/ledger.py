"""
The transaction ledger: every buy, sell, deposit, withdrawal, dividend and split, and
everything the portfolio is derived from them.

    replay(txns, withholding)    holdings, cash, principal, realized trades, flows
    view(book, prices, ...)      P&L, XIRR, per-name contribution, closed-trade stats

Transactions (dicts, amounts in USD unless named):
    OPEN      the book the ledger started from: a position (ticker, shares, price = the
              average cost, mkt_price = the close that day) or cash (amount). Cost is
              principal; mkt_price is where "since the ledger started" returns begin.
    DEPOSIT / WITHDRAW   amount, optional thb (baht moved) -> principal in USD and THB
    BUY / SELL           ticker, shares, price, fee
    DIVIDEND             ticker, amount (gross); US withholding is taken at `withholding`
    SPLIT                ticker, ratio (4 = a 4-for-1 split)

Average cost: a buy's cost and fee go into the position's cost; a sell realizes
proceeds - fee - average cost x shares and leaves the average unchanged. A buy the
cash on the ledger cannot pay for is funded by an implied deposit of the shortfall,
listed so the owner can record the real one.

Pure: no I/O, no clock.
"""
import datetime as dt

TYPES = ("OPEN", "DEPOSIT", "WITHDRAW", "BUY", "SELL", "DIVIDEND", "SPLIT")
TRADE = ("BUY", "SELL")
EPS = 1e-9
XIRR_MIN_DAYS = 30          # annualizing a few days' return says nothing


class LedgerError(ValueError):
    pass


def _d(s):
    return dt.date.fromisoformat(s)


def ordered(txns):
    """Replay order: by date; within a day OPEN and cash moves first, then trades."""
    rank = {"OPEN": 0, "DEPOSIT": 1, "SPLIT": 2, "BUY": 3, "SELL": 4, "DIVIDEND": 5, "WITHDRAW": 6}
    return sorted(txns, key=lambda x: (x["date"], rank.get(x["type"], 9), x.get("id", 0)))


def validate(t):
    """A transaction's own fields; raises LedgerError naming the problem."""
    typ = t.get("type")
    if typ not in TYPES:
        raise LedgerError(f"unknown type {typ!r}")
    try:
        _d(t.get("date") or "")
    except ValueError:
        raise LedgerError("date must be YYYY-MM-DD")
    need_ticker = typ in TRADE + ("DIVIDEND", "SPLIT") or (typ == "OPEN" and t.get("shares"))
    if need_ticker and not t.get("ticker"):
        raise LedgerError(f"{typ} needs a ticker")
    pos = lambda k: isinstance(t.get(k), (int, float)) and t[k] > 0
    if typ in TRADE and not (pos("shares") and pos("price")):
        raise LedgerError(f"{typ} needs shares > 0 and price > 0")
    if typ in ("DEPOSIT", "WITHDRAW", "DIVIDEND") and not pos("amount"):
        raise LedgerError(f"{typ} needs amount > 0 (USD)")
    if typ == "SPLIT" and not pos("ratio"):
        raise LedgerError("SPLIT needs ratio > 0 (4 = 4-for-1)")
    if (t.get("fee") or 0) < 0 or (t.get("thb") or 0) < 0:
        raise LedgerError("fee and thb cannot be negative")
    return t


def _pos():
    return {"shares": 0.0, "cost": 0.0, "avg_date_ord": 0.0, "realized": 0.0,
            "dividends": 0.0, "withheld": 0.0, "fees": 0.0,
            "invested": 0.0, "proceeds": 0.0, "open_value": 0.0}


def replay(txns, withholding=0.0):
    """Walk the ledger in order. Raises LedgerError on a sell of more than is held.
    `flows` are the external money moves ({date, amount, id}, + in): deposits,
    withdrawals and implied deposits; buys, sells and dividends stay inside the book."""
    pos, cash = {}, 0.0
    flows, trades, implied = [], [], []
    principal_usd, principal_thb, thb_missing = 0.0, 0.0, 0.0
    start, open_value = None, 0.0
    for t in ordered(txns):
        typ, day = t["type"], t["date"]
        tk = (t.get("ticker") or "").upper()
        fee = float(t.get("fee") or 0.0)
        if typ == "OPEN":
            start = start or day
            if tk and t.get("shares"):
                p = pos.setdefault(tk, _pos())
                sh, cost = float(t["shares"]), float(t["shares"]) * float(t["price"])
                p["avg_date_ord"] = _d(t.get("acquired") or day).toordinal()
                p["shares"] += sh
                p["cost"] += cost
                mv = sh * float(t.get("mkt_price") or t["price"])
                p["open_value"] += mv
                open_value += mv
                principal_usd += cost
            else:
                amt = float(t.get("amount") or 0.0)
                cash += amt
                open_value += amt
                principal_usd += amt
                cost = amt
            if t.get("thb"):
                principal_thb += float(t["thb"])
            else:
                thb_missing += cost
        elif typ in ("DEPOSIT", "WITHDRAW"):
            sign = 1 if typ == "DEPOSIT" else -1
            amt = float(t["amount"])
            cash += sign * amt
            principal_usd += sign * amt
            if t.get("thb"):
                principal_thb += sign * float(t["thb"])
            else:
                thb_missing += sign * amt
            flows.append({"date": day, "amount": sign * amt, "id": t.get("id")})
        elif typ == "BUY":
            p = pos.setdefault(tk, _pos())
            sh, px = float(t["shares"]), float(t["price"])
            spend = sh * px + fee
            if cash + EPS < spend:
                short = spend - max(cash, 0.0)
                implied.append({"date": day, "ticker": tk, "amount": round(short, 2), "id": t.get("id")})
                cash += short
                principal_usd += short
                thb_missing += short
                flows.append({"date": day, "amount": short, "id": f"implied-{t.get('id')}"})
            cash -= spend
            prev = p["shares"]
            p["avg_date_ord"] = ((p["avg_date_ord"] * prev + _d(day).toordinal() * sh) / (prev + sh)
                                 if prev > EPS else _d(day).toordinal())
            p["shares"] += sh
            p["cost"] += spend
            p["fees"] += fee
            p["invested"] += spend
        elif typ == "SELL":
            p = pos.get(tk)
            sh, px = float(t["shares"]), float(t["price"])
            if not p or p["shares"] + EPS < sh:
                held = p["shares"] if p else 0.0
                raise LedgerError(f"{day} SELL {sh:g} {tk}: only {held:g} held")
            avg = p["cost"] / p["shares"]
            proceeds = sh * px - fee
            pnl = proceeds - avg * sh
            held_days = _d(day).toordinal() - p["avg_date_ord"]
            trades.append({"date": day, "ticker": tk, "shares": sh, "price": px,
                           "avg_cost": round(avg, 4), "proceeds": round(proceeds, 2),
                           "pnl": round(pnl, 2), "pnl_pct": round(pnl / (avg * sh) * 100, 2) if avg > 0 else None,
                           "days": int(round(held_days)), "id": t.get("id")})
            cash += proceeds
            p["shares"] -= sh
            p["cost"] -= avg * sh
            p["realized"] += pnl
            p["fees"] += fee
            p["proceeds"] += proceeds
            if p["shares"] < EPS:
                p["shares"], p["cost"] = 0.0, 0.0
        elif typ == "DIVIDEND":
            p = pos.setdefault(tk, _pos())
            gross = float(t["amount"])
            wh = float(t["withheld"]) if t.get("withheld") is not None else gross * withholding
            p["dividends"] += gross - wh
            p["withheld"] += wh
            cash += gross - wh
        elif typ == "SPLIT":
            p = pos.get(tk)
            if p and p["shares"] > EPS:
                p["shares"] *= float(t["ratio"])
    return {"positions": pos, "cash": cash, "flows": flows, "trades": trades,
            "implied_deposits": implied, "start": start, "open_value": open_value,
            "principal_usd": principal_usd, "principal_thb": principal_thb,
            "thb_missing_usd": thb_missing}


def holdings(book):
    """{ticker: {shares, avg_cost}} for the names still held."""
    return {t: {"shares": round(p["shares"], 6), "avg_cost": round(p["cost"] / p["shares"], 4)}
            for t, p in book["positions"].items() if p["shares"] > EPS}


# --------------------------------------------------------------------------- #
#  Returns                                                                     #
# --------------------------------------------------------------------------- #
def xirr(cashflows):
    """Annual rate r with sum(cf / (1+r)^(days/365)) = 0. cashflows: [(date, amount)],
    money in negative, money out (and the final value) positive. None when the flows do
    not change sign or span under a day."""
    cfs = [(_d(d), float(a)) for d, a in cashflows if a]
    if len(cfs) < 2 or not (any(a < 0 for _, a in cfs) and any(a > 0 for _, a in cfs)):
        return None
    d0 = min(d for d, _ in cfs)
    if (max(d for d, _ in cfs) - d0).days < 1:
        return None
    yrs = [((d - d0).days / 365.0, a) for d, a in cfs]

    def npv(r):
        return sum(a / (1 + r) ** y for y, a in yrs)

    lo, hi = -0.9999, 10.0
    f_lo, f_hi = npv(lo), npv(hi)
    if f_lo * f_hi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        f_mid = npv(mid)
        if abs(f_mid) < 1e-7:
            break
        if f_lo * f_mid < 0:
            hi = mid
        else:
            lo, f_lo = mid, f_mid
    return mid


def _closed_stats(trades):
    if not trades:
        return {"n": 0}
    wins = [x for x in trades if x["pnl"] > 0]
    losses = [x for x in trades if x["pnl"] <= 0]
    gain, loss = sum(x["pnl"] for x in wins), -sum(x["pnl"] for x in losses)
    avg = lambda xs, k: round(sum(x[k] for x in xs) / len(xs), 2) if xs else None
    return {"n": len(trades), "wins": len(wins), "win_rate_pct": round(100 * len(wins) / len(trades), 1),
            "avg_win_pct": avg([x for x in wins if x["pnl_pct"] is not None], "pnl_pct"),
            "avg_loss_pct": avg([x for x in losses if x["pnl_pct"] is not None], "pnl_pct"),
            "profit_factor": round(gain / loss, 2) if loss > 0 else None,
            "avg_days": round(sum(x["days"] for x in trades) / len(trades)),
            "realized": round(gain - loss, 2)}


def view(book, prices, today, usdthb=None, nav_log=None):
    """Everything the Performance tab shows. `prices` {ticker: last close}; a held name
    without a price is valued at its average cost and listed in `unpriced`."""
    pos, unpriced, rows = book["positions"], [], []
    mv_total = 0.0
    tot = {k: 0.0 for k in ("realized", "unrealized", "dividends", "withheld", "fees", "since")}
    for tk, p in sorted(pos.items()):
        px = prices.get(tk)
        if p["shares"] > EPS and not px:
            unpriced.append(tk)
            px = p["cost"] / p["shares"]
        mv = p["shares"] * (px or 0.0)
        unreal = mv - p["cost"]
        since = mv + p["proceeds"] + p["dividends"] - p["open_value"] - p["invested"]
        mv_total += mv
        for k, v in (("realized", p["realized"]), ("unrealized", unreal), ("dividends", p["dividends"]),
                     ("withheld", p["withheld"]), ("fees", p["fees"]), ("since", since)):
            tot[k] += v
        rows.append({"ticker": tk, "shares": round(p["shares"], 6), "price": px,
                     "avg_cost": round(p["cost"] / p["shares"], 4) if p["shares"] > EPS else None,
                     "market_value": round(mv, 2), "cost": round(p["cost"], 2),
                     "unrealized": round(unreal, 2),
                     "unrealized_pct": round(unreal / p["cost"] * 100, 2) if p["cost"] > EPS else None,
                     "realized": round(p["realized"], 2), "dividends": round(p["dividends"], 2),
                     "lifetime_pnl": round(p["realized"] + unreal + p["dividends"], 2),
                     "since_start_pnl": round(since, 2), "held": p["shares"] > EPS})
    nav = mv_total + book["cash"]
    since_total = tot["since"]
    for r in rows:
        r["contribution_pct"] = (round(r["since_start_pnl"] / since_total * 100, 1)
                                 if abs(since_total) > EPS else None)
    principal = book["principal_usd"]
    start = book["start"]
    flows = [(start, -book["open_value"])] if start and book["open_value"] else []
    flows += [(f["date"], -f["amount"]) for f in book["flows"]] + [(today, nav)]
    span = (_d(today) - _d(start)).days if start else 0
    irr = xirr(flows) if span >= XIRR_MIN_DAYS else None
    out = {
        "start": start, "nav": round(nav, 2), "cash": round(book["cash"], 2),
        "invested_value": round(mv_total, 2),
        "principal_usd": round(principal, 2),
        "principal_share_pct": round(principal / nav * 100, 1) if nav > EPS else None,
        "total_gain": round(nav - principal, 2),
        "total_gain_pct": round((nav - principal) / principal * 100, 2) if principal > EPS else None,
        "since_start_gain": round(since_total, 2),
        "realized": round(tot["realized"], 2), "unrealized": round(tot["unrealized"], 2),
        "dividends_net": round(tot["dividends"], 2), "withheld": round(tot["withheld"], 2),
        "fees": round(tot["fees"], 2),
        "xirr_pct": round(irr * 100, 2) if irr is not None else None,
        "xirr_note": None if irr is not None else
            f"ขึ้นเมื่อ ledger ครบ {XIRR_MIN_DAYS} วัน (ตอนนี้ {span} วัน) — ผลตอบแทนช่วงสั้นทำเป็นรายปีไม่มีความหมาย",
        "positions": rows, "closed": _closed_stats(book["trades"]), "trades": book["trades"],
        "implied_deposits": book["implied_deposits"], "unpriced": unpriced,
        "turnover_pct": _turnover(book["trades"], nav_log, start, today),
    }
    if usdthb:
        nav_thb = nav * usdthb
        known = book["principal_thb"] + book["thb_missing_usd"] * usdthb
        out["thb"] = {"usdthb": usdthb, "nav": round(nav_thb, 0),
                      "principal": round(known, 0),
                      "principal_estimated_usd": round(book["thb_missing_usd"], 2),
                      "gain": round(nav_thb - known, 0),
                      "gain_pct": round((nav_thb - known) / known * 100, 2) if known > EPS else None}
    return out


def _turnover(trades, nav_log, start, today):
    """Sales over the period / average NAV, per year (how much of the book changes hands)."""
    navs = [e["nav"] for e in (nav_log or []) if e.get("nav") and (not start or e["date"] >= start)]
    if not trades or not navs or not start:
        return None
    yrs = max((_d(today) - _d(start)).days, 1) / 365.0
    sold = sum(x["proceeds"] for x in trades)
    return round(sold / (sum(navs) / len(navs)) / max(yrs, 1 / 12) * 100, 1)


def max_drawdown(nav_log):
    """Worst peak-to-trough fall of the time-weighted NAV index, in percent."""
    rows = [e for e in (nav_log or []) if e.get("nav") and e["nav"] > 0]
    if len(rows) < 2:
        return None
    idx, peak, worst = 1.0, 1.0, 0.0
    for prev, cur in zip(rows, rows[1:]):
        idx *= (cur["nav"] - (cur.get("flow") or 0.0)) / prev["nav"]
        peak = max(peak, idx)
        worst = min(worst, idx / peak - 1)
    return round(worst * 100, 2)
