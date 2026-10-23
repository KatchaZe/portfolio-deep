"""
quarterly — a TTM base for a foreign filer whose SEC base is a year-old 20-F.

ASML, TSM and NVO report quarters on 6-K, which carries no XBRL, so SEC companyfacts
stops at the last 20-F and the DCF runs on a base 7-10 months behind the price (audit
2026-10-21: ASML FV $1,196 vs $1,536 on the reported quarters, TSM $322 vs $477).
User decision 2026-10-24: rebuild the DCF base from quarterly statements.

Source: Alpha Vantage INCOME_STATEMENT / BALANCE_SHEET / CASH_FLOW (quarterly),
checked by Yahoo fundamentals-timeseries. Nothing is trusted on its own word:

  1. CALIBRATION. The four AV quarters ending at the filed fiscal year must reproduce
     the filed figures (revenue <= 1%, every other field <= 5%). A field that does not
     reproduce is not moved; it is cleared (an old FY flow beside a new TTM is the
     clock mismatch). Balance-sheet definitions differ by filer (ASML's
     shortLongTermDebtTotal leaves out the current portion, TSM's includes leases), so
     each candidate definition is tried and the one that reproduces the filing is used.
  2. CURRENCY. Every quarter used is in the filer's reporting currency. A quarter
     with no currency (TSM Q3 2026, live) is accepted only when the other three agree
     and its revenue is within 0.5-2x the quarter before; the card says so.
  3. SECOND SOURCE. Every quarter Yahoo also has must agree within 2% on revenue and
     operating income, else the whole base is refused. A quarter only AV has is named.
  4. ONE-OFFS. A quarter whose operating margin exceeds 1.5x the median of the four is
     named (NVO Q1 2026: a provision reversal doubled EBIT); the add-back rules only
     cover charges, so a gain is shown, not removed.

The scalars then go through dataquality.apply_stale_fallback (the one registered FX
conversion site) with the fiscal-year series KEPT: TTM scalars beside FY series is the
shape every US 10-Q filer already has, and the engine reads both on their own clocks.
Pure except `run`, which fetches.
"""
import datetime as dt
import logging

from domain.facts import FinancialFacts
from pipeline import dataquality

log = logging.getLogger("portfolio.quarterly")

GAP_DAYS = (80, 100)             # consecutive fiscal quarters
FY_MATCH_DAYS = 10               # the AV window must end on the filed fiscal year end
REVENUE_TOL, FIELD_TOL, YAHOO_TOL = 0.01, 0.05, 0.02
SPIKE = 1.5

INCOME = {"revenue": "totalRevenue", "operating_income": "operatingIncome",
          "net_income": "netIncome", "income_before_tax": "incomeBeforeTax",
          "tax_expense": "incomeTaxExpense", "interest_expense": "interestExpense",
          "rnd_expense": "researchAndDevelopment"}
CASH = {"cfo": "operatingCashflow", "capex": "capitalExpenditures",
        "dep_amort": "depreciationDepletionAndAmortization"}


def _date(s):
    return dt.date.fromisoformat(str(s)[:10])


def window(quarters, end=None):
    """The four consecutive quarter ends (newest first) ending at `end` (or the newest),
    else None."""
    ends = sorted(quarters, reverse=True)
    if end is not None:
        ends = [e for e in ends if abs((_date(e) - _date(end)).days) <= FY_MATCH_DAYS] + \
               [e for e in ends if _date(e) < _date(end) - dt.timedelta(days=FY_MATCH_DAYS)]
    if len(ends) < 4:
        return None
    w = ends[:4]
    for a, b in zip(w, w[1:]):
        if not GAP_DAYS[0] <= (_date(a) - _date(b)).days <= GAP_DAYS[1]:
            return None
    return w


def _sum(quarters, w, key):
    vals = [quarters[e].get(key) for e in w]
    return None if any(v is None for v in vals) else sum(vals)


def _close(a, b, tol):
    if a is None or b is None:
        return False
    if b == 0:
        return a == 0
    return abs(a / b - 1) <= tol


def currency_check(inc, w, ccy):
    """(ok, note). Every quarter in `w` reports `ccy`; one blank currency is tolerated
    when the rest agree and its revenue sits within 0.5-2x the previous quarter's."""
    blanks = [e for e in w if not inc[e].get("currency")]
    wrong = [e for e in w if inc[e].get("currency") and inc[e]["currency"] != ccy]
    if wrong:
        return False, f"quarter {wrong[0]} reports {inc[wrong[0]]['currency']}, not {ccy}"
    if len(blanks) > 1:
        return False, f"{len(blanks)} quarters carry no currency"
    if blanks:
        e = blanks[0]
        prev = sorted(q for q in inc if q < e)
        r0, r1 = inc[e].get("totalRevenue"), inc[prev[-1]].get("totalRevenue") if prev else None
        if not (r0 and r1 and 0.5 <= r0 / r1 <= 2):
            return False, f"quarter {e} carries no currency and its revenue is out of line"
        return True, f"Alpha Vantage gives no currency for {e}; read as {ccy} like the other quarters"
    return True, None


