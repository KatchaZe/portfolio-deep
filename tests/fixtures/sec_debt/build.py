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
TICKERS = ("TMDX", "LLY", "ABBV", "AXON", "BKD", "UTHR", "AVGO", "MELI", "ENSG")

if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    for t in TICKERS:
        cf = json.load(open(os.path.join(sys.argv[1], "companyfacts_%s.json" % t)))
        g = cf["facts"]["us-gaap"]
        trimmed = {"cik": cf.get("cik"), "entityName": cf.get("entityName"),
                   "facts": {"us-gaap": {c: g[c] for c in REV + OP + BS + DEBT if c in g}}}
        with open(os.path.join(here, t + ".json"), "w") as fh:
            json.dump(trimmed, fh, separators=(",", ":"))
        print(t, len(trimmed["facts"]["us-gaap"]))
