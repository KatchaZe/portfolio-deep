"""Independent reference for PeerPanel / MoatTrend numbers (docs/peer-panel/SPEC.md).

Computed from raw SEC companyfacts only. Imports nothing from the app.
Point in time: Company(cf, as_of) sees only facts with filed <= as_of, and for a
period reported more than once it takes the latest filing visible at as_of.

Concept choices (mine, not the app's):
  revenue      per period, the value of the LATEST filing among
               RevenueFromContractWithCustomerExcludingAssessedTax, Revenues, SalesRevenueNet,
               RevenueFromContractWithCustomerIncludingAssessedTax (ifrs-full: Revenue); within
               that filing the largest. Largest alone took a stale pre-restatement year (SNPS)
               and a mis-tagged one (ASTS FY2024, live 2026-11-17). Every metric is also
               computed on each concept alone (by_concept), so a difference that is only the
               app's concept choice is named as such.
  gross profit GrossProfit for the exact FY period; else revenue - cost of revenue
               (CostOfRevenue, CostOfGoodsAndServicesSold, CostOfGoodsSold; ifrs CostOfSales)
               for the same period; else None with a flag. Never an older year.
  op income    OperatingIncomeLoss (ifrs ProfitLossFromOperatingActivities). GAAP, no add-back.
               A filer with no OperatingIncomeLoss period ending within 2 years of its newest
               revenue period (LLY, PFE, JNJ, HCA): pretax income + interest expense for the
               same period (the documented EBIT fallback, 2026-08-02).
  net income   NetIncomeLoss, else ProfitLoss for a period NetIncomeLoss lacks.
  shares       WeightedAverageNumberOfDilutedSharesOutstanding, the shortest duration
               ending at the newest end date. No split adjustment.
  debt         DebtLongtermAndShorttermCombinedAmount; else LongTermDebt + short-term
               (ShortTermBorrowings, CommercialPaper); else noncurrent + current legs; else
               LongTermNotesAndLoans + NotesPayableCurrent. Plus finance lease liabilities.
  leases       OperatingLeaseLiability, else Noncurrent + Current.
  cash         CashCashEquivalentsAndShortTermInvestments, else
               CashAndCashEquivalentsAtCarryingValue + ShortTermInvestments (else current
               available-for-sale debt securities, else current marketable securities). Managed care
               (MANAGED_CARE): cash and equivalents only (user rule 2026-10-18).
  minority     MinorityInterest at the balance-sheet date, else 0.
Ratios are fractions (0.25 = 25%). Money is in the filer's reporting currency.
"""
import datetime as dt
import json
import statistics

REV = {"us-gaap": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
                   "SalesRevenueNet", "RevenueFromContractWithCustomerIncludingAssessedTax"],
       "ifrs-full": ["Revenue"]}
GP = {"us-gaap": ["GrossProfit"], "ifrs-full": ["GrossProfit"]}
COST = {"us-gaap": ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"],
        "ifrs-full": ["CostOfSales"]}
OI = {"us-gaap": ["OperatingIncomeLoss"], "ifrs-full": ["ProfitLossFromOperatingActivities"]}
PRETAX = ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
          "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"]
INTEREST = ["InterestExpense", "InterestExpenseNonoperating", "InterestAndDebtExpense", "InterestExpenseDebt"]
OI_STALE_DAYS = 730
MANAGED_CARE = {"UNH", "ELV", "CI", "CVS", "HUM", "CNC", "MOH"}
NI = {"us-gaap": ["NetIncomeLoss", "ProfitLoss"], "ifrs-full": ["ProfitLoss"]}
SHARES = ["WeightedAverageNumberOfDilutedSharesOutstanding"]

