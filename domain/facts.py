"""
FinancialFacts — the single normalized data object the DEEP engine consumes.

Every value carries its source in `provenance` so any number can be traced. A
ticker-level `confidence` (0-100) and `flags` are filled by pipeline/validate.py.
Plain values + a provenance dict keep it JSON-serialisable for the store.
"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class FinancialFacts:
    ticker: str
    company: Optional[str] = None
    sector: Optional[str] = None
    currency: str = "USD"
    fiscal_year: Optional[str] = None          # latest annual period end (YYYY-MM-DD)
    as_of: Optional[str] = None                 # when this snapshot was built

    price: Optional[float] = None

    # income (latest annual — FMP normalized, no TTM-summing)
    revenue: Optional[float] = None
    operating_income: Optional[float] = None
    net_income: Optional[float] = None
    eps_gaap: Optional[float] = None
    shares_diluted: Optional[float] = None
    income_before_tax: Optional[float] = None
    tax_expense: Optional[float] = None

    # balance
    total_debt: Optional[float] = None
    cash: Optional[float] = None
    equity: Optional[float] = None

    # cash flow
    capex: Optional[float] = None
    dep_amort: Optional[float] = None
    sbc: Optional[float] = None

    # market / consensus
    beta: Optional[float] = None
    forward_eps: Optional[float] = None         # NTM adjusted consensus
    growth_lt: Optional[float] = None           # decimal, e.g. 0.15

    # history for CAGR (newest first): [latest_FY, FY-1, FY-2, FY-3]
    revenue_annuals: list = field(default_factory=list)

    # quality
    provenance: dict = field(default_factory=dict)   # {field_name: source}
    confidence: int = 0
    confidence_tier: str = ""                          # green / yellow / red
    flags: list = field(default_factory=list)
    forward_eps_raw: Optional[float] = None            # consensus before plausibility fix

    def set(self, name, value, source):
        """Set a field and record where it came from."""
        if value is not None:
            setattr(self, name, value)
            self.provenance[name] = source

    @property
    def tax_rate(self):
        if self.income_before_tax and self.tax_expense is not None and self.income_before_tax != 0:
            r = self.tax_expense / self.income_before_tax
            if 0 <= r <= 0.6:
                return r
        return 0.21

    def to_dict(self):
        d = {k: v for k, v in self.__dict__.items()}
        d["tax_rate"] = self.tax_rate
        return d
