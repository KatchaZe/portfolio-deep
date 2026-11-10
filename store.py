"""
Local JSON store (data/portfolio.json). Holdings (shares + avg cost), cached
fundamentals and engine results are kept only for portfolio tickers; the watchlist
keeps names only. Removing a holding deletes all its cached data.

This module owns the local file. The Drive mirror policy lives in store_sync.py:
load() asks it for the cold-start pull, save() hands it the push, and every user
mutation below calls store_sync.mark_local_edit() so a later pull cannot overwrite it.
"""
import os
import re
import copy
import json
import threading
import contextlib
import datetime as dt

import config
import store_sync

_TICKER_RE = re.compile(r"[^A-Z0-9.\-]")

PATH = os.path.join(config.DATA_DIR, "portfolio.json")

# Serializes load -> mutate -> save so concurrent requests can't lose updates.
# Re-entrant so a job may nest store calls. Reads are safe lock-free because
# save() is atomic.
LOCK = threading.RLock()

_DEFAULT = {"holdings": {}, "watchlist": [], "facts": {}, "results": {},
            "momentum": {}, "fmp_usage": {}, "updated": {},
            "rev_snapshots": {}, "rev_surprises": {},
            # manual monthly ERP / MARKET_PE from the dashboard form
            "assumptions": {},
            # uninvested cash in USD, entered by hand (the Risk Desk's whole-portfolio view)
            "cash": {},
            # NAV log + flows not yet booked (pipeline/performance.py)
            "performance": {},
            # transactions; once started, holdings and cash are derived from it (domain/ledger)
            "ledger": {}}

_PER_TICKER = ("holdings", "facts", "results", "momentum", "rev_snapshots", "rev_surprises",
               "refreshed_at", "skipped")


def clean_ticker(t):
    """Uppercase, strip, drop non-ticker characters (e.g. a stray Thai vowel that
    produced 'ืNVDA'). Returns '' if nothing valid remains."""
    return _TICKER_RE.sub("", (t or "").upper().strip())


def today():
    return dt.date.today().isoformat()


def now_iso():
    """UTC, to the minute: orders refreshes and labels a card's last run."""
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


# --- file ------------------------------------------------------------------ #
def load():
    os.makedirs(config.DATA_DIR, exist_ok=True)
    store_sync.ensure_pull(PATH)
    if not os.path.exists(PATH):
        return copy.deepcopy(_DEFAULT)
    with open(PATH, encoding="utf-8") as fh:
        s = json.load(fh)
    for k, v in _DEFAULT.items():
        s.setdefault(k, copy.deepcopy(v))
    _apply_assumptions(s)
    return s


def save(s):
    """Atomic write (temp file + os.replace) so a crash mid-write can never corrupt
    the live store, then queue the Drive mirror on its background worker so a Drive
    outage can never stall a request holding LOCK.

    This does NOT count as a user edit: save() also persists the empty default store
    after a failed cold-start pull, and that must never outrank a good Drive backup.
    """
    os.makedirs(config.DATA_DIR, exist_ok=True)
    tmp = f"{PATH}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(s, fh, indent=2, ensure_ascii=False)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, PATH)
    store_sync.schedule_push(PATH)


@contextlib.contextmanager
def transaction():
    """`with transaction() as s:` loads under LOCK, yields the store, and saves it
    on a clean exit. An exception skips the save. Keep slow network work outside."""
    with LOCK:
        s = load()
        yield s
        save(s)


# --- assumptions ----------------------------------------------------------- #
def _apply_assumptions(s):
    """Overlay saved manual assumptions onto the config module so every call-time
    reader (engine Ke/WACC, validate band, market overlay) uses them without a
    restart. Never raises."""
    a = s.get("assumptions") or {}
    try:
        # newest dated ERP wins: a manual value saved this month beats last month's
        # page, and the page beats a manual value it has since superseded
        from sources import damodaran
        live = damodaran.cached_implied_erp(config.CACHE_DIR)
        manual_as_of = (a.get("erp_as_of") or "") if a.get("erp") else None
        if live and (manual_as_of is None or live["as_of"] > manual_as_of):
            config.ERP, config.ERP_AS_OF = float(live["erp"]), live["as_of"]
            config.ERP_SOURCE = "damodaran-live"
        elif a.get("erp"):
            config.ERP = float(a["erp"])
            config.ERP_AS_OF = a.get("erp_as_of") or config.ERP_AS_OF
            config.ERP_SOURCE = "manual"
        if a.get("market_pe"):
            config.MARKET_PE = float(a["market_pe"])
            config.MARKET_PE_AS_OF = a.get("market_pe_as_of") or config.MARKET_PE_AS_OF
    except Exception:
        pass
    # revenue mixes typed on the dashboard; replaced wholesale so a cleared one goes
    config.REVENUE_MIX_MANUAL = {t: dict(m) for t, m in (a.get("revenue_mix") or {}).items()
                                 if isinstance(m, dict) and m}


