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


# Financial statements come only from periodic reports. Companyfacts also carries facts
# from proxy statements, registration statements and 8-Ks; a DEF 14A pay-versus-
# performance table tags NetIncomeLoss with the sign flipped (INGN FY2025: +22.7M
# against the 10-K's -22.7M) and, filed later, won the latest-filing tie-break.
PERIODIC_FORMS = frozenset(("10-K", "10-K/A", "10-Q", "10-Q/A", "10-KT", "10-QT",
                            "20-F", "20-F/A", "40-F", "40-F/A"))
# A foreign private issuer (one that files 20-F or 40-F) publishes its annual and interim
# statements on 6-K as well: NVO's FY2015-2018 revenue and EBIT exist only there.
FOREIGN_FORMS = frozenset(("20-F", "20-F/A", "40-F", "40-F/A"))
FOREIGN_INTERIM_FORMS = frozenset(("6-K", "6-K/A"))


def _periodic(e, foreign=False):
    """A fact from a periodic report. A fact with no form is a hand-built test fact."""
    f = e.get("form")
    return f is None or f in PERIODIC_FORMS or (foreign and f in FOREIGN_INTERIM_FORMS)


def _forms(ns):
    return {e.get("form") for node in ns.values() for arr in (node.get("units") or {}).values()
            for e in arr}


TTM_GAP_DAYS = (80, 100)   # same rule as pipeline/quarterly.GAP_DAYS (sources/ must not import pipeline/)


class _Xbrl:
    """Read-only views over one filer's companyfacts namespace (us-gaap, else ifrs-full).

    What "annual", "quarter" and "instant" mean is defined here once, for every field."""

    def __init__(self, facts, periodic_only=True):
        self.ns = facts.get("us-gaap") or facts.get("ifrs-full") or {}
        self.periodic_only = periodic_only
        self.foreign = bool(_forms(self.ns) & FOREIGN_FORMS)
        self.standard = ("us-gaap" if facts.get("us-gaap") else
                         "ifrs" if facts.get("ifrs-full") else None)
        self.q4_derived = set()        # fiscal-year ends whose Q4 quarters() computed

    def entries(self, concept, prefer=USD):
        """One entry per (start, end) period, keeping the most recently filed."""
        u, _ = _units(self.ns.get(concept), prefer)
        if not u:
            return []
        best = {}
        for x in u:
            if not x.get("end") or (self.periodic_only and not _periodic(x, self.foreign)):
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
        return self._ttm(concept, prefer)[0]

    def ttm_gapped(self, concept, prefer=USD):
        """True when this concept's TTM is None because its last four quarters skip a period."""
        v, gapped = self._ttm(concept, prefer)
        return gapped and v is None

    def _ttm(self, concept, prefer=USD):
        """(value, quarters_not_contiguous). Pure: callers decide whether a gap matters."""
        es = [e for e in self.entries(concept, prefer) if e.get("start")]
        if not es:
            return None, False
        latest_end = max(e["end"] for e in es)
        cur = max((e for e in es if e["end"] == latest_end), key=_span)
        if _is_annual(cur):
            return cur["val"], False
        annual = max((e for e in es if _is_annual(e)), key=lambda e: e["end"], default=None)
        if annual:
            # latest FY + current YTD - the same YTD a year earlier
            prior = _year_earlier(es, latest_end, _span(cur))
            if prior and annual["end"] > prior["end"]:
                return annual["val"] + cur["val"] - prior["val"], False
        q4 = list(self.quarters(concept, prefer).items())[:4]
        if len(q4) == 4 and all(
                TTM_GAP_DAYS[0] <= _days(q4[i + 1][0], q4[i][0]) <= TTM_GAP_DAYS[1]
                for i in range(3)):
            return sum(v for _, v in q4), False
        # four quarters that skip a period are not a year (SPCX summed Q2'25+Q3'25+Q2'26+Q3'26)
        if annual and _days(annual["end"], latest_end) <= 100:
            return annual["val"], True
        return None, len(q4) == 4

    def annual_series_dated(self, concept, prefer=USD):
        """[[FY_end, value], ...] newest first, one per year."""
        annuals = _newest_first(e for e in self.entries(concept, prefer) if _is_annual(e))
        return [[e["end"], e["val"]] for e in _one_per_year(annuals)]

    def fy_series_dated(self, concept, prefer=USD):
        """{FY_end: value}, one per fiscal-year END DATE (latest filing wins). Unlike
        annual_series_dated this keeps two year ends in one calendar year (JNJ's FY2022
        ends 2023-01-01, FY2023 2023-12-31) and never drops a 52/53-week year."""
        best = {}
        for e in self.entries(concept, prefer):
            if _is_annual(e) and (e["end"] not in best or e.get("filed", "") > best[e["end"]].get("filed", "")):
                best[e["end"]] = e
        return {d: e["val"] for d, e in best.items()}

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
       "RevenueFromContractWithCustomerIncludingAssessedTax", "Revenue", "SalesRevenueNet",
       "RevenuesNetOfInterestExpense"]
# Income-statement TOP LINES. ASC 606 contract revenue is a subset of them: it leaves out
# interest on a credit book (MELI $24.5B of $35.2B, SOFI $0.6B of $4.3B) and derivative
# or lease revenue (CEG). Filed for the same period and >5% larger, the top line wins.
REV_TOTALS = ("Revenues", "RevenuesNetOfInterestExpense")
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
              "PaymentsToAcquireOtherPropertyPlantAndEquipment"]      # LLY since 2025
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
            "ExpenseFromSharebasedPaymentTransactionsWithEmployees"]

# Debt: a filed total wins on its date, else long-term + current legs off that date.
# Ladders run most-specific-first; the convertible and capital-lease variants come
# last, so they only answer for a filer that tags nothing else (HIMS files only
# ConvertibleDebtNoncurrent; KO only the ...AndCapitalLeaseObligations family).
COMBINED_DEBT = ("DebtLongtermAndShorttermCombinedAmount",
                 "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities")
LT_DEBT = ("LongTermDebtNoncurrent", "LongTermNotesAndLoans", "LongTermDebt",
           "LongTermNotesPayable", "Borrowings", "LongTermDebtAndCapitalLeaseObligations",
           "ConvertibleDebtNoncurrent", "ConvertibleNotesPayable", "ConvertibleDebt",
           "SeniorNotes")
ST_DEBT = ("LongTermDebtCurrent", "DebtCurrent", "ShortTermBorrowings",
           "NotesPayableCurrent", "NotesAndLoansPayableCurrent",
           "LongTermDebtAndCapitalLeaseObligationsCurrent", "ConvertibleDebtCurrent",
           "ConvertibleNotesPayableCurrent")
