"""
SEC EDGAR source adapter — the PRIMARY financial source (free, all US + 20-F filers).

Robust TTM = latest full fiscal year + current YTD - prior-year YTD, with
sum-of-4Q and latest-annual fallbacks. De-duplicates restated facts by keeping the
most recently filed value. Growth uses the clean annual series.

`extract` is table-driven: most fields are a tag ladder read under one period rule
(FIELD_LADDERS). The few fields that combine ladders or dates have their own step.
"""
import os
import json
import time
import logging
import threading
import datetime as dt

log = logging.getLogger("portfolio.sec")

# Throttle SEC requests across threads to honour SEC fair-access (<10 req/s).
_sec_lock = threading.Lock()
_sec_last = [0.0]


def _throttle(min_interval):
    with _sec_lock:
        wait = min_interval - (time.time() - _sec_last[0])
        if wait > 0:
            time.sleep(wait)
        _sec_last[0] = time.time()


USD = ("USD", "usd")
SHARES = ("shares",)


def _date(s):
    return dt.date.fromisoformat(s)


def _days(a, b):
    return (_date(b) - _date(a)).days


def _span(e):
    return _days(e["start"], e["end"])


def _is_annual(e):
    return bool(e.get("start")) and 350 <= _span(e) <= 380


def _is_quarter(e):
    return bool(e.get("start")) and 80 <= _span(e) <= 100


def _newest_first(es):
    return sorted(es, key=lambda e: e["end"], reverse=True)


def _one_per_year(es):
    """First entry per calendar year of `es` (already newest-first)."""
    out, seen = [], set()
    for e in es:
        yr = e["end"][:4]
        if yr not in seen:
            seen.add(yr)
            out.append(e)
    return out


def _units(node, prefer):
    if not node:
        return None, None
    u = node.get("units", {})
    for k in prefer:
        if k in u and u[k]:
            return u[k], k
    for k, arr in u.items():            # non-USD (IFRS filers like NVO/DKK)
        if arr and len(k) == 3:
            return arr, k
    for k, arr in u.items():
        if arr:
            return arr, k
    return None, None