def reapply_assumptions():
    """Re-run the overlay after a fresh implied ERP was cached (network paths)."""
    _apply_assumptions(load())


def set_assumptions(s, erp_pct=None, market_pe=None):
    """Store the manual monthly values (validated by the caller), stamped with the
    current YYYY-MM. Returns the stored assumptions dict."""
    store_sync.mark_local_edit()
    a = s.setdefault("assumptions", {})
    month = today()[:7]
    if erp_pct is not None:
        a["erp"] = round(float(erp_pct) / 100.0, 5)
        a["erp_as_of"] = month
    if market_pe is not None:
        a["market_pe"] = round(float(market_pe), 2)
        a["market_pe_as_of"] = month
    a["updated"] = today()
    a["source"] = "manual"
    _apply_assumptions(s)
    return a


def parse_revenue_mix(text):
    """'US 56, China 20, Western Europe 24' -> ({market: weight}, None) or (None, error).
    Markets are country names, ISO-2 codes or a region country_risk knows."""
    from sources import country_risk
    mix, bad = {}, []
    for part in [p.strip() for p in (text or "").split(",") if p.strip()]:
        m = re.match(r"^(.*?)[\s:=]+([0-9]+(?:\.[0-9]+)?)%?$", part)
        if not m:
            return None, f"cannot read '{part}' (write: market weight, e.g. 'Japan 12')"
        market, w = m.group(1).strip(), float(m.group(2))
        if country_risk.market_crp(market, country_risk.CRP_FALLBACK) is None:
            bad.append(market)
        elif w > 0:
            mix[market] = mix.get(market, 0.0) + w
    if bad:
        return None, "unknown market: " + ", ".join(bad)
    return (mix, None) if mix else (None, "no market with a positive weight")


def set_revenue_mix(s, ticker, mix):
    """Save (or, with mix None, clear) a ticker's dashboard revenue mix."""
    store_sync.mark_local_edit()
    a = s.setdefault("assumptions", {})
    mixes = a.setdefault("revenue_mix", {})
    if mix:
        mixes[ticker] = mix
    else:
        mixes.pop(ticker, None)
    a["updated"] = today()
    _apply_assumptions(s)
    return mixes


# --- holdings -------------------------------------------------------------- #
def _book_shares(s, t, d_shares):
    """Money moved by buying (+) or selling (-) `d_shares`, for the performance log.
    Valued at the close on file, or at the next NAV entry's close when there is none.
    Nothing before the first NAV entry: that is the starting book. Before the ledger
    starts only: after it, flows are the ledger's deposits and withdrawals."""
    perf = s.setdefault("performance", {})
    if not perf.get("nav") or not d_shares:
        return
    price = ((s.get("momentum") or {}).get(t) or {}).get("price") or \
        (s.get("facts", {}).get(t) or {}).get("price")
    if price:
        perf["pending_flow"] = (perf.get("pending_flow") or 0.0) + d_shares * price
    else:
        ps = perf.setdefault("pending_shares", {})
        ps[t] = ps.get(t, 0.0) + d_shares


def _book_cash(s, d_usd):
    perf = s.setdefault("performance", {})
    if perf.get("nav") and d_usd:
        perf["pending_flow"] = (perf.get("pending_flow") or 0.0) + d_usd


def set_holding(s, ticker, shares=None, avg_cost=None):
    if ledger_active(s):
        from domain.ledger import LedgerError
        raise LedgerError("ledger ทำงานอยู่ — บันทึกเป็นรายการ BUY / SELL / DEPOSIT / WITHDRAW แทน")
    store_sync.mark_local_edit()
    t = clean_ticker(ticker)
    h = s["holdings"].setdefault(t, {"shares": 0, "avg_cost": 0.0, "added": today()})
    if shares is not None:
        _book_shares(s, t, float(shares) - float(h.get("shares") or 0))
        h["shares"] = float(shares)
    if avg_cost is not None:
        h["avg_cost"] = float(avg_cost)
    return h


CASH_MAX = 1e9


