"""
Engine contract — the STABLE interface between the app and any DEEP version.

Everything downstream (store, API, dashboard, daily action) depends only on:
  * FinancialFacts   (input, defined in domain/facts.py)
  * Valuation        (output, defined here)
  * DeepEngine.evaluate(facts, rf) -> Valuation

A new framework version (e.g. 7.4) just implements DeepEngine and returns a
Valuation. No other module changes. Keep this file backward-compatible: only
ADD optional fields, never remove or repurpose existing ones.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Valuation:
    version: str
    # DEEP sub-scores (0-5) + aggregate
    D: Optional[float] = None
    E_exec: Optional[float] = None
    E_econ: Optional[float] = None
    P: Optional[float] = None
    composite: Optional[float] = None
    stars: str = ""
    recommendation: Optional[str] = None        # BUY / HOLD / Accumulate / SELL ...
    signal: Optional[str] = None                 # BUY / HOLD / SELL (for daily action grid)

    # fair value (per share)
    anchor_method: Optional[str] = None
    anchor_value: Optional[float] = None
    range_low: Optional[float] = None
    range_high: Optional[float] = None
    fv_peg: Optional[float] = None
    fv_fvp: Optional[float] = None

    reverse_dcf: dict = field(default_factory=dict)
    key_metrics: dict = field(default_factory=dict)   # wacc_pct, roic_pct, spread_pct, growth_pct, beta
    verdict: str = ""                                  # short ifa-style summary line
    flags: list = field(default_factory=list)

    def to_dict(self):
        return dict(self.__dict__)


class DeepEngine(ABC):
    """Implement this for each DEEP framework version."""
    version: str = "?"

    @abstractmethod
    def evaluate(self, facts, rf: float = 0.045) -> "Valuation":
        """Pure: map a validated FinancialFacts -> Valuation. No network."""
        raise NotImplementedError
