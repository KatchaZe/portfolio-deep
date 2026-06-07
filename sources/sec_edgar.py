"""
SEC EDGAR source adapter — the PRIMARY financial source (free, all US + 20-F filers).

Robust TTM = latest full fiscal year + current YTD - prior-year YTD, with
sum-of-4Q and latest-annual fallbacks. De-duplicates restated facts by keeping the
most recently filed value. Growth uses the clean annual series. This is the
extraction proven (v1) to fix AVGO / ORCL / NVO.
"""
import datetime as dt


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

    return latest, ttm, annual_series, currency, latest_end


REV = ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
       "RevenueFromContractWithCustomerIncludingAssessedTax", "Revenue", "SalesRevenueNet"]
OP = ["OperatingIncomeLoss", "OperatingIncome", "OperatingProfitLoss", "ProfitLossFromOperatingActivities"]
NI = ["NetIncomeLoss", "ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"]


def extract(companyfacts):
    """Return a dict of SEC-derived financial values (in reported currency)."""
    facts = companyfacts.get("facts", companyfacts)
    latest, ttm, annual_series, currency, latest_end = _accessors(facts)

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
    operating_income, _ = pick(OP)
    ref_end = _latest_end(facts, rev_concept)

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
    total_debt = (fresh("DebtLongtermAndShorttermCombinedAmount")
                  or _sum(fresh("LongTermDebtNoncurrent", "LongTermDebt", "LongTermNotesPayable", "Borrowings"),
                          fresh("LongTermDebtCurrent", "DebtCurrent", "ShortTermBorrowings", "NotesPayableCurrent")))

    return {
        "currency": (currency(rev_concept) if rev_concept else None) or "USD",
        "revenue": rev,
        "revenue_annuals": annual_series(rev_concept) if rev_concept else [],
        "net_income": net_income,
        "operating_income": operating_income,
        "eps_gaap": ttm("EarningsPerShareDiluted", prefer=("USD/shares",)),
        "shares_diluted": _val(latest("WeightedAverageNumberOfDilutedSharesOutstanding", prefer=("shares",))
                               or latest("CommonStockSharesOutstanding", prefer=("shares",))),
        "total_debt": total_debt,
        "cash": fresh("CashAndCashEquivalentsAtCarryingValue", "CashAndCashEquivalents"),
        "equity": fresh("StockholdersEquity", "Equity",
                        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"),
        "capex": ttm("PaymentsToAcquirePropertyPlantAndEquipment") or ttm("PurchaseOfPropertyPlantAndEquipment"),
        "dep_amort": ttm("DepreciationAndAmortization") or ttm("Depreciation") or ttm("DepreciationDepletionAndAmortization"),
        "income_before_tax": ttm("IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest")
                             or ttm("ProfitLossBeforeTax"),
        "tax_expense": ttm("IncomeTaxExpenseBenefit"),
        "latest_period_end": _latest_end(facts, rev_concept),
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
              "income_before_tax", "tax_expense", "revenue_annuals"):
        ff.set(k, d.get(k), "sec")
    if d.get("latest_period_end"):
        ff.set("fiscal_year", d["latest_period_end"], "sec")
    return ff, d


# fetch (runs where SEC is reachable)
def fetch_companyfacts(cik, user_agent, requests_mod=None, timeout=30):
    import requests as _r
    requests_mod = requests_mod or _r
    cik10 = str(cik).zfill(10)
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json"
    r = requests_mod.get(url, headers={"User-Agent": user_agent}, timeout=timeout)
    r.raise_for_status()
    return r.json()
