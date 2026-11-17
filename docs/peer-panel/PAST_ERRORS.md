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
| E23 | One concept's FY series with a hole after a concept switch: GOOGL FY2022 filed only under ASC 606, so FY3 was null (live verifier 2026-11-17) | Panel revenue merged across concepts by date, a filler must agree within 2% (test_R2_B_a_revenue_concept_switch, test_R2_B_a_fill_from_a_concept_that_disagrees; mutants M18, M19) |
| E24 | Fiscal years collapsed by calendar year: JNJ FY2022 ends 2023-01-01, EXEL/STAA FY2024 ends 2025-01-03 (live 2026-11-17) | One value per year-end date and a 330-400 day window (test_R2_B_52_53_week_years; mutant M17) |
| E25 | A target-path rule missing on the peer path: ELV as a peer counted claim-reserve securities as cash (live 2026-11-17) | peer_point passes cash_excludes_securities (test_R2_F; mutant M20) |
| E26 | Share group filtered by domicile dropped the target itself (MELI, Uruguay) and every foreign 10-K filer | Foreign-domiciled members in frames are counted (test_R2_C; mutant M21) |
| E27 | Industry frames on two of the revenue concepts; one missing concept would have failed a whole year | Four concepts, 404 skipped, old cache refreshed (test_R2_A_*; mutants M22, M23) |
| E28 | Target row without period_end for 20-F filers; a missing target row with a generic flag (TSM, NVO, ASML, SPCX) | fiscal_year fallback and the reason in the flag (test_R2_D_*; mutant M24) |
| E29 | Verifier rules drifting from SPEC resolutions (ordinal ranks, S3/S4 in pp, net debt backed out of multiples, largest-concept revenue taking stale or mis-tagged years) | verify/ implements R2, R5, R8, R9, R14 and latest-filing revenue (test_R2_H_*) |

Open, outside this feature (scored EBIT path, not changed in this round; the verifier now reports it):
sec_edgar._pick accepts a stale OperatingIncomeLoss (ASTS: last filed for 2021-03-31, TTM -2.4M)
instead of the pretax + interest fallback, and _derived_ebit takes the first interest concept with
any data however old (LLY: InterestExpense ends 2024-03-31, InterestExpenseNonoperating is current).

