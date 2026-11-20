"""Rebuild the trimmed real-facts fixtures for tests/test_sec_extract_s.py.

usage: python3 -B tests/fixtures/sec_s/build.py ~/work/sec_cache
Each fixture is one cached SEC companyfacts file (downloaded 2026-11-21) cut to the
concepts its test reads; every fact of those concepts is kept as filed.
"""
import json
import os
import sys

REV = ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
       "RevenueFromContractWithCustomerIncludingAssessedTax"]
OP = ["OperatingIncomeLoss"]
PRE = ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
       "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"]
INT = ["InterestExpense", "InterestAndDebtExpense", "InterestExpenseDebt", "InterestExpenseNonoperating"]
LEASE = ["OperatingLeaseLiability", "OperatingLeaseLiabilityNoncurrent", "OperatingLeaseLiabilityCurrent"]
WANT = {
    "INGN": REV + ["NetIncomeLoss"],
    "ASTS": REV + OP + PRE + INT,
    "KLAC": REV + OP + PRE + INT,
    "JNJ": REV + OP + PRE + INT,
    "DDS": REV + OP + PRE + INT,
    "LLY": REV + OP + PRE + INT,
    "BJ": REV + LEASE,
    "DG": REV + LEASE,
    # verifier fixtures (verify/reference.py)
    "OPCH": REV,
    "TGT": REV + ["Assets", "CashAndCashEquivalentsAtCarryingValue",
                  "CashCashEquivalentsAndShortTermInvestments"],
    "NVDA": REV + ["Assets", "CashAndCashEquivalentsAtCarryingValue", "DebtSecuritiesCurrent",
                   "MarketableSecuritiesCurrent", "ShortTermInvestments"],
}

if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    for t, concepts in WANT.items():
        cf = json.load(open(os.path.join(sys.argv[1], "companyfacts_%s.json" % t)))
        g = cf["facts"]["us-gaap"]
        trimmed = {"cik": cf.get("cik"), "entityName": cf.get("entityName"),
                   "facts": {"us-gaap": {c: g[c] for c in concepts if c in g}}}
        with open(os.path.join(here, t + ".json"), "w") as fh:
            json.dump(trimmed, fh, separators=(",", ":"))
        print(t, sorted(trimmed["facts"]["us-gaap"]))