# total_debt reads ONE date as buckets, because the tags overlap: LongTermDebt already
# holds the current maturities that LongTermDebtCurrent repeats (MELI 4.57 + 4.623 =
# 9.193), and a filed 0 (ABBV's ShortTermBorrowings) must not hide the 8.3B of current
# maturities beside it.
#   combined total                                  -> as filed
#   else NONCURRENT + (DebtCurrent | maturities + short-term borrowings)
#   else TOTAL long-term + (DebtCurrent | short-term borrowings)
# plus finance leases unless a ...CapitalLeaseObligations tag already carries them.
DEBT_COMBINED = ("DebtLongtermAndShorttermCombinedAmount",
                 "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities",
                 "Borrowings")
DEBT_NONCURRENT = ("LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations",
                   "LongTermNotesAndLoans", "LongTermNotesPayable", "ConvertibleDebtNoncurrent")
DEBT_TOTAL_LT = ("LongTermDebt", "ConvertibleNotesPayable", "ConvertibleDebt")
DEBT_CURRENT_TOTAL = ("DebtCurrent",)
DEBT_MATURITIES = ("LongTermDebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent",
                   "ConvertibleDebtCurrent", "ConvertibleNotesPayableCurrent")
DEBT_SHORT = ("ShortTermBorrowings", "CommercialPaper", "NotesPayableCurrent",
              "NotesAndLoansPayableCurrent", "OtherShortTermBorrowings")
# Finance (capital) leases are debt too and sit outside LongTermDebt*: MSFT carries
# $66.6B of them.
FIN_LEASE_TOTAL = ("FinanceLeaseLiability",)
FIN_LEASE_LEGS = ("FinanceLeaseLiabilityNoncurrent", "FinanceLeaseLiabilityCurrent")
LEASE_INCLUSIVE = "CapitalLeaseObligations"
DEBT_LADDERS = (DEBT_COMBINED, DEBT_NONCURRENT, DEBT_TOTAL_LT, DEBT_CURRENT_TOTAL,
                DEBT_MATURITIES, DEBT_SHORT, FIN_LEASE_TOTAL, FIN_LEASE_LEGS)
EQUITY_TAGS = ("StockholdersEquity", "Equity",
               "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest")
# The current-year and prior-year lookups share one ladder each, so the two can never
# resolve to different concepts and produce a bogus delta.
AR_TAGS = ("AccountsReceivableNetCurrent", "ReceivablesNetCurrent",
           "AccountsAndOtherReceivablesNetCurrent")
INV_TAGS = ("InventoryNet", "InventoryGross", "Inventories")
AP_TAGS = ("AccountsPayableCurrent", "AccountsPayableTradeCurrent",
           "AccountsPayableAndAccruedLiabilitiesCurrent")
CASH_TAGS = ("CashAndCashEquivalentsAtCarryingValue", "CashAndCashEquivalents",
             # TMDX tags only the restricted-cash-inclusive total since 2024-09
             "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents")
# Damodaran nets cash AND marketable securities out of debt and invested capital.
# Cash-only read GOOGL at $55.9B against $242.5B, MSFT $20.9B against $76.8B.
COMBINED_CASH = "CashCashEquivalentsAndShortTermInvestments"
STI_TAGS = ("ShortTermInvestments", "MarketableSecuritiesCurrent", "DebtSecuritiesCurrent",
            # PFE files its balance-sheet line here; AFS debt below is one part of it
            "OtherShortTermInvestments",
            "AvailableForSaleSecuritiesDebtSecuritiesCurrent")
DEFREV_TAGS = ("ContractWithCustomerLiabilityCurrent", "ContractWithCustomerLiability",
               "DeferredRevenueCurrent", "DeferredRevenue")
RND_TAGS = ("ResearchAndDevelopmentExpense",
            "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost")
# Operating leases (US GAAP) or the IFRS 16 lease liability, which covers every lease.
LEASE_TOTAL = ("OperatingLeaseLiability", "LeaseLiabilities")
LEASE_LEGS = (("OperatingLeaseLiabilityNoncurrent", "NoncurrentLeaseLiabilities"),
              ("OperatingLeaseLiabilityCurrent", "CurrentLeaseLiabilities"))
# Charges that are not the cost of running the business this year (Damodaran: normalize
# EBIT before valuing it). Impairments write down capital already spent, so they are
# always non-recurring cash-wise; restructuring and acquired IPR&D count only when they
# do not recur (see domain/one_time.py). Tags within a group overlap (PFE files the
# same 4.94B under two of them), so a group takes its largest TTM, never the sum.
IMPAIRMENT_TAGS = ("AssetImpairmentCharges", "GoodwillAndIntangibleAssetImpairment",
                   "GoodwillImpairmentLoss", "ImpairmentOfIntangibleAssetsExcludingGoodwill",
                   "ImpairmentOfIntangibleAssetsFinitelived",
                   "ImpairmentOfIntangibleAssetsIndefinitelivedExcludingGoodwill",
                   "ImpairmentOfLongLivedAssetsHeldForUse")
RESTRUCTURING_TAGS = ("RestructuringCharges", "RestructuringCosts",
                      "RestructuringCostsAndAssetImpairmentCharges")
ACQUIRED_IPRD_TAGS = ("ResearchAndDevelopmentInProcess",
                      "ResearchAndDevelopmentAssetAcquiredOtherThanThroughBusinessCombinationWrittenOff")
ACQUISITION_TAGS = ("PaymentsToAcquireBusinessesNetOfCashAcquired",
                    "PaymentsToAcquireBusinessesAndInterestInAffiliates",
                    "PaymentsToAcquireBusinessesGross")
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
        self.prior_fallbacks = set()  # tags whose prior-year value is the prior FY end
        self.notes = []               # flags raised while a field was read


def _live_tags(x, ladder, at):
    """The ladder's (tag, unit) pairs that are still being filed, freshest first.

    Ladder order alone is not enough: a filer that moves to a new concept leaves the old
    one in companyfacts with its last value, forever. NVDA's capex resolved to a 2020
    figure ($0.46B against $6.6B now), LLY's R&D to 2023 and 12 of 24 filers' interest
    expense to 2024. A tag whose newest period ends more than 540 days before the
    revenue anchor is dead and is skipped; among live tags the newest period wins and
    ladder order breaks ties."""
    live = []
    for i, (t, p) in enumerate(_tag_units(ladder)):
        end = x.latest_end(t, p)
        if end and _recent(end, at.ref_end):
            live.append((end, -i, t, p))
    return [(t, p) for _, _, t, p in sorted(live, reverse=True)]


