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
            instant_series, annual_series_dated)


REV = ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
       "RevenueFromContractWithCustomerIncludingAssessedTax", "Revenue", "SalesRevenueNet"]
OP = ["OperatingIncomeLoss", "OperatingIncome", "OperatingProfitLoss", "ProfitLossFromOperatingActivities"]
NI = ["NetIncomeLoss", "ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"]
# EBIT fallback inputs — used ONLY when no OP concept above exists (see extract()).
PRETAX = ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
          "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
          "ProfitLossBeforeTax"]
INT_EXP = ["InterestExpense", "InterestAndDebtExpense", "InterestExpenseDebt", "InterestExpenseNonoperating"]


def extract(companyfacts):
    """Return a dict of SEC-derived financial values (in reported currency)."""
    facts = companyfacts.get("facts", companyfacts)
    (latest, ttm, annual_series, currency, latest_end, quarters,
     instant_series, annual_series_dated) = _accessors(facts)

    def pick(concepts):
        """Choose the concept with the FRESHEST data (most recent end date), then
        larger |TTM|. Fixes filers that switch tags over time, e.g. AVGO moving
        net income from NetIncomeLoss (ends FY2024) to ProfitLoss (current)."""
        best = None  # (latest_end, abs_ttm), value, concept
        for c in concepts:
            le = latest_end(c)
            v = ttm(c)
            if le and v is not None:
                key = (le, abs(v))
                if best is None or key > best[0]:
                    best = (key, v, c)
        return (best[1], best[2]) if best else (None, None)

    rev, rev_concept = pick(REV)
    net_income, _ = pick(NI)
    operating_income, _op_concept = pick(OP)
    ref_end = _latest_end(facts, rev_concept)

    # --- EBIT fallback (P-A) ------------------------------------------------
    # Some filers never tag an operating-income subtotal (LLY, PFE: the income
    # statement runs revenue -> costs -> "income before income taxes"). Without it
    # NOPAT/FCFF are None and the engine mistakes a very profitable company for a
    # pre-profit one. Damodaran S5: EBIT = pretax income + interest expense (add
    # back the financing charge). Approximate — pretax also carries non-operating
    # items — so it is flagged via provenance and only used when OP is absent.
    op_derived = operating_income is None
    _op_ann, _op_qtr = [], {}
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

    return {
        "currency": (currency(rev_concept) if rev_concept else None) or "USD",
        "revenue": rev,
        "revenue_annuals": annual_series(rev_concept) if rev_concept else [],
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
        "total_debt": total_debt,
        "cash": fresh(*CASH_TAGS),
        "equity": fresh(*EQUITY_TAGS),
        "capex": ttm("PaymentsToAcquirePropertyPlantAndEquipment") or ttm("PurchaseOfPropertyPlantAndEquipment"),
        "dep_amort": ttm("DepreciationAndAmortization") or ttm("Depreciation") or ttm("DepreciationDepletionAndAmortization"),
        "income_before_tax": ttm("IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest")
                             or ttm("ProfitLossBeforeTax"),
        "tax_expense": ttm("IncomeTaxExpenseBenefit"),
        "latest_period_end": _latest_end(facts, rev_concept),
        # --- v8.2 additions ---
        "cfo": ttm("NetCashProvidedByUsedInOperatingActivities")
               or ttm("NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"),
        "total_assets": fresh("Assets"),
        "receivables": fresh("AccountsReceivableNetCurrent", "ReceivablesNetCurrent",
                             "AccountsAndOtherReceivablesNetCurrent"),
        "interest_expense": ttm("InterestExpense") or ttm("InterestAndDebtExpense") or ttm("InterestExpenseDebt"),
        "rnd_expense": ttm("ResearchAndDevelopmentExpense")
                       or ttm("ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost"),
        "rnd_annuals": annual_series("ResearchAndDevelopmentExpense")
                       or annual_series("ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost"),
        "operating_income_annuals": annual_series(_op_concept) if _op_concept else _op_ann,
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
        "eps_annuals_dated": annual_series_dated("EarningsPerShareDiluted", prefer=("USD/shares",)),
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
              # P1-5: lease capitalization
              "operating_leases",
              # round 3 addition
              "eps_annuals_dated"):
        ff.set(k, d.get(k), "sec")
    if d.get("operating_income_derived"):
        # P-A: filer tags no operating-income subtotal — EBIT approximated as
        # pretax income + interest expense. Mark it so the engine can flag it.
        for k in ("operating_income", "operating_income_annuals", "operating_income_quarters"):
            if d.get(k) is not None:
                ff.provenance[k] = "sec-derived (EBIT = pretax + interest)"
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
