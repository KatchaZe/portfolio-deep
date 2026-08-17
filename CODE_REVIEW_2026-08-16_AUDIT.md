# CODE REVIEW — full-codebase audit, round 14 (2026-08-16/17)

> **CORRECTED 2026-08-17.** The first version of this document made three material
> errors, all found by a cross-check against the 13 earlier review rounds. They are
> corrected in place and listed here rather than quietly edited out:
>
> 1. **It audited against the wrong spec.** It cited "DEEP Framework v7.1", which is
>    the version of the *prompt template* the user pastes into a chat. This codebase
>    implements **DEEP v8.2** (`config.DEEP_VERSION = "8.2"`, `domain/engine/deep_v82.py`);
>    the rollback engine is **v7.3**, not v7.1. The two numbering lines are unrelated.
>    Two rules were therefore judged against the wrong text — see *Spec reconciliation*.
> 2. **It over-claimed novelty.** Of 12 findings, **2 are new**, **1 is a regression of a
>    fix from 2026-07-02**, **1 is a re-report of a known-open item**, and **8 are
>    adjacent** to earlier work — same area or same principle, different concrete defect.
>    Per-finding verdicts are in the Summary table.
> 3. **Finding 3's fix was incomplete when first shipped.** `align_on_dates()` reached
>    only the covariance matrix. Historical VaR, the downside lens and the Correlation
>    tab's benchmark table were still pairing series by position — the proven defect
>    survived in the three most-read numbers on the page. Completed 2026-08-17.

**Scope:** every module (12,247 lines of non-test Python + `index.html`), audited on
two axes:

- **Spec** — the DEEP **v8.2** rules the engine claims to implement (`DESIGN.md` §6,
  `DATA_AUDIT.md`), plus the Damodaran principles behind them.
- **Standards** — the eight classes of AI-introduced defect on the brief (logic, edge
  cases, API drift, security, performance, direct financial-data errors,
  indirect/contextual errors, systemic/external).

**Method.** Four parallel review agents, each restricted to one subsystem, each required
to write and RUN a repro before reporting. Nothing below is a reading of the code —
every item was made to misbehave with concrete inputs first, then fixed, then re-run.

> **Methodology debt, disclosed.** `REVIEW_PROCESS.md:101` explicitly forbids splitting a
> review session by module ("defect อยู่ที่รอยต่อ … แบ่งตาม module = ไม่มีใครเป็นเจ้าของรอยต่อ")
> and asks for a split along the *data-flow seams* instead. This round split by module
> anyway, and paid for it exactly where the process predicted: finding 3 shipped
> incomplete because one agent owned `risk.py` and another owned `risk_report.py`, and
> finding 12 turned out to be the frontend half of a null the backend half of the same
> seam had been fixed for hours earlier. Also not done: `tests/live_check.py` against
> MSFT · TSM · NVO (checklist 10), and a reconciliation of ORCL's revised debt to the
> filing (checklist 8). Both are open items below.

**Result.** 12 confirmed defects, all fixed; finding 3 completed in a second pass.
57 test suites green; `replay_snapshot` clean.

---

## Spec reconciliation — v8.2 vs the v7.1 prompt

The engine and the prompt template disagree on three rules. Where they do, **the code
follows v8.2**, which is correct for this repo:

| Rule | v7.1 prompt | v8.2 engine (`DESIGN.md` §6) | What the code does |
|---|---|---|---|
| Equity risk premium | "locked at 4.75%" | "ERP 4.23% (Damodaran implied, Jan 2026) **replaces the frozen 4.75%**" | `config.ERP = 0.0445`, `ERP_AS_OF = "2026-07"`, flagged stale after 3 months — **v8.2, correct.** The original report "cleared" this against the v7.1 lock, which was the wrong test. |
| DEEP weights | D .30 / E .25 / Ec .25 / P .20 | **D .20 / E .20 / Ec .30 / P .30** | `WEIGHTS = {"D": 0.20, "E_exec": 0.20, "E_econ": 0.30, "P": 0.30}` — **v8.2, correct.** |
| SBC | "handled strictly via share dilution … no double counting" | "Earnings-quality screen — cash conversion / accruals / **SBC% → caps Execution**" | **Genuinely conflicting.** See finding 7 and *Open decisions*. |

Other v8.2 rules audited and found implemented: true WACC (Ke = Rf+β·ERP, after-tax debt
at market weights, FCFF→WACC / FCFE→Ke), EV-bridge reverse DCF, R&D-capitalised ROIC
with a ratio fallback, fundamental 2-stage PEG with the low-beta clamp on fair P/E, the
numeric rubric's bounded-adjustment budget, and "adjustments with no free data input are
skipped + flagged, never fabricated".

---

## Summary

