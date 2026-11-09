# Stage 0.5 — Connector Data Pull (Bigdata.com) — v8.1 ★ NEW

> **v9.0:** background on the method. Every number comes from `scripts/value.py` (the portfolio-deep app's engine). Where this page and the engine disagree, the engine is right.

**Role:** institutional DATA LAYER. Pull a complete, verified dataset once, tag every field with its
source, and hand clean inputs to the V8 engine. The engine (scripts) still does ALL the math.

> Verified 17 Jun 2026: `bigdata_company_tearsheet` returns figures that match SEC filings exactly,
> and fixes V8's weakest spots (no more guessing SBC / CFO / forward EPS). See
> `CONNECTOR-VERIFICATION-bigdata.md` and `BIGDATA-ENHANCE-v8-damodaran.md`.

## Workflow
```
1. find_securities(ticker) → confirm the right entity → rp_entity_id + type (Public/Private)
2. bigdata_company_tearsheet(rp_entity_id, company_type, interval="annual")   → 5 fiscal years
   bigdata_company_tearsheet(rp_entity_id, company_type, interval="quarter")  → 8 quarters
   (sections default = all; or pick: financial_statements, key_metrics, analyst_estimates,
    revenue_segmentation, analyst_ratings, hiring_trends, latest_earnings, earnings_calendar)
3. CROSS-CHECK price + core financials vs SEC EDGAR JSON (data.sec.gov). Deviation gate:
   price ≤ 2% (T-1), financials ≤ 1%. Fail → flag, do not proceed silently.
4. Hand the bundle to the V8 engine (Stage 1+). Tag every field [BD]/[SEC]/[WS].
```

## Data depth (tested, NVDA)
| Data | History | Forward |
|---|---|---|
| Income/Balance/Cash-flow | 5 years / 8 quarters | — |
| Analyst estimates (rev, EPS, EBIT, NI) | — | 5 years |
| Key metrics (ROIC, margins, SBC%, cash conversion, DSO) | 5 years | — |
| Revenue segmentation (geo + product) | ~5 quarters | — |
| Hiring trends, latest earnings (surprise), earnings calendar | recent / next | — |
| `bigdata_search` (transcripts/filings/news/research) | multi-year, date-filterable | — |

## What it feeds (→ V8 / Damodaran)
- **Stage 0 bundle:** price, diluted shares, total debt, cash+investments, beta, tax rate
- **Cash flows:** CFO, capex, FCF, NOPAT  → Damodaran input 1
- **Growth:** analyst_estimates PATH (per-FY rev & EPS) → Rule 5 (input 2)
- **Quality:** key_metrics ROIC / margins / SBC% + R&D history → Economics, R&D capitalization (input 3)
- **Risk:** beta → WACC (input 4)  [cost of debt / credit rating still need search — label fallback]
- **Terminal:** competitors + segment margins → terminal margin (input 5) [+ sector search]
- **Pre-profit survival:** cash + FCF + current ratio → runway → p_survival (input 6)
- **Story / catalysts:** segmentation, hiring, `bigdata_search`, `sentiment_tearsheet`, `events_calendar`

## Fallback chain (connector unavailable)
```
Bigdata  →  SEC EDGAR JSON API (data.sec.gov)  →  WebSearch
⚠️ yfinance is blocked in this sandbox (proxy 403). Use only where it can reach Yahoo.
```

## Guardrail at this stage
- Tag EVERY field with its source. Missing + no documented fallback → `⛔ MISSING: <field>`.
- Do NOT fabricate or interpolate. Growth comes from `analyst_estimates` (see Rule 5), never hand-picked.
- Assumptions (terminal margin, target P/E, p_survival) are NOT pulled facts → label + sensitivity downstream.