def set_cash(s, usd):
    """Record the uninvested cash (USD). Raises ValueError outside 0..CASH_MAX."""
    usd = float(usd)
    if not (0 <= usd <= CASH_MAX):
        raise ValueError(f"cash must be between 0 and {CASH_MAX:,.0f} USD")
    if ledger_active(s):
        from domain.ledger import LedgerError
        raise LedgerError("ledger ทำงานอยู่ — บันทึกเป็นรายการ BUY / SELL / DEPOSIT / WITHDRAW แทน")
    store_sync.mark_local_edit()
    _book_cash(s, round(usd, 2) - cash_usd(s))
    s["cash"] = {"usd": round(usd, 2), "updated": today()}
    return s["cash"]


def cash_usd(s):
    """The stored cash in USD (0 when none was entered)."""
    try:
        return max(0.0, float((s.get("cash") or {}).get("usd") or 0))
    except (TypeError, ValueError):
        return 0.0


def remove_holding(s, ticker):
    if ledger_active(s):
        from domain.ledger import LedgerError
        raise LedgerError("ledger ทำงานอยู่ — บันทึกเป็นรายการ BUY / SELL / DEPOSIT / WITHDRAW แทน")
    store_sync.mark_local_edit()
    t = clean_ticker(ticker)
    _book_shares(s, t, -float((s.get("holdings", {}).get(t) or {}).get("shares") or 0))
    for k in _PER_TICKER:
        s.get(k, {}).pop(t, None)


# --- ledger ---------------------------------------------------------------- #
def ledger_active(s):
    return bool((s.get("ledger") or {}).get("txns"))


def _last_price(s, t):
    m = (s.get("momentum") or {}).get(t) or {}
    return m.get("price") or (s.get("facts", {}).get(t) or {}).get("price")


def ledger_start(s, usdthb=None):
    """Open the ledger from the book on file: one OPEN per holding at its average cost
    (the principal) and today's close (where since-start returns begin), and one for
    the cash. Baht principal is left to estimate at today's rate until edited."""
    from domain import ledger as L
    if ledger_active(s):
        raise L.LedgerError("ledger already started")
    store_sync.mark_local_edit()
    day, txns, n = today(), [], 0
    for t, h in sorted((s.get("holdings") or {}).items()):
        sh = float(h.get("shares") or 0)
        if sh <= 0:
            continue
        n += 1
        cost = float(h.get("avg_cost") or 0) or float(_last_price(s, t) or 0)
        txns.append({"id": n, "type": "OPEN", "date": day, "ticker": t, "shares": sh,
                     "price": cost, "mkt_price": _last_price(s, t) or cost,
                     "acquired": h.get("added") or day, "note": "ยกมาจากยอดถือ"})
    if cash_usd(s) > 0:
        n += 1
        txns.append({"id": n, "type": "OPEN", "date": day, "amount": cash_usd(s), "note": "เงินสดยกมา"})
    if not txns:
        raise L.LedgerError("ยังไม่มีหุ้นหรือเงินสดในพอร์ต — เริ่มด้วย DEPOSIT แทน")
    s["ledger"] = {"txns": txns, "next_id": n + 1, "started": day, "usdthb_at_start": usdthb}
    _apply_ledger(s)
    return s["ledger"]


def ledger_save(s, txn):
    """Add a transaction (or replace the one with the same id). Validated, then the
    whole ledger is replayed before anything is kept: a sell of more than is held
    raises LedgerError and leaves the store as it was. Returns the stored txn."""
    from domain import ledger as L
    t = {k: v for k, v in dict(txn).items() if v not in (None, "")}
    t["type"] = str(t.get("type") or "").upper()
    if t.get("ticker"):
        t["ticker"] = clean_ticker(t["ticker"])
    for k in ("shares", "price", "fee", "amount", "thb", "ratio", "mkt_price", "withheld"):
        if k in t:
            t[k] = float(t[k])
    L.validate(t)
    if not ledger_active(s) and ((s.get("holdings") or {}) or cash_usd(s) > 0):
        # the first entry would replace a book the ledger has never seen
        raise L.LedgerError("มีหุ้น/เงินสดในพอร์ตอยู่แล้ว — กด \"เริ่ม ledger\" เพื่อยกยอดมาก่อน")
    led = s.get("ledger") or {}
    if led.get("started") and t["type"] != "OPEN" and t["date"] < led["started"]:
        raise L.LedgerError(f"วันที่ {t['date']} อยู่ก่อนวันเริ่ม ledger ({led['started']}) — "
                            "รายการก่อนหน้านั้นรวมอยู่ในยอดยกมาแล้ว")
    txns = list(led.get("txns") or [])
    if t.get("id") is not None:
        t["id"] = int(t["id"])
        if not any(x["id"] == t["id"] for x in txns):
            raise L.LedgerError(f"no transaction {t['id']}")
        txns = [t if x["id"] == t["id"] else x for x in txns]
    else:
        t["id"] = int(led.get("next_id") or (max([x["id"] for x in txns], default=0) + 1))
        txns.append(t)
    L.replay(txns, config.DIVIDEND_WITHHOLDING)            # raises before anything is kept
    store_sync.mark_local_edit()
    led["txns"] = txns
    led["next_id"] = max(int(led.get("next_id") or 0), t["id"] + 1)
    led.setdefault("started", t["date"])
    s["ledger"] = led
    _apply_ledger(s)
    return t


