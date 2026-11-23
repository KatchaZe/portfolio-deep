"""Rebuild the trimmed real-facts fixtures for tests/test_sec_debt.py and audit2 A14.

usage: python3 -B tests/fixtures/sec_debt/build.py ~/work/sec_cache
Each fixture is one cached SEC companyfacts file (downloaded 2026-11-21) cut to the
concepts the extract reads for invested capital and total debt; every fact of those
concepts is kept as filed.
"""
import json
import os
import sys

REV = ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
       "RevenueFromContractWithCustomerIncludingAssessedTax"]
OP = ["OperatingIncomeLoss"]
BS = ["Assets", "StockholdersEquity", "Equity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
      "CashAndCashEquivalentsAtCarryingValue", "CashAndCashEquivalents",
      "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "CashCashEquivalentsAndShortTermInvestments",
      "ShortTermInvestments", "MarketableSecuritiesCurrent", "DebtSecuritiesCurrent", "OtherShortTermInvestments",
      "AvailableForSaleSecuritiesDebtSecuritiesCurrent"]
DEBT = ["DebtLongtermAndShorttermCombinedAmount", "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities",
        "Borrowings", "LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations", "ConvertibleDebtNoncurrent",
        "ConvertibleLongTermNotesPayable", "LongTermNotesAndLoans", "LongTermNotesPayable", "LongTermLoansPayable",
        "SeniorLongTermNotes", "LongTermLineOfCredit", "OtherLongTermDebtNoncurrent", "CommercialPaperNoncurrent",
        "SecuredLongTermDebt", "UnsecuredLongTermDebt", "LongTermDebt", "ConvertibleDebt", "ConvertibleNotesPayable",
        "DebtCurrent", "LongTermDebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent", "ConvertibleDebtCurrent",
        "ConvertibleNotesPayableCurrent", "OtherLongTermDebtCurrent", "SeniorNotesCurrent", "SecuredDebtCurrent",
        "UnsecuredDebtCurrent", "NotesPayableCurrent", "NotesAndLoansPayableCurrent", "ShortTermBorrowings",
        "CommercialPaper", "OtherShortTermBorrowings", "FinanceLeaseLiability", "CapitalLeaseObligations",
        "FinanceLeaseLiabilityNoncurrent", "CapitalLeaseObligationsNoncurrent", "FinanceLeaseLiabilityCurrent",
        "CapitalLeaseObligationsCurrent", "SeniorNotes"]
# read beside the debt lines (E64-E68): the unamortized discount that tells principal from
# carrying value, interest payable that shows untagged debt, and the balance-sheet total
AUX = ["DebtInstrumentUnamortizedDiscountPremiumAndDebtIssuanceCostsNet", "DebtInstrumentUnamortizedDiscount",
       "UnamortizedDebtIssuanceExpense", "DebtInstrumentUnamortizedDiscountPremiumNet", "LiabilitiesAndStockholdersEquity",
       "InterestPayableCurrent", "InterestPayableCurrentAndNoncurrent",
       # E72-E74: debt evidence the tree does not read as a value, finance-lease interest, and
       # the liabilities side that makes a date a balance sheet
       "DebtInstrumentCarryingAmount", "DebtInstrumentFaceAmount", "LongTermDebtFairValue", "SecuredDebt",
       "UnsecuredDebt", "LinesOfCreditCurrent", "OtherLongTermDebt", "LineOfCredit", "NotesPayable", "LoansPayable",
       "InterestExpenseLesseeAssetsUnderCapitalLease", "FinanceLeaseInterestExpense", "Liabilities"]
TICKERS = ("TMDX", "LLY", "ABBV", "AXON", "BKD", "UTHR", "AVGO", "MELI", "ENSG",
           "HCA", "AAPL", "MSFT", "DIS", "ASML", "F", "VRTX", "NEOG", "TTWO", "INCY", "GKOS",
           "CNK", "BIIB", "REGN", "NVO")
# an IFRS filer keeps its whole ifrs-full namespace (NVO: 253 concepts, 0.7 MB)
WHOLE_NS = ("ifrs-full",)

if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    for t in TICKERS:
        cf = json.load(open(os.path.join(sys.argv[1], "companyfacts_%s.json" % t)))
        facts = {ns: g for ns, g in cf["facts"].items() if ns in WHOLE_NS}
        g = cf["facts"].get("us-gaap")
        if g:
            facts["us-gaap"] = {c: g[c] for c in REV + OP + BS + DEBT + AUX if c in g}
        trimmed = {"cik": cf.get("cik"), "entityName": cf.get("entityName"), "facts": facts}
        with open(os.path.join(here, t + ".json"), "w") as fh:
            json.dump(trimmed, fh, separators=(",", ":"))
        print(t, {ns: len(v) for ns, v in facts.items()})
