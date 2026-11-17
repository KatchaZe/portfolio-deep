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
    "share_change_pp": newest - 3 years earlier, on the matched set (R13),
    "top5_change_pp": same rule,
    "matched_n": int, "matched_share_then": float, "matched_share_now": float,
    "matched_top5_then": float, "matched_top5_now": float
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
- S2 share_change: matched_share_now / matched_share_then - 1 (R13), direction by +/- SHARE_REL.
- S3 gm_gap_change: (target GM FY0 - peer median GM FY0) - (same at FY3), direction by +/- GM_GAP_PP.
- S4 incremental_margin: (OI FY0 - OI FY3)/(Rev FY0 - Rev FY3) vs OI/Rev FY0; only when
  revenue grew; direction +1 when incremental > average + INC_PP, -1 when below - INC_PP.
Label: null when fewer than 2 signals; else sum of directions >= +2 improving,
<= -2 eroding, otherwise stable. A missing signal is never counted as 0.
Source hints are worded "consistent with", never as findings.

## Resolutions 2026-11-16 (binding for implementer and verifier)
R1 Units: ratios and shares are fractions; HHI on 0-1; fields ending `_pp` are percentage points (x100).
R2 op_margin_ttm and ev_ebit use the one-time add-back EBIT (same as the relative block); each row also stores `addback` (fraction of revenue) so GAAP is recoverable.
R3 stats exclude the target row; quantiles by linear interpolation (Python statistics.quantiles method="inclusive"); IQR fence 1.5x.
R4 rank3: scale = revenue_ttm, growth = cagr3_fy, margin = op_margin_ttm; terciles over peers plus target; 1 = top third.
R5 pe = market_cap / net_income_ttm, null when net income <= 0; EV as domain/relative.point (mcap + debt + leases - cash + minority interest).
R6 growth_fy = revenue FY0 / FY-1 - 1 (dated join).
R7 S2 value = share_change_pp / (100 x matched_share_then) (R13); null when that share is 0 or missing.
R8 S4 value = incremental margin - average margin FY0 (fraction).
R9 S3 peer median excludes the target; each peer uses its own FY0 and FY3.
R10 Each row stores `fy0_end` and `frames_cy`. CY assignment: SEC's own `frame` tag on the fact when present; else calendar year of (fy end - 180 days). Not "majority of days" (MSFT FY ending 2025-06-30 is CY2025 per SEC tags; ORCL FY ending 2025-05-31 is CY2024).
R11 Restatements: the latest filing with filed <= as_of wins.
R12 period_end = extract()'s latest_period_end, as the relative block already stores.

## Resolutions 2026-11-17 (independent cross-check findings)
R13 Share change on the matched set. target_share, top5_share and hhi per year stay on that
year's own filers (display). share_change_pp, top5_change_pp and S2 use the MATCHED set: the
members with positive frames revenue in BOTH the newest CY and the CY 3 years earlier.
matched_n = size of that set; matched_share_then / matched_share_now = the target's revenue over
the matched set's total in the older / newest year; matched_top5_then / matched_top5_now = the
five largest matched filers over the same totals. share_change_pp = (matched_share_now -
matched_share_then) x 100; top5_change_pp the same on the top5 pair. Null (both changes, and S2
absent) when either year fails its own 70% coverage rule, or when matched_n < 70% of the older
year's filers with revenue (flag names both counts). share_change_pp is also null when the target
is not in the matched set. When the matched set is smaller than either year's own set, a flag
names how many filers were left out. Reason: a filer missing from one year (ORCL's May fiscal
year is not yet in the newest CY) changes the denominator, not the target's position.
R14 rank3 terciles: rank = floor(3i/n) + 1, i the 0-based index of the row's value in the values
of peers plus target sorted descending (ties take the first index), n the count of rows with the
value; none under 3 values. S3 needs >= 3 peers with gross margin at both their FY0 and FY3
(the peer medians at FY0 and FY3 are over that same set); else S3 is absent with a flag.