def _ttm(x, ladder, at):
    """First truthy TTM among the live tags (a 0 falls through to the next tag)."""
    return _or(x.ttm(t, p) for t, p in _live_tags(x, ladder, at))


def _ttm_present(x, ladder, at):
    """First TTM that exists among the live tags (a filed 0 is kept)."""
    return next((v for v in (x.ttm(t, p) for t, p in _live_tags(x, ladder, at))
                 if v is not None), None)


def _ttm_max(x, ladder, at):
    """Largest TTM among the live tags of one group of overlapping concepts."""
    vals = [v for v in (x.ttm(t, p) for t, p in _live_tags(x, ladder, at)) if v]
    return max(vals) if vals else None


def _annual_max_dated(x, ladder, at):
    """Per fiscal-year end, the largest value among the live tags of one group of
    overlapping concepts (the annual twin of _ttm_max)."""
    out = {}
    for t, p in _live_tags(x, ladder, at):
        for d, v in x.annual_series_dated(t, p):
            if v and (d not in out or v > out[d]):
                out[d] = v
    return [[d, out[d]] for d in sorted(out, reverse=True)]


def _annual(x, ladder, at):
    return _or(x.annual_series(t, p) for t, p in _live_tags(x, ladder, at))


def _annual_or_ytd_dated(x, ladder, at):
    """Annual series per fiscal year end, merged across the live tags, and for a fiscal
    year with no full-year value the largest year-to-date figure inside it. CELH's
    10-K carries no full-year M&A payment for 2025, only the 10-Q's $1.28B nine-month
    YTD, so the year it bought Alani Nu read as organic growth."""
    out = {}
    for t, p in _live_tags(x, ladder, at):
        for d, v in x.annual_series_dated(t, p):
            out.setdefault(d, v)
    for fy in at.fy_ends or []:
        if any(abs(_days(fy, d)) <= 7 for d in out):
            continue
        ytd = [e["val"] for t, p in _live_tags(x, ladder, at) for e in x.entries(t, p)
               if e.get("start") and e.get("val") is not None
               and 0 <= _days(e["end"], fy) <= 370 and 0 < _days(e["start"], fy) <= 380]
        if ytd:
            out[fy] = max(ytd)
    return [[d, out[d]] for d in sorted(out, reverse=True)]


def _annual_dated(x, ladder, at):
    return _or(x.annual_series_dated(t, p) for t, p in _live_tags(x, ladder, at))


def _trend_series(x, ladder, at):
    """First tag with >= 2 dated annual points, else [].

    Deliberately NOT a merge across tags: MSFT reports CostOfRevenue up to 2017 and
    CostOfGoodsAndServicesSold after, and a trend drawn across that break measures
    the accounting change, not the business."""
    for t, p in _live_tags(x, ladder, at):
        s = x.annual_series_dated(t, p)
        if len(s) >= 2:
            return s
    return []


def _quarters(x, ladder, at):
    return _or(x.quarters(t, p) for t, p in _live_tags(x, ladder, at))


def _latest_value(x, ladder, at):
    e = next((e for e in (x.latest(t, p) for t, p in _live_tags(x, ladder, at)) if e), None)
    return e.get("val") if e else None


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


PRIOR_TOLERANCE_DAYS = 45


def _year_before(dates, current):
    """The date in `dates` nearest one year before `current`, within 45 days, or None.

    "Prior year" means the same balance-sheet date a year earlier, so a change sits
    beside the year-over-year growth it is compared with. The newest instant of the
    previous calendar year was 3 to 6 months back for a quarterly filer, and for UNH
    an equity figure from 2014."""
    if not current:
        return None
    target = _date(current) - dt.timedelta(days=365)
    best = None
    for d in dates:
        gap = abs((_date(d) - target).days)
        if gap <= PRIOR_TOLERANCE_DAYS and (best is None or gap < best[0]):
            best = (gap, d)
    return best[1] if best else None


def _instants(x, t, p):
    return {e["end"]: e["val"] for e in x.entries(t, p)
            if not e.get("start") and e.get("val") is not None}


def _prior_year(x, ladder, at):
    """The balance-sheet value one year before the current one (_fresh's date), from
    the first live tag that filed it."""
    live = _live_tags(x, ladder, at)
    current = max((x.latest_end(t, p) for t, p in live), default=None)
    for t, p in live:
        series = _instants(x, t, p)
        d = _year_before(series, current)
        if d:
            return series[d]
    # not filed a year back (quarter-end instants are optional): the previous fiscal
    # year-end balance, which every 10-K carries, rather than nothing
    fy = next((e for e in at.fy_ends if current and _days(e, current) > 60), None)
    if fy:
        for t, p in live:
            series = _instants(x, t, p)
            near = [e for e in series if abs(_days(e, fy)) <= 7]
            if near:
                at.prior_fallbacks.add(t)
                return series[near[0]]
    return None


def _total_else_legs(rule):
    """Rule over (total ladder, *leg ladders): the filed total, else the sum of legs."""
    def read(x, ladders, at):
        total, *legs = ladders
        return rule(x, total, at) or _sum(*(rule(x, leg, at) for leg in legs))
    return read


def _total_debt(x, ladders, at, prior=False):
    """Total debt read off ONE balance-sheet date: the latest that yields a figure
    (with `prior`, the date a year before it). See DEBT_LADDERS for the buckets.

    The legs must never be summed across dates. ORCL files the combined amount
    annually (92.6B @ 2025-05-31) and the legs quarterly (134.6B @ 2026-02-28); either
    shortcut understated debt by 31%, which flowed into invested capital, net debt,
    EV and every multiple."""
    window = 540 + (400 if prior else 0)
    comb, nc, tot, cur, mat, short, fl_total, fl_legs = (
        {t: {e["end"]: e["val"] for e in x.entries(t)
             if not e.get("start") and e.get("val") is not None
             and _recent(e["end"], at.ref_end, days=window)} for t in tags}
        for tags in ladders)

    def first(bucket, d):
        """(value, tag) of the first tag filed on `d`; a 0 counts only if nothing else is."""
        hits = [(v[d], t) for t, v in bucket.items() if d in v]
        nonzero = [h for h in hits if h[0]]
        return (nonzero or hits or [(None, None)])[0]

    dates = sorted({d for b in (comb, nc, tot) for v in b.values() for d in v}, reverse=True)
    if prior and dates:
        d0 = _year_before(dates, dates[0])
        dates = [d0] if d0 else []
    for d in dates:
        used = []
        v, t = first(comb, d)
        if v is not None:
            debt = v
            used.append(t)
        else:
            v, t = first(nc, d)
            if v is not None:
                used.append(t)
                c, ct = first(cur, d)
                if c is not None:
                    debt = _sum(v, c)
                else:
                    m, mt = first(mat, d)
                    sb, _ = first(short, d)
                    debt = _sum(v, m, sb)
                    used.append(mt or "")
            else:
                v, t = first(tot, d)
                if v is None:
                    continue
                used.append(t)
                c, _ = first(cur, d)
                debt = _sum(v, c if c is not None else first(short, d)[0])
        if not any(LEASE_INCLUSIVE in (u or "") for u in used):
            fin, _ = first(fl_total, d)
            if fin is None:
                fin = _sum(*(fl_legs[t].get(d) for t in fl_legs))
            debt = _sum(debt, fin)
        return debt
    return None


