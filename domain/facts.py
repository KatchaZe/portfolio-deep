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

    # --- v8.2 extra fundamentals (SEC-sourced) -------------------------------
    cfo: Optional[float] = None                 # cash flow from operations (TTM) — earnings quality
    total_assets: Optional[float] = None        # accruals-ratio denominator
    receivables: Optional[float] = None         # AR vs revenue (channel-stuffing check)
    interest_expense: Optional[float] = None    # → interest coverage → synthetic Kd for true WACC
    rnd_expense: Optional[float] = None          # current-year R&D (TTM)
    rnd_annuals: list = field(default_factory=list)               # R&D series (newest first) — R&D capitalization
    operating_income_annuals: list = field(default_factory=list)  # op-income series (newest first) — margin trend / ΔNOPAT
    # prior fiscal-year balance (instant) — prior invested capital for 5y spread trend
    equity_prior: Optional[float] = None
    total_debt_prior: Optional[float] = None
    cash_prior: Optional[float] = None
    # --- 🟢 round 2: organic growth + billings (SEC) -------------------------
    acquisitions_net: Optional[float] = None    # M&A cash spent (TTM) — organic-growth penalty
    deferred_revenue: Optional[float] = None    # contract liability — billings/leading signal
    deferred_revenue_prior: Optional[float] = None
    # --- 🟢 round 2: consensus path + peers (FMP) ----------------------------
    fwd_growth_near: Optional[float] = None      # next-FY consensus revenue growth
    fwd_growth_far: Optional[float] = None       # final-FY consensus revenue growth (fade)
    n_analysts: Optional[int] = None             # consensus breadth (reliability gate)
    peer_median_growth: Optional[float] = None   # median revenue growth of the sector cohort
    peers: list = field(default_factory=list)    # FMP stock-peers (when fetched)
    # --- 🟢 round 3: own 5y P/E percentile (re-rating, Price adj) -------------
    own_pe_pctile: Optional[float] = None        # 0=cheapest / 1=richest vs own 5y P/E range
    eps_annuals_dated: list = field(default_factory=list)  # [[FY_end, diluted_EPS], …] newest first

    # market / consensus
    beta: Optional[float] = None
    dividend_ps: Optional[float] = None         # last annual dividend/share (FMP lastDiv); 0/None = non-payer
    forward_eps: Optional[float] = None         # NTM adjusted consensus (blended median)
    growth_lt: Optional[float] = None           # decimal, e.g. 0.15
    # --- Phase 2: multi-source forward-EPS blend (dispersion) ----------------
    forward_eps_sources: dict = field(default_factory=dict)  # {source: eps} that fed the blend
    forward_eps_low: Optional[float] = None
    forward_eps_high: Optional[float] = None
    forward_eps_spread_pct: Optional[float] = None           # (high-low)/median*100
    forward_eps_n: int = 0                                    # number of sources blended

    # history for CAGR (newest first): [latest_FY, FY-1, FY-2, FY-3]
    revenue_annuals: list = field(default_factory=list)

    # EPS surprise track record (oldest->newest), each: quarter/eps_actual/
    # eps_estimate/surprise_pct/grade(beat|meet|miss). Display + confidence input.
    earnings_surprises: list = field(default_factory=list)

    # revenue beat/miss (built forward): the current-quarter consensus we snapshot,
    # and the SEC ~90-day actuals used to grade past snapshots. History itself
    # lives in the store (accumulates across refreshes), not here.
    rev_estimate_curq: Optional[dict] = None            # {quarter_end, estimate}
    revenue_quarters: dict = field(default_factory=dict)  # {end_date: actual_revenue}
    # quarterly operating income ({end_date: op_income}) — paired with revenue_quarters
    # to build the operating-margin trend (margin_track). SEC ~90-day periods.
    operating_income_quarters: dict = field(default_factory=dict)
    # quarterly diluted EPS actuals ({end_date: eps}) from SEC — the free, always-
    # available ACTUAL used to grade analyst estimates immediately (surprise_backfill),
    # so the EPS row fills even when Yahoo earningsHistory is blocked.
    eps_quarters: dict = field(default_factory=dict)
    # Phase 3: immediate revenue beat/miss from FMP (revenueActual vs revenueEstimated),
    # a fallback for the build-forward history so the Rev row isn't empty for ~1 year.
    rev_surprises_fmp: list = field(default_factory=list)
    # Immediate EPS beat/miss reconstructed from FMP quarterly estimates x SEC EPS
    # actuals (surprise_backfill) — fallback when reconciled earnings_surprises is empty.
    eps_surprises_backfill: list = field(default_factory=list)

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