class _Xbrl:
    """Read-only views over one filer's companyfacts namespace (us-gaap, else ifrs-full).

    What "annual", "quarter" and "instant" mean is defined here once, for every field."""

    def __init__(self, facts):
        self.ns = facts.get("us-gaap") or facts.get("ifrs-full") or {}
        self.q4_derived = set()        # fiscal-year ends whose Q4 quarters() computed
        self.stale_hit = None          # set by _freshest when it refuses a dead tag

    def entries(self, concept, prefer=USD):
        """One entry per (start, end) period, keeping the most recently filed."""
        u, _ = _units(self.ns.get(concept), prefer)
        if not u:
            return []
        best = {}
        for x in u:
            if not x.get("end"):
                continue
            k = (x.get("start"), x["end"])
            if k not in best or x.get("filed", "") > best[k].get("filed", ""):
                best[k] = x
        return list(best.values())

    def currency(self, concept):
        return _units(self.ns.get(concept), USD)[1]

    def latest(self, concept, prefer=USD):
        es = self.entries(concept, prefer)
        return max(es, key=lambda x: (x["end"], x.get("filed", ""))) if es else None

    def latest_end(self, concept, prefer=USD):
        return max((e["end"] for e in self.entries(concept, prefer)), default=None)

    def ttm(self, concept, prefer=USD):
        es = [e for e in self.entries(concept, prefer) if e.get("start")]
        if not es:
            return None
        latest_end = max(e["end"] for e in es)
        cur = max((e for e in es if e["end"] == latest_end), key=_span)
        if _is_annual(cur):
            return cur["val"]
        annual = max((e for e in es if _is_annual(e)), key=lambda e: e["end"], default=None)
        if annual:
            # latest FY + current YTD - the same YTD a year earlier
            prior = _year_earlier(es, latest_end, _span(cur))
            if prior and annual["end"] > prior["end"]:
                return annual["val"] + cur["val"] - prior["val"]
        last4 = list(self.quarters(concept, prefer).values())[:4]
        if len(last4) == 4:
            return sum(last4)
        return annual["val"] if annual else None

    def annual_series_dated(self, concept, prefer=USD):
        """[[FY_end, value], ...] newest first, one per year."""
        annuals = _newest_first(e for e in self.entries(concept, prefer) if _is_annual(e))
        return [[e["end"], e["val"]] for e in _one_per_year(annuals)]

    def annual_series(self, concept, prefer=USD):
        return [v for _, v in self.annual_series_dated(concept, prefer)]

    def quarters(self, concept, prefer=USD):
        """{end_date: value} for single-quarter periods, newest first.

        Most US filers never tag a fiscal-Q4 quarter: Q4 is inside the 10-K. It is
        derived as FY - 9M YTD (else FY - Q1..Q3) for each fiscal year that has no
        reported Q4; a reported Q4 always wins."""
        es = self.entries(concept, prefer)
        out = {}
        for e in _newest_first(e for e in es if _is_quarter(e)):
            out.setdefault(e["end"], e["val"])
        for fy in _one_per_year(_newest_first(e for e in es if _is_annual(e))):
            if any(abs(_days(fy["end"], end)) <= 7 for end in out):
                continue
            q4 = _q4_from_year(es, fy)
            if q4 is not None:
                out[fy["end"]] = q4
                self.q4_derived.add(fy["end"])
        return dict(sorted(out.items(), reverse=True))

    def instant_series(self, concept, prefer=USD):
        """Balance-sheet values, one per calendar year, newest first."""
        insts = _newest_first(e for e in self.entries(concept, prefer) if not e.get("start"))
        return [e["val"] for e in _one_per_year(insts)]

    def instant_at_dates(self, concepts, dates, prefer=USD, tol_days=6):
        """Balance-sheet value AT each fiscal-year-end date -> ({date: value}, tag).

        Read at the income statement's FY-end dates (±tol_days), never "the newest
        instant in that calendar year": a June-year-end filer files four instants a
        year, and pairing a March balance sheet with a June income statement breaks
        the per-year ROIC.

        ONE concept is chosen for the whole series (most dates covered, ladder order
        on ties). A per-date merge across tags is wrong: AVGO's FY2022-23 would fill
        from LongTermDebtCurrent, the current portion only ($0.4B vs ~$39B), and ROIC
        would read 130-140%. A year the chosen concept misses is simply absent."""
        want = [d for d in dates if d]
        if not want:
            return {}, None
        best = None
        for tag in concepts:
            by_date = {e["end"]: e["val"] for e in self.entries(tag, prefer)
                       if not e.get("start") and e.get("val") is not None}
            if not by_date:
                continue
            hit = {}
            for d in want:
                if d in by_date:
                    hit[d] = by_date[d]
                else:
                    near = [(abs(_days(d, e)), e) for e in by_date if abs(_days(d, e)) <= tol_days]
                    if near:
                        hit[d] = by_date[min(near)[1]]
            if hit and (best is None or len(hit) > len(best[0])):
                best = (hit, tag)
        return best if best else ({}, None)


def _q4_from_year(es, fy):
    """FY minus the nine months before it: the 9M YTD entry if filed, else the three
    quarters inside the year. None unless the nine months are complete."""
    def in_year(e):
        return e.get("start") and abs(_days(fy["start"], e["start"])) <= 7 and e["end"] < fy["end"]
    ytd = [e for e in es if in_year(e) and 255 <= _span(e) <= 285]
    if ytd:
        nine = max(ytd, key=lambda e: e["end"])["val"]
    else:
        qs = {}
        for e in es:
            if (_is_quarter(e) and e["end"] < fy["end"]
                    and _days(fy["start"], e["start"]) >= -7):
                qs.setdefault(e["end"], e["val"])
        if len(qs) != 3:
            return None
        nine = sum(qs.values())
    q4 = fy["val"] - nine
    return q4 if isinstance(q4, int) else round(q4, 6)


def _year_earlier(es, latest_end, span):
    """The entry of the same length ending ~365 days before `latest_end`."""
    prior, gap = None, 999
    for e in es:
        if abs(_span(e) - span) <= 15:
            g = abs((_date(latest_end) - _date(e["end"])).days - 365)
            if g <= 25 and g < gap:
                gap, prior = g, e
    return prior


def _recent(end_str, ref_str, days=540):
    """True if `end_str` is within `days` before `ref_str` (the latest filing)."""
    if not end_str:
        return False
    if not ref_str:
        return True
    try:
        return _date(end_str) >= _date(ref_str) - dt.timedelta(days=days)
    except Exception:
        return True