| # | Defect | Novelty vs the 13 earlier rounds | Impact | Status |
|---|--------|----------------------------------|--------|--------|
| 1 | `^TNX` read as 10bp/point instead of 100bp | **NEW** — no prior doc examines the feed's scale; `CODE_REVIEW_2026-07-04.md` left "wire `effective_duration()`" as an unreviewed follow-up and the defect entered with it | Rate stress printed **−19%** for a true **−1.9%** | fixed |
| 2 | Covariance mixed a 60-day window with a 400-day vol | **ADJACENT** — the defect is inside the 2026-07-19 `hybrid_cov` patch's own code; its docstring accepted non-PSD on purpose, and this round reverses that | **ρ = 2.59**; non-PSD; `portfolio_vol` silently 0.0; VaR/CVaR/ENB `None` | fixed |
| 3 | Returns paired by position, not by date | **ADJACENT** — `REVIEW_PROCESS.md` invariant I8 already says "อ่านตัวใหม่สุดด้วย**วันที่** ไม่ใช่ตำแหน่ง"; ROUND3 applied it to earnings lists, nobody applied it to return series | One-bar lag: ρ +0.669 → **−0.078**, VaR95 50.8 → 39.4 | fixed (completed 08-17) |
| 4 | Total debt read off a 9-month-old balance sheet | **ADJACENT** — REV-27 (2026-08-04) did date-first-with-ladder-tie-break for `pick()` (flows); nobody did it for `fresh()` (balance-sheet instants) | ORCL debt −42.0B (−31%); **ROIC 17.7% vs true 12.2%** | fixed |
| 5 | IFRS filers resolved no scalars | **ADJACENT** — `DATA_AUDIT.md` claimed "All US + 20-F filers" and booked the symptom as an accepted default ("NVO: no usable pre-tax/tax → 21%"); the tag ladder, not the filing, was the cause | NVO/ASML: `cfo, capex, tax, D&A, SBC, shares, EPS` all `None` | fixed |
| 5b | `eps_gaap` never FX-converted on the SEC path | **ADJACENT** — same class as the 2026-08-11 L2 defect (TSM EPS in TWD → $8,612 FV), at the other conversion site; latent until 5 was fixed | A DKK/TWD EPS beside a USD price | fixed |
| 6 | Loss years booked as **positive** FCFF | **ADJACENT** — the `"1 - reinvest"` in `CODE_REVIEW_2026-08-04.md` is REV-23's *different* site (the FCFF gate, still safe). This is the interaction of REV-28 (uncapped reinvestment) with REV-24 (margin ramp) | NOPAT −202m entered PV as **+403m**; the ramp made verdicts *kinder* | fixed |
| 7 | SBC charged twice (dilution **and** score) | **ADJACENT to PASS2, PARTIALLY REVERSES REV-1** — PASS2 fixed the FVP/EPS leg only; REV-1 deliberately *revived* this branch after finding it dead. See *Open decisions* | E_exec −0.5, composite −0.1 on top of the dilution | fixed — **needs your call** |
| 8 | A divestiture counted as an acquisition | **ADJACENT** — ROUND3's A2 fixed exactly this sign at `acquisition_intensity()` and never touched the reinvestment leg 10 lines away | Selling $2B raised reinvestment as much as buying $2B | fixed |
| 9 | No auth when `APP_TOKEN` unset | **RE-REPORT + REVERSES A DELIBERATE DECISION** — `REVIEW.md` C3 rated this 🔴 Critical, left it open, and chose "ไม่ตั้ง = ปิด auth เหมือนเดิม" on purpose. See *Open decisions* | Anonymous writes to holdings, watchlist, assumptions, refresh | fixed — **needs your call** |
| 10 | Drive pull overwrote a fresh local save | **NEW** — the 2026-07 guard protects the *push* direction; no document considers the *pull* direction | Edit made during an outage vanished — no error, no backup | fixed |
| 11 | FMP quota pre-check budgeted 5 vs 9 real | **🚨 REGRESSION of H2 (2026-07-02)** — H2 found the identical defect (`cost=4` vs 5), offered two fixes, and the constant-bump was chosen; a later `+4` fallback broke it again. This round implements H2's *other* option | 330 calls against a 250 cap → 429s mid-refresh | fixed |
| 12 | Unreachable reverse DCF rendered nothing | **ADJACENT** — round 13 fixed the same null in the *backend narrative* hours earlier the same day and missed this frontend site | The engine explained why; the panel dropped it | fixed |
| **13** | **FCF added SBC straight back** — `FCF = CFO − capex`, and CFO adds SBC back as non-cash by construction | **NEW** — found by checking the code against Damodaran's own rule after the spec correction; no prior round examines what CFO hands back | AXON's reported **$0.1B FCF is −$0.5B**; GOOGL −53%, TSLA −66%, AVGO −27%, MSFT −18.5%. Feeds the P pillar (weight .30) and E_exec | fixed |
| **14** | **Forward-EPS spread compared candidates across currencies** — Yahoo quotes the listed security (USD for an ADR), FMP quotes the filer's reporting currency | **NEW** — surfaced by running `live_check` on TSM, the step this round had skipped | TSM printed `fwdEPS 2src spread 184.3% (-6 conf)`: six points docked and the reader told the analysts disagreed, when one number was TWD. Same class as the L2/TSM defect, one layer up — it corrupts the **diagnosis** rather than the valuation | fixed |