def _cash_and_securities(x, ladders, at, prior=False):
    """Cash plus short-term marketable securities, read off ONE balance-sheet date.

    The filed combined amount wins on its date; otherwise cash plus the first
    securities tag filed on that SAME date. Legs from different dates are never
    summed: NVDA's MarketableSecuritiesCurrent stops in 2025 while its cash is 2026."""
    combined, cash_tags, sti_tags = ladders

    def by_end(tags):
        out = {}
        for tag in tags:
            for e in x.entries(tag):
                d, v = e.get("end"), e.get("val")
                if d and v is not None and not e.get("start") and d not in out and _recent(d, at.ref_end):
                    out[d] = v
        return out

    comb, cash, sti = by_end(combined), by_end(cash_tags), by_end(sti_tags)
    dates = sorted(set(comb) | set(cash), reverse=True)
    if prior and dates:
        # the same date rule as every other *_prior field (one year before the
        # current balance sheet), so prior invested capital is read off one date
        d0 = _year_before(dates, dates[0])
        dates = [d0] if d0 else []
    for d in dates:
        return comb[d] if d in comb else cash[d] + sti.get(d, 0)
    return None


def _leases(x, ladders, at, prior=False):
    """Operating leases read off ONE balance-sheet date (with `prior`, the date a year
    before it): the total filed on that date, else noncurrent + current filed on it.

    "Total else legs" read each from its own newest date. BJ's OperatingLeaseLiability
    of 93.6M at 2026-07-31 is a footnote figure beside the 2,144M balance sheet of
    2026-08-01; DG and EBAY file the total only in the 10-K, so a nine-month-old
    total beat this quarter's legs.

    A date with the noncurrent leg alone is incomplete when the filer files a current
    leg elsewhere: BIIB's and HCA's 10-Qs leave the current part untagged (HCA 1.93B
    noncurrent against a 2.21B total). The newest complete date is read instead, and a
    flag says it is older than the balance sheet."""
    window = 540 + (400 if prior else 0)

    def by_end(tags):
        out = {}
        for tag in tags:
            for e in x.entries(tag):
                d, v = e.get("end"), e.get("val")
                if (d and v is not None and not e.get("start") and d not in out
                        and _recent(d, at.ref_end, days=window)):
                    out[d] = v
        return out

    total, non, cur = (by_end(tags) for tags in ladders)
    dates = sorted(set(total) | {d for d in non if d in cur or not cur}, reverse=True)
    if prior and dates:
        d0 = _year_before(dates, dates[0])
        if d0 is None:
            # no balance filed a year back: the previous fiscal year end, as _prior_year
            fy = next((e for e in at.fy_ends if _days(e, dates[0]) > 60), None)
            d0 = next((d for d in dates if fy and abs(_days(d, fy)) <= 7), None)
            if d0:
                at.prior_fallbacks.add("operating leases")
        dates = [d0] if d0 else []
    for d in dates:
        newest = max(non, default=d)
        if not prior and newest > d:
            at.notes.append("operating leases from %s, the latest date with the current part "
                            "filed (noncurrent alone at %s)" % (d, newest))
        return total[d] if d in total else _sum(non.get(d), cur.get(d))
    return None


def _ic_components(x, ladders, at):
    """{FY_end: {"equity", "debt", "cash", "lt_source"}} at the income-statement
    year-ends, for the per-year ROIC series.

    A year is returned ONLY when every leg is filed: invested capital from a partial
    balance sheet reads as a real change in the capital base. If a long-term debt
    concept appears in ANY year of the window it is required in EVERY year: a missing
    year is a filing gap, not a debt-free year."""
    (eq, _), (cash, _), (lt, lt_tag), (st, _) = (
        x.instant_at_dates(list(tags), at.fy_ends) for tags in ladders[:4])
    # securities tags are alternatives for ONE line, so each date takes the first filed
    sti = {}
    for tag in ladders[4]:
        for d, v in x.instant_at_dates([tag], at.fy_ends)[0].items():
            sti.setdefault(d, v)
    out = {}
    for d in at.fy_ends:
        if d not in eq or d not in cash:
            continue
        if lt_tag and d not in lt:
            continue
        out[d] = {"equity": eq[d], "cash": cash[d] + (sti.get(d) or 0),
                  "debt": (lt.get(d) or 0) + (st.get(d) or 0),
                  "lt_source": lt_tag}
    return out


LEASE_LADDERS = (LEASE_TOTAL, *LEASE_LEGS)

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
    "total_debt": (_total_debt, DEBT_LADDERS),
    "cash": (_cash_and_securities, ((COMBINED_CASH,), CASH_TAGS, STI_TAGS)),
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
                                             (*COMBINED_DEBT, *LT_DEBT), ST_DEBT, STI_TAGS)),
    "dep_amort": (_ttm, ("DepreciationAndAmortization", "Depreciation",
                         "DepreciationDepletionAndAmortization",
                         "DepreciationAndAmortisationExpense",
                         "DepreciationAmortisationAndImpairmentLossReversalOfImpairmentLossRecognisedInProfitOrLoss")),
    # ORCL stopped filing the total in 2018 and tags only the domestic and foreign legs
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
    "total_debt_prior": (lambda x, ladders, at: _total_debt(x, ladders, at, prior=True),
                         DEBT_LADDERS),
    "cash_prior": (lambda x, ladders, at: _cash_and_securities(x, ladders, at, prior=True),
                   ((COMBINED_CASH,), CASH_TAGS, STI_TAGS)),
    "acquisitions_net": (_ttm, ACQUISITION_TAGS),
    "acquisitions_annuals_dated": (_annual_or_ytd_dated, ACQUISITION_TAGS),
    "impairment_ttm": (_ttm_max, IMPAIRMENT_TAGS),
    "impairment_annuals_dated": (_annual_max_dated, IMPAIRMENT_TAGS),
    "restructuring_ttm": (_ttm_max, RESTRUCTURING_TAGS),
    "restructuring_annuals_dated": (_annual_dated, RESTRUCTURING_TAGS),
    "acquired_iprd_ttm": (_ttm_max, ACQUIRED_IPRD_TAGS),
    "acquired_iprd_annuals_dated": (_annual_dated, ACQUIRED_IPRD_TAGS),
    "deferred_revenue": (_fresh, DEFREV_TAGS),
    "deferred_revenue_prior": (_prior_year, DEFREV_TAGS),
    # Leases are debt (Damodaran); the prior year too, or a lease-heavy filer is
    # compared against its own lease-free past and reads as deteriorating.
    "operating_leases": (_leases, LEASE_LADDERS),
    "operating_leases_prior": (lambda x, ladders, at: _leases(x, ladders, at, prior=True),
                               LEASE_LADDERS),
    "eps_annuals_dated": (_annual_dated, (EPS_DILUTED,)),
    # Noncontrolling interest at book, the latest balance sheet: a claim on the
    # consolidated firm that is not the common shareholder's (deducted like debt).
    "minority_interest": (_fresh, ("MinorityInterest", "MinorityInterestInConsolidatedSubsidiaries")),
}