def _sum(*xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return sum(xs) if xs else None


def _or(values):
    """`a or b or c` over a lazy sequence: first truthy value, else the last one."""
    v = None
    for v in values:
        if v:
            return v
    return v


def _tag_units(ladder, prefer=USD):
    """A ladder item is a tag, or (tag, unit preference) for per-share/count units."""
    for item in ladder:
        yield (item, prefer) if isinstance(item, str) else item


# --------------------------------------------------------------------------- #
#  Tag ladders                                                                 #
# --------------------------------------------------------------------------- #
# Every ladder is written most-specific-first; `pick` and the period rules honour that
# order. IFRS variants go LAST so a US filer can never resolve to them by accident.
REV = ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
       "RevenueFromContractWithCustomerIncludingAssessedTax", "Revenue", "SalesRevenueNet"]
OP = ["OperatingIncomeLoss", "OperatingIncome", "OperatingProfitLoss", "ProfitLossFromOperatingActivities"]
NI = ["NetIncomeLoss", "ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"]
# EBIT fallback inputs — used ONLY when no OP concept above exists.
PRETAX = ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
          "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
          "ProfitLossBeforeTax"]
PRETAX_LEGS = ("IncomeLossFromContinuingOperationsBeforeIncomeTaxesDomestic",
               "IncomeLossFromContinuingOperationsBeforeIncomeTaxesForeign")
INT_EXP = ["InterestExpense", "InterestAndDebtExpense", "InterestExpenseDebt", "InterestExpenseNonoperating"]

CFO_TAGS = ["NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
            "CashFlowsFromUsedInOperatingActivities"]
CAPEX_TAGS = ["PaymentsToAcquirePropertyPlantAndEquipment",
              "PurchaseOfPropertyPlantAndEquipment",
              "PaymentsToAcquireProductiveAssets",
              "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
              "PaymentsToAcquireOtherPropertyPlantAndEquipment"]
GROSS_PROFIT_TAGS = ["GrossProfit"]
# ABBV and many pharma filers report no GrossProfit subtotal but do report the cost
# line, so gross profit is derived as revenue - cost (see domain/trend.py).
COST_OF_REV_TAGS = ["CostOfGoodsAndServicesSold", "CostOfRevenue", "CostOfSales",
                    "CostOfGoodsSold", "CostOfSalesExcludingAmortisation"]
# SBC per YEAR, not just TTM: CFO adds SBC back as a non-cash item, so FCF = CFO - capex
# is flattered unless SBC is deducted year by year (Damodaran: SBC is a real expense).
SBC_TAGS = ["ShareBasedCompensation",
            "AllocatedShareBasedCompensationExpense",
            "ShareBasedCompensationArrangementByShareBasedPaymentAwardCompensationCost",
            "ExpenseFromSharebasedPaymentTransactionsWithEmployees",
            "AdjustmentsToAdditionalPaidInCapitalSharebasedCompensationRequisiteServicePeriodRecognitionValue"]

COMBINED_DEBT = "DebtLongtermAndShorttermCombinedAmount"
LT_DEBT = ("LongTermDebtNoncurrent", "LongTermNotesAndLoans", "LongTermDebt",
           "LongTermNotesPayable", "Borrowings")
ST_DEBT = ("LongTermDebtCurrent", "DebtCurrent", "ShortTermBorrowings",
           "NotesPayableCurrent", "NotesAndLoansPayableCurrent")
EQUITY_TAGS = ("StockholdersEquity", "Equity",
               "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest")
# The current-year and prior-year lookups share one ladder each, so the two can never
# resolve to different concepts and produce a bogus delta.
AR_TAGS = ("AccountsReceivableNetCurrent", "ReceivablesNetCurrent",
           "AccountsAndOtherReceivablesNetCurrent")
INV_TAGS = ("InventoryNet", "InventoryGross", "Inventories")
AP_TAGS = ("AccountsPayableCurrent", "AccountsPayableTradeCurrent",
           "AccountsPayableAndAccruedLiabilitiesCurrent")
