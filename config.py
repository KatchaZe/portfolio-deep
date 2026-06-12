"""
Configuration for the DEEP v7.3 app (v2).

The FMP API key is read from the environment, never hard-coded:
    Windows PowerShell:  $env:FMP_API_KEY="your_key_here"
    macOS / Linux:       export FMP_API_KEY=your_key_here
"""
import os

FMP_API_KEY = os.environ.get("FMP_API_KEY", "")
FMP_BASE = "https://financialmodelingprep.com/stable"     # stable endpoints (?symbol=)
FMP_LEGACY = "https://financialmodelingprep.com/api/v3"   # fallback path style

SEC_USER_AGENT = "PortfolioDeepApp katcha2002@gmail.com"  # SEC fair-access contact

# Active DEEP framework version — change this ONE line to swap engines (e.g. "7.4")
DEEP_VERSION = "7.3"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
CACHE_DIR = os.path.join(DATA_DIR, "cache")          # SEC companyfacts + CIK map cache
FIXTURE_DIR = os.path.join(BASE_DIR, "tests", "fixtures")

# SEC fair-access: stay well under 10 req/s and cache filings (they change quarterly)
SEC_MIN_INTERVAL = 0.15      # seconds between SEC requests (~6-7/s)
SEC_CACHE_TTL_HOURS = 12     # re-use cached companyfacts within this window

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
