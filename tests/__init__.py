"""Test package bootstrap: FMP's request ledger goes to a temp file, so a test run
never reads or raises the real day's count in data/cache/fmp_ledger.json."""
import os
import tempfile

from sources import fmp

fmp.LEDGER = fmp.Ledger(os.path.join(tempfile.mkdtemp(prefix="fmp_ledger_"), "fmp_ledger.json"))