FY_DAYS = (350, 380)
Q_DAYS = (80, 100)
YTD9_DAYS = (260, 290)
TTM_DAYS = (355, 380)
YEAR_TOL_DAYS = 20          # 52/53-week filers move their year end by a few days
MONEY_METRICS = ("revenue_ttm",)
RATIO_METRICS = ("growth_fy", "cagr3_fy", "gross_margin_fy", "op_margin_ttm")
MULTIPLE_METRICS = ("ev_sales", "ev_ebit", "pe")
METRICS = MONEY_METRICS + RATIO_METRICS + MULTIPLE_METRICS


def D(s):
    return dt.date.fromisoformat(s)


def span_days(start, end):
    return (D(end) - D(start)).days + 1


def years_before(end, n):
    d = D(end)
    try:
        return d.replace(year=d.year - n)
    except ValueError:
        return d.replace(year=d.year - n, day=28)


def frame_cy(start, end):
    """Calendar year SEC frames assign a ~1-year duration.

    Measured on the fixtures' own `frame` tags (tests/test_panel_pointintime.py asserts it):
    a year ending 31 May lands in the PREVIOUS calendar year, a year ending 30 June lands
    in the SAME one, although 184 of its 365 days are in the previous year. So "the CY
    holding most of the period" is wrong for June year ends. Rule used: the calendar year
    of (end - 180 days), so 30 June is the first year end that stays in its own year.
    Year ends between 1 and 29 June are untested."""
    return (D(end) - dt.timedelta(days=180)).year


class Company:
    def __init__(self, companyfacts, as_of=None):
        self.cf = companyfacts
        self.as_of = as_of
        facts = companyfacts.get("facts") or {}
        self.taxonomy = "us-gaap" if "us-gaap" in facts else ("ifrs-full" if "ifrs-full" in facts else None)
        self.node = facts.get(self.taxonomy) or {}
        self.currency = self._currency()
        self.flags = []

    def _currency(self):
        for c in REV.get(self.taxonomy, []):
            units = (self.node.get(c) or {}).get("units") or {}
            for u in units:
                if "/" not in u and u != "shares":
                    return u
        return None

    def _facts(self, concept, unit):
        out = []
        for e in ((self.node.get(concept) or {}).get("units") or {}).get(unit, []):
            if self.as_of and e.get("filed", "9999") > self.as_of:
                continue
            f = dict(e)
            f["concept"] = concept
            out.append(f)
        return out

    def durations(self, concepts, unit=None, pick="first"):
        """{(start, end): fact}. Per concept the latest filing wins; across concepts the
        first in the ladder (pick="first"), the largest value (pick="max"), or the latest
        filing and within it the largest (pick="latest")."""
        unit = unit or self.currency
        per = []
        for c in concepts:
            m = {}
            for f in self._facts(c, unit):
                if "start" not in f:
                    continue
                k = (f["start"], f["end"])
                if k not in m or f["filed"] >= m[k]["filed"]:
                    m[k] = f
            per.append(m)
        out = {}
        for m in per:
            for k, f in m.items():
                if k not in out:
                    out[k] = f
                elif pick == "max" and f["val"] > out[k]["val"]:
                    out[k] = f
                elif pick == "latest" and (f["filed"], f["val"]) > (out[k]["filed"], out[k]["val"]):
                    out[k] = f
        return out

    def instants(self, concept, unit=None):
        m = {}
        for f in self._facts(concept, unit or self.currency):
            if "start" in f:
                continue
            if f["end"] not in m or f["filed"] >= m[f["end"]]["filed"]:
                m[f["end"]] = f
        return m


def annuals(durs):
    """{fy_end: fact} for ~1-year durations."""
    out = {}
    for (s, e), f in durs.items():
        if FY_DAYS[0] <= span_days(s, e) <= FY_DAYS[1]:
            if e not in out or f["filed"] >= out[e]["filed"]:
                out[e] = f
    return out


