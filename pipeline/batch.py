"""
Per-ticker fan-out under the FMP daily quota.

The quota is split in two: every ticker's GUARANTEED spend is admitted up front
(`partition_by_quota`, worst case per ticker), and an OPTIONAL spend that only
some tickers make is reserved at the moment of spending from what is left
(`Budget`), so parallel workers cannot overshoot the cap together.
"""
import threading
from concurrent.futures import ThreadPoolExecutor

MAX_WORKERS = 4                    # parallel ticker fetches (SEC throttle is global)
# profile + quote-fallback + earnings + estimates + quarterly-est backfill
FMP_CALLS_PER_TICKER = 5


def run_parallel(fn, items):
    """{item: finished Future of fn(item)}, MAX_WORKERS at a time. Callers read
    results in their own (input) order so errors and rows stay stable."""
    if not items:
        return {}
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(items))) as ex:
        return {t: ex.submit(fn, t) for t in items}


def guaranteed_cost(fmp_key):
    return FMP_CALLS_PER_TICKER if fmp_key else 0


def partition_by_quota(tickers, cost, quota_used, quota_cap):
    """(todo, quota_errors): admit tickers while their worst-case cost fits."""
    todo, errors = [], []
    budget = max(0, quota_cap - quota_used)
    for t in tickers:
        if cost and (len(todo) + 1) * cost > budget:
            errors.append(f"{t} (quota)")
        else:
            todo.append(t)
    return todo, errors


class Budget:
    """Thread-safe remaining-call counter for OPTIONAL FMP spending: two workers
    each seeing "4 left" and both spending is the race this closes."""

    def __init__(self, n):
        self._n = max(0, int(n))
        self._lock = threading.Lock()

    def take(self, n):
        with self._lock:
            if self._n >= n:
                self._n -= n
                return True
            return False

    def left(self):
        with self._lock:
            return self._n


def quota_take(budget, n):
    """True when `n` optional calls may be spent. None = no budget tracking."""
    return True if budget is None else budget.take(n)
