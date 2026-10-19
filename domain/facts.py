"""
FinancialFacts — the single normalized data object the DEEP engine consumes.

Every value carries its source in `provenance` so any number can be traced. A
ticker-level `confidence` (0-100) and `flags` are filled by pipeline/validate.py.
Plain values + a provenance dict keep it JSON-serialisable for the store.
"""
from dataclasses import dataclass, field
from typing import Optional

DEFAULT_TAX_RATE = 0.21


def effective_tax_rate(income_before_tax, tax_expense):
    """tax / pretax income when it lands in 0-60%, else None (a loss year or a one-off)."""
    if income_before_tax and tax_expense is not None and income_before_tax != 0:
        r = tax_expense / income_before_tax
        if 0 <= r <= 0.6:
            return r
    return None


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
    # P2-1: diluted share count by fiscal year (newest first). The REAL dilution the
    # valuation cares about — net of buybacks — rather than the SBC/market-cap proxy,
    # which measures gross grants and moves with the share PRICE. A share count, so it
    # is deliberately NOT in normalize._MONEY: never FX-convert it.
    shares_diluted_annuals: list = field(default_factory=list)
    income_before_tax: Optional[float] = None
    tax_expense: Optional[float] = None

    # balance
    total_debt: Optional[float] = None
    cash: Optional[float] = None
    equity: Optional[float] = None
    operating_leases: Optional[float] = None    # P1-5: lease liability (SEC ASC 842) — treated as debt
    operating_leases_prior: Optional[float] = None  # REV-4: prior-year lease liability (like-for-like IC trend)

    # cash flow
    capex: Optional[float] = None
    dep_amort: Optional[float] = None
    sbc: Optional[float] = None

    # --- v8.2 extra fundamentals (SEC-sourced) -------------------------------
    cfo: Optional[float] = None                 # cash flow from operations (TTM) — earnings quality
    total_assets: Optional[float] = None        # accruals-ratio denominator
    receivables: Optional[float] = None         # AR vs revenue (channel-stuffing check)
    receivables_prior: Optional[float] = None   # REV-19: prior AR — makes that check computable
    # REV-18: working-capital components -> dWC leg of Damodaran's reinvestment
    inventory: Optional[float] = None
    inventory_prior: Optional[float] = None
    accounts_payable: Optional[float] = None
    accounts_payable_prior: Optional[float] = None
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
    impairment_ttm: Optional[float] = None      # asset/goodwill impairment charges (TTM)
    restructuring_ttm: Optional[float] = None   # restructuring charges (TTM)
    acquired_iprd_ttm: Optional[float] = None   # acquired in-process R&D expensed (TTM)
    operating_income_gaap: Optional[float] = None   # reported EBIT before the add-back
    deferred_revenue: Optional[float] = None    # contract liability — billings/leading signal
    deferred_revenue_prior: Optional[float] = None
    # --- 🟢 round 2: consensus path + peers (FMP) ----------------------------
    fwd_growth_near: Optional[float] = None      # next-FY consensus revenue growth
    fwd_growth_far: Optional[float] = None       # final-FY consensus revenue growth (fade)
    n_analysts: Optional[int] = None             # consensus breadth (reliability gate)
    fwd_growth_y1: Optional[float] = None        # next-FY consensus revenue vs last actual FY
    # consensus revenue growth year by year from the last actual FY: [[growth, analysts], ...]
    rev_growth_path: list = field(default_factory=list)
    n_analysts_y2: Optional[int] = None          # coverage of the second estimate year
    peer_median_growth: Optional[float] = None   # median revenue growth of the sector cohort
    terminal_roic_sector: Optional[float] = None  # Damodaran industry ROIC — perpetuity ceiling
    # Damodaran industry unlevered beta (corrected for cash) and the industry it came
    # from — the engine re-levers it at the firm's market D/E (bottom-up beta).
    beta_unlevered_industry: Optional[float] = None
    beta_industry: Optional[str] = None
    # Where the company sits, for every industry- and country-level input. `industry`
    # and `country` are the FMP profile's own labels; `damodaran_industry` is the
    # resolved Damodaran industry and `industry_basis` how it was resolved
    # (override / industry / keyword / sector, see sources/industries.py).
    industry: Optional[str] = None
    country: Optional[str] = None
    damodaran_industry: Optional[str] = None
    industry_basis: Optional[str] = None
    # Industry operating margin and sales-to-capital (Damodaran datasets): the young
    # DCF's target margin and the FCFF DCF's reinvestment band for any new name.
    industry_margin: Optional[float] = None
    industry_sales_to_capital: Optional[float] = None
    # Country risk premium over the US, weighted by where revenue is earned, and how
    # the weights were set (revenue-mix override, or the domicile alone).
    crp: Optional[float] = None
    crp_basis: Optional[str] = None
    # "us-gaap" or "ifrs" (SEC companyfacts namespace). Under US GAAP the whole
    # operating-lease cost sits in EBIT; under IFRS 16 its interest is below EBIT.
    accounting_standard: Optional[str] = None
    # Market cap as reported for the US-LISTED security. Quoted in the same unit as
    # `price`, so for an ADR it already embeds the depositary ratio — which is why it
    # beats price x SEC-share-count for foreign filers (TSM files 25.9B ORDINARY
    # shares against a price per ADR worth 5 of them). See pipeline/normalize.py.
    market_cap: Optional[float] = None
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

    # --- T5: dated annual series for the 5-year trend strip ------------------
    # Each is [[FY_end, value], …] newest first. DATED because two SEC tags do not
    # necessarily cover the same fiscal years — a filer can report GrossProfit for 18
    # years and revenue for 10 — so a margin built by zipping bare lists on INDEX would
    # divide one year's profit by another year's revenue. domain/trend.py aligns on the
    # date and drops years where either side is missing. All are money and DO get
    # FX-converted (pipeline/normalize._MONEY_SERIES_DATED).
    revenue_annuals_dated: list = field(default_factory=list)
    operating_income_annuals_dated: list = field(default_factory=list)
    cfo_annuals_dated: list = field(default_factory=list)
    # 2026-08-17: per-year SBC, so free_cash_flow can deduct the expense CFO adds back
    sbc_annuals_dated: list = field(default_factory=list)
    capex_annuals_dated: list = field(default_factory=list)
    gross_profit_annuals_dated: list = field(default_factory=list)
    cost_of_revenue_annuals_dated: list = field(default_factory=list)
    # M&A cash per fiscal year: tells organic growth from bought growth (CELH +85%)
    acquisitions_annuals_dated: list = field(default_factory=list)
    # one-time charges inside EBIT (pipeline/one_time.py decides which are added back)
    restructuring_annuals_dated: list = field(default_factory=list)
    acquired_iprd_annuals_dated: list = field(default_factory=list)
    # {FY_end: {equity, cash, debt, lt_source}} at the income-statement year-ends —
    # invested capital per year, so ROIC can be a SERIES rather than one snapshot.
    # A dict, not a list: FX conversion handles it separately (see normalize).
    ic_components_dated: dict = field(default_factory=dict)

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
    # L4 (2026-08-10): False when the filed per-share earnings and the quoted price are
    # not in the same currency x share unit. TSM files in TWD while the ADR is quoted in
    # USD, and `currency` reported USD, so nothing converted: EPS came out ~35x too
    # large and every PER-SHARE fair value inherited it. Gating one consumer at a time
    # does not work — the forward-EPS gate closed the PEG path and the Future Value
    # Projection, which builds its own EPS from net_income/shares, produced $5,881
    # against a $421 price instead. This flag is read once, where per-share value is
    # created, so a new valuation method cannot reopen the hole by accident.
    per_share_unit_ok: bool = True
    flags: list = field(default_factory=list)
    forward_eps_raw: Optional[float] = None            # consensus before plausibility fix

    def set(self, name, value, source):
        """Set a field and record where it came from."""
        if value is not None:
            setattr(self, name, value)
            self.provenance[name] = source

    @property
    def tax_rate(self):
        r = effective_tax_rate(self.income_before_tax, self.tax_expense)
        return DEFAULT_TAX_RATE if r is None else r

    def to_dict(self):
        d = {k: v for k, v in self.__dict__.items()}
        d["tax_rate"] = self.tax_rate
        return d