def quarters(durs):
    """{end: {"start","end","val","parts"}}: filed quarters plus a fourth quarter derived
    as FY - 9-month YTD, or FY - three filed quarters."""
    q = {}
    for (s, e), f in durs.items():
        if Q_DAYS[0] <= span_days(s, e) <= Q_DAYS[1]:
            q[e] = {"start": s, "end": e, "val": f["val"], "parts": [f]}
    for (s, e), fy in durs.items():
        if not FY_DAYS[0] <= span_days(s, e) <= FY_DAYS[1] or e in q:
            continue
        ytd = [f for (s2, e2), f in durs.items()
               if s2 == s and YTD9_DAYS[0] <= span_days(s2, e2) <= YTD9_DAYS[1]]
        if ytd:
            y = max(ytd, key=lambda f: f["end"])
            q4s = (D(y["end"]) + dt.timedelta(days=1)).isoformat()
            if Q_DAYS[0] <= span_days(q4s, e) <= Q_DAYS[1]:
                q[e] = {"start": q4s, "end": e, "val": fy["val"] - y["val"], "parts": [fy, y]}
                continue
        inside = sorted((x for x in q.values() if x["start"] >= s and x["end"] < e),
                        key=lambda x: x["end"])
        if len(inside) == 3 and inside[0]["start"] == s and _contiguous(inside):
            q4s = (D(inside[-1]["end"]) + dt.timedelta(days=1)).isoformat()
            if Q_DAYS[0] <= span_days(q4s, e) <= Q_DAYS[1]:
                q[e] = {"start": q4s, "end": e, "val": fy["val"] - sum(x["val"] for x in inside),
                        "parts": [fy] + [p for x in inside for p in x["parts"]]}
    return q


def _contiguous(qs):
    return all((D(b["start"]) - D(a["end"])).days == 1 for a, b in zip(qs, qs[1:]))


def ttm(durs):
    """Trailing twelve months ending at the newest period: four contiguous quarters,
    or the fiscal year itself when the newest period is a year end."""
    ann, qs = annuals(durs), quarters(durs)
    last_fy = max(ann) if ann else None
    last_q = max(qs) if qs else None
    if last_fy and (not last_q or last_fy >= last_q):
        f = ann[last_fy]
        return {"start": f["start"], "end": f["end"], "val": f["val"], "parts": [f], "basis": "FY"}
    if not last_q:
        return None
    four = sorted(qs.values(), key=lambda x: x["end"])[-4:]
    if len(four) < 4 or not _contiguous(four):
        return {"start": None, "end": last_q, "val": None, "parts": [],
                "basis": "gap", "ends": [x["end"] for x in four]}
    days = span_days(four[0]["start"], four[-1]["end"])
    if not TTM_DAYS[0] <= days <= TTM_DAYS[1]:
        return {"start": None, "end": last_q, "val": None, "parts": [], "basis": "span %d" % days}
    return {"start": four[0]["start"], "end": four[-1]["end"],
            "val": sum(x["val"] for x in four),
            "parts": [p for x in four for p in x["parts"]], "basis": "4Q",
            "quarters": [(x["start"], x["end"]) for x in four]}


def fy_near(ann, target_date):
    """The fiscal-year end closest to target_date, within YEAR_TOL_DAYS. Joined by date."""
    best = None
    for e in ann:
        gap = abs((D(e) - target_date).days)
        if gap <= YEAR_TOL_DAYS and (best is None or gap < best[0]):
            best = (gap, e)
    return best[1] if best else None


def _slim(f):
    return {k: f.get(k) for k in ("concept", "start", "end", "val", "filed", "form", "fp", "fy", "frame", "accn")}


def _ratio(a, b):
    return a / b if a is not None and b not in (None, 0) and b > 0 else None