CASH_TAGS = ("CashAndCashEquivalentsAtCarryingValue", "CashAndCashEquivalents")
DEFREV_TAGS = ("ContractWithCustomerLiabilityCurrent", "ContractWithCustomerLiability",
               "DeferredRevenueCurrent", "DeferredRevenue")
RND_TAGS = ("ResearchAndDevelopmentExpense",
            "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost")
LEASE_TOTAL = "OperatingLeaseLiability"
LEASE_LEGS = ("OperatingLeaseLiabilityNoncurrent", "OperatingLeaseLiabilityCurrent")
# Per-share units: an IFRS filer reports EPS in its own currency.
EPS_DILUTED = ("EarningsPerShareDiluted", ("USD/shares",))


# --------------------------------------------------------------------------- #
#  Period rules: (xbrl, ladder, anchor) -> value                               #
# --------------------------------------------------------------------------- #
class _Anchor:
    """What every balance-sheet read is dated against."""

    def __init__(self, ref_end, fy_ends):
        self.ref_end = ref_end        # latest revenue period end
        self.fy_ends = fy_ends        # income-statement fiscal-year ends, newest first


# A flow or series whose newest period ends this long before the latest filing is not
# today's number: the filer moved to another tag and the old one stopped. NVDA's capex
# read $0.46B off a tag last filed for FY2020 while the live tag said $6.6B.
STALE_DAYS = 400


def _freshest(x, ladder, at, value_of, keep):
    """Value of the ladder tag whose data reaches CLOSEST TO THE PRESENT; ladder order
    breaks ties. A winner older than STALE_DAYS before the anchor is refused (None) and
    recorded on `x.stale_hit` so the caller can flag it instead of using it silently."""
    best = None                          # ((end, -index), value, tag)
    for i, (t, p) in enumerate(_tag_units(ladder)):
        v = value_of(t, p)
        if not keep(v):
            continue
        key = (x.latest_end(t, p) or "", -i)
        if best is None or key > best[0]:
            best = (key, v, t)
    if best is None:
        return None
    (end, _), v, tag = best
    if at.ref_end and end and not _recent(end, at.ref_end, STALE_DAYS):
        x.stale_hit = f"{tag} last filed {end}"
        return None
    return v


def _ttm(x, ladder, at):
    """Freshest truthy TTM down the ladder (a 0 falls through to the next tag)."""
    return _freshest(x, ladder, at, x.ttm, bool)


def _ttm_present(x, ladder, at):
    """Freshest TTM that exists down the ladder (a filed 0 is kept)."""
    return _freshest(x, ladder, at, x.ttm, lambda v: v is not None)


def _annual(x, ladder, at):
    return _freshest(x, ladder, at, x.annual_series, bool) or []


def _annual_dated(x, ladder, at):
    return _freshest(x, ladder, at, x.annual_series_dated, bool) or []


def _trend_series(x, ladder, at):
    """Freshest tag with >= 2 dated annual points, else [].

    Deliberately NOT a merge across tags: MSFT reports CostOfRevenue up to 2017 and
    CostOfGoodsAndServicesSold after, and a trend drawn across that break measures
    the accounting change, not the business."""
    return _freshest(x, ladder, at, x.annual_series_dated, lambda s: len(s or []) >= 2) or []


def _quarters(x, ladder, at):
    return _freshest(x, ladder, at, x.quarters, bool) or {}


def _latest_value(x, ladder, at):
    def val(t, p):
        e = x.latest(t, p)
        return e.get("val") if e else None
    return _freshest(x, ladder, at, val, lambda v: v is not None)


def _fresh(x, ladder, at):
    """Latest value among the ladder's tags measured CLOSEST TO THE PRESENT, within
    540d of the anchor. Ladder order breaks ties only.

    Date first, concept second: a balance-sheet stock must be read as near as possible
    to the period end of the flow it is divided by. ORCL files a combined debt tag
    annually and the split legs quarterly; ladder-first read debt off a 9-month-old
    balance sheet (-31%) and overstated ROIC by 5.5pp."""
    best = None
    for t, p in _tag_units(ladder):
        e = x.latest(t, p)
        if not e or e.get("val") is None or not _recent(e.get("end"), at.ref_end):
            continue
        if best is None or (e.get("end") or "") > (best.get("end") or ""):
            best = e
    return best["val"] if best else None


