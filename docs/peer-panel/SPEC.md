# Peer panel and moat trend: data contract (2026-11-16)

Display only. Nothing here may change a fair value, a DEEP pillar, the composite,
the recommendation, the DCF length, or the ADD gate. Invariant PP1 enforces this.

## Sources (no new vendor)
- Industry membership: Damodaran `indname.xls` (sources/damodaran_companies.py, cached).
- Industry revenue by year: SEC XBRL frames `Revenues` and
  `RevenueFromContractWithCustomerExcludingAssessedTax`, USD, CY{year}, the larger per CIK
  (existing `load_revenue_frames`). Five calendar years: frames_year(today) and the 4 before.
- Peer and target fundamentals: SEC companyfacts via the existing peer points
  (pipeline/peers.py), same one-time add-back as the target.
- Prices: existing T-1 close (Yahoo, then Stooq).
- FMP: 0 calls for this feature.

## Clocks (every number carries one)
- TTM: revenue, operating income, net income, EV/Sales, EV/EBIT, P/E.
- FY0 / FY3: each company's own latest fiscal year and the one three years before it,
  joined by DATE (never list position). Gross margin and the 3-year revenue CAGR use FY.
- CY: frames calendar years. A filer's fiscal year is mapped to the calendar year frames
  assign it; the frames year and the as-of date are both stored.
- No arithmetic between a TTM value and an FY value (domain/contracts.py clocks).

## PeerPanel (results[ticker]["peer_panel"])
{
  "as_of": "YYYY-MM-DD", "version": 1,
  "industry": str, "industry_basis": str,
  "table": {
    "rows": [{"symbol", "is_target": bool, "period_end", "fy0_end", "price_date",
              "revenue_ttm", "growth_fy", "cagr3_fy", "gross_margin_fy", "op_margin_ttm",
              "ev_sales", "ev_ebit", "pe", "rank3": {"scale","growth","margin"} (1..3 or null),
              "outlier": [metric names outside the IQR fence]}],
    "stats": {metric: {"n", "max", "p75", "median", "p25", "min"}},
    "target_vs_median": {metric: target - median (pp for ratios, x for multiples)}
  },
  "industry_share": {
    "group": "damodaran_industry_us_sec_filers",
    "years": [CY...oldest->newest],
    "n_members": int, "n_with_revenue": {CY: int},
    "target_share": {CY: float}, "top5_share": {CY: float}, "hhi": {CY: float},
    "share_change_pp": newest - 3 years earlier (null if either missing),
    "top5_change_pp": same rule
  },
  "flags": [str]   # every missing input says why; nothing missing is silent
}
Share is "share of revenue among US SEC filers in the Damodaran industry", never market share.
Coverage below 70% of members with revenue in a year sets a flag and nulls that year's share.

## MoatTrend (results[ticker]["moat_trend"])
{
  "label": "improving" | "stable" | "eroding" | null,
  "n_signals": int,
  "signals": [{"name", "value", "direction": "+1|0|-1", "window": "FYa..FYb or CYa..CYb"}],
  "source_hints": [{"type": "intangible|scale|switching_costs|network",
                    "strength": "weak|moderate", "evidence": str}],
  "thresholds": "provisional",
  "flags": [str]
}
Signals:
- S1 spread_slope: OLS slope of ROIC - WACC over the contiguous FY window (trend._window),
  pp per year; direction by +/- SPREAD_SLOPE_PP.
- S2 share_change: industry_share.share_change_pp relative to the target's share
  (relative change), direction by +/- SHARE_REL.
- S3 gm_gap_change: (target GM FY0 - peer median GM FY0) - (same at FY3), direction by +/- GM_GAP_PP.
- S4 incremental_margin: (OI FY0 - OI FY3)/(Rev FY0 - Rev FY3) vs OI/Rev FY0; only when
  revenue grew; direction +1 when incremental > average + INC_PP, -1 when below - INC_PP.
Label: null when fewer than 2 signals; else sum of directions >= +2 improving,
<= -2 eroding, otherwise stable. A missing signal is never counted as 0.
Source hints are worded "consistent with", never as findings.