def _balance(co, bs_date, cash_only=False):
    """(debt, leases, cash, used) at bs_date. Every leg from the same balance-sheet date."""
    used = []

    def at(concept, date=bs_date):
        f = co.instants(concept).get(date)
        if f:
            used.append(_slim(f))
        return f["val"] if f else None

    def first(*cs):
        for c in cs:
            v = at(c)
            if v is not None:
                return v
        return None

    debt = at("DebtLongtermAndShorttermCombinedAmount")
    if debt is None:
        lt = at("LongTermDebt")
        short = (at("ShortTermBorrowings") or 0) + (at("CommercialPaper") or 0)
        if lt is not None:
            debt = lt + short
        else:
            non = first("LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations")
            cur = first("LongTermDebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent", "DebtCurrent")
            if non is not None:
                debt = non + (cur or 0) + short
            else:
                notes = at("LongTermNotesAndLoans")         # ORCL tags its bonds as notes
                if notes is not None:
                    debt = notes + (at("NotesPayableCurrent") or 0) + short
    if debt is not None:
        # finance leases are debt (Damodaran; SPEC R5 takes EV from relative.point, whose
        # debt carries them): MSFT's are $63B beside $40B of bonds
        fin = at("FinanceLeaseLiability")
        if fin is None:
            fn, fc = at("FinanceLeaseLiabilityNoncurrent"), at("FinanceLeaseLiabilityCurrent")
            fin = (fn or 0) + (fc or 0) if fn is not None else None
        debt += fin or 0
    leases = None
    lease_dates = sorted((d for d in co.instants("OperatingLeaseLiabilityNoncurrent")
                          if d <= bs_date and (D(bs_date) - D(d)).days <= 370), reverse=True)
    for d in [bs_date] + lease_dates:
        leases = at("OperatingLeaseLiability", d)
        if leases is None:
            non, cur = at("OperatingLeaseLiabilityNoncurrent", d), at("OperatingLeaseLiabilityCurrent", d)
            leases = (non or 0) + (cur or 0) if non is not None else None
        if leases is not None:
            if d != bs_date:
                co.flags.append("operating leases from %s, balance sheet %s" % (d, bs_date))
            break
    cash = None if cash_only else at("CashCashEquivalentsAndShortTermInvestments")
    if cash_only:
        cash = at("CashAndCashEquivalentsAtCarryingValue")
    elif cash is None:
        c = at("CashAndCashEquivalentsAtCarryingValue")
        sti = first("ShortTermInvestments", "AvailableForSaleSecuritiesDebtSecuritiesCurrent",
                    "MarketableSecuritiesCurrent")
        cash = c + (sti or 0) if c is not None else None
    return debt, leases, cash, used


def ebit_fallback(co):
    """{(start, end): fact} of pretax income + interest expense, same period. A period with
    no interest fact takes pretax alone (no debt, or interest netted elsewhere)."""
    pre, intr = co.durations(PRETAX), co.durations(INTEREST)
    out = {}
    for k, f in pre.items():
        i = intr.get(k)
        out[k] = dict(f, val=f["val"] + (i["val"] if i else 0), concept=f["concept"] + (" + " + i["concept"] if i else ""))
    return out


def concept_metrics(rev, gp, cost):
    """growth, CAGR, gross margin and TTM revenue on ONE revenue concept's own periods."""
    ann = annuals(rev)
    fy0 = max(ann) if ann else None
    fy1 = fy_near(ann, years_before(fy0, 1)) if fy0 else None
    fy3 = fy_near(ann, years_before(fy0, 3)) if fy0 else None
    v = lambda e: ann[e]["val"] if e else None
    g0 = None
    if fy0:
        k = (ann[fy0]["start"], fy0)
        g0 = gp[k]["val"] if k in gp else (v(fy0) - cost[k]["val"] if k in cost else None)
    t = ttm(rev)
    return {"fy0_end": fy0, "revenue_ttm": t["val"] if t else None,
            "growth_fy": v(fy0) / v(fy1) - 1 if v(fy0) and v(fy1) and v(fy1) > 0 else None,
            "cagr3_fy": _g(v(fy0), v(fy3), 3), "gross_margin_fy": _ratio(g0, v(fy0))}