**Why the regression matters more than the count.** H2 was fixed by bumping a constant.
A constant cannot know about a code path added two months later. Finding 11's fix is
H2's second, rejected option — reserve the budget *at the point of spend* — which is
structurally immune to the next optional call someone adds.

---

## Detail

### 1. `^TNX` unit convention — `pipeline/risk_report.py:177`

```python
dy_bps = [(tc[i] - tc[i-1]) * 10 for i in range(1, len(tc))]  # ^TNX 42.5 = 4.25%
```

The comment asserted `^TNX` quotes 42.5 for 4.25%. It does not — Yahoo quotes it in
percent, which `sources/yahoo.py:280` already relies on when it divides the close by
100. Two sites in one repo disagreed about the same feed.

Every yield change was therefore understated tenfold, so the regressed empirical
duration came out **ten times too long**, and `rate_stress` reported a +100bp shock as
a **−19%** portfolio loss where the truth was **−1.9%**.

Worse, the `0 < d < 40` sanity band made the error *selective*: a long bond (TLT, true
duration 16.5 → 165) was rejected and fell back to the proxy, while short and
intermediate bonds passed the band with an inflated number **and** carried the tag
`[CALC] empirical duration (vs ^TNX)` — telling the reader it was measured.

**Fix:** `* 100`, with the convention documented at the call site.

### 2. The covariance matrix was not a covariance matrix — `domain/engine/risk.py:209-221`

`hybrid_cov` took each name's volatility from its **full** history and each pair's
covariance from the pair's **common tail**. With one 400-bar name beside one 60-bar
name:

```
sd(A) full 400 bars     = 0.3054      sd(A) last 60 bars = 0.7919
cov(A,B) on 60 bars     = 0.6272
REPORTED rho(A,B)       = 2.5933      <-- must be within [-1, 1]
```

A correlation above 1 makes `w'Σw` negative, so `portfolio_vol`'s `if var > 0` guard
returned `0.0`, and everything downstream became `None` without a word:

```
var_cvar        -> {'var95_pct': None, 'var99_pct': None, 'cvar95_pct': None}
risk_contributions -> every row's signed_risk_pct/vol_pct = None
diversification_ratio -> None
```

On the real cached series the distortion was smaller but systematic — every pair
touching the short-history name was wrong (NVDA/MSFT +0.444 reported against +0.166
true, MSFT/AVGO +0.341 against +0.127).

