"""
The NAV log behind realized performance (domain/performance.py).

record(s)  after a daily commit: append today's NAV (holdings at the close on file +
           cash), the money moved in or out since the last entry, and SPY's close.
           Skipped, with the flows kept for the next entry, while any holding has no
           close for SPY's date: a NAV of mixed days would book price moves as returns.
view(s)    the summary the dashboard shows.

Flows: once the ledger runs, its deposits and withdrawals (and implied deposits for a
buy the recorded cash could not pay), at the amounts recorded; buys, sells and
dividends move money inside the book and are not flows. Before the ledger, edits via
store.set_holding / set_cash / remove_holding are valued at the close on file. Edits
before the first entry are the starting book, not flows.
"""
from domain import ledger as ledger_mod
from domain import performance as perf_mod

MAX_ENTRIES = 3000          # ~12 years of trading days; the oldest drop off


def _price(s, t):
    m = (s.get("momentum") or {}).get(t) or {}
    v2 = m.get("v2") or {}
    return m.get("price") or (s.get("facts", {}).get(t) or {}).get("price"), \
        m.get("price_date") or v2.get("price_date")


def record(s):
    """Append or update today's entry. Returns the entry, or None with nothing done."""
    perf = s.setdefault("performance", {})
    log = perf.setdefault("nav", [])
    market = s.get("market") or {}
    spy, day = market.get("spy_close"), str(market.get("as_of") or "")[:10]
    if not spy or not day:
        return None
    holdings = s.get("holdings") or {}
    nav = 0.0
    prices = {}
    for t, h in holdings.items():
        if not h.get("shares"):
            continue
        p, pdate = _price(s, t)
        if not p or not pdate or pdate < day:
            return None
        prices[t] = p
        nav += h["shares"] * p
    cash = float(((s.get("cash") or {}).get("usd")) or 0.0)
    nav += cash
    if nav <= 0:
        return None
    import store as store_mod
    flow = float(perf.get("pending_flow") or 0.0)
    for t, d in (perf.get("pending_shares") or {}).items():
        flow += d * (prices.get(t) or 0.0)
    new_ids, ledger_flow = store_mod.ledger_flows_unbooked(s)
    if log:
        flow += ledger_flow
    if log and log[-1]["date"] == day:
        entry = log[-1]
        entry.update(nav=round(nav, 2), spy=spy, flow=round((entry.get("flow") or 0.0) + flow, 2))
    elif log and log[-1]["date"] > day:
        return None
    else:
        entry = {"date": day, "nav": round(nav, 2), "flow": round(flow, 2) if log else 0.0,
                 "spy": spy}
        log.append(entry)
        del log[:-MAX_ENTRIES]
    perf["pending_flow"], perf["pending_shares"] = 0.0, {}
    perf["booked_flows"] = (perf.get("booked_flows") or []) + new_ids
    return entry


def view(s):
    log = ((s.get("performance") or {}).get("nav")) or []
    out = perf_mod.summary(log)
    out["max_drawdown_pct"] = ledger_mod.max_drawdown(log)
    return out


def ledger_view(s):
    """The Performance tab: the ledger's transactions and what they add up to at the
    closes on file. {"active": False} before the ledger starts."""
    import store as store_mod
    import config
    if not store_mod.ledger_active(s):
        return {"active": False}
    txns = s["ledger"]["txns"]
    book = ledger_mod.replay(txns, config.DIVIDEND_WITHHOLDING)
    held = [t for t, p in book["positions"].items() if p["shares"] > 1e-9]
    prices = {t: _price(s, t)[0] for t in held}
    fx = ((s.get("market") or {}).get("usdthb") or {})
    out = ledger_mod.view(book, {t: p for t, p in prices.items() if p}, store_mod.today(),
                          usdthb=fx.get("rate"),
                          nav_log=((s.get("performance") or {}).get("nav")) or [])
    out.update(active=True, usdthb_date=fx.get("date"),
               txns=sorted(txns, key=lambda x: (x["date"], x["id"]), reverse=True),
               performance=view(s), withholding=config.DIVIDEND_WITHHOLDING)
    return out
