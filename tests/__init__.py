"""Test package bootstrap: FMP's request ledger and FMP answer cache go to temp files,
so a test run never reads or raises the real day's count in data/cache/fmp_ledger.json,
and never serves (or writes) a cached FMP answer from data/cache/fmp_cache.json."""
import os
import tempfile

from pipeline import fmp_cache
from sources import fmp

fmp.LEDGER = fmp.Ledger(os.path.join(tempfile.mkdtemp(prefix="fmp_ledger_"), "fmp_ledger.json"))
fmp_cache.CACHE = fmp_cache.FmpCache(os.path.join(tempfile.mkdtemp(prefix="fmp_cache_"),
                                                  "fmp_cache.json"))
