"""
DEPRECATED shim (2026-07-19) — the risk-returns ladder + daily cache moved to
pipeline/prices.py (the single price-series module). Kept so old imports keep
working; new code should import pipeline.prices directly.
"""
from pipeline.prices import (DEFAULT_DAYS, RISK_CACHE_PATH as CACHE_PATH,   # noqa: F401
                             holdings_key, load_cache, save_cache, fetch_returns)