# --------------------------------------------------------------------------- #
#  Headline income-statement lines                                             #
# --------------------------------------------------------------------------- #
# A concept whose newest period ends more than CURRENT_DAYS before the newest revenue
# period is no longer being filed: ASTS last tagged OperatingIncomeLoss for 2021-03-31
# and its TTM read -2.4M, KLAC's for 2015-03-31 read a 4.4% margin against ~44%. A
# concept filed only in the 10-K is current when its newest period is the latest
# fiscal year end (LLY's InterestExpenseNonoperating).
CURRENT_DAYS = 200


class _Clock:
    """The newest revenue period and the latest revenue fiscal-year end."""

    def __init__(self, ref_end, fy_end):
        self.ref_end, self.fy_end = ref_end, fy_end

    def current(self, x, concept):
        le = x.latest_end(concept)
        if not le or not self.ref_end:
            return bool(le)
        return (_days(le, self.ref_end) <= CURRENT_DAYS
                or bool(self.fy_end and abs(_days(le, self.fy_end)) <= 7))


def _newest_current(x, concepts, clock):
    """The current concepts, newest period first, ladder order on ties."""
    live = [(x.latest_end(c), -i, c) for i, c in enumerate(concepts) if clock.current(x, c)]
    return [c for _, _, c in sorted(live, reverse=True)]


def _stale_note(x, field, concepts, chosen, clock, instead):
    """The flag for a concept the clock rejected that would otherwise have been read."""
    old = [c for c in concepts if x.latest_end(c) and not clock.current(x, c)
           and (chosen is None or x.latest_end(c) > (x.latest_end(chosen) or ""))]
    return ["%s: %s last filed for %s, revenue for %s - not current, %s"
            % (field, c, x.latest_end(c), clock.ref_end, instead) for c in old]


def _pick(x, concepts, clock=None):
    """(value, concept, same-period alternatives that disagree by >5%).

    The concept with the FRESHEST data wins (AVGO moved net income from NetIncomeLoss
    to ProfitLoss), then LIST ORDER. Not the larger |TTM|: a filer tagging both
    `Revenues` and the ASC 606 core concept would get the broader number, which can
    carry other income, deflate every margin and make the reverse DCF too kind.
    With a clock, a concept that is not current is never picked."""
    best = None                      # ((end, -list_index), value, concept)
    for i, c in enumerate(concepts):
        if clock and not clock.current(x, c):
            continue
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


def _prefer_top_line(x, val, concept, alts):
    """Swap ASC 606 contract revenue for the income statement's total when both cover
    the same latest period and the total is larger (REV_TOTALS). The contract figure
    stays listed as the alternative, so the conflict line still shows both."""
    if not concept or concept in REV_TOTALS:
        return val, concept, alts
    for c, v in alts:
        if c in REV_TOTALS and val and v > val:
            rest = [(a, av) for a, av in alts if a != c] + [(concept, val)]
            return v, c, rest
    return val, concept, alts


def _conflicts(*picked):
    """Two concepts covering the SAME period that disagree by >5% are a real ambiguity;
    list order resolves it predictably, and this says so."""
    return [f"{fld}: used {used} ({base/1e9:.2f}B) but {alt} covers the same "
            f"period at {av/1e9:.2f}B"
            for fld, (base, used, alts) in picked
            if alts and base for alt, av in alts]


def _ebit_concepts(x, clock):
    """(pretax concept, TTM interest concept, interest concepts for history) for
    EBIT = pretax + interest. Pretax and the TTM interest are the newest current
    concepts: DDS's first pretax entry stopped at 2025-11-01, and InterestExpense, the
    first interest entry, ends 2024-03-31 for LLY, KLAC and AIR. History reads the
    newest concept first and fills the years before it from the older ones, which carry
    the same line under its old name (LLY 2021 from InterestExpense)."""
    pre = _newest_current(x, PRETAX, clock)
    cur = _newest_current(x, INT_EXP, clock)
    hist = cur + sorted((t for t in INT_EXP if t not in cur and x.latest_end(t)),
                        key=x.latest_end, reverse=True)
    return (pre[0] if pre else None), (cur[0] if cur else None), hist


def _merged(series, concepts):
    """{date: value} across concepts, the first concept that files a date winning."""
    out = {}
    for c in concepts:
        for d, v in series(c).items():
            out.setdefault(d, v)
    return out


def _derived_ebit(x, clock):
    """EBIT = pretax income + interest expense, for filers with no current OP concept.

    LLY and PFE tag no operating-income subtotal; without it the engine mistakes a very
    profitable company for a pre-profit one. Approximate (pretax carries non-operating
    items), so it is flagged via provenance."""
    out = {"operating_income": None, "operating_income_annuals": [],
           "operating_income_annuals_dated": [], "operating_income_quarters": {}}
    pre_c, int_c, int_hist = _ebit_concepts(x, clock)
    if pre_c:
        p_ttm = x.ttm(pre_c)
        if p_ttm is not None:
            out["operating_income"] = p_ttm + ((x.ttm(int_c) or 0) if int_c else 0)
            # a gapped interest TTM reads as 0 here; _gap_flags gates the row on it
            out["interest_ttm_gapped"] = bool(int_c and x.ttm_gapped(int_c))
        pa = dict(x.annual_series_dated(pre_c))
        ia = _merged(lambda c: dict(x.annual_series_dated(c)), int_hist)
        dated = [[d, pa[d] + ia.get(d, 0)] for d in sorted(pa, reverse=True)]
        iq = _merged(x.quarters, int_hist)
        out["operating_income_annuals"] = [v for _, v in dated]
        out["operating_income_annuals_dated"] = dated
        out["operating_income_quarters"] = {d: v + iq.get(d, 0)
                                            for d, v in x.quarters(pre_c).items()}
    out["operating_income_derived"] = out["operating_income"] is not None
    return out


