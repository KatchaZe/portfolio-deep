# Past errors this feature must not repeat

Each line names the shipped defect, where it was recorded, and the guard that must exist
for the peer panel. A guard counts only if a mutation test has shown it going red.

| # | Past defect (record) | Guard required here |
|---|---|---|
| E1 | TTM revenue divided by a fiscal-year revenue, called 1-year growth (P3-1, 2026-08-08) | Every new field gets a clock in domain/contracts.py; growth/CAGR/GM from FY only |
| E2 | Two SEC series zipped by index (T5 trap 1) | FY0/FY3 and frames years joined by date |
| E3 | Window reached across a filing gap (AVGO, A3/A4) | Every windowed series goes through trend._window |
| E4 | Stale row under a "latest" heading (ORCL GM 2017-18, T5 trap 4) | Row whose newest year lags is dropped with a flag |
| E5 | Missing read as normal; renormalising over missing pillars (P3-2, REV-8) | A missing signal is null, never 0; label null under 2 signals |
| E6 | One-sided threshold; ratio blind to a negative denominator (B5, PFE fade, AXON/MELI) | Tests on both sides of every threshold; share change with zero/negative base |
| E7 | Newest-first list read as oldest-first (Yahoo surprises) | Sort by date before any [-1]/[0] |
| E8 | Currency of one source applied to another (TSM L5); 20-F filers | Non-USD or non-us-gaap peers excluded with reason |
| E9 | SEC companyfacts missing a filing (HUM/F/CTSH 2026-07) | sec_gap completion kept on the peer path |
| E10 | Filter after cap dropped the 10-Q (CTSH Form 4s) | Filter before cap in any new list |
| E11 | Peer list spanning industries (FMP: HIMS vs cosmetics) | Share group = Damodaran industry members only |
| E12 | Peer EBIT not normalised (GILD IPR&D) | Peers keep the same one_time add-back |
| E13 | Silent failure returned with no flag (submissions feed) | Every early return appends a flag string |
| E14 | Partial value used as whole (CFO without capex; AP without AR) | A ratio needs both legs from the same period |
| E15 | Stored facts lack a new field until refresh | Panel absent with a flag, not an error, on old facts |
| E16 | Blanket exception for the policed pair (contracts ALLOWED) | No new global ALLOWED entries |
| E17 | Print-only check rots (test_review3 residual) | Assertions only |
| E18 | Display feature moving a score (REV-23 -> P2-5 routing shift) | Invariant PP1 + replay baseline unchanged |
| E19 | Test writing to real storage (quickpatch_corr to Drive) | New suites stub store_sync and run under cut_network |
| E20 | git write commands from the mounted sandbox left index.lock | Work in a clone; deliver a bundle |
| E21 | Fiscal-year to calendar-year mapping assumed | Store each filer's fy end and the frames CY it landed in |
| E22 | Look-ahead: using a fact filed after the as-of date | Point-in-time backtest asserts filed <= as_of |