def yahoo_check(inc, w, yq):
    """(ok, note) against Yahoo's quarters: each shared quarter within YAHOO_TOL."""
    shared = [e for e in w if e in (yq or {})]
    for e in shared:
        for mine, theirs in (("totalRevenue", "revenue"), ("operatingIncome", "operating_income")):
            a, b = inc[e].get(mine), yq[e].get(theirs)
            if a is not None and b is not None and not _close(a, b, YAHOO_TOL):
                return False, f"{e} {theirs}: Alpha Vantage {a:,.0f} vs Yahoo {b:,.0f}"
    only = [e for e in w if e not in (yq or {})]
    if not shared:
        return True, "no Yahoo quarter to cross-check (Alpha Vantage only)"
    return True, (f"{', '.join(only)} from Alpha Vantage only (Yahoo not yet updated)" if only else None)


def spikes(inc, w):
    m = {e: inc[e]["operatingIncome"] / inc[e]["totalRevenue"] for e in w
         if inc[e].get("operatingIncome") is not None and inc[e].get("totalRevenue")}
    if len(m) < 4:
        return []
    med = sorted(m.values())[1:3]
    med = sum(med) / 2
    return [e for e, v in m.items() if med > 0 and v > SPIKE * med]


# balance-sheet definitions tried in turn against the filed fiscal-year figure
def _bs(q, *keys):
    vals = [q.get(k) for k in keys]
    return None if vals[0] is None else sum(v or 0 for v in vals)


CASH_DEFS = (("cash + short-term investments", ("cashAndCashEquivalentsAtCarryingValue", "shortTermInvestments")),
             ("cash", ("cashAndCashEquivalentsAtCarryingValue",)))
DEBT_DEFS = (("total debt", ("shortLongTermDebtTotal",)),
             ("long-term + current portion", ("longTermDebt", "currentLongTermDebt")),
             ("long-term + short-term", ("longTermDebt", "shortTermDebt")),
             ("total debt + leases", ("shortLongTermDebtTotal", "capitalLeaseObligations")),
             ("long-term + current portion + leases", ("longTermDebt", "currentLongTermDebt", "capitalLeaseObligations")))


def _calibrated(defs, at_fy, now, target, tol=FIELD_TOL):
    """(value now, definition) for the first definition that reproduces `target` at the
    fiscal year end, else (None, None)."""
    for name, keys in defs:
        if _close(_bs(at_fy, *keys), target, tol):
            return _bs(now, *keys), name
    return None, None