def _prior_year(x, ladder, at):
    """The previous year-end balance-sheet value of the freshest tag that has one."""
    def prior(t, p):
        s = x.instant_series(t, p)
        return s[1] if len(s) > 1 else None
    return _freshest(x, ladder, at, prior, lambda v: v is not None)


def _total_else_legs(rule):
    """Rule over (total ladder, *leg ladders): the filed total, else the sum of legs."""
    def read(x, ladders, at):
        total, *legs = ladders
        return rule(x, total, at) or _sum(*(rule(x, leg, at) for leg in legs))
    return read


def _total_debt(x, ladders, at):
    """Total debt read off ONE balance-sheet date: the latest that yields a figure.

    The combined tag must not short-circuit on `or`, and the two legs must not be
    summed across dates. ORCL files the combined amount annually (92.6B @ 2025-05-31)
    and the legs quarterly (134.6B @ 2026-02-28); either shortcut understated debt by
    31%, which flowed into invested capital, net debt, EV and every multiple."""
    def by_end(tags):
        out = {}
        for tag in tags:            # first tag in ladder order wins each date
            for e in x.entries(tag):
                d, v = e.get("end"), e.get("val")
                if d and v is not None and d not in out and _recent(d, at.ref_end):
                    out[d] = v
        return out

    comb, lt, st = (by_end(tags) for tags in ladders)
    for d in sorted(set(comb) | set(lt), reverse=True):
        if d in comb:
            return comb[d]
        if d in lt:
            # no current-debt tag on that date means none split out; a leg from
            # another date would be worse
            return _sum(lt[d], st.get(d))
    return None


def _ic_components(x, ladders, at):
    """{FY_end: {"equity", "debt", "cash", "lt_source"}} at the income-statement
    year-ends, for the per-year ROIC series.

    A year is returned ONLY when every leg is filed: invested capital from a partial
    balance sheet reads as a real change in the capital base. If a long-term debt
    concept appears in ANY year of the window it is required in EVERY year: a missing
    year is a filing gap, not a debt-free year."""
    (eq, _), (cash, _), (lt, lt_tag), (st, _) = (
        x.instant_at_dates(list(tags), at.fy_ends) for tags in ladders)
    out = {}
    for d in at.fy_ends:
        if d not in eq or d not in cash:
            continue
        if lt_tag and d not in lt:
            continue
        out[d] = {"equity": eq[d], "cash": cash[d],
                  "debt": (lt.get(d) or 0) + (st.get(d) or 0),
                  "lt_source": lt_tag}
    return out


LEASE_LADDERS = ((LEASE_TOTAL,), *((leg,) for leg in LEASE_LEGS))