# The TTM fields a fair value is built from. A gap in one of these means the year is
# not a year, and the row is NOT RATED (deep_v82.not_rated_reasons matches GAP_FLAG).
# Interest drives value only when EBIT is derived as pretax + interest (LLY, PFE).
VALUE_DRIVING_TTM = ("revenue", "operating_income", "net_income", "cfo", "capex")
GAP_FLAG = "TTM: quarters not contiguous (%s)"
# Sporadic items (restructuring, impairments, acquisitions) skip quarters normally:
# shown, never a reason to withhold the rating.
GAP_INFO = "TTM gap (info): %s quarters not contiguous - read as none"


def _gap_flags(x, at, rev_tags, op_tags, ni_tags, out):
    """GAP_FLAG for each value-driving field that ended with NO value because a
    candidate tag's last four quarters skip a period; GAP_INFO for any other TTM field.
    A probe that lost to a tag with a good TTM raises nothing."""
    cands = {"revenue": rev_tags, "operating_income": op_tags, "net_income": ni_tags}
    for field, (rule, ladder) in FIELD_LADDERS.items():
        if rule in (_ttm, _ttm_present, _ttm_max) and out.get(field) is None:
            cands[field] = [t for t, _ in _live_tags(x, ladder, at)]
    driving = set(VALUE_DRIVING_TTM)
    if out.get("operating_income_derived"):
        driving.add("interest_expense")
    gapped = {f for f, tags in cands.items() if tags and any(x.ttm_gapped(t) for t in tags)}
    if out.get("interest_ttm_gapped"):
        gapped.add("interest_expense")
    return sorted((GAP_FLAG if f in driving else GAP_INFO) % f for f in gapped)


# --------------------------------------------------------------------------- #
#  Fiscal-year series for the peer panel (display only)                        #
# --------------------------------------------------------------------------- #
FY_FILL_AGREE = 0.02            # a fill concept must match the primary within 2% where both are filed


def _dated(m):
    return [[d, m[d]] for d in sorted(m, reverse=True)]


def _merged_fy(x, primary, ladder):
    """The primary concept's fiscal years, each year it lacks filled from the next
    concept of the ladder that files it, provided that concept agrees with the merged
    series within FY_FILL_AGREE on every year both file (and shares at least one).
    GOOGL moved revenue to `Revenues` in its FY2025 10-K: FY2022 is filed only under the
    ASC 606 concept, which matches `Revenues` exactly where both exist. MELI's ASC 606
    figure leaves out credit income and disagrees by 20%+, so it never fills."""
    out = x.fy_series_dated(primary) if primary else {}
    for c in ladder:
        if c == primary:
            continue
        alt = x.fy_series_dated(c)
        both = set(alt) & set(out)
        if not both or not (set(alt) - set(out)):
            continue
        if all(out[d] and abs(alt[d] - out[d]) <= FY_FILL_AGREE * abs(out[d]) for d in both):
            out.update({d: v for d, v in alt.items() if d not in out})
    return _dated(out)


def _first_fy(x, ladder, at):
    """The first live tag with two or more fiscal years (no merge: see _trend_series)."""
    for t, p in _live_tags(x, ladder, at):
        s = x.fy_series_dated(t, p)
        if len(s) >= 2:
            return _dated(s)
    return []


def panel_fy_series(x, rev_concept, op_concept, at, clock):
    """The peer panel's fiscal-year series, one value per year-end DATE: revenue merged
    across the revenue ladder (_merged_fy), gross profit and cost of revenue from their
    first live tag, operating income from the chosen concept or pretax + interest
    (_derived_ebit's rule). The scored *_annuals_dated series are left as they are."""
    if op_concept:
        oi = _dated(x.fy_series_dated(op_concept))
    else:
        pre_c, _, int_hist = _ebit_concepts(x, clock)
        pa = x.fy_series_dated(pre_c) if pre_c else {}
        ia = _merged(x.fy_series_dated, int_hist)
        oi = _dated({d: v + ia.get(d, 0) for d, v in pa.items()})
    return {"revenue_fy_dated": _merged_fy(x, rev_concept, REV),
            "gross_profit_fy_dated": _first_fy(x, GROSS_PROFIT_TAGS, at),
            "cost_of_revenue_fy_dated": _first_fy(x, COST_OF_REV_TAGS, at),
            "operating_income_fy_dated": oi}


def _series(x, concept, name):
    return {name + "_annuals": x.annual_series(concept),
            name + "_annuals_dated": x.annual_series_dated(concept),
            name + "_quarters": x.quarters(concept)}


def _nonperiodic_forms(x):
    return sorted(f for f in _forms(x.ns) if not _periodic({"form": f}, x.foreign))


def extract(companyfacts, splits=None, cash_excludes_securities=False):
    """Return a dict of SEC-derived financial values (in reported currency).

    `splits`: [(ex-date, ratio)] from the market (Yahoo), oldest first; per-share
    history is then restated by filing date (see _split_adjust_dated).
    `cash_excludes_securities`: for firms whose short-term investments back their
    liabilities (health insurers' claim reserves), cash is cash and equivalents only.

    Only periodic-report facts are read (PERIODIC_FORMS). When the ignored facts would
    have changed a value, a DATA note names the fields: the file is read a second time
    with them, so the note is exact rather than a guess from the facts' periods."""
    facts = companyfacts.get("facts", companyfacts)
    x = _Xbrl(facts)
    out = _extract(x, splits, cash_excludes_securities)
    forms = _nonperiodic_forms(x)
    if forms:
        unfiltered = _extract(_Xbrl(facts, periodic_only=False), splits, cash_excludes_securities)
        changed = sorted(k for k in set(out) | set(unfiltered)
                         if k != "xbrl_flags" and out.get(k) != unfiltered.get(k))
        if changed:
            out["xbrl_flags"].append(
                "DATA[info] SEC facts from non-periodic filings (%s) ignored; they would have "
                "changed %s" % (", ".join(forms), ", ".join(changed)))
    return out


