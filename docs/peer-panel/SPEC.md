# Peer panel and moat trend: data contract (2026-11-16)

Display only. Nothing here may change a fair value, a DEEP pillar, the composite,
the recommendation, the DCF length, or the ADD gate. Invariant PP1 enforces this.

## Sources (no new vendor)
- Industry membership: Damodaran `indname.xls` (sources/damodaran_companies.py, cached).
- Industry revenue by year: SEC XBRL frames `Revenues`,
  `RevenueFromContractWithCustomerExcludingAssessedTax`, `SalesRevenueNet` and
  `RevenueFromContractWithCustomerIncludingAssessedTax`, USD, CY{year}, the largest per CIK
  (`load_revenue_frames`; R15). Five calendar years: frames_year(today) and the 4 before.
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
  "source_hints": [{"type": "intangible|scale|efficient_scale|cost_advantage",
                    "strength": "weak|moderate", "evidence": str,
                    "measures": [{"metric", "value", "period"}], "trend": +1|0|-1|null}],
  "watch": [Thai signal name, up to 3],
  "thresholds": "provisional",
  "flags": [str]
}
Signals:
- S1 spread_slope: OLS slope of ROIC - WACC over the contiguous FY window (trend._window),
  pp per year; direction by +/- SPREAD_SLOPE_PP.
- S2 share_change: matched_share_now / matched_share_then - 1 (R13), direction by +/- SHARE_REL.
- S3 gm_gap_change: (target GM FY0 - peer median GM FY0) - (same at FY3), direction by +/- GM_GAP_PP.
- S4 incremental_margin: (OI FY0 - OI FY3)/(Rev FY0 - Rev FY3) vs OI/Rev FY0; only when
  revenue grew at least 10% (R21); direction +1 when incremental > average + INC_PP, -1 when below - INC_PP.
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

## Resolutions 2026-11-17b (first live verifier run, fix round 2)
R15 Frames read the four revenue concepts above; a concept SEC has no frame for in a year (404)
is skipped, not a failed year; a cache written on another concept list is refreshed.
R16 Share group: the US-domiciled members of the Damodaran industry plus the US-listed members
domiciled elsewhere whose CIK is in SEC frames in one of the panel years (a 10-K filer in USD,
such as MELI; a 20-F filer never is). The flag says how many such members were counted.
R17 The panel's fiscal years come from `sec_edgar.panel_fy_series`: one value per year-end DATE
(two year ends in one calendar year both kept), revenue merged across the revenue ladder where a
filler concept agrees with the merged series within 2% on every shared year; the FY window is
contiguous when consecutive ends are 330-400 days apart (52/53-week years). Points and the target
keep `fy_hist`: up to five years of revenue and gross profit, newest first. Scored series are
unchanged.
R18 Rows store the EV legs: `total_debt`, `operating_leases`, `cash`, `minority_interest`,
`market_cap`. A managed-care peer (sources.industries.MANAGED_CARE) counts cash and equivalents
only, as the target does.
R19 The target row's period_end is its newest TTM quarter, else facts.fiscal_year (the TTM end
of a 20-F filer after the quarterly fallback and FX). When the target cannot form a row, the flag
carries relative.point's reason.

## Resolutions 2026-11-21 (display upgrades with the UI tab; thresholds stay provisional)
R20 Coverage denominator. A member reports in a CY when it has a revenue frame that year (any
value, 0 included) OR its CIK is in SEC's year-end balance-sheet frame us-gaap/Assets/USD/
CY{year}Q4I: it filed and booked no revenue (a pre-revenue biotech), so it counts at revenue
0. coverage = reporting members / members with a CIK; `n_covered` per CY stores the count,
`n_with_revenue` stays the positive-revenue count. Revenue 0 adds nothing to any share, top-5
or HHI; the matched set (R13) stays the positive-revenue filers. Assets frames are cached 30
days per year (sec_frames_assets_CY{year}.json); a failed year counts revenue filers only and
is named in the notes. Measured by tools/panel_probe (revenue/covered per CY).
R21 S4 needs revenue FY0 / FY3 - 1 >= 0.10; else absent with the flag "S4: revenue grew X% in
3 years (need ≥10%)". A near-zero revenue delta makes the ratio noise (ABBV 2026-11-20:
+0.2% gave -122.6 pp and a -1 vote). Non-positive revenue at either end: "S4: revenue not
positive at FY0 or FY3".
R22 Two more source hints, worded "consistent with":
  efficient_scale: newest-CY top5_share >= 0.80 AND |share_change_pp| <= 2 AND n_members <= 30
  -> weak; with top5_share >= 0.90 -> moderate. Any input null -> no hint.
  cost_advantage: op_margin_ttm - peer median op_margin_ttm >= 5pp AND gross_margin FY0 - peer
  median FY0 <= 2pp (>= 3 peers with each) -> moderate. Each gap is against its own clock's
  median; no TTM-FY arithmetic.
  UI labels: ขนาดตลาดที่พอดี (efficient scale), ความได้เปรียบด้านต้นทุน (cost advantage).
R23 Every hint carries `measures` (the numbers behind it; the UI writes the Thai sentence, the
English `evidence` stays for the data notes) and `trend`: intangible <- S3 direction, scale <-
S2 direction, efficient_scale <- top5_change_pp beyond +/-2pp, cost_advantage <- change in
the FY operating-margin gap to the peer median FY3 -> FY0 beyond +/-2pp (same >= 3 peers at
both years); null when the source is absent. moat_trend.watch: Thai names of up to 3
present signals nearest a band edge, distance = | |value| - band | / band.
R24 Source hints need excess returns (2026-11-21, lead's decision). A hint says where a moat
could come from, so it needs a moat to explain: moat_trend.build takes `spread_pct`, the current
ROIC - WACC in points that the card's Moat chip shows (key_metrics.spread_pct). Spread > 0 keeps
the hints of R22-R23 unchanged. Spread <= 0, null, NaN or not a number: source_hints is [] and
flags carries "hints: no excess return (ROIC − WACC ≤ 0)". Label, signals and watch are not
touched. The tab shows "ยังไม่มีกำไรส่วนเกิน (ROIC ≤ WACC) จึงไม่นับเป็นแหล่ง moat" in place of
the list. Rows stored before this rule keep their stored hints until the next refresh.