# field -> (period rule, tag ladder), in evaluation order. Reported currency;
# normalize converts to USD.
FIELD_LADDERS = {
    "eps_quarters": (_quarters, (EPS_DILUTED, ("DilutedEarningsLossPerShare",
                                               ("USD/shares", "DKK/shares")))),
    # scalar eps_gaap is set against the USD listed price, so it is FX converted
    # downstream; eps_annuals_dated pairs with a LOCAL-currency price and is not.
    "eps_gaap": (_ttm, (EPS_DILUTED, ("DilutedEarningsLossPerShare",
                                      ("USD/shares", "DKK/shares", "EUR/shares")))),
    "shares_diluted": (_latest_value, tuple((t, SHARES) for t in (
        "WeightedAverageNumberOfDilutedSharesOutstanding", "AdjustedWeightedAverageShares",
        "WeightedAverageShares", "CommonStockSharesOutstanding"))),
    # The actual diluted count NET of buybacks, year by year. The SBC$/market-cap proxy
    # measures GROSS grants and disagrees in SIGN for MSFT and ABBV. A count, never
    # FX-converted.
    "shares_diluted_annuals": (_annual, (("WeightedAverageNumberOfDilutedSharesOutstanding", SHARES),)),
    "total_debt": (_total_debt, ((COMBINED_DEBT,), LT_DEBT, ST_DEBT)),
    "cash": (_fresh, CASH_TAGS),
    "equity": (_fresh, EQUITY_TAGS),
    "capex": (_ttm_present, CAPEX_TAGS),
    # Dated, not bare lists: two tags need not cover the same fiscal years, and
    # zipping by INDEX would divide FY2025 gross profit by FY2017 revenue. The date
    # forces domain/trend.py to align on it.
    "cfo_annuals_dated": (_trend_series, CFO_TAGS),
    "sbc_annuals_dated": (_trend_series, SBC_TAGS),
    "capex_annuals_dated": (_trend_series, CAPEX_TAGS),
    "gross_profit_annuals_dated": (_trend_series, GROSS_PROFIT_TAGS),
    "cost_of_revenue_annuals_dated": (_trend_series, COST_OF_REV_TAGS),
    "ic_components_dated": (_ic_components, (EQUITY_TAGS, CASH_TAGS,
                                             (COMBINED_DEBT, *LT_DEBT), ST_DEBT)),
    "dep_amort": (_ttm, ("DepreciationAndAmortization", "Depreciation",
                         "DepreciationDepletionAndAmortization",
                         "DepreciationAndAmortisationExpense",
                         "DepreciationAmortisationAndImpairmentLossReversalOfImpairmentLossRecognisedInProfitOrLoss")),
    # ORCL stopped tagging the total in 2018 and files the domestic + foreign legs
    "income_before_tax": (_total_else_legs(_ttm), (tuple(PRETAX), PRETAX_LEGS[:1], PRETAX_LEGS[1:])),
    "tax_expense": (_ttm, ("IncomeTaxExpenseBenefit", "IncomeTaxExpenseContinuingOperations")),
    "cfo": (_ttm_present, CFO_TAGS),
    "sbc": (_ttm_present, SBC_TAGS),
    "total_assets": (_fresh, ("Assets",)),
    "receivables": (_fresh, AR_TAGS),
    "receivables_prior": (_prior_year, AR_TAGS),
    # Working capital comes from the BALANCE SHEET, not IncreaseDecreaseInOperatingCapital,
    # whose sign convention varies by filer. (AR + inventory - AP) has one reading.
    "inventory": (_fresh, INV_TAGS),
    "inventory_prior": (_prior_year, INV_TAGS),
    "accounts_payable": (_fresh, AP_TAGS),
    "accounts_payable_prior": (_prior_year, AP_TAGS),
    "interest_expense": (_ttm, tuple(INT_EXP)),
    "rnd_expense": (_ttm, RND_TAGS),
    "rnd_annuals": (_annual, RND_TAGS),
    "equity_prior": (_prior_year, EQUITY_TAGS),
    "total_debt_prior": (_total_else_legs(_prior_year),
                         ((COMBINED_DEBT,), (COMBINED_DEBT, *LT_DEBT), ST_DEBT)),
    "cash_prior": (_prior_year, CASH_TAGS),
    "acquisitions_net": (_ttm, ("PaymentsToAcquireBusinessesNetOfCashAcquired",
                                "PaymentsToAcquireBusinessesAndInterestInAffiliates",
                                "PaymentsToAcquireBusinessesGross")),
    "deferred_revenue": (_fresh, DEFREV_TAGS),
    "deferred_revenue_prior": (_prior_year, DEFREV_TAGS),
    # Leases are debt (Damodaran); the prior year too, or a lease-heavy filer is
    # compared against its own lease-free past and reads as deteriorating.
    "operating_leases": (_total_else_legs(_fresh), LEASE_LADDERS),
    "operating_leases_prior": (_total_else_legs(_prior_year), LEASE_LADDERS),
    "eps_annuals_dated": (_annual_dated, (EPS_DILUTED,)),
}


# --------------------------------------------------------------------------- #
#  Headline income-statement lines                                             #
# --------------------------------------------------------------------------- #
def _pick(x, concepts):
    """(value, concept, same-period alternatives that disagree by >5%).

    The concept with the FRESHEST data wins (AVGO moved net income from NetIncomeLoss
    to ProfitLoss), then LIST ORDER. Not the larger |TTM|: a filer tagging both
    `Revenues` and the ASC 606 core concept would get the broader number, which can
    carry other income, deflate every margin and make the reverse DCF too kind."""
    best = None                      # ((end, -list_index), value, concept)
    for i, c in enumerate(concepts):
        le, v = x.latest_end(c), x.ttm(c)
        if le and v is not None:
            key = (le, -i)
            if best is None or key > best[0]:
                best = (key, v, c)
    if best is None:
        return None, None, []
    _, val, concept = best
    end = x.latest_end(concept)
    alts = [(c, x.ttm(c)) for c in concepts
            if c != concept and x.latest_end(c) == end and x.ttm(c) is not None
            and val and abs(x.ttm(c) - val) / abs(val) > 0.05]
    return val, concept, alts