def company_reference(companyfacts, as_of=None, price=None, symbol=None):
    """Every per-company number a PeerPanel row or a MoatTrend signal needs."""
    co = Company(companyfacts, as_of)
    r = {"symbol": symbol, "as_of": as_of, "taxonomy": co.taxonomy, "currency": co.currency,
         "excluded": None, "flags": co.flags, "used": {}}
    if co.taxonomy != "us-gaap" or co.currency != "USD":
        r["excluded"] = "not us-gaap/USD (%s, %s)" % (co.taxonomy, co.currency)
    tx = co.taxonomy if co.taxonomy in REV else "us-gaap"
    rev = co.durations(REV[tx], pick="latest")
    oi = co.durations(OI[tx])
    newest_rev = max((e for (_s, e) in rev), default=None)
    newest_oi = max((e for (_s, e) in oi), default=None)
    r["ebit_basis"] = "OperatingIncomeLoss"
    if tx == "us-gaap" and newest_rev and (not newest_oi or (D(newest_rev) - D(newest_oi)).days > OI_STALE_DAYS):
        oi = ebit_fallback(co)
        r["ebit_basis"] = "pretax + interest expense"
        co.flags.append("no OperatingIncomeLoss within %d days of %s (newest %s): EBIT = pretax + interest"
                        % (OI_STALE_DAYS, newest_rev, newest_oi))
    ni = co.durations(NI[tx])
    gp = co.durations(GP[tx])
    cost = co.durations(COST[tx])
    rev_a, oi_a = annuals(rev), annuals(oi)

    def used(name, *facts):
        r["used"][name] = [_slim(f) for f in facts if f]

    fy0 = max(rev_a) if rev_a else None
    fy1 = fy_near(rev_a, years_before(fy0, 1)) if fy0 else None
    fy3 = fy_near(rev_a, years_before(fy0, 3)) if fy0 else None
    r.update(fy0_end=fy0, fy1_end=fy1, fy3_end=fy3)
    for name, e in (("fy0", fy0), ("fy1", fy1), ("fy3", fy3)):
        f = rev_a.get(e) if e else None
        r["revenue_" + name] = f["val"] if f else None
        used("revenue_" + name, f)
        o = oi.get((f["start"], f["end"])) if f else None
        r["op_income_" + name] = o["val"] if o else None
        used("op_income_" + name, o)
        g = gp.get((f["start"], f["end"])) if f else None
        c = cost.get((f["start"], f["end"])) if f else None
        if g:
            r["gross_profit_" + name] = g["val"]
            used("gross_profit_" + name, g)
        elif c:
            r["gross_profit_" + name] = f["val"] - c["val"]
            used("gross_profit_" + name, f, c)
        else:
            r["gross_profit_" + name] = None
            if f and name != "fy1":
                newest = max((e2 for (_s, e2) in list(gp) + list(cost)), default=None)
                co.flags.append("gross profit not filed for %s %s (newest gross-profit period %s)"
                                % (name.upper(), e, newest))
        r["gross_margin_" + name] = _ratio(r["gross_profit_" + name], r["revenue_" + name])
        r["op_margin_" + name] = _ratio(r["op_income_" + name], r["revenue_" + name])
    r["gross_margin_fy"] = r["gross_margin_fy0"]
    r["growth_fy"] = (r["revenue_fy0"] / r["revenue_fy1"] - 1
                      if r["revenue_fy0"] and r["revenue_fy1"] and r["revenue_fy1"] > 0 else None)
    r["cagr3_fy"] = ((r["revenue_fy0"] / r["revenue_fy3"]) ** (1 / 3) - 1
                     if r["revenue_fy0"] and r["revenue_fy3"] and r["revenue_fy3"] > 0
                     and r["revenue_fy0"] > 0 else None)
    if fy0 and not fy3:
        co.flags.append("no fiscal year within %d days of 3 years before %s" % (YEAR_TOL_DAYS, fy0))

    r["by_concept"] = {c: concept_metrics(co.durations([c]), gp, cost) for c in REV[tx]
                       if annuals(co.durations([c]))}
    t_rev, t_oi, t_ni = ttm(rev), ttm(oi), ttm(ni)
    r["period_end"] = t_rev["end"] if t_rev else None
    r["ttm_window"] = (t_rev["start"], t_rev["end"]) if t_rev and t_rev["val"] is not None else None
    for name, t in (("revenue_ttm", t_rev), ("op_income_ttm", t_oi), ("net_income_ttm", t_ni)):
        ok = t and t["val"] is not None and t_rev and (t["start"], t["end"]) == (t_rev["start"], t_rev["end"])
        r[name] = t["val"] if ok else None
        r["used"][name] = [_slim(f) for f in t["parts"]] if ok else []
        if not ok:
            co.flags.append("%s: no TTM on the revenue window (%s)" % (name, t and t.get("basis")))
    r["ttm_basis"] = t_rev.get("basis") if t_rev else None
    r["ttm_quarters"] = t_rev.get("quarters") if t_rev else None
    r["op_margin_ttm"] = _ratio(r["op_income_ttm"], r["revenue_ttm"])

    r["frames"] = {}
    for e, f in rev_a.items():
        r["frames"][str(frame_cy(f["start"], f["end"]))] = {"fy_end": e, "revenue": f["val"],
                                                            "sec_tag": f.get("frame")}

    sh = co.durations(SHARES, unit="shares")
    if sh:
        newest = max(e for (_s, e) in sh)
        k = min((k for k in sh if k[1] == newest), key=lambda k: span_days(*k))
        r["shares_diluted"] = sh[k]["val"]
        used("shares_diluted", sh[k])
    else:
        r["shares_diluted"] = None
    cash_dates = set(co.instants("CashAndCashEquivalentsAtCarryingValue"))
    bs = max((d for d in cash_dates if not r["period_end"] or d <= r["period_end"]), default=None)
    debt = leases = cash = None
    if bs:
        debt, leases, cash, bused = _balance(co, bs, cash_only=str(symbol or "").upper() in MANAGED_CARE)
        r["used"]["balance_sheet"] = bused
        mi = co.instants("MinorityInterest").get(bs)
    r.update(balance_date=bs, total_debt=debt, operating_leases=leases, cash=cash,
             minority_interest=(mi["val"] if bs and mi else 0) if bs else None)
    if bs and debt is None:
        co.flags.append("no debt concept of the reference ladder at %s: EV not computed" % bs)
    r["net_debt_and_leases"] = ((debt or 0) + (leases or 0) - cash + (r["minority_interest"] or 0)
                                if cash is not None and debt is not None else None)
    r.update(ev_sales=None, ev_ebit=None, pe=None, market_cap=None, ev=None)
    if price and r["shares_diluted"] and r["net_debt_and_leases"] is not None:
        r["market_cap"] = price * r["shares_diluted"]
        r["ev"] = r["market_cap"] + r["net_debt_and_leases"]
        r["ev_sales"] = _ratio(r["ev"], r["revenue_ttm"])
        r["ev_ebit"] = _ratio(r["ev"], r["op_income_ttm"])
        r["pe"] = _ratio(r["market_cap"], r["net_income_ttm"])

    # What each headline would be if two clocks were mixed (E1) or a series were read
    # by list position instead of by date (E2, E7). compare.py names the mistake.
    ordered = [rev_a[e]["val"] for e in sorted(rev_a)]
    r["wrong"] = {
        "growth_fy": {"TTM revenue / FY-1 revenue": _g(r["revenue_ttm"], r["revenue_fy1"], 1),
                      "TTM revenue / FY0 revenue": _g(r["revenue_ttm"], r["revenue_fy0"], 1)},
        "cagr3_fy": {"TTM revenue over FY3": _g(r["revenue_ttm"], r["revenue_fy3"], 3),
                     "FY0 over the 3rd list item, newest first (FY2 by position)":
                         _g(ordered[-1], ordered[-3], 3) if len(ordered) >= 3 else None,
                     "FY0 over FY4": _g(ordered[-1], ordered[-5], 3) if len(ordered) >= 5 else None,
                     "oldest year read as newest": _g(ordered[0], ordered[3], 3) if len(ordered) >= 4 else None},
        "gross_margin_fy": {"FY gross profit / TTM revenue": _ratio(r["gross_profit_fy0"], r["revenue_ttm"])},
        "op_margin_ttm": {"TTM operating income / FY0 revenue": _ratio(r["op_income_ttm"], r["revenue_fy0"]),
                          "FY0 operating income / TTM revenue": _ratio(r["op_income_fy0"], r["revenue_ttm"]),
                          "FY0 operating margin": r["op_margin_fy0"]},
    }
    return r