def ledger_delete(s, txn_id):
    from domain import ledger as L
    led = s.get("ledger") or {}
    txns = [x for x in (led.get("txns") or []) if x["id"] != int(txn_id)]
    if len(txns) == len(led.get("txns") or []):
        raise L.LedgerError(f"no transaction {txn_id}")
    L.replay(txns, config.DIVIDEND_WITHHOLDING)
    store_sync.mark_local_edit()
    led["txns"] = txns
    _apply_ledger(s)


def _apply_ledger(s):
    """Holdings and cash as the ledger says. A name sold out leaves the book (its
    ledger history stays); a new name enters with today as its `added` date."""
    from domain import ledger as L
    book = L.replay(s["ledger"]["txns"], config.DIVIDEND_WITHHOLDING)
    held = L.holdings(book)
    hold = s.setdefault("holdings", {})
    for t in [t for t in hold if t not in held]:
        for k in _PER_TICKER:
            s.get(k, {}).pop(t, None)
    for t, h in held.items():
        cur = hold.setdefault(t, {"added": today()})
        cur["shares"], cur["avg_cost"] = h["shares"], h["avg_cost"]
    s["cash"] = {"usd": round(max(book["cash"], 0.0), 2), "updated": today()}
    return book


def ledger_flows_unbooked(s, day=None):
    """External flows no NAV entry has counted yet, whatever their date: the NAV is
    priced on the current book, which already holds every ledger entry, so a deposit
    dated after the close in use (Bangkok runs a day ahead of New York) and one typed
    in late both belong to the period being recorded."""
    from domain import ledger as L
    if not ledger_active(s):
        return [], 0.0
    booked = set(map(str, (s.get("performance") or {}).get("booked_flows") or []))
    book = L.replay(s["ledger"]["txns"], config.DIVIDEND_WITHHOLDING)
    new = [f for f in book["flows"] if str(f["id"]) not in booked]
    return [str(f["id"]) for f in new], sum(f["amount"] for f in new)


# --- watchlist ------------------------------------------------------------- #
def add_watch(s, ticker):
    store_sync.mark_local_edit()
    t = clean_ticker(ticker)
    if t and t not in s["watchlist"]:
        s["watchlist"].append(t)


def remove_watch(s, ticker):
    store_sync.mark_local_edit()
    t = clean_ticker(ticker)
    if t in s["watchlist"]:
        s["watchlist"].remove(t)


# --- FMP quota counter ----------------------------------------------------- #
def commit_watch_cache(s, entries):
    """Merge the watchlist's last-good subset + weekly peer fit, and drop entries for
    names no longer watched. An app write, not a user edit (not marked dirty)."""
    wc = s.setdefault("watch_cache", {})
    wc.update(entries or {})
    for t in [t for t in wc if t not in (s.get("watchlist") or [])]:
        del wc[t]


def sync_fmp_usage(s):
    """Copy today's FMP count from the request ledger (sources.fmp.LEDGER, every
    request FMP saw) into the store, which keeps the last week for the dashboard.
    The ledger is never lowered: a store restored from an older Drive copy brings
    its own figure up to the ledger, and a higher stored figure raises the ledger."""
    from sources import fmp
    d = today()
    fmp.LEDGER.raise_to(s["fmp_usage"].get(d, 0))
    s["fmp_usage"][d] = fmp.LEDGER.used()
    # keep only ~the last week (8 dated entries incl. today)
    cutoff = (dt.date.today() - dt.timedelta(days=7)).isoformat()
    s["fmp_usage"] = {k: v for k, v in s["fmp_usage"].items() if k >= cutoff}
    return s["fmp_usage"][d]


def fmp_used_today(s):
    from sources import fmp
    return max(s["fmp_usage"].get(today(), 0), fmp.LEDGER.used())