def _conflicts(*picked):
    """Two concepts covering the SAME period that disagree by >5% are a real ambiguity;
    list order resolves it predictably, and this says so."""
    return [f"{fld}: used {used} ({base/1e9:.2f}B) but {alt} covers the same "
            f"period at {av/1e9:.2f}B"
            for fld, (base, used, alts) in picked
            if alts and base for alt, av in alts]


def _derived_ebit(x):
    """EBIT = pretax income + interest expense, for filers with no OP concept.

    LLY and PFE tag no operating-income subtotal; without it the engine mistakes a very
    profitable company for a pre-profit one. Approximate (pretax carries non-operating
    items), so it is flagged via provenance."""
    out = {"operating_income": None, "operating_income_annuals": [],
           "operating_income_annuals_dated": [], "operating_income_quarters": {}}
    pre_c = next((t for t in PRETAX if x.latest_end(t)), None)
    int_c = next((t for t in INT_EXP if x.latest_end(t)), None)
    if pre_c:
        p_ttm = x.ttm(pre_c)
        if p_ttm is not None:
            out["operating_income"] = p_ttm + ((x.ttm(int_c) or 0) if int_c else 0)
        pa = dict(x.annual_series_dated(pre_c))
        ia = dict(x.annual_series_dated(int_c)) if int_c else {}
        dated = [[d, pa[d] + ia.get(d, 0)] for d in sorted(pa, reverse=True)]
        iq = x.quarters(int_c) if int_c else {}
        out["operating_income_annuals"] = [v for _, v in dated]
        out["operating_income_annuals_dated"] = dated
        out["operating_income_quarters"] = {d: v + iq.get(d, 0)
                                            for d, v in x.quarters(pre_c).items()}
    out["operating_income_derived"] = out["operating_income"] is not None
    return out


def _series(x, concept, name):
    return {name + "_annuals": x.annual_series(concept),
            name + "_annuals_dated": x.annual_series_dated(concept),
            name + "_quarters": x.quarters(concept)}


def extract(companyfacts):
    """Return a dict of SEC-derived financial values (in reported currency)."""
    x = _Xbrl(companyfacts.get("facts", companyfacts))

    rev, rev_concept, rev_alts = _pick(x, REV)
    net_income, ni_concept, ni_alts = _pick(x, NI)
    oi, op_concept, _ = _pick(x, OP)
    ebit = None if op_concept else _derived_ebit(x)
    revenue = _series(x, rev_concept, "revenue")
    operating = ({"operating_income": oi, **_series(x, op_concept, "operating_income"),
                  "operating_income_derived": False} if op_concept else ebit)

    # The trend strip's fiscal-year ends come from the INCOME statement, since every
    # ratio there has it in the numerator; the balance sheet is read at these dates.
    fy_ends = [e for e, _ in (operating["operating_income_annuals_dated"]
                              or revenue["revenue_annuals_dated"])][:12]
    at = _Anchor(x.latest_end(rev_concept), fy_ends)
    fields, stale = {}, {}
    for field, (rule, ladder) in FIELD_LADDERS.items():
        x.stale_hit = None
        fields[field] = rule(x, ladder, at)
        if x.stale_hit and not fields[field] and fields[field] != 0:
            stale[field] = x.stale_hit
    return {
        "currency": x.currency(rev_concept) or "USD",
        "latest_period_end": at.ref_end,
        "revenue": rev,
        "net_income": net_income,
        "concept_conflicts": _conflicts(("revenue", (rev, rev_concept, rev_alts)),
                                        ("net income", (net_income, ni_concept, ni_alts))),
        **revenue,
        **operating,
        **fields,
        "stale_fields": stale,
        "q4_derived_ends": sorted(x.q4_derived),
    }


