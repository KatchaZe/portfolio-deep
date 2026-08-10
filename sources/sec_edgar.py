"""
SEC EDGAR source adapter — the PRIMARY financial source (free, all US + 20-F filers).

Robust TTM = latest full fiscal year + current YTD - prior-year YTD, with
sum-of-4Q and latest-annual fallbacks. De-duplicates restated facts by keeping the
most recently filed value. Growth uses the clean annual series. This is the
extraction proven (v1) to fix AVGO / ORCL / NVO.
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


def _date(s):
    return dt.date.fromisoformat(s)


def _days(a, b):
    return (_date(b) - _date(a)).days


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


def _accessors(facts):
    ns = facts.get("us-gaap") or facts.get("ifrs-full") or {}

    def entries(concept, prefer):
        u, _ = _units(ns.get(concept), prefer)
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

    def latest(concept, prefer=("USD", "usd")):
        es = entries(concept, prefer)
        return sorted(es, key=lambda x: (x["end"], x.get("filed", "")), reverse=True)[0] if es else None

    def ttm(concept, prefer=("USD", "usd")):
        es = [e for e in entries(concept, prefer) if e.get("start") and e.get("end")]
        if not es:
            return None
        latest_end = max(e["end"] for e in es)
        cur = max([e for e in es if e["end"] == latest_end], key=lambda e: _days(e["start"], e["end"]))
        cd = _days(cur["start"], cur["end"])
        if 350 <= cd <= 380:
            return cur["val"]
        annuals = sorted([e for e in es if 350 <= _days(e["start"], e["end"]) <= 380],
                         key=lambda e: e["end"], reverse=True)
        if annuals:
            A = annuals[0]
            prior, gap = None, 999
            for e in es:
                if abs(_days(e["start"], e["end"]) - cd) <= 15:
                    g = abs((_date(latest_end) - _date(e["end"])).days - 365)
                    if g <= 25 and g < gap:
                        gap, prior = g, e
            if prior and A["end"] > prior["end"]:
                return A["val"] + cur["val"] - prior["val"]
        qs = sorted([e for e in es if 80 <= _days(e["start"], e["end"]) <= 100],
                    key=lambda e: e["end"], reverse=True)
        seen, l4 = set(), []
        for e in qs:
            if e["end"] not in seen:
                seen.add(e["end"]); l4.append(e)
            if len(l4) == 4:
                break
        if len(l4) == 4:
            return sum(e["val"] for e in l4)
        return annuals[0]["val"] if annuals else None

    def annual_series(concept, prefer=("USD", "usd")):
        es = entries(concept, prefer)
        annuals = sorted([e for e in es if e.get("start") and 350 <= _days(e["start"], e["end"]) <= 380],
                         key=lambda e: e["end"], reverse=True)
        out, seen = [], set()
        for a in annuals:
            yr = a["end"][:4]
            if yr not in seen:
                seen.add(yr); out.append(a["val"])
        return out

    def currency(concept):
        _, k = _units(ns.get(concept), ("USD", "usd"))
        return k

    def latest_end(concept, prefer=("USD", "usd")):
        es = entries(concept, prefer)
        return max((e["end"] for e in es if e.get("end")), default=None)

    def quarters(concept, prefer=("USD", "usd")):
        """{end_date: value} for ~90-day (single-quarter) periods, recent first
        de-duplicated by end date. Used to grade our snapshotted revenue estimates."""
        es = entries(concept, prefer)
        qs = sorted([e for e in es if e.get("start") and 80 <= _days(e["start"], e["end"]) <= 100],
                    key=lambda e: e["end"], reverse=True)
        out = {}
        for e in qs:
            out.setdefault(e["end"], e["val"])
        return out

    def instant_series(concept, prefer=("USD", "usd")):
        """Point-in-time (balance-sheet) values at distinct fiscal year-ends, newest
        first. Lets v8.2 read PRIOR-year invested-capital components (equity/debt/cash)
        for the 5y ROIC-spread-trend signal — all from the same free SEC filing."""
        es = entries(concept, prefer)
        insts = sorted([e for e in es if not e.get("start")], key=lambda e: e["end"], reverse=True)
        out, seen = [], set()
        for e in insts:
            yr = e["end"][:4]
            if yr not in seen:
                seen.add(yr); out.append(e["val"])
        return out

    def instant_at_dates(concepts, dates, prefer=("USD", "usd"), tol_days=6):
        """T5: balance-sheet value AT each given fiscal-year-end date -> {date: value}.

        `instant_series` above picks one observation per CALENDAR year, which is fine
        for "the prior year" but not for building a per-year ROIC series: a company
        with a June year-end files four instants a year, and taking whichever happens
        to be newest in each calendar year would pair a March balance sheet with a
        June income statement. Here the income-statement FY-ends drive the lookup and
        the balance sheet is read AT those dates (±`tol_days` for filers whose
        year-end shifts by a day or two), so every ROIC in the series is
        NOPAT and invested capital measured over the same period.

        `concepts` is a tag ladder, and ONE concept is chosen for the whole series —
        the one covering the most of `dates`, ties broken by ladder order. Merging
        across tags per-date was tried first and is wrong: AVGO tags
        `LongTermDebtNoncurrent` in FY2021 and FY2025 but nothing long-term in FY2022
        or FY2023, so a per-date fallback filled those years from
        `LongTermDebtCurrent` — the CURRENT portion only, $0.4B against a real debt
        load near $39B. Invested capital collapsed for exactly those two years and
        ROIC read 130% and 140%. A partial value consumed as a whole one, which is the
        P2-3 defect. A year the chosen concept does not cover is simply absent, and
        the caller drops it.

        Returns (values_by_date, chosen_tag_or_None)."""
        want = [d for d in dates if d]
        if not want:
            return {}, None
        best = None
        for i, tag in enumerate(concepts):
            by_date = {e["end"]: e["val"] for e in entries(tag, prefer)
                       if not e.get("start") and e.get("end") and e.get("val") is not None}
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

    def annual_series_dated(concept, prefer=("USD", "usd")):
        """Like annual_series but keeps the FY-end date: [[end, val], …] newest first.
        Used to align annual EPS with year-end prices for the own-5y-P/E percentile."""
        es = entries(concept, prefer)
        annuals = sorted([e for e in es if e.get("start") and 350 <= _days(e["start"], e["end"]) <= 380],
                         key=lambda e: e["end"], reverse=True)
        out, seen = [], set()
        for a in annuals:
            yr = a["end"][:4]
            if yr not in seen:
                seen.add(yr); out.append([a["end"], a["val"]])
        return out

    return (latest, ttm, annual_series, currency, latest_end, quarters,
            instant_series, annual_series_dated, instant_at_dates)


REV = ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
       "RevenueFromContractWithCustomerIncludingAssessedTax", "Revenue", "SalesRevenueNet"]
OP = ["OperatingIncomeLoss", "OperatingIncome", "OperatingProfitLoss", "ProfitLossFromOperatingActivities"]
NI = ["NetIncomeLoss", "ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"]
# EBIT fallback inputs — used ONLY when no OP concept above exists (see extract()).
PRETAX = ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
          "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
          "ProfitLossBeforeTax"]
INT_EXP = ["InterestExpense", "InterestAndDebtExpense", "InterestExpenseDebt", "InterestExpenseNonoperating"]

# --- T5: tag ladders for the 5-year trend strip -----------------------------
# Written most-specific-first, the same convention `pick()` relies on (REV-27).
# IFRS variants are listed after the US-GAAP ones so a US filer is never resolved to
# them by accident; NVO (IFRS) tags none of the US-GAAP cash-flow concepts at all.
CFO_TAGS = ["NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
            "CashFlowsFromUsedInOperatingActivities"]
CAPEX_TAGS = ["PaymentsToAcquirePropertyPlantAndEquipment",
              "PurchaseOfPropertyPlantAndEquipment",
              "PaymentsToAcquireProductiveAssets",
              "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"]
GROSS_PROFIT_TAGS = ["GrossProfit"]
# ABBV and many pharma filers report no GrossProfit subtotal but do report the cost
# line, so gross profit is derived as revenue - cost (see domain/trend.py).
COST_OF_REV_TAGS = ["CostOfGoodsAndServicesSold", "CostOfRevenue", "CostOfSales",
                    "CostOfGoodsSold", "CostOfSalesExcludingAmortisation"]


def _ic_components(instant_at_dates, fy_ends, lt_tags=(), st_tags=(), eq_tags=(), cash_tags=()):
    """T5: {FY_end: {"equity":…, "debt":…, "cash":…}} at the given income-statement
    year-ends, for the per-year ROIC series.

    A year is returned ONLY when every leg it needs is filed. Invested capital assembled
    from a partial balance sheet is the P2-3 defect: the missing leg does not read as
    missing, it reads as a real change in the capital base, and the ROIC built on it
    swings for accounting reasons that have nothing to do with the business.

    The long-term debt leg gets the extra rule. If the filer reports a long-term debt
    concept in ANY year of the window, it is required in EVERY year — a company does not
    retire its entire long-term debt and reissue it, so a year where the tag is absent is
    a filing gap, not a debt-free year. Only when no long-term concept appears anywhere
    is the company treated as genuinely having none."""
    eq, _ = instant_at_dates(list(eq_tags), fy_ends)
    cash, _ = instant_at_dates(list(cash_tags), fy_ends)
    lt, lt_tag = instant_at_dates(list(lt_tags), fy_ends)
    st, _ = instant_at_dates(list(st_tags), fy_ends)
    lt_expected = bool(lt_tag)
    out = {}
    for d in fy_ends:
        if d not in eq or d not in cash:
            continue
        if lt_expected and d not in lt:
            continue                     # filing gap in the debt leg — drop the year
        out[d] = {"equity": eq[d], "cash": cash[d],
                  "debt": (lt.get(d) or 0) + (st.get(d) or 0),
                  "lt_source": lt_tag}
    return out


def _first_dated(annual_series_dated, tags, min_points=2):
    """First tag in `tags` that yields at least `min_points` annual observations,
    as [[FY_end, value], …] newest first. [] when none qualifies.

    Deliberately NOT a merge across tags: a filer that switched concepts mid-history
    (MSFT reports CostOfRevenue up to 2017 and CostOfGoodsAndServicesSold after) would
    otherwise produce a series with a definitional break in the middle, and a trend
    drawn across that break measures the accounting change, not the business."""
    for t in tags:
        s = annual_series_dated(t)
        if len(s) >= min_points:
            return s
    return []


def extract(companyfacts):
    """Return a dict of SEC-derived financial values (in reported currency)."""
    facts = companyfacts.get("facts", companyfacts)
    (latest, ttm, annual_series, currency, latest_end, quarters,
     instant_series, annual_series_dated, instant_at_dates) = _accessors(facts)

    def pick(concepts):
        """Choose the concept with the FRESHEST data, then by LIST ORDER.

        Freshness fixes filers that switch tags over time (AVGO moved net income from
        NetIncomeLoss, ending FY2024, to ProfitLoss) — that part is unchanged and is
        what the original fix was for.

        REV-27: the tie-break used to be the larger |TTM|, which is a magnitude
        preference dressed up as a rule. When a filer tags BOTH `Revenues` and
        `RevenueFromContractWithCustomerExcludingAssessedTax` for the same period —
        common, because the first is the legacy total and the second the ASC 606 core
        — "bigger wins" silently picks the broader number, which can carry other
        income. Overstated revenue then deflates every margin and, in the reverse DCF,
        enlarges the base the market's growth is measured against, so the implied CAGR
        comes out too low and the verdict too kind.

        The concept lists in this module are already written in preference order
        (most specific first). Honouring that order is the rule the lists imply, and
        it does not depend on which number happens to be larger this year. A material
        disagreement between the chosen concept and a same-date sibling is reported so
        it can never be silent.
        """
        best = None                      # ((end, -list_index), value, concept)
        for i, c in enumerate(concepts):
            le, v = latest_end(c), ttm(c)
            if le and v is not None:
                key = (le, -i)                     # freshest first, then list order
                if best is None or key > best[0]:
                    best = (key, v, c)
        if best is None:
            return None, None, []
        _, val, concept = best
        end = latest_end(concept)
        alts = [(c, ttm(c)) for c in concepts
                if c != concept and latest_end(c) == end and ttm(c) is not None
                and val and abs(ttm(c) - val) / abs(val) > 0.05]
        return val, concept, alts

    rev, rev_concept, rev_alts = pick(REV)
    net_income, ni_concept, ni_alts = pick(NI)
    operating_income, _op_concept, _ = pick(OP)
    ref_end = _latest_end(facts, rev_concept)
    # REV-27: two concepts covering the SAME period that disagree by >5% is a real
    # ambiguity about what the filer means, not a detail. The old magnitude tie-break
    # resolved it invisibly; list order resolves it predictably and says so.
    concept_conflicts = [f"{fld}: used {used} ({base/1e9:.2f}B) but {alt} covers the same "
                         f"period at {av/1e9:.2f}B"
                         for fld, used, base, alts in (("revenue", rev_concept, rev, rev_alts),
                                                       ("net income", ni_concept, net_income, ni_alts))
                         if alts and base for alt, av in alts]

    # --- EBIT fallback (P-A) ------------------------------------------------
    # Some filers never tag an operating-income subtotal (LLY, PFE: the income
    # statement runs revenue -> costs -> "income before income taxes"). Without it
    # NOPAT/FCFF are None and the engine mistakes a very profitable company for a
    # pre-profit one. Damodaran S5: EBIT = pretax income + interest expense (add
    # back the financing charge). Approximate — pretax also carries non-operating
    # items — so it is flagged via provenance and only used when OP is absent.
    op_derived = operating_income is None
    _op_ann, _op_qtr, _op_ann_dated = [], {}, []
    if op_derived:
        def _has(tag):
            return tag if latest_end(tag) else None

        _pre_c = next((t for t in PRETAX if _has(t)), None)
        _int_c = next((t for t in INT_EXP if _has(t)), None)
        if _pre_c:
            _p_ttm = ttm(_pre_c)
            if _p_ttm is not None:
                operating_income = _p_ttm + (ttm(_int_c) or 0 if _int_c else 0)
            _pa = dict(annual_series_dated(_pre_c))
            _ia = dict(annual_series_dated(_int_c)) if _int_c else {}
            _op_ann = [_pa[d] + _ia.get(d, 0) for d in sorted(_pa, reverse=True)]
            _op_ann_dated = [[d, _pa[d] + _ia.get(d, 0)] for d in sorted(_pa, reverse=True)]
            _pq = quarters(_pre_c)
            _iq = quarters(_int_c) if _int_c else {}
            _op_qtr = {d: v + _iq.get(d, 0) for d, v in _pq.items()}
        op_derived = operating_income is not None

    def fresh(*tags, prefer=("USD", "usd")):
        """Latest value among `tags` whose period end is recent (<540d before the
        latest filing) — rejects stale tags a filer abandoned (e.g. ORCL's
        LongTermDebt frozen at 2022)."""
        for tag in tags:
            e = latest(tag, prefer)
            if e and e.get("val") is not None and _recent(e.get("end"), ref_end):
                return e["val"]
        return None

    # total debt: prefer an explicit combined tag, else long-term + current
    LT_DEBT = ("LongTermDebtNoncurrent", "LongTermDebt", "LongTermNotesPayable", "Borrowings")
    ST_DEBT = ("LongTermDebtCurrent", "DebtCurrent", "ShortTermBorrowings", "NotesPayableCurrent")
    total_debt = (fresh("DebtLongtermAndShorttermCombinedAmount")
                  or _sum(fresh(*LT_DEBT), fresh(*ST_DEBT)))

    # --- v8.2 additions (all from the same companyfacts JSON) -----------------
    EQUITY_TAGS = ("StockholdersEquity", "Equity",
                   "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest")
    # REV-18/REV-19: working-capital + receivables tag ladders (shared by the
    # current-year `fresh` lookup and the prior-year `prior_instant` one, so the two
    # can never drift onto different concepts and produce a bogus delta).
    AR_TAGS = ("AccountsReceivableNetCurrent", "ReceivablesNetCurrent",
               "AccountsAndOtherReceivablesNetCurrent")
    INV_TAGS = ("InventoryNet", "InventoryGross", "Inventories")
    AP_TAGS = ("AccountsPayableCurrent", "AccountsPayableTradeCurrent",
               "AccountsPayableAndAccruedLiabilitiesCurrent")
    CASH_TAGS = ("CashAndCashEquivalentsAtCarryingValue", "CashAndCashEquivalents")
    DEFREV_TAGS = ("ContractWithCustomerLiabilityCurrent", "ContractWithCustomerLiability",
                   "DeferredRevenueCurrent", "DeferredRevenue")

    def prior_instant(*tags):
        for t in tags:
            s = instant_series(t)
            if len(s) > 1:
                return s[1]
        return None

    prior_lt = prior_instant("DebtLongtermAndShorttermCombinedAmount", *LT_DEBT)
    prior_st = prior_instant(*ST_DEBT)
    total_debt_prior = (prior_instant("DebtLongtermAndShorttermCombinedAmount")
                        or _sum(prior_lt, prior_st))

    # T5: the fiscal-year ends the trend strip is built on. Driven by the INCOME
    # statement (operating income, else revenue) because that is what every ratio in
    # the strip has in its numerator; the balance sheet is then read at these dates.
    _rev_dated = annual_series_dated(rev_concept) if rev_concept else []
    _oi_dated = (annual_series_dated(_op_concept) if _op_concept else _op_ann_dated)
    _fy_ends = [d for d, _ in (_oi_dated or _rev_dated)][:12]

    return {
        "currency": (currency(rev_concept) if rev_concept else None) or "USD",
        "revenue": rev,
        "revenue_annuals": annual_series(rev_concept) if rev_concept else [],
        # T5: same series WITH the fiscal-year end, so the trend strip can align
        # revenue against gross profit / operating income by DATE rather than by
        # position. The bare list above is untouched — every existing consumer of
        # revenue_annuals keeps the exact input it had.
        "revenue_annuals_dated": annual_series_dated(rev_concept) if rev_concept else [],
        "revenue_quarters": quarters(rev_concept) if rev_concept else {},
        "operating_income_quarters": quarters(_op_concept) if _op_concept else _op_qtr,
        # quarterly diluted EPS actuals (USD/shares) — the free ACTUAL we grade
        # analyst estimates against in surprise_backfill (US GAAP + IFRS filers).
        "eps_quarters": (quarters("EarningsPerShareDiluted", prefer=("USD/shares",))
                         or quarters("DilutedEarningsLossPerShare", prefer=("USD/shares", "DKK/shares"))),
        "net_income": net_income,
        "operating_income": operating_income,
        "eps_gaap": ttm("EarningsPerShareDiluted", prefer=("USD/shares",)),
        "shares_diluted": _val(latest("WeightedAverageNumberOfDilutedSharesOutstanding", prefer=("shares",))
                               or latest("CommonStockSharesOutstanding", prefer=("shares",))),
        # P2-1: the ACTUAL diluted share count, year by year. The SBC-dilution proxy
        # (SBC$ / market cap) measures GROSS grants; what divides earnings is the
        # count NET of buybacks. On the committed fixtures the two disagree in SIGN
        # for MSFT (proxy +0.39%/yr, actual -0.05%/yr) and ABBV (+0.28% vs 0.00%),
        # because both grant heavily and buy back more. This series is in the same
        # companyfacts JSON already being downloaded — 10-18 annual points per US
        # filer — so the proxy was never necessary for them.
        "shares_diluted_annuals": annual_series(
            "WeightedAverageNumberOfDilutedSharesOutstanding", prefer=("shares",)),
        "total_debt": total_debt,
        "cash": fresh(*CASH_TAGS),
        "equity": fresh(*EQUITY_TAGS),
        "capex": ttm("PaymentsToAcquirePropertyPlantAndEquipment") or ttm("PurchaseOfPropertyPlantAndEquipment"),
        # --- T5: multi-year series for the 5-year trend strip -------------------
        # DATED, not bare lists. `annual_series` returns values newest-first, and two
        # tags do not necessarily cover the same set of fiscal years — a filer can
        # report GrossProfit for 18 years and revenue for 10. Zipping them by INDEX
        # would then divide FY2025 gross profit by FY2017 revenue and call it a margin,
        # which is exactly the period-mismatch class P3-1 was about. Keeping the FY-end
        # date with every value forces the consumer (domain/trend.py) to align on the
        # date and simply drop years where one side is missing.
        "cfo_annuals_dated": _first_dated(annual_series_dated, CFO_TAGS),
        "capex_annuals_dated": _first_dated(annual_series_dated, CAPEX_TAGS),
        "gross_profit_annuals_dated": _first_dated(annual_series_dated, GROSS_PROFIT_TAGS),
        # cost of revenue lets gross profit be DERIVED when the filer tags no subtotal
        "cost_of_revenue_annuals_dated": _first_dated(annual_series_dated, COST_OF_REV_TAGS),
        # T5: invested capital AT each income-statement year-end -> a real per-year
        # ROIC series (NOPAT_t / IC_t), which is Damodaran's own moat measure over
        # time. Read at the FY-end dates, never "whichever instant is newest in that
        # calendar year" — see instant_at_dates.
        "ic_components_dated": _ic_components(
            instant_at_dates, _fy_ends,
            lt_tags=("DebtLongtermAndShorttermCombinedAmount",) + LT_DEBT,
            st_tags=ST_DEBT, eq_tags=EQUITY_TAGS, cash_tags=CASH_TAGS),
        "dep_amort": ttm("DepreciationAndAmortization") or ttm("Depreciation") or ttm("DepreciationDepletionAndAmortization"),
        "income_before_tax": ttm("IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest")
                             or ttm("ProfitLossBeforeTax"),
        "tax_expense": ttm("IncomeTaxExpenseBenefit"),
        "latest_period_end": _latest_end(facts, rev_concept),
        # --- v8.2 additions ---
        "cfo": ttm("NetCashProvidedByUsedInOperatingActivities")
               or ttm("NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"),
        # REV-1: stock-based compensation. Was declared on FinancialFacts and consumed
        # by earnings_quality + the SBC-dilution rule, but NOTHING ever populated it on
        # the live path (only fmp.parse(), which the pipeline does not call) — so
        # f.sbc was permanently None: the "SBC >10% of revenue" branch could never
        # fire and forward EPS was never diluted. Both concepts below are the standard
        # cash-flow-statement add-back.
        "sbc": ttm("ShareBasedCompensation")
               or ttm("AllocatedShareBasedCompensationExpense")
               or ttm("ShareBasedCompensationArrangementByShareBasedPaymentAwardCompensationCost"),
        "total_assets": fresh("Assets"),
        "receivables": fresh(*AR_TAGS),
        # REV-19: the prior-year AR, so the channel-stuffing check the `receivables`
        # comment has always promised can finally be computed (AR growing much
        # faster than revenue = revenue that has not turned into cash).
        "receivables_prior": prior_instant(*AR_TAGS),
        # REV-18: working-capital components. Damodaran's reinvestment is
        # capex + acquisitions + dWC - D&A; the dWC leg was missing entirely.
        # Derived from the BALANCE SHEET rather than the cash-flow tag
        # `IncreaseDecreaseInOperatingCapital`, whose sign convention varies by filer
        # — a silent sign flip here would be exactly the class of bug this codebase
        # keeps paying for. (AR + inventory - AP) has one unambiguous reading.
        "inventory": fresh(*INV_TAGS),
        "inventory_prior": prior_instant(*INV_TAGS),
        "accounts_payable": fresh(*AP_TAGS),
        "accounts_payable_prior": prior_instant(*AP_TAGS),
        "interest_expense": ttm("InterestExpense") or ttm("InterestAndDebtExpense") or ttm("InterestExpenseDebt"),
        "rnd_expense": ttm("ResearchAndDevelopmentExpense")
                       or ttm("ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost"),
        "rnd_annuals": annual_series("ResearchAndDevelopmentExpense")
                       or annual_series("ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost"),
        "operating_income_annuals": annual_series(_op_concept) if _op_concept else _op_ann,
        # T5: dated twin. For an EBIT-fallback filer (LLY/PFE — P-A) `_op_ann` is
        # already built from dated pretax+interest maps, so the dates come from there.
        "operating_income_annuals_dated": (annual_series_dated(_op_concept) if _op_concept
                                           else _op_ann_dated),
        "operating_income_derived": op_derived,
        "equity_prior": prior_instant(*EQUITY_TAGS),
        "total_debt_prior": total_debt_prior,
        "cash_prior": prior_instant(*CASH_TAGS),
        # --- round 2: organic growth + billings ---
        "acquisitions_net": ttm("PaymentsToAcquireBusinessesNetOfCashAcquired")
                            or ttm("PaymentsToAcquireBusinessesAndInterestInAffiliates")
                            or ttm("PaymentsToAcquireBusinessesGross"),
        "deferred_revenue": fresh(*DEFREV_TAGS),
        "deferred_revenue_prior": prior_instant(*DEFREV_TAGS),
        # P1-5 (Philosophy-2026 S5): operating-lease liability — Damodaran treats
        # leases as debt; capitalized into invested capital + debt by the engine.
        "operating_leases": fresh("OperatingLeaseLiability")
                            or _sum(fresh("OperatingLeaseLiabilityNoncurrent"),
                                    fresh("OperatingLeaseLiabilityCurrent")),
        # REV-4: the PRIOR-year lease liability. Invested capital now capitalizes
        # leases, but the prior-year IC used for the ROIC-spread TREND did not — so a
        # lease-heavy filer was compared against its own lease-free past and read as
        # deteriorating every single year. Same instant series, one year back.
        "operating_leases_prior": prior_instant("OperatingLeaseLiability")
                                  or _sum(prior_instant("OperatingLeaseLiabilityNoncurrent"),
                                          prior_instant("OperatingLeaseLiabilityCurrent")),
        "eps_annuals_dated": annual_series_dated("EarningsPerShareDiluted", prefer=("USD/shares",)),
        "concept_conflicts": concept_conflicts,      # REV-27
    }


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


def _val(node):
    return node.get("val") if isinstance(node, dict) else node


def _sum(*xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return sum(xs) if xs else None


def _latest_end(facts, concept):
    ns = facts.get("us-gaap") or facts.get("ifrs-full") or {}
    u, _ = _units(ns.get(concept or ""), ("USD", "usd"))
    return max((e["end"] for e in u if e.get("end")), default=None) if u else None


def populate(ff, companyfacts):
    """Fill SEC-sourced fields into a FinancialFacts object (source tag 'sec')."""
    d = extract(companyfacts)
    for k in ("currency", "revenue", "net_income", "operating_income", "eps_gaap",
              "shares_diluted", "total_debt", "cash", "equity", "capex", "dep_amort",
              "income_before_tax", "tax_expense", "revenue_annuals", "revenue_quarters",
              "operating_income_quarters", "eps_quarters",
              # v8.2 additions
              "cfo", "total_assets", "receivables", "interest_expense", "rnd_expense",
              "rnd_annuals", "operating_income_annuals",
              "equity_prior", "total_debt_prior", "cash_prior",
              # round 2 additions
              "acquisitions_net", "deferred_revenue", "deferred_revenue_prior",
              # P1-5: lease capitalization (+ REV-4 prior-year, for a like-for-like IC)
              "operating_leases", "operating_leases_prior",
              # REV-1: SBC — earnings-quality flag AND forward-EPS dilution
              "sbc",
              # P2-1: real share-count history (NOT a currency field — never FX-converted)
              "shares_diluted_annuals",
              # REV-18/19: working-capital reinvestment + the AR-vs-revenue check
              "receivables_prior", "inventory", "inventory_prior",
              "accounts_payable", "accounts_payable_prior",
              # round 3 addition
              "eps_annuals_dated",
              # T5: dated annual series behind the 5-year trend strip
              "revenue_annuals_dated", "operating_income_annuals_dated",
              "cfo_annuals_dated", "capex_annuals_dated",
              "gross_profit_annuals_dated", "cost_of_revenue_annuals_dated",
              "ic_components_dated"):
        ff.set(k, d.get(k), "sec")
    if d.get("operating_income_derived"):
        # P-A: filer tags no operating-income subtotal — EBIT approximated as
        # pretax income + interest expense. Mark it so the engine can flag it.
        for k in ("operating_income", "operating_income_annuals", "operating_income_quarters"):
            if d.get(k) is not None:
                ff.provenance[k] = "sec-derived (EBIT = pretax + interest)"
    for _c in (d.get("concept_conflicts") or []):    # REV-27: ambiguous XBRL tagging
        ff.flags.append("SEC tag conflict - " + _c)
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
        # H1: serve the EXPIRED cache (flagged) instead of failing the ticker.
        # Filings change quarterly, so stale-but-real data beats no data during
        # an SEC outage. Callers surface "_stale_cache" as a data flag.
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