def _extract(x, splits, cash_excludes_securities):

    rev, rev_concept, rev_alts = _pick(x, REV)
    rev, rev_concept, rev_alts = _prefer_top_line(x, rev, rev_concept, rev_alts)
    net_income, ni_concept, ni_alts = _pick(x, NI)
    revenue = _series(x, rev_concept, "revenue")
    clock = _Clock(x.latest_end(rev_concept), (revenue["revenue_annuals_dated"] or [[None]])[0][0])
    oi, op_concept, _ = _pick(x, OP, clock)
    ebit = None if op_concept else _derived_ebit(x, clock)
    stale = _stale_note(x, "operating income", OP, op_concept, clock,
                        "used %s" % op_concept if op_concept else "EBIT = pretax + interest")
    operating = ({"operating_income": oi, **_series(x, op_concept, "operating_income"),
                  "operating_income_derived": False} if op_concept else ebit)

    # The trend strip's fiscal-year ends come from the INCOME statement, since every
    # ratio there has it in the numerator; the balance sheet is read at these dates.
    fy_ends = [e for e, _ in (operating["operating_income_annuals_dated"]
                              or revenue["revenue_annuals_dated"])][:12]
    at = _Anchor(x.latest_end(rev_concept), fy_ends)
    out = {
        "currency": x.currency(rev_concept) or "USD",
        "accounting_standard": x.standard,
        "latest_period_end": at.ref_end,
        "revenue": rev,
        "net_income": net_income,
        "concept_conflicts": _conflicts(("revenue", (rev, rev_concept, rev_alts)),
                                        ("net income", (net_income, ni_concept, ni_alts))),
        **revenue,
        **operating,
        **{field: rule(x, ladder, at) for field, (rule, ladder) in FIELD_LADDERS.items()},
        "q4_derived_ends": sorted(x.q4_derived),
        **panel_fy_series(x, rev_concept, op_concept, at, clock),
    }
    out["xbrl_flags"] = _gap_flags(
        x, at, rev_concept is None and REV,
        op_concept is None and out.get("operating_income") is None and OP + PRETAX,
        ni_concept is None and NI, out) + stale + at.notes
    if cash_excludes_securities:
        cash_only = ((), CASH_TAGS, ())
        out["cash"] = _cash_and_securities(x, cash_only, at)
        out["cash_prior"] = _cash_and_securities(x, cash_only, at, prior=True)
    dated = _split_adjust_dated(x, out, at, splits) if splits and x.standard == "us-gaap" else None
    out["split_boundaries"] = dated if dated is not None else _split_adjust(x, out, ni_concept)
    out["prior_fallbacks"] = sorted(at.prior_fallbacks)
    return out


# --------------------------------------------------------------------------- #
#  Stock splits                                                                #
# --------------------------------------------------------------------------- #
# A 10-K restates the years it presents for a split; years before them keep the old
# per-share basis forever. NVDA's diluted share series reads 24,514M ... 25,070M then
# 2,535M (10-for-1, 2024), its EPS 0.17 then 3.85. The filer's own split-ratio tag says
# WHICH factors exist; the series says WHERE each one falls. Requiring both keeps
# ordinary share growth (TSLA issued +30% in 2020) from being read as a split.
SPLIT_TAGS = ("StockholdersEquityNoteStockSplitConversionRatio1",
              "StockholdersEquityNoteStockSplitConversionRatio")
SPLIT_MATCH = 0.12              # a year-over-year share ratio within 12% of a filed factor


def _split_factors(x):
    """The split ratios the filer has tagged (forward splits only, > 1.5)."""
    out = set()
    for t in SPLIT_TAGS:
        for unit in ("pure", "shares"):
            for e in x.entries(t, (unit,)):
                v = e.get("val")
                if isinstance(v, (int, float)) and v > 1.5:
                    out.add(float(v))
    return out


def split_boundaries(dated_counts, factors):
    """[(date, cumulative factor)] for a newest-first [[date, share count]] series:
    every entry ON OR BEFORE `date` must be multiplied by the factor to be on today's
    basis. A boundary needs the year-over-year ratio to sit within 12% of a filed
    factor (or of the product of two)."""
    if not factors or len(dated_counts) < 2:
        return []
    # products only of two DIFFERENT filed factors (two splits inside one gap): f x f
    # would read a merger that doubles the share count, at a filer with an old 2-for-1,
    # as a second split
    cands = set(factors) | {a * b for a in factors for b in factors if a < b}
    out, cum = [], 1.0
    for (d0, v0), (d1, v1) in zip(dated_counts, dated_counts[1:]):
        if not v0 or not v1 or v1 <= 0:
            continue
        r = v0 / v1
        hit = min(cands, key=lambda f: abs(r / f - 1))
        if abs(r / hit - 1) <= SPLIT_MATCH:
            cum *= hit
            out.append((d1, cum))
    return out


def _factor_at(boundaries, date):
    """Cumulative factor that applies to a value dated `date`."""
    f = 1.0
    for d, cum in boundaries:
        if date <= d:
            f = cum
    return f


def _filing_factor(filed, splits):
    """Factor that puts a value filed on `filed` on today's share basis: a filing
    made after a split already restates every period it presents (ASC 260 is
    retrospective), one made before it does not."""
    f = 1.0
    for day, ratio in splits:
        if filed and filed < day:
            f *= ratio
    return f


def _annual_filed(x, concept, prefer):
    """{FY_end: filing date} for the entries annual_series_dated returns."""
    annuals = _newest_first(e for e in x.entries(concept, prefer) if _is_annual(e))
    return {e["end"]: e.get("filed") for e in _one_per_year(annuals)}


def _quarter_factors(x, concept, prefer, splits):
    """{quarter_end: factor} for quarters(concept). A Q4 derived as FY - 9M takes the
    factor its legs share, or None when a split falls between their filings."""
    es = x.entries(concept, prefer)
    out = {}
    for e in _newest_first(e for e in es if _is_quarter(e)):
        out.setdefault(e["end"], _filing_factor(e.get("filed"), splits))
    for fy in _one_per_year(_newest_first(e for e in es if _is_annual(e))):
        if fy["end"] in out:
            continue
        legs = [e for e in es if e.get("start") and e["end"] <= fy["end"]
                and _days(fy["start"], e["start"]) >= -7]
        fs = {_filing_factor(e.get("filed"), splits) for e in legs}
        out[fy["end"]] = fs.pop() if len(fs) == 1 else None
    return out


