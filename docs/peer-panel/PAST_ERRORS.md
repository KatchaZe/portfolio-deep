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
| E30 | A proxy statement's fact replaced the 10-K's: companyfacts keeps DEF 14A pay-versus-performance net income (sign flipped for INGN, in thousands for AVAH and CHE) and latest-filed won (S1, 2026-11-21) | sec_edgar.PERIODIC_FORMS (6-K only for 20-F/40-F filers) with a DATA[info] note naming the fields; test_S1_*; tests/audit2.py A11 spies every fact extract reads, with a mutation self-test |
| E31 | A concept the filer stopped tagging still won the pick: OperatingIncomeLoss last filed 2021 (ASTS), 2015 (KLAC, JNJ), 2022 (EHC, BIIB), 2020 (ACHC) (S2) | _Clock: a concept more than 200 days behind the newest revenue period, and not on the latest fiscal year end, is not current; flag names it; test_S2_* |
| E32 | EBIT that is not GAAP + the stored add-back on the verifier (ACHC, BIIB, JNJ, DDS, EHC, AIR) had no cause of its own: each is E31 or E33 (S3) | test_S3_dds_*; tools/sec_cache_xcheck.py op_margin rows |
| E33 | The EBIT fallback added the first interest concept with any data, InterestExpense, which most filers stopped tagging in 2024 (LLY, KLAC, AIR, DDS) (S4) | newest current interest concept for the TTM, older names fill history only; test_S4_* |
| E34 | A lease total filed on another date beat this balance sheet's legs: BJ's 93.6M footnote total, DG/CPNG/EBAY/DLTR 10-K totals nine months old (S5) | sec_edgar._leases reads one complete date (total, or noncurrent + current on it); test_S5_* |
| E35 | Verifier defects that read as app defects: balance sheet dated by a cash concept the filer stopped tagging (TGT 2017, BIIB 2019, GILD 2022), DebtSecuritiesCurrent not counted (NVDA), revenue concept switched per year (OPCH cagr3, false mixing alarm), four quarters never re-filed after a restatement (INGN), proxy-statement facts read | verify/reference.py rules written apart from the app; tests Reference.* in tests/test_sec_extract_s.py |

The stale OperatingIncomeLoss and interest concepts recorded here as open on 2026-11-17 are
E31 and E33. Still open (not changed in this round): ACHC tags interest only as
InterestIncomeExpenseNet, outside INT_EXP, so its fallback EBIT is pretax alone; LLY files
InterestExpenseNonoperating only in the 10-K, so its TTM EBIT adds the FY2025 interest;
the app's and the reference's debt ladders disagree on 36 of 183 cached filers; debt and
cash come from different balance-sheet dates for AHCO, COCO, GM, GRDN, KTOS (one concept
tagged only annually).