def _g(a, b, n):
    return (a / b) ** (1.0 / n) - 1 if a and b and a > 0 and b > 0 else None


# ---- panel-level numbers -------------------------------------------------------------

def quantile(xs, q):
    """Linear interpolation between order statistics (numpy's default)."""
    xs = sorted(xs)
    if not xs:
        return None
    pos = (len(xs) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def stats(values):
    xs = [v for v in values if isinstance(v, (int, float))]
    if not xs:
        return {"n": 0, "max": None, "p75": None, "median": None, "p25": None, "min": None}
    return {"n": len(xs), "max": max(xs), "p75": quantile(xs, 0.75),
            "median": statistics.median(xs), "p25": quantile(xs, 0.25), "min": min(xs)}


def iqr_outlier(v, st, k=1.5):
    if v is None or st["n"] < 4:
        return False
    iqr = st["p75"] - st["p25"]
    return v < st["p25"] - k * iqr or v > st["p75"] + k * iqr


def table_reference(rows):
    """stats, target_vs_median and outliers from row dicts. Stats cover every row with a
    value, the target included (SPEC does not say; compare.py also tries peers only)."""
    out = {"stats": {}, "target_vs_median": {}, "stats_peers_only": {}, "target_vs_median_peers_only": {}}
    target = next((x for x in rows if x.get("is_target")), None)
    for m in METRICS:
        for key, sub in (("", rows), ("_peers_only", [x for x in rows if not x.get("is_target")])):
            st = stats([x.get(m) for x in sub])
            out["stats" + key][m] = st
            tv = target.get(m) if target else None
            out["target_vs_median" + key][m] = (tv - st["median"]
                                                if tv is not None and st["median"] is not None else None)
    return out


def share_reference(member_revenue_by_year, target_key, n_members):
    """industry_share numbers from {CY: {member: revenue}}. Shares are fractions, HHI is
    the sum of squared fractional shares (0..1)."""
    out = {"target_share": {}, "top5_share": {}, "hhi": {}, "n_with_revenue": {}}
    for cy, revs in member_revenue_by_year.items():
        pos = {k: v for k, v in revs.items() if isinstance(v, (int, float)) and v > 0}
        total = sum(pos.values())
        out["n_with_revenue"][cy] = len(pos)
        covered = n_members and len(pos) / n_members >= 0.70
        if not total or not covered:
            out["target_share"][cy] = out["top5_share"][cy] = out["hhi"][cy] = None
            continue
        out["target_share"][cy] = pos.get(target_key, 0) / total if target_key in pos else None
        out["top5_share"][cy] = sum(sorted(pos.values(), reverse=True)[:5]) / total
        out["hhi"][cy] = sum((v / total) ** 2 for v in pos.values())
    out.update(matched_reference(member_revenue_by_year, target_key, out))
    return out


def matched_reference(member_revenue_by_year, target_key, per_year):
    """SPEC R13 (2026-11-17), from its text: share_change_pp, top5_change_pp and S2 on
    the MATCHED set = members with positive revenue in both the newest CY and the CY
    3 years earlier. Null when either year fails its own coverage (its per-year
    top5 is None here) or the matched set is under 70% of the older year's filers;
    share_change_pp also null when the target is not matched."""
    res = {"matched_n": None, "matched_share_then": None, "matched_share_now": None,
           "matched_top5_then": None, "matched_top5_now": None,
           "share_change_pp": None, "top5_change_pp": None}
    years = sorted(member_revenue_by_year, key=int)
    if not years:
        return res
    ny, oy = years[-1], str(int(years[-1]) - 3)
    if oy not in member_revenue_by_year or per_year["top5_share"].get(ny) is None \
            or per_year["top5_share"].get(oy) is None:
        return res
    pos = lambda y: {k: v for k, v in member_revenue_by_year[y].items()
                     if isinstance(v, (int, float)) and v > 0}
    now, then = pos(ny), pos(oy)
    matched = [k for k in then if k in now]
    res["matched_n"] = len(matched)
    if len(matched) < 0.70 * len(then):
        return res
    tot_now, tot_then = sum(now[k] for k in matched), sum(then[k] for k in matched)
    t5 = lambda d, tot: sum(sorted((d[k] for k in matched), reverse=True)[:5]) / tot
    res["matched_top5_then"], res["matched_top5_now"] = t5(then, tot_then), t5(now, tot_now)
    res["top5_change_pp"] = (res["matched_top5_now"] - res["matched_top5_then"]) * 100
    if target_key in matched:
        res["matched_share_then"] = then[target_key] / tot_then
        res["matched_share_now"] = now[target_key] / tot_now
        res["share_change_pp"] = (res["matched_share_now"] - res["matched_share_then"]) * 100
    return res


def moat_reference(target, peers):
    """S3 and S4 from company_reference outputs, as FRACTIONS (SPEC R1, R8, R9). S1 needs
    the app's ROIC and WACC and S2 needs industry_share; compare.py checks those from the
    stored panel instead.
    S3 (R9, R14): the peers with a gross margin at both their own FY0 and FY3 (at least
    3); the target's gap to their median at FY0 minus the same at FY3.
    S4 (R8): (OI FY0 - OI FY3) / (Rev FY0 - Rev FY3) minus OI FY0 / Rev FY0, only when
    revenue grew."""
    out = {}
    both = [(p["gross_margin_fy0"], p["gross_margin_fy3"]) for p in peers
            if not p["excluded"] and p["gross_margin_fy0"] is not None and p["gross_margin_fy3"] is not None]
    t0, t3 = target["gross_margin_fy0"], target["gross_margin_fy3"]
    out["gm_gap_change"] = None
    out["gm_gap_peers"] = len(both)
    if t0 is not None and t3 is not None and len(both) >= 3:
        out["gm_gap_change"] = ((t0 - statistics.median(a for a, _ in both))
                                - (t3 - statistics.median(b for _, b in both)))
    r0, r3, o0, o3 = (target[k] for k in ("revenue_fy0", "revenue_fy3", "op_income_fy0", "op_income_fy3"))
    if None not in (r0, r3, o0, o3) and r3 > 0 and r0 > r3:
        inc, avg = (o0 - o3) / (r0 - r3), o0 / r0
        out["incremental_margin"] = {"incremental": inc, "average": avg, "value": inc - avg}
    else:
        out["incremental_margin"] = None
    out["window_fy"] = (target["fy3_end"], target["fy0_end"])
    return out


def rank3(values):
    """SPEC R14: {key: 1..3} by floor(3i/n) + 1, i the first index of the value in the
    values sorted descending, n the count; {} under 3 values."""
    vals = [v for v in values.values() if isinstance(v, (int, float))]
    if len(vals) < 3:
        return {}
    desc = sorted(vals, reverse=True)
    return {k: 3 * desc.index(v) // len(vals) + 1 for k, v in values.items() if isinstance(v, (int, float))}


def load(path):
    with open(path) as fh:
        return json.load(fh)