def _split_adjust_dated(x, d, at, splits):
    """Per-share history on today's basis from KNOWN split dates. Returns the splits
    that restated something (empty when none did), or None when `splits` is unusable.

    The share-ratio test in _split_adjust cannot see a split in a year the share count
    also grew for other reasons (TSLA 3:1 in 2022). With the dates known, the filing
    date of each value says which basis it is on, so no ratio test is needed."""
    splits = sorted((str(day)[:10], float(r)) for day, r in splits
                    if isinstance(r, (int, float)) and r > 0 and abs(r - 1) > 1e-9)
    if not splits:
        return None
    used = set()

    def fac(filed):
        f = _filing_factor(filed, splits)
        if f != 1.0:
            used.update(day for day, _ in splits if filed and filed < day)
        return f

    shares_c = "WeightedAverageNumberOfDilutedSharesOutstanding"
    shares = x.annual_series_dated(shares_c, SHARES)
    if shares:
        filed = _annual_filed(x, shares_c, SHARES)
        adj = [v * fac(filed.get(e)) for e, v in shares]
        if adj != [v for _, v in shares]:
            d["shares_diluted_annuals"] = adj
    eps = d.get("eps_annuals_dated") or []
    if eps:
        tag, unit = EPS_DILUTED
        filed = _annual_filed(x, tag, unit)
        d["eps_annuals_dated"] = [[e, round(v / fac(filed.get(e)), 4)] for e, v in eps]
    q = d.get("eps_quarters")
    if q:
        src = next(((t, p) for t, p in _live_tags(x, FIELD_LADDERS["eps_quarters"][1], at)
                    if x.quarters(t, p) == q), None)
        if src:
            fs = _quarter_factors(x, src[0], src[1], splits)
            adj = {}
            for e, v in q.items():
                f = fs.get(e, 1.0)
                if f is None:       # a derived Q4 whose legs sit on two bases
                    continue
                if f != 1.0:
                    used.update(day for day, _ in splits if day > e)
                adj[e] = round(v / f, 4)
            d["eps_quarters"] = adj
    return [(day, r) for day, r in splits if day in used]


def _split_adjust(x, d, ni_concept):
    """Put the share and per-share series on today's basis. Returns the factors found."""
    factors = _split_factors(x)
    if not factors:
        return []
    shares = x.annual_series_dated("WeightedAverageNumberOfDilutedSharesOutstanding", SHARES)
    bounds = split_boundaries(shares, factors)
    # where the share series stops early (GOOGL), net income / EPS stands in for it
    eps = d.get("eps_annuals_dated") or []
    if ni_concept and eps:
        ni = dict(x.annual_series_dated(ni_concept))
        implied = [[e, ni[e] / v] for e, v in eps if v and e in ni and ni[e] and ni[e] / v > 0]
        known = {b for b, _ in bounds}
        last = shares[-1][0] if shares else None
        extra = [(b, f) for b, f in split_boundaries(implied, factors)
                 if b not in known and (last is None or b < last)]
        if extra:
            base = bounds[-1][1] if bounds else 1.0
            bounds += [(b, f * base) for b, f in extra]
    if not bounds:
        return []
    if shares:
        d["shares_diluted_annuals"] = [v * _factor_at(bounds, e) for e, v in shares]
    if eps:
        d["eps_annuals_dated"] = [[e, round(v / _factor_at(bounds, e), 4)] for e, v in eps]
    q = d.get("eps_quarters")
    if q:
        # 10-Qs restate only one year back, so the quarterly series has its own
        # boundaries, found on the quarterly share count
        qs = list(x.quarters("WeightedAverageNumberOfDilutedSharesOutstanding", SHARES).items())
        qb = split_boundaries(qs, factors)
        adj = {}
        for e, v in q.items():
            # a Q4 derived as FY - 9M straddles two bases when a boundary sits inside
            # the year; it is dropped rather than shown wrong
            if e in x.q4_derived and any(0 < _days(b, e) < 366 for b, _ in qb):
                continue
            adj[e] = round(v / _factor_at(qb, e), 4)
        d["eps_quarters"] = adj
    return bounds


# Fields copied onto FinancialFacts, in provenance order.
POPULATED = (
    "currency", "accounting_standard", "revenue", "net_income", "operating_income", "eps_gaap",
    "shares_diluted", "total_debt", "cash", "equity", "capex", "dep_amort",
    "income_before_tax", "tax_expense", "revenue_annuals", "revenue_quarters",
    "operating_income_quarters", "eps_quarters",
    "cfo", "total_assets", "receivables", "interest_expense", "rnd_expense",
    "rnd_annuals", "operating_income_annuals",
    "equity_prior", "total_debt_prior", "cash_prior",
    "acquisitions_net", "deferred_revenue", "deferred_revenue_prior",
    "operating_leases", "operating_leases_prior", "minority_interest",
    "sbc",
    "shares_diluted_annuals",        # a COUNT, never FX-converted
    "receivables_prior", "inventory", "inventory_prior",
    "accounts_payable", "accounts_payable_prior",
    "eps_annuals_dated",
    "revenue_annuals_dated", "operating_income_annuals_dated",
    "cfo_annuals_dated", "capex_annuals_dated", "sbc_annuals_dated",
    "gross_profit_annuals_dated", "cost_of_revenue_annuals_dated",
    "ic_components_dated",
    "acquisitions_annuals_dated", "impairment_ttm", "impairment_annuals_dated", "restructuring_ttm",
    "restructuring_annuals_dated", "acquired_iprd_ttm", "acquired_iprd_annuals_dated",
    "revenue_fy_dated", "gross_profit_fy_dated", "cost_of_revenue_fy_dated",
    "operating_income_fy_dated")
EBIT_DERIVED = ("operating_income", "operating_income_annuals", "operating_income_quarters")
QUARTER_FIELDS = ("revenue_quarters", "operating_income_quarters", "eps_quarters")


def populate(ff, companyfacts, splits=None, cash_excludes_securities=False):
    """Fill SEC-sourced fields into a FinancialFacts object (source tag 'sec')."""
    d = extract(companyfacts, splits, cash_excludes_securities)
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
    for f in d.get("xbrl_flags") or []:
        ff.flags.append(f)
    if d.get("prior_fallbacks"):
        ff.flags.append("prior-year balance for " + ", ".join(d["prior_fallbacks"])
                        + " read at the previous fiscal year end (no balance filed a year "
                        "before the latest one)")
    if d.get("split_boundaries"):
        ff.flags.append("stock split: share and EPS history before "
                        + ", ".join(f"{b} (x{f:g})" for b, f in d["split_boundaries"])
                        + " restated to today's share basis")
    # A TTM EPS that straddles a split mixes the two bases (NVDA read 12.87 against
    # 2.13). Net income / diluted shares cannot: use it when they disagree 1.8x+.
    if ff.eps_gaap and ff.net_income and ff.shares_diluted:
        implied = ff.net_income / ff.shares_diluted
        if implied and (ff.eps_gaap / implied > 1.8 or implied / ff.eps_gaap > 1.8):
            ff.flags.append(f"TTM EPS {ff.eps_gaap:.2f} disagrees with net income / shares "
                            f"{implied:.2f} (split inside the TTM window?) - the latter used")
            ff.set("eps_gaap", round(implied, 4), "sec-derived (NI / diluted shares)")
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