def build(ff, inc, bs, cf, yq, ccy, fx_rate):
    """(alt FinancialFacts in `ccy`, notes) or (None, reason).

    `ff` holds the filed fiscal year in USD at `fx_rate` (USD per unit of `ccy`);
    `inc`/`bs`/`cf` are parse_quarters() of the three AV statements; `yq` is Yahoo's."""
    if not (inc and fx_rate):
        return None, "no quarterly statements"
    fy = getattr(ff, "fiscal_year", None)
    w_now, w_fy = window(inc), window(inc, fy) if fy else None
    if not w_now:
        return None, "Alpha Vantage has no four consecutive quarters"
    if not w_fy or abs((_date(w_fy[0]) - _date(fy)).days) > FY_MATCH_DAYS:
        return None, f"Alpha Vantage has no quarters ending at the filed year end {fy}"
    if _date(w_now[0]) <= _date(fy) + dt.timedelta(days=FY_MATCH_DAYS):
        return None, f"no quarter after {fy}"
    local = lambda v: None if v is None else v / fx_rate      # noqa: E731  (ff is USD)
    rev_fy = _sum(inc, w_fy, "totalRevenue")
    if not _close(rev_fy, local(ff.revenue), REVENUE_TOL):
        return None, (f"Alpha Vantage FY revenue {rev_fy or 0:,.0f} does not reproduce the filed "
                      f"{local(ff.revenue) or 0:,.0f} {ccy}")
    for chk in (currency_check(inc, w_now, ccy), yahoo_check(inc, w_now, yq)):
        if not chk[0]:
            return None, chk[1]
    notes = [n for n in (currency_check(inc, w_now, ccy)[1], yahoo_check(inc, w_now, yq)[1]) if n]
    alt = FinancialFacts(ff.ticker)
    alt.currency, alt.fiscal_year = ccy, w_now[0]
    src = f"alphavantage quarterly TTM to {w_now[0]}"
    moved, skipped = [], []
    for name, key in INCOME.items():
        filed, mine = local(getattr(ff, name, None)), _sum(inc, w_fy, key)
        tol = REVENUE_TOL if name == "revenue" else FIELD_TOL
        if filed is not None and _close(mine, filed, tol):
            alt.set(name, _sum(inc, w_now, key), src)
            moved.append(name)
        else:
            skipped.append(name)
    cfq = cf or {}
    cw_fy, cw_now = window(cfq, fy) if cfq else None, window(cfq) if cfq else None
    for name, key in CASH.items():
        filed = local(getattr(ff, name, None))
        mine = _sum(cfq, cw_fy, key) if cw_fy else None
        if cw_now and cw_now[0] == w_now[0] and filed is not None and _close(abs(mine or 0) or None, abs(filed), FIELD_TOL):
            alt.set(name, abs(_sum(cfq, cw_now, key)), src)
            moved.append(name)
        else:
            skipped.append(name)
    b_fy, b_now = (bs or {}).get(w_fy[0]), (bs or {}).get(w_now[0]) or {}
    kept = []
    filed_debt = (getattr(ff, "total_debt", None) or 0) + (getattr(ff, "operating_leases", None) or 0)
    balance = (("cash", CASH_DEFS, getattr(ff, "cash", None)),
               ("total_debt", DEBT_DEFS, filed_debt or None),
               ("equity", (("equity", ("totalShareholderEquity",)),), getattr(ff, "equity", None)),
               ("total_assets", (("assets", ("totalAssets",)),), getattr(ff, "total_assets", None)))
    for name, defs, filed_usd in balance:
        v, how = _calibrated(defs, b_fy, b_now, local(filed_usd)) if b_fy else (None, None)
        if how and v is not None:
            alt.set(name, v, f"{src} ({how})")
            moved.append(name)
        elif filed_usd is not None:
            # A balance is a point in time, not a flow: the filed year-end figure is
            # the nearest true value when the quarter cannot be read on the same
            # definition (ASML's Q3 2026 balance sheet leaves its debt lines blank).
            alt.set(name, local(filed_usd), f"filed {fy} (quarterly not reproducible)")
            kept.append(name)
    if kept:
        notes.append(f"balance as filed at {fy} (the quarter could not be read on the filed "
                     f"definition): {', '.join(kept)}")
    for name in ("revenue", "operating_income"):
        if name not in moved:
            return None, f"Alpha Vantage {name} does not reproduce the filed fiscal year"
    sp = spikes(inc, w_now)
    if sp:
        notes.append(f"operating margin in {', '.join(sp)} is over {SPIKE}x the other quarters' "
                     f"(a one-time gain is not removed)")
    notes.append(f"TTM base to {w_now[0]} from quarterly statements: {', '.join(moved)}"
                 + (f"; not reproduced, cleared: {', '.join(skipped)}" if skipped else ""))
    return alt, notes


def apply(ff, alt, notes, sec_currency, sec_fx, fetch_rate):
    """Move the calibrated TTM scalars onto ff through the registered conversion site,
    keeping the fiscal-year series. Returns True when applied."""
    applied, more = dataquality.apply_stale_fallback(ff, alt, sec_currency, sec_fx, fetch_rate,
                                                      keep_series=True)
    if applied:
        ff.flags.extend(f"QUARTERLY {n}" for n in notes)
    else:
        ff.flags.extend(f"QUARTERLY not used: {n}" for n in more)
    return applied


def run(ff, t, sec_currency, sec_fx, key, cache_dir, fetch_rate):
    """Fetch (cached 7 days), build and apply. Returns AV calls spent. Never raises."""
    from sources import alphavantage, yahoo
    if not key or not any(d["code"] == "interim_not_used" for d in dataquality.assess(ff)):
        return 0
    calls, raw = 0, {}
    try:
        for fn in alphavantage.STATEMENTS:
            raw[fn], c = alphavantage.cached(
                f"q_{fn.lower()}", t, key, lambda s, k, fn=fn: alphavantage.fetch_statement(fn, s, k),
                cache_dir, ttl_days=alphavantage.STATEMENT_TTL_DAYS)
            calls += c
        try:
            yq = yahoo.parse_quarterly_timeseries(yahoo.fetch_quarterly_timeseries(t))
        except Exception as e:
            log.info("%s Yahoo quarterly failed: %s", t, e)
            yq = {}
        inc = alphavantage.parse_quarters(raw["INCOME_STATEMENT"])
        ccy = next((q["currency"] for _, q in sorted(inc.items(), reverse=True) if q.get("currency")), None)
        fx_rate = dataquality.fallback_rate(ccy, sec_currency, sec_fx, fetch_rate) if ccy else None
        alt, notes = build(ff, inc, alphavantage.parse_quarters(raw["BALANCE_SHEET"]),
                           alphavantage.parse_quarters(raw["CASH_FLOW"]), yq, ccy, fx_rate)
        if alt is None:
            ff.flags.append(f"QUARTERLY not used: {notes}")
            return calls
        apply(ff, alt, notes, sec_currency, sec_fx, fetch_rate)
    except Exception as e:
        log.warning("%s quarterly base failed: %s", t, e)
        ff.flags.append(f"QUARTERLY not used: {type(e).__name__}: {str(e)[:60]}")
    return calls
