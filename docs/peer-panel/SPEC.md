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

## Resolutions 2026-11-22 (same business; rules A, B, C approved by the user)
Damodaran industries mix business models, so a gross-margin (GM) comparison across them can
be meaningless. Measured on the live store 2026-11-22: UNH (SIC 6324) GM 88.7% against
insurer peers CI 21.8%, ELV 89.4%, CNC 7.3%, MOH 13.1%, and the old UNH intangible hint read
"76pp above the peer median" against distributors (MCK 5122). Healthcare Support Services
also holds distributors 5122, pharmacies 5912, labs 8071 and services 8090.
R25 SIC source and financial firms. One source for the target, every peer and the sub-group:
the "SIC Code" column of Damodaran's company list (`damodaran_indname.json`, the snapshot
that already sets industry membership; peer_panel.SIC_SOURCE = "damodaran_indname").
Reasons: it covers 4,779 of 4,784 US-listed rows offline, so the sub-group (R28) needs no
request per member; it is the same vintage as membership; the SEC submissions SIC carried
on stored peer points (`sic`) disagrees with it on 61 of 187 points (Damodaran rounds to
7370, 8000, 5960, 3840 where SEC has 7372, 5912, 7389, 3841), so mixing the two would match
or split firms by source. The point's `sic` stays for the relative block's SIC check and is
not read by the panel. Parsing (`sic4`): "6324", 6324, 6324.0, "06324" and " 6324 " are
"6324"; "100" is "0100"; None, "", "0", "0000", non-numeric and > 9999 are no code, and no
code never matches anything (a skip with a reason). A target Damodaran does not list in the
US (the ADRs ASML, NVO, TSM; SPCX) takes its code from his non-US rows when R29 finds a
unique match, else has no code: its GM comparison and sub-group are skipped.
Financial: SIC 6000-6799 (banks, credit, brokers, insurers, agents, REITs). A financial
target gets no GM number in the table (every row "—"), no GM median or rank, no S3 and no
GM-based hint (intangible, cost_advantage); a financial peer row of any target shows "—"
too. Reason (Thai, peer_panel.GM_NA_TH): "gross margin ไม่ใช่ตัวชี้วัดของธุรกิจนี้
(ธนาคาร/ประกัน/การเงิน SIC 6000–6799)". Insurers tag cost of revenue inconsistently (the
figures above), so GM is not a valid insurer metric.
R26 GM comparators (`peer_panel.gm_scope`). Every GM comparison (table median and
target_vs_median, the GM outlier fence, S3, the intangible and cost_advantage hints) uses
the peers whose 4-digit SIC equals the target's AND that have GM at every leg the
comparison reads (FY0 for the table and hints; FY0 and FY3 for S3), at least 3. Fewer than
3: the same 3-digit SIC, at least 3. Otherwise skipped with a Thai reason naming both
counts. Stored as `table.gm_compare` and `signals[S3].comparators`: {sic_source, sic,
applies, level 4|3|null, symbols, n, n4, n3, legs, reason}; each row carries `sic`,
`same_business`, `gm_comparator`, `gm_na`. Operating margin, growth, rank3 and the cost
hint's operating-margin half stay on the full peer set; the UI labels each set.
R27 Votes. moat_trend.votes = {possible, cast, not_applicable}: possible is 4 minus the
signals that do not apply to this business (S3 for a financial target), cast the signals
measured. The label rule is unchanged (sum of the cast votes >= +2 / <= -2, under 2 cast
no label); the tab shows "x จาก y" so a skipped S3 cannot pass for a stable one.
R28 Same-business sub-group (`industry_share.sub_group`, `peer_panel.sub_group`): the
members of the target's Damodaran industry (the full membership industry_share uses, R16
foreign rule included) with the target's 4-digit SIC. Share, top-5, HHI, the matched set,
coverage and calendar years follow R10, R13 and R20 exactly (it is industry_share on that
member list). `members`: one row per filer (share classes together) with its newest-CY
revenue and sub-group share (null when the year fails coverage) and `in_panel`. S2 uses
the sub-group when its matched_n >= 5 and it has the target's matched share
(`basis: "sub_group"`), else the full industry (`basis: "industry"`, `basis_reason` says
why). The scale hint's trend follows the full industry's own share change, the set its share
is measured on. The toggle "เฉพาะธุรกิจเดียวกัน (N ราย)" / "ทั้งกลุ่ม Damodaran" sits in the
share box heading (build 2026-11-22d) and drives the share charts and the panel rows together;
it starts on the same business when a sub-group exists. The share box draws each basis from
its `chart` block: a dumbbell (top 8 by newest-CY share, the target, "อื่น ๆ") over the matched
set, a 100% stacked area (the target, the newest CY's top 5 for every year, the rest), the
Top-5/HHI line, and every member with its yearly shares behind "ดูตารางตัวเลข" (CY frames
revenue, never in the TTM column: R1 clocks). Peer bands are hatched by rank (horizontal,
vertical, dots, diagonal, cross-hatch; others solid; the target solid accent) and the same
swatch marks the company in the legend, the dumbbell and the table. Display only: no new
request and no change to peer selection.
Membership is Damodaran's, not edited by hand. UNH's sub-group is ALHC, CI, CLOV, CNC, ELV,
HQY, HUM, MOH, UNH. HQY (HealthEquity) is an HSA administrator that Damodaran classifies 6324;
CVS (Aetna's parent) is 8000 in Damodaran's list (5912 in SEC submissions), so it is outside.

## Resolutions 2026-11-22b (fix round D1-D9; the user chose Damodaran's global list for ADRs)
R29 Home-listed targets. indname.xls ("By industry") lists every firm once, an ADR under its
home ticker (ENXTAM:ASML, CPSE:NOVO B, TWSE:2330). Its non-US-exchange rows go to their own
cache, damodaran_global_sic.json ([name, exchange:ticker, country, SIC, industry]; 43,359 rows,
43,287 with a SIC in the 2026 vintage), written by the same download as the US table and
never read for membership or industry_share. A target absent from the US rows gets its SIC
there by an automatic match (domain/global_sic.resolve): the profile's company name,
normalised (case, "&", parentheses, "." and "/" joined so N.V. = NV and A/S = AS, ADR words
dropped anywhere, legal-form words dropped from the end), EQUAL to exactly one row's
normalised name, whose country equals the profile country (ISO-2 mapped to Damodaran's
country name). Two hits, a country mismatch, no hit or no SIC is a skip with a Thai reason.
No substring, prefix or ticker matching: "Taiwan Semiconductor Co., Ltd." (TPEX:5425, also
3674, Taiwan) must not match TSMC, and the ticker text "BASML" must not match ASML. No
hand-written ADR map. The code is the target's only: its sub-group is still the US-filer
members of its Damodaran industry with that SIC (R28), so the target itself is not a member
(it files 20-F, outside SEC frames). panel.target_sic = {code, source, ticker, reason}, source
"damodaran_indname" or "Damodaran global: <exchange:ticker>", shown in the tab.
R30 Share-chart data (engine only, no UI yet): industry_share.chart and sub_group.chart =
{years, window [CY-3, CY], members[{cik, symbols, share [per CY, on that year's covered
filers, null when the year failed R20 or no revenue], revenue_now, matched (in the S2
window's R13 matched set; null when either year failed coverage), share_then, share_now,
change_pp (matched-set shares; null under the 70% matched rule)}], target_rank {rank by
newest-CY revenue among the n_members filers, null without revenue; of; cy}}. Top-5 and HHI
per year stay in top5_share / hhi.
R31 Display fixes. S2's basis_reason names the first rule that failed with its counts
(coverage of a year, the matched set, SUB_MIN_MATCHED). The sub-group's flags are panel
flags and show in its box. table.sub_stats holds the same-business view's medians (peer
rows with the target's SIC, at least 3 values, else null). Every count of filers says what
it counts (members vs those with revenue in both CYs). GM is hidden fail-closed in the
renderer: gm_compare.applies false, or any SIC the row knows (panel, sub-group, target_sic,
else the profile's SEC SIC, used only to hide) in 6000-6799, or a pre-R25 panel with no known
SIC; the drawer's 5-year strip drops its Gross margin row for a financial SIC. A financial
target shows one reason ("หุ้นนี้เป็นธุรกิจการเงิน/ประกัน จึงไม่เทียบ GM"); a financial row of
a non-financial target shows its own reason, visibly. Stored reasons stay English and render
in Thai through card_peer.js PP_TH (tests/test_peer_panel_th.py fails on an unmapped one).

## Resolutions 2026-11-22c (share-data fix; independent share audit D1-D4)
R32 Share-class tickers. A member resolves by its ticker as written (pipeline.cik.resolve_cik)
first. A dotted Damodaran ticker (BRK.A) is then tried as SEC spells classes: BASE-CLASS,
BASECLASS, every BASE-* ticker, and the bare BASE. All spellings found must give ONE CIK;
two CIKs is a skip with a Thai reason. A CIK found only through the bare BASE also needs
SEC's title to equal the Damodaran name after normalisation (QVCG.A -> QVCG). Share classes
are one filer: industry_share counts CIKs, never tickers.
R33 Name fallback. A member still without a CIK is matched by its Damodaran company name
(damodaran_us_names.json, from the same indname.xls download) to the SEC frames entity names
of the panel years: domain/member_cik.sec_normalise (global_sic.normalise, R29, after SEC's
"/DE" state suffix) must be EQUAL for exactly one CIK, and that CIK must have revenue in a
panel year. No prefix, substring or fuzzy match and no hand-written map. Anything else is
unresolved with a Thai reason; the flag "หา CIK ไม่ได้ N ราย (ไม่นับ): ..." names them. The
relative model's peer sizing (Universe.candidates) still resolves by exact ticker (PP1).
R34 IFRS. ifrs-full Revenue frames in USD count for a filer with no us-gaap revenue value.
IFRS frames in IFRS_OTHER_CURRENCIES are read only to name such members ("รายงาน IFRS สกุล X
ไม่นับ N ราย"); they are never converted or counted. Calls per frames year: 4 us-gaap + 1
IFRS USD + 18 currencies, through sec_edgar's throttle (0.15 s), cached 30 days; the panel
reads one year before its first (R36), so a refresh is 6 x 23 = 138 calls a month, up from 20.
A failed IFRS call costs the IFRS part of that year only (noted).
R35 Revenue concept. Per filer-year, among the us-gaap concepts (ifrs-full only without one):
keep the concepts whose frame value comes from the newest accession year (the filing the
company still reports that year in); drop a value 1000x or 1e6x another kept value (note
unit_1000x); take Revenues, else RevenueFromContract excl. tax, incl. tax, SalesRevenueNet;
Revenues gives way to a contract-revenue concept of the same filing that exceeds it by more
than 10% (a component line: BLK, GIS, BNSF; note revenues_component). A pick other than
Revenues while Revenues was filed only in an older filing notes revenues_older_filing. Never
the largest across filings. Measured on the cached frames: NVDA, ABT, MDT, the nine SIC 6324
insurers (total revenue incl. investment income) and MELI keep every year's value; TMDX CY2021
becomes $30.262M. The legacy largest-concept value stays in the cache as rev for peer sizing.
R36 June years. A fiscal year ending within 7 days of June 30 counts in the calendar year it
ends in, the year SEC gives a June-30 year (MSFT, RMD); frames are rebucketed on load
(frames_revenue.rebucket) and peer_panel.frames_cy applies the same rule before SEC's tag.
SYNA's FY ending 2025-06-28 is CY2025 beside RMD's ending 2025-06-30.
R37 Scope. sub_group.scope_note says the group is Damodaran's SIC group and that same-business
firms filed under another SIC are not counted. The UI shows it; no hand-written exclusions.
R38 Denominator report. industry_share and sub_group store members_unresolved, unresolved
[[ticker, reason]], coverage_incl_unresolved per CY (covered over members plus unresolved),
members_ifrs_other [[currency, n]], revenue_notes {CY: [[cik, note]]} and dropped_weight
(null: no revenue is known for a member without a CIK, and non-USD revenue is not converted).
The 70% coverage rule (R20) is unchanged and still reads coverage.

## Resolution 2026-11-22f (S1 window)
R39 S1 is today's trend. spread_slope reads the newest contiguous ROIC run (E3) and is absent
when that run ends more than one fiscal year (over 400 days) before the newest fiscal year with
operating income, with the flag "S1: ROIC ends FY<a>, <n> fiscal years before the latest FY<b>".
TMDX's invested capital stops at FY2022 (its debt moved from LongTermDebtNoncurrent to
ConvertibleDebtNoncurrent in FY2023 and _ic_components requires the first tag every year),
so S1 read FY2019..FY2022 beside S3 and S4 on FY2022..FY2025. Display only (PP1).

## Resolutions 2026-11-23 (roic-convert-excise)
R40 No-revenue members. A member industry_share counts (it has a CIK) with no positive revenue
in any panel year is listed in no_revenue [[tickers, cik, Thai reason]] and counted in a flag
per reason ("ไม่มีรายได้ใน SEC frames <n> ราย (...): <reason>"). Reasons: a balance sheet with
revenue 0 (R20); no us-gaap revenue and the run could not read IFRS frames (the years named,
from the frames cache's ifrs_note); no revenue under either taxonomy. A non-USD IFRS member is
named by its R34 flag instead. GFS (20-F, IFRS, USD) on an offline cache with no IFRS frames
is the case that had no reason. Display only (PP1).
R41 Net revenue. The industry share counts revenue net of excise for every filer. The frames
fetch also reads us-gaap ExciseAndSalesTaxes per year (evidence only, never sized on; the cache
stores excise_read). When the chosen value minus another revenue value of the same filer-year
equals the filer's excise for that period end (within 1% of the larger), the smaller value is the
revenue: note excise_tags_swapped when the gross sits under ...ExcludingAssessedTax and the net
under ...IncludingAssessedTax (Molson Coors), flagged "แท็ก SEC สลับกัน ใช้ค่ารายได้สุทธิที่ต่ำกว่า
<n> ราย (<tickers>)"; else excise_net. No excise tag: no change (Xylem's two revenue values differ
by 4% with no excise). A cache without excise_read is refreshed, and while it is the only copy
the panel notes that net revenue was not applied. Display only (PP1).
R42 Debt (SEC extract; scored, outside the panel). total_debt, total_debt_prior and every
ic_components_dated year read one balance-sheet date through sec_edgar.debt_on: the combined
total, else noncurrent + (DebtCurrent | current maturities + short-term borrowings), else
LongTermDebt as the total (when the filer's own dates show LongTermDebt = noncurrent + current)
or as the noncurrent line, plus finance leases unless a ...CapitalLeaseObligations concept
already carries them. Each bucket takes the first total filed on the date, else the sum of its
disjoint parts: never a total and its own part, never one concept carried across years, never
a total below one of its parts. An IC year is left out only when no debt concept is filed on
it. A filer with finance leases only keeps total_debt None (Ford's debt is in extension tags).
S1 then reads TMDX's FY2021..FY2025 ROIC run (R39's guard no longer fires for it).