# Fields copied onto FinancialFacts, in provenance order.
POPULATED = (
    "currency", "revenue", "net_income", "operating_income", "eps_gaap",
    "shares_diluted", "total_debt", "cash", "equity", "capex", "dep_amort",
    "income_before_tax", "tax_expense", "revenue_annuals", "revenue_quarters",
    "operating_income_quarters", "eps_quarters",
    "cfo", "total_assets", "receivables", "interest_expense", "rnd_expense",
    "rnd_annuals", "operating_income_annuals",
    "equity_prior", "total_debt_prior", "cash_prior",
    "acquisitions_net", "deferred_revenue", "deferred_revenue_prior",
    "operating_leases", "operating_leases_prior",
    "sbc",
    "shares_diluted_annuals",        # a COUNT, never FX-converted
    "receivables_prior", "inventory", "inventory_prior",
    "accounts_payable", "accounts_payable_prior",
    "eps_annuals_dated",
    "revenue_annuals_dated", "operating_income_annuals_dated",
    "cfo_annuals_dated", "capex_annuals_dated", "sbc_annuals_dated",
    "gross_profit_annuals_dated", "cost_of_revenue_annuals_dated",
    "ic_components_dated")
EBIT_DERIVED = ("operating_income", "operating_income_annuals", "operating_income_quarters")
QUARTER_FIELDS = ("revenue_quarters", "operating_income_quarters", "eps_quarters")


def populate(ff, companyfacts):
    """Fill SEC-sourced fields into a FinancialFacts object (source tag 'sec')."""
    d = extract(companyfacts)
    for k in POPULATED:
        ff.set(k, d.get(k), "sec")
    if d.get("operating_income_derived"):
        for k in EBIT_DERIVED:
            if d.get(k) is not None:
                ff.provenance[k] = "sec-derived (EBIT = pretax + interest)"
    for k in QUARTER_FIELDS:
        derived = sorted(set(d.get(k) or {}) & set(d.get("q4_derived_ends") or ()))
        if derived:
            ff.provenance[k] = (ff.provenance.get(k, "sec") + " + derived Q4 (FY - 9M YTD) for "
                                + ", ".join(derived))
    for c in d.get("concept_conflicts") or []:
        ff.flags.append("SEC tag conflict - " + c)
    for field, why in sorted((d.get("stale_fields") or {}).items()):
        ff.flags.append(f"DATA[warn] stale tag - {field} not used: {why}, over {STALE_DAYS}d "
                        f"before the latest filing {d.get('latest_period_end')}")
    if d.get("latest_period_end"):
        ff.set("fiscal_year", d["latest_period_end"], "sec")
    return ff, d


# fetch (runs where SEC is reachable)
def fetch_companyfacts(cik, user_agent, requests_mod=None, timeout=30,
                       cache_dir=None, ttl_hours=12, min_interval=0.15):
    """Fetch SEC companyfacts, with a disk cache (filings change quarterly, so a
    short TTL avoids re-downloading multi-MB JSON every refresh) and a global
    throttle to respect SEC fair-access."""
    import requests as _r
    requests_mod = requests_mod or _r
    cik10 = str(cik).zfill(10)
    path = os.path.join(cache_dir, f"companyfacts_{cik10}.json") if cache_dir else None
    if path and os.path.exists(path):
        try:
            if (time.time() - os.path.getmtime(path)) < ttl_hours * 3600:
                with open(path, encoding="utf-8") as fh:
                    return json.load(fh)
        except Exception as e:
            log.warning("companyfacts cache read failed (%s): %s", cik10, e)
    _throttle(min_interval)
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json"
    try:
        r = requests_mod.get(url, headers={"User-Agent": user_agent}, timeout=timeout)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        # Serve the EXPIRED cache (flagged) instead of failing the ticker: filings
        # change quarterly, so stale-but-real beats nothing during an SEC outage.
        # Callers surface "_stale_cache" as a data flag.
        if path and os.path.exists(path):
            log.warning("SEC fetch failed (%s); serving stale cache: %s", cik10, e)
            try:
                with open(path, encoding="utf-8") as fh:
                    stale = json.load(fh)
                stale["_stale_cache"] = True
                return stale
            except Exception as e2:
                log.warning("stale companyfacts cache read failed (%s): %s", cik10, e2)
        raise
    if path:
        try:
            os.makedirs(cache_dir, exist_ok=True)
            tmp = f"{path}.tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            os.replace(tmp, path)
        except Exception as e:
            log.warning("companyfacts cache write failed (%s): %s", cik10, e)
    return data