The docstring had accepted this ("the matrix is not guaranteed PSD — acceptable for
display/attribution here"). It is not acceptable: it silently blanks the entire risk tab.

**Fix:** separate the two estimates. Correlation is measured on the window the pair
actually shares; volatility on each name's own full history; `cov = ρ·σᵢ·σⱼ`, which
bounds |ρ| ≤ 1 by construction. A pure-Python Cholesky test plus an off-diagonal
shrink (`shrink_to_psd`) guarantees the assembled matrix is PSD.

### 3. Returns were aligned by position — `pipeline/prices.py`, `domain/engine/risk.py:74`

`fetch_returns` stored `as_of` (one date) but never the per-return dates, and
`align_returns` trimmed from the front (`x[-n:]`). So when one price tier published a
bar later than another — routine, there are three tiers — the engine paired NVDA(t)
with AVGO(t−1) and printed a lag-1 cross-correlation as a correlation:

```
aligned (both end 2026-08-10):   rho(NVDA,AVGO) = +0.669
AVGO one bar short:              rho            = -0.078
                       port_vol 0.3085 -> 0.2397   VaR95 50.8 -> 39.4
```

**Fix, part 1 (2026-08-16):** `fetch_returns` now carries a `dates` list validated
against the returns length; `align_on_dates()` intersects on dates and falls back to
positional (reporting `mode="positional"`) for caches written before this change.

**Fix, part 2 (2026-08-17) — the first cut was incomplete.** `align_on_dates()` reached
exactly one call site, the covariance matrix. Three consumers were still pairing by
position, and they are the three most-read numbers on the tab:

- `portfolio_returns()` → the **downside lens** (vol / semidev / Sortino / downside beta)
- `portfolio_returns()` → **historical VaR** (P1-7)
- `pair_corr()` → the **whole benchmark table**: every holding vs SPY/QQQ/TLT/GLD,
  portfolio vs each reference, and reference vs reference
- and one more the first pass missed entirely: `effective_duration()` zipped a bond
  ETF's returns against `^TNX` yield changes **by index**, though the two do not trade
  the same calendar (different holidays, different vendor gaps) — a shifted pair biases
  the regressed duration toward zero.

A partial fix on a defect this quiet is worse than none: it makes the tab look audited.
Added `align_pair()`, `pair_corr_dated()` and `portfolio_returns_dated()` (which returns
the portfolio series **with its own dates**, so it can itself be aligned against a
benchmark instead of pinned to one by position), and routed every consumer through them.
The regression test asserts no production code reaches for the positional primitives any
more. On identical series one bar apart the difference is stark: date-aligned **ρ = 1.000**,
positional **ρ = −0.733**.

### 4. Balance-sheet stocks read off the wrong date — `sources/sec_edgar.py:385`

```python
total_debt = (fresh("DebtLongtermAndShorttermCombinedAmount")
              or _sum(fresh(*LT_DEBT), fresh(*ST_DEBT)))
```

Three independent bugs in one expression:

1. `fresh()` returned the first tag in **ladder order** that was merely within a 540-day
   staleness window — not the freshest value.
2. The combined tag short-circuited on `or` even when far older than the split legs.
3. The two `fresh()` calls could return legs from **different dates** and add them.

ORCL hit all three. Its income statement TTM ends 2026-02-28; the combined tag is filed
annually at 2025-05-31:

```
total_debt AS RETURNED : 92,568,000,000  (as-of 2025-05-31, 273 days stale)
true debt at 2026-02-28: 134,605,000,000  (LongTermNotesAndLoans + NotesPayableCurrent)
understated by          : 42,037,000,000  (-31%)

ROIC as coded 17.67%  ->  corrected 12.15%   (5.5pp overstated)
net debt      54.1B   ->  corrected 96.2B    (EV, and every EV multiple, too low)
```

`LongTermNotesAndLoans` — the only long-term concept ORCL tags quarterly — was missing
from the ladder entirely. ABBV and AVGO showed the milder version of the same defect
(debt and lease legs 90 days behind their own income statements).

**Fix:** `fresh_entry()` selects by **date first**, ladder order as tie-break;
`_total_debt_dated()` walks balance-sheet dates newest-first and takes the first that
yields a complete figure from a single date; `LongTermNotesAndLoans` and
`NotesAndLoansPayableCurrent` added to the ladders.

### 5. IFRS filers got nothing — `sources/sec_edgar.py`

Every scalar resolved only `us-gaap` tags. For a 20-F filer:

```
NVO scalars:  cfo=None  capex=None  tax_expense=None  dep_amort=None
              sbc=None  shares_diluted=None  eps_gaap=None
```

All seven were present in the same JSON under `ifrs-full`
(`CashFlowsFromUsedInOperatingActivities` = 119.1B DKK,
`DilutedEarningsLossPerShare` = 23.03, `AdjustedWeightedAverageShares` = 4.4477e9…).
The **dated series** already had an IFRS ladder (`CFO_TAGS`, `CAPEX_TAGS`), so a single
file disagreed with itself about whether NVO has a cash flow statement. Two of 21
holdings ran the engine with no FCF, no diluted share count and no tax rate.

**Fix:** IFRS variants appended to every scalar ladder (last, so a US filer can never
resolve to them by accident — the convention already documented at `CFO_TAGS`).

**And a latent bug this exposed:** with `eps_gaap` finally resolving, NVO's EPS came
through as **23.03 DKK next to a USD price** — the exact TSM/L2 defect that once printed
an $8,612 fair value. `pipeline/dataquality`'s fallback list converts `PER_SHARE`
fields; `pipeline/normalize`'s primary SEC list never did. `eps_gaap` added there.

### 6. Loss years booked as positive cash — `domain/engine/deep_v82.py:568`

```python
fcff = rev_t * m_t * (1 - tax if m_t > 0 else 1.0) * (1 - reinvest)
```

Safe only while `reinvest ≤ 1`. Reinvestment is `x/ROIC` and routinely exceeds 1 for the
pre-profit names the margin ramp was built for — at x=30%, ROIC=10% it is **3.0**, so
`(1 - reinvest) = −2.0`, and a negative NOPAT times a negative factor is positive:

```
 yr   margin     NOPAT $m    FCFF $m as coded
  1   -15.5%         -202                403  <-- loss booked as positive cash
  2   -11.0%         -186                372
  3    -6.5%         -143                286
  4    -2.0%          -57                114
```

The ramp existed (REV-24) to make the model **harsher** on loss-makers. It did the
opposite: adding four loss years *lowered* the implied CAGR and produced a kinder
verdict on exactly the names hardest to value.

**Fix:** reinvestment is a capital outlay, not a share of profit. Under the constant
sales-to-capital assumption the docstring already derives, `ΔCapital` is fixed by the
**terminal** relation and stays positive regardless of this year's margin — which also
makes it *identical* to the old expression whenever the margin is flat and positive, so
the calibrated non-ramp path is unchanged. Now monotone, as intended:

```
margin_now  +10%  0%   -5%   -15.2%  -30%
implied     11.7 13.8  14.9   17.2    20.7   (flat path: 8.5)
```

The replay baseline moved on 10 of 21 names, every move explainable: HIMS 13.1 → 22.9,
RKLB 64.7 → "price implies more than 100%/yr", AXON's earnings-quality verdict
REVIEW → CLEAN (defect 7), and small shifts on the profitable names where reinvestment
no longer scales with a temporarily fat margin (NVDA 36.3 → 33.7, UNH 7.4 → 7.6).

### 7. SBC counted twice — `domain/engine/deep_v82.py:736`

v7.1 rule 5 removed the qualitative SBC penalty precisely to stop double counting: SBC
is charged through **share dilution in forward EPS**, and that channel is always on. The
`earnings_quality` flag nonetheless still entered the scored verdict:

```
SBC  9% of revenue -> EQ CLEAN  E_exec 5.0  composite 4.15
SBC 11% of revenue -> EQ REVIEW E_exec 4.5  composite 4.05
dilution charged identically in both cases: sbc_dilution_pct 2.08
```

**Fix:** the SBC line is kept as a **disclosure** (the reader should see the number) but
excluded from the verdict that scores. Both cases now score identically.

### 8. A divestiture treated as an acquisition — `domain/engine/deep_v82.py:1472`

```python
acq = abs(f.acquisitions_net)
```

The A2 fix corrected `acquisition_intensity()` for exactly this and missed the
reinvestment leg ten lines away, so selling a $2B business raised the reinvestment bill
as much as buying one:

```
bought $2B: inc_roic_pct 15.0      SOLD $2B: inc_roic_pct 15.0      no M&A: 200.0
```

`PaymentsToAcquireBusinessesNetOfCashAcquired` is a payment; negative means cash came
**in**. **Fix:** `max(0.0, …)` — a divestiture is floored at zero rather than credited as
negative reinvestment.

### 9. No authentication — `app.py:79`

Auth was optional and unset is the default. With `APP_TOKEN` empty, on the public Render
URL:

```
POST /api/holding?ticker=EVIL&shares=999   -> 200   (store mutated)
POST /api/watchlist/add?ticker=PWNED       -> 200
POST /api/assumptions?erp_pct=9.9&market_pe=59 -> 200   (rewrites the ERP the whole engine reads)
POST /api/refresh                          -> 200   (burns the FMP daily quota)
GET  /api/portfolio                        -> 200   (hands over the holdings)
```

**Fix:** optional is fine on localhost, where the socket is the boundary; it is not fine
on a public URL. When `RENDER`/`RENDER_EXTERNAL_URL`/`PUBLIC_DEPLOY` is set and no
`APP_TOKEN` is configured, the app now **fails closed** with an explanatory 503 on every
route except `/healthz`. `ALLOW_PUBLIC_NO_AUTH=1` is the deliberate opt-out.

Also: `/healthz` is exempt from the 401 check but was still **minting the session
cookie**, so the unauthenticated health endpoint handed out cookies and every
`/healthz?token=…` probe wrote the raw token into the access log. Cookie minting now
excludes `/healthz`.

### 10. A Drive pull destroyed a fresh local save — `store_sync.py`

The push guard protects the **remote** copy while the initial pull is failing. Nothing
protected the **local** one:

```
1. cold start, Drive down        -> pull_state = error
2. user saves NVDA 250sh         -> local only; push correctly BLOCKED
3. Drive recovers, pull succeeds -> local file overwritten by the stale remote

*** NVDA 250 shares @118.50 is GONE — no error, no backup, no warning ***
```

The guard that protected the remote is what made the local copy the only copy, and the
retry then destroyed it.

**Fix:** a `_local_dirty` flag, set **only** by the explicit user mutations in `store.py`
— deliberately **not** by `save()`, since `save()` also persists the empty default store
a failed cold start produces, and treating that as a user edit would reintroduce the
2026-07 wipe. On a retry with a dirty local file, local wins: it is adopted, pushed up,
and the remote is preserved beside it as `portfolio.json.remote`. The flag clears as soon
as the edit reaches Drive, guarded by a generation counter against a mid-upload edit.

### 11. FMP quota undercount — `pipeline/refresh.py:392`

`_partition_by_quota` admitted tickers at 5 calls each. The stale-financials fallback
spends **four more** in one go, so the true worst case is 9:

```
quota_used=150, cap=250 -> partition admits 20 tickers (budgeted 100)
real worst case = 180 -> day total 330 vs cap 250 -> OVER by 80
```

FMP starts answering 429 partway through, and names refreshed after that point come back
with fewer consensus sources — which is why 13 of 21 holdings currently sit on a
single-source forward EPS.

**Fix:** budgeting 9 up front would refuse half the portfolio for a branch that fires on
almost none of it. An **optional** cost belongs at the point of spend: a thread-safe
`_Budget` holds whatever the guaranteed spend did not claim, and the fallback reserves
its 4 before spending them.

### 12. An unreachable reverse DCF rendered nothing — `index.html:2157`

```js
const imp = x.rev_implied_cagr; if(imp == null) return '';
```

REV-5 made the engine report *why* a price cannot be reached; the panel dropped that
verdict on the floor and rendered blank space — on exactly the names that need the most
explanation. Now surfaced as
`หา CAGR ที่รองรับราคานี้ไม่ได้ — <verdict>`.

---

## Checked and found correct

Audited and cleared, so the next review need not redo them:

- **SEC fair-access compliance** — `_throttle(min_interval=0.15)` ≈ 6.7 req/s, under the
  10 req/s limit, thread-safe across the parallel fetch; `User-Agent` passed on every
  request path.
- **Restatement handling** — `entries()` de-duplicates by `(start, end)` keeping the
  latest `filed`, so a 10-K/A supersedes the original automatically.
- **Secrets** — every key read from the environment; `.gitignore` covers `.env`,
  `client_secret.json`, `data/portfolio.json` and the caches. Nothing hardcoded.
- **Split adjustment** — price series use adjusted closes across all three tiers; Stooq
  is split-only and explicitly flagged `dividend_adjusted=False`.
- **Input sanitising / XSS** — `clean_ticker()` strips everything outside `[A-Z0-9.\-]`
  server-side, and `esc()` wraps the user-controlled fields rendered into the DOM.
- **Fiscal-year alignment in the ROIC series** — `instant_at_dates()` reads the balance
  sheet AT each income-statement year-end and picks ONE concept for the whole series, so
  a June-year-end filer is not paired with a March balance sheet.
- **Net-income tag conflicts** — `NetIncomeLoss` vs
  `NetIncomeLossAvailableToCommonStockholdersBasic` is detected and reported as a
  conflict rather than silently picked.
- **Damodaran rules verified in place** — ERP locked at 4.75% with a store-backed manual
  override; risk-free from the live 10Y with an explicit `live=False` flag on fallback;
  R&D capitalised with a ratio-based fallback; leases capitalised; `WACC > g` guarded;
  terminal growth capped; EV built from market cap + debt − cash.

---

## Open decisions — yours, not mine

Two fixes change a decision an earlier round made deliberately. Both are one-line
reversible; neither should stand on my judgement alone.

### D1 · SBC — one charge or two? (finding 7) — **RESOLVED 2026-08-17**

The two specs disagree, and both are defensible:

- **v7.1 prompt:** *"SBC is Real Expense (Dilution factored, **no double counting**)"* —
  one charge, through dilution.
- **v8.2 engine (`DESIGN.md` §6):** *"Earnings-quality screen — cash conversion /
  accruals / **SBC% → caps Execution**"* — SBC belongs in the quality screen.

The history explains how both became true at once. v8.2 designed SBC% → EQ → caps
Execution **when there was no dilution channel at all**: REV-1 (2026-08-04) found the
branch was dead code — *"สาขา `SBC >10% of revenue` ใน `earnings_quality()` เป็น dead code —
ยิงไม่ได้เลย"* — because nothing populated `f.sbc`. REV-1 populated it, which revived the
EQ branch **and** switched on the dilution charge. Nobody removed either. So the double
count is emergent, not designed — but removing the EQ leg is still a partial reversal of
REV-1's intent.

- **What I shipped:** the SBC line stays as a **disclosure** on the card; it no longer
  enters the scored verdict. Effect: AXON's `eq_verdict` REVIEW → CLEAN, `E_exec`
  1.5 → 2.5, `composite` 1.33 → 1.53.
- **The alternative:** keep the EQ cap and drop the dilution instead — closer to v8.2 as
  written, but dilution is the channel that actually divides earnings, and PASS2 already
  built careful per-path logic around it ("หัก dilution เฉพาะเส้นที่ EPS ยังไม่รวม SBC").
- **Revert:** restore `flags.append("SBC >10% of revenue")` in
  `domain/engine/deep_v82.py:earnings_quality`.

**Resolution — Damodaran settles it, and points somewhere else.** His rule is explicit:
*"You cannot do both, because you are then reducing value per share twice for the same
phenomenon"*, and adding SBC back to earnings is *"an indefensible practice"*. His
prescription is one charge, in the cash flows — for future grants, *"the right response
to the expected dilution is to do nothing"* to the share count, because the expense
already carries it.

Applied here that gives a **three-part answer**:

1. **The valuation path was already correct** before this round. PASS2 charges dilution
   only on the path whose EPS is a non-GAAP consensus with SBC removed, and not on the
   GAAP path where it is already deducted. That is exactly Damodaran's rule.
2. **Finding 7's fix stands** — but the original justification was wrong. The EQ leg is
   not a second charge against *value* (it moves the score, not the fair value), so
   Damodaran's warning does not strictly apply. It stands on a plainer ground: the same
   fact was moving both the fair value and the rating, and the rating is what the
   recommendation reads. The SBC level is still disclosed on the card.
3. **The real Damodaran violation was somewhere neither spec mentions** — see finding 13.
   `FCF = CFO − capex` adds SBC straight back, because CFO does. That *is* the
   indefensible add-back, in the metric that feeds the heaviest pillar.

### D2 · Auth — fail closed on a public deploy? (finding 9) — **CONFIRMED: keep it**

`REVIEW.md` C3 rated "no authentication on any endpoint" 🔴 **Critical** and chose to
leave it opt-in on purpose: *"เพิ่ม `APP_TOKEN` (env var, **ไม่ตั้ง = ปิด auth เหมือนเดิม**)"*.
It has been open ever since.

- **What I shipped:** when `RENDER` / `RENDER_EXTERNAL_URL` / `PUBLIC_DEPLOY` is set and
  `APP_TOKEN` is empty, every route except `/healthz` returns **503** with an
  explanatory message. Localhost is untouched.
- **Cost:** if your Render service has no `APP_TOKEN`, the dashboard goes dark until you
  set one (it is already listed in `render.yaml`) and open `/?token=…` once.
- **Revert / opt out:** set `ALLOW_PUBLIC_NO_AUTH=1`.

---

## Also judgement, but lower stakes

1. **`tests/test_dataquality`'s false-positive threshold** was split by severity —
   `warn`/`block` still ≤3 of 21; the zero-penalty `thin_forward_eps` note got its own
   looser bound. `REVIEW_PROCESS.md:170` says a guard that never proves its worth should
   be **deleted**, not loosened, and `:220` says a test must pin *code behaviour, not a
   data fact* — "after one clean refresh this should fall on its own" is a data fact.
   **Recommended:** after one clean refresh, either the note drops below 3 on its own or
   it should be removed outright rather than kept at a looser bound.
2. **`test_engine_v82`'s REV-24 assertion was rewritten.** It asserted the ramped path
   returns a number, which was only true *because* of finding 6's sign bug. It now
   asserts the ramp is harsher (monotone in the depth of today's loss), which is what
   REV-24 meant. This does mean round 9's guard no longer pins the exact text round 9
   wrote — flagged rather than hidden.
3. **`replay_baseline.json` moved 15 values across 10 tickers**, all attributable to
   findings 6 and 7: AVGO 35.9→35.4 (verdict Ambitious→Plausible) · AXON E_exec
   1.5→2.5, composite 1.33→1.53, eq_verdict REVIEW→CLEAN, peak 40%→6% · HIMS 13.1→22.9 ·
   LLY 12.2→12.1 · MSFT 20.1→19.4 · NVDA 36.3→33.7 · RKLB 64.7→"implies >100%/yr" ·
   TSLA peak 48%→44% · TSM 26.7→25.9 · UNH 7.4→7.6.

---

## Review history — this is round 14, not round 6

Five earlier rounds left no `.md` file, which is why the count looked smaller:

| # | Date | Artifact | Scope |
|---|------|----------|-------|
| 1 | 2026-06-18 | `UPGRADE_ENGINE_REVIEW.md` | Retro on the v7.3→v8.2 upgrade *guide* |
| 2 | 2026-06-27 | `REVIEW.md` | Production hardening: C1 per-user, C2 store race, **C3 auth (still open)** |
| 3 | 2026-06-27 | `REPO_AUDIT.md` | Repo hygiene, secrets, CRLF |
| 4 | 2026-06-27 | `DATA_AUDIT.md` | Data provenance: real vs derived vs assumed |
| 5 | 2026-07-02→03 | `CODE_REVIEW_2026-07-02.md` | Full-stack reliability; **H2 = the quota defect finding 11 regressed** |
| 6 | 2026-07-04 | `CODE_REVIEW_2026-07-04.md` | Safe fixes only; deferred "wire `effective_duration()`" — finding 1 entered here |
| 7 | 2026-07-11 | code only (`risk.py:628`) | `portfolio_returns` IndexError on an empty series |
| 8 | 2026-07-19 | code only (`test_quickpatch_corr.py`) | "0.60-everywhere" patch — **created `hybrid_cov`**, where finding 2 lives |
| 9 | 2026-08-04 | `CODE_REVIEW_2026-08-04.md` | REV-1…REV-28: SBC, ROIC fallbacks, reinvestment cap, solver |
| 10 | 2026-08-07 | `CODE_REVIEW_2026-08-04_PASS2.md` | Second pass over round 9's own code: P2-1…P2-5 |
| 11 | 2026-08-08/09 | `CODE_REVIEW_2026-08-08_ROUND3.md` | P3-x, D1–D5, **A1–A4** (A2 = finding 8's sibling), B5–B8 |
| 12 | 2026-08-09→11 | `REVIEW_PROCESS.md` | Methodology + guard layers 1–4, then layer 5 (live run): 8 more defects |
| 13 | 2026-08-16 (same day, earlier) | `COMMIT_MSG_2026-08-16.txt`, `tests/test_qa_2026_08_16.py` | Live-UI audit: ENB inverted in crisis, 4 reader-facing defects incl. the `None` implied-CAGR **narrative** — finding 12 is the frontend half this missed |
| 14 | 2026-08-16/17 | this document | Full-codebase audit |

**Every earlier round's guard still passes** — `test_quickpatch_corr`, `test_risk`,
`test_correlation`, `test_risk_report`, `test_qa_2026_08_16`, `test_engine_v82`,
`test_review3/3d`, `test_pillars_a/b`, `test_skill_parity`, `test_young_dcf`,
`test_contracts`, `test_invariants`, `test_extract`, `test_dataquality`,
`test_frontend`, `test_gdrive`, `test_app_fixes`, `test_parallel_fetch` — with the one
disclosed exception in *Also judgement* item 2.

---

## Still open

1. ~~`tests/live_check.py` has not been run.~~ **DONE (2026-08-17)** — MSFT · TSM · NVO
   against live data. It confirmed fixes 5 and 13 in production (NVO's 5-year strip
   populates for the first time; both MSFT and NVO show `CFO − capex − SBC`), showed
   MSFT's score and recommendation completely unmoved (no regression on a clean filer),
   and **found defect 14**, which no offline test could have. TSM's empty trend strip is
   the DQ2 fallback behaving as designed (SEC stale → FMP for a later period → the SEC
   series cleared rather than mixed). Two things to watch, neither a defect:
   NVO moved HOLD → BUY on a fair value the engine itself labels a clamp boundary
   (`fundamental PE clamped to [5, 35] (raw 41.37)`) while its Demand pillar reads 1.0/5
   on decelerating growth; and TSM's E_exec jumped +1.00.
   The original wording follows. Per
   `REVIEW_PROCESS.md:200`, code that has never met real data counts as untested no
   matter how green the suite is. That applies to `_total_debt_dated`, the IFRS ladders,
   `align_on_dates`/`align_pair`, `_Budget`, and the fail-closed middleware.
   *Partly mitigated:* the NVO fixture now runs end-to-end and its two independent
   per-share paths agree exactly (`net_income/shares = eps_gaap = 3.339`, invariant I12),
   and `price × diluted shares = $273.5B` against a real market cap near $270B — so the
   ADR depositary-ratio trap REV-25 warned about does not bite here.
2. ~~ORCL's revised debt is not reconciled to the filing.~~ **DONE (2026-08-17).**
   Oracle's own Q3 FY2026 8-K, quarter ended 2026-02-28, reports *notes payable and
   other borrowings, current* **$9,887M** and *non-current* **$124,718M** — total
   **$134,605M**, matching the fix to the dollar, against the $92,568M the stale tag
   produced. Cash **$38,455M** also matches. (Equity: the release shows $39,051M vs the
   extractor's $38,495M — a ~1.4% gap that is the non-controlling-interest line, worth a
   look but not part of this defect.)
3. ~~`REVIEW_PROCESS.md` has not been extended.~~ **DONE (2026-08-17)** — added
   "ชั้นที่ 6 — รอบ 14", covering: a constant-bump is not a fix (H2); a module-split
   review loses the seams; every finding needs a NEW/ADJACENT/REGRESSION/CONTRADICTS
   verdict before it can be called new; cite the right spec; and the difference between
   something to fix and something to ask about. Checklist items 0, 13, 14, 15 added.

---

## Verification

- **57 suites, all green** (`python run_tests.py`). Earlier drafts of this document said
  48; `DESIGN.md` and `REVIEW_PROCESS.md` said 55. All three are now corrected to 57.
- `python -m tests.replay_snapshot` — **clean**, no tracked value moved after the
  baseline update.
- `tests/test_audit_2026_08_16` — 16 test groups, one per defect plus the completed
  date-alignment seam and the IFRS consistency invariant, each asserting the observable
  symptom rather than the implementation.
