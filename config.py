"""
Configuration for the DEEP portfolio app. Secrets come from the environment only:
    Windows PowerShell:  $env:FMP_API_KEY="your_key_here"
    macOS / Linux:       export FMP_API_KEY=your_key_here

Deployment/auth env vars (APP_TOKEN, RENDER, WEB_CONCURRENCY, ...) are read by app.py
and Drive credentials (GDRIVE_*) by sources/gdrive_store.py, at import time there.
"""
import os

# --- data-source keys and endpoints ------------------------------------------
FMP_API_KEY = os.environ.get("FMP_API_KEY", "")
FMP_BASE = "https://financialmodelingprep.com/stable"     # stable endpoints (?symbol=)
FMP_LEGACY = "https://financialmodelingprep.com/api/v3"   # fallback path style
QUOTA_CAP = 250            # FMP free-tier daily call budget (single source of truth)

# Optional consensus cross-check sources; without a key the source is skipped.
#   Finnhub:        free 60 req/min  -- EPS surprise history
#   Alpha Vantage:  free 25 req/day  -- historical quarterly EPS surprise
FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY", "")
ALPHAVANTAGE_API_KEY = os.environ.get("ALPHAVANTAGE_API_KEY", "")

# SEC fair-access requires a contact in the User-Agent; keep the address out of git.
SEC_CONTACT_EMAIL = os.environ.get("SEC_CONTACT_EMAIL", "your-email@example.com")
SEC_USER_AGENT = f"PortfolioDeepApp {SEC_CONTACT_EMAIL}"

# --- engine -------------------------------------------------------------------
DEEP_VERSION = "8.2"         # active DEEP framework version; change this ONE line to swap engines

# Equity risk premium (Damodaran implied, US). No free live feed, so it is refreshed
# by hand; the engine and the validate sanity band both read it from here (a value
# saved from the dashboard overrides it at runtime, see store._apply_assumptions).
# ifa-stock-analysis-v8/scripts keep their own CLI default: update those too.
ERP = 0.0445                 # decimal, not percent
ERP_AS_OF = "2026-07"        # year-month this ERP was last set
ERP_STALE_MONTHS = 3         # flag the ERP as stale once it is older than this

# --- paths and caches -----------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
CACHE_DIR = os.path.join(DATA_DIR, "cache")          # SEC companyfacts + CIK map cache
FIXTURE_DIR = os.path.join(BASE_DIR, "tests", "fixtures")

# SEC fair-access: stay well under 10 req/s and cache filings (they change quarterly)
SEC_MIN_INTERVAL = 0.15      # seconds between SEC requests (~6-7/s)
SEC_CACHE_TTL_HOURS = 12     # re-use cached companyfacts within this window
PRICE_CACHE_TTL_HOURS = 18   # R4: re-use the long (2y) price series within a trading day

# --- reference data ---------------------------------------------------------------
# Core 12 (ticker -> SEC CIK) — used for SEC cross-check
CIKS = {
    "NVDA": "0001045810", "MSFT": "0000789019", "AVGO": "0001730168", "TSM": "0001046179",
    "GOOGL": "0001652044", "ORCL": "0001341439", "MELI": "0001099590", "ABBV": "0001551152",
    "TMDX": "0001756262", "LLY": "0000059478", "TSLA": "0001318605", "NVO": "0000353278",
}

# Tickers used to validate the data layer in Phase 1 (the ones that broke v1).
PROBE_TICKERS = ["AVGO", "ABBV", "ORCL", "NVO", "MSFT"]

# Known-good reference values (from multi-source cross-check) to sanity-check the
# probe output. Ranges, not exact, because TTM windows shift with the date.
KNOWN_GOOD = {
    "AVGO": {"net_income_ttm_bn": (20, 28), "revenue_ttm_bn": (60, 72), "note": "v1 wrongly got 2.99B; tag switched to ProfitLoss"},
    "ORCL": {"revenue_ttm_bn": (54, 72), "note": "v1 wrongly got 161B; TTM grows past FY2025 57.4B"},
    "ABBV": {"net_income_ttm_bn": (3, 6), "note": "GAAP genuinely low (amortization); adjusted ~$10 EPS"},
    "MSFT": {"revenue_ttm_bn": (290, 340), "note": "v1 was correct ~312B"},
    "NVO":  {"note": "reports in DKK -> convert to USD in normalize"},
}

# --- Damodaran S6: trading costs & taxes are a real drag on net returns -------
# ASSUMPTIONS (no live feed; set here like ERP). Anything derived from these is
# labelled net-of-cost/tax in the UI, with the gross shown alongside.
CAPGAINS_TAX_RATE = 0.15     # investor capital-gains tax on realized gains (long-term US default)
TRADING_COST_BPS = 20        # round-trip trading cost in bps (bid-ask + impact); 20 = 0.20%

# --- Damodaran S32/33: market-valuation overlay (manual, refresh monthly like ERP)
# S&P 500 trailing P/E; no free API, update from multpl.com / Damodaran monthly.
# Overridden at runtime by a dashboard-saved value, like ERP.
MARKET_PE = 25.1
MARKET_PE_AS_OF = "2026-06"

# --- Damodaran S3 (bonds) + S38-41 (alternatives): multi-asset-class support ---
# Map ticker -> asset class so the risk engine treats bonds/gold/crypto correctly
# (default "equity"). Crypto/collectibles have NO cash flow -> PRICING plays (S40-41);
# the UI labels them so and caps their weight.
ASSET_CLASS_MAP = {
    "TLT": "bond", "IEF": "bond", "SHY": "bond", "AGG": "bond", "BND": "bond",
    "LQD": "bond", "TIP": "bond", "GOVT": "bond",
    "GLD": "gold", "IAU": "gold",
    "BTC-USD": "crypto", "ETH-USD": "crypto", "IBIT": "crypto",
}
# Effective-duration proxy (years) for bond ETFs (S3); empirical value (price vs
# ^TNX) overrides when computable. [JUDG-PROXY]
DURATION_PROXY = {
    "SHY": 1.9, "IEF": 7.5, "TLT": 16.5, "AGG": 6.0, "BND": 6.0,
    "LQD": 8.5, "TIP": 7.0, "GOVT": 6.0,
}
# Annual vol proxy by asset class (decimals) when price history is thin.
CLASS_PROXY_VOL = {"equity": 0.18, "bond": 0.06, "gold": 0.15, "crypto": 0.70,
                   "collectible": 0.30, "cash": 0.01}
# P1-11 (S40-41): assets with NO cash flow (crypto/collectible) are PRICING plays —
# harder single-position weight cap than ordinary equities (position_sizing).
PRICING_ASSET_CAP = 0.05

# Build stamp — bump together with DASH_BUILD in index.html (frontend/deploy guard).
BUILD = "2026-08-18a"
