"""
FMP answers kept on disk, and mirrored to Drive like the risk cache, so a refresh
re-buys a ticker's statements, estimates and profile only when they can have changed.

One rule per kind (RULES). A statement-bound kind also expires the moment SEC lists a
10-Q / 10-K / 20-F / 40-F filed on or after the day the answer was fetched: FMP's
statements can only be as new as the last filing, so a filing the cache has not seen
means the cache may be one quarter behind. The newest filing date comes from the SEC
documents the refresh already read (sources.sec_gap.newest_filing), so this check
sends nothing to SEC.

Every lookup returns a Use, which says where the data came from in one line for the
ticker's provenance: "live", "cache <day>" or "refetched: <why>".
"""
import json
import logging
import os
import threading
import time
from dataclasses import dataclass

import config

log = logging.getLogger("portfolio.fmp_cache")

REMOTE_NAME = "fmp_cache.json"


@dataclass(frozen=True)
class Rule:
    ttl_days: int
    filing_bound: bool


RULES = {
    "profile": Rule(30, False),
    "statements": Rule(7, True),       # fmp.fetch: income / balance / cash flow / key metrics
    "estimates": Rule(7, True),        # analyst-estimates, annual
}


@dataclass(frozen=True)
class Use:
    data: object
    source: str            # one provenance line
    hit: bool


def _day(epoch):
    return time.strftime("%Y-%m-%d", time.localtime(epoch))


def stale_reason(entry, rule, newest_filed, now):
    """Why a cached entry may not be served (None = it may)."""
    age = now - entry.get("at", 0)
    if age >= rule.ttl_days * 86400:
        return f"cache {_day(entry.get('at', 0))} older than {rule.ttl_days} days"
    if rule.filing_bound and newest_filed and newest_filed >= _day(entry.get("at", 0)):
        return f"SEC filing {newest_filed} on/after cache {_day(entry.get('at', 0))}"
    return None


class FmpCache:
    def __init__(self, path, remote_name=REMOTE_NAME):
        self.path, self.remote_name = path, remote_name
        self._lock = threading.Lock()
        self._entries = None

    def _load(self):
        """Disk, merged once per process with the Drive copy (newest entry wins)."""
        if self._entries is not None:
            return self._entries
        try:
            with open(self.path, encoding="utf-8") as fh:
                self._entries = json.load(fh).get("entries") or {}
        except (OSError, ValueError):
            self._entries = {}
        try:
            from sources import gdrive_store
            remote = gdrive_store.drive_pull_json(self.remote_name) or {}
        except Exception:
            remote = {}
        for k, e in (remote.get("entries") or {}).items():
            if e.get("at", 0) > self._entries.get(k, {}).get("at", 0):
                self._entries[k] = e
        return self._entries

    def _save(self, now):
        keep = max(r.ttl_days for r in RULES.values()) * 86400
        self._entries = {k: e for k, e in self._entries.items() if now - e.get("at", 0) < keep}
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = f"{self.path}.{os.getpid()}.{threading.get_ident()}.tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"entries": self._entries}, fh)
            os.replace(tmp, self.path)
        except OSError as e:
            log.warning("FMP cache not written: %s", e)
            return
        try:
            import store_sync
            store_sync.schedule_push_named(self.path, self.remote_name)
        except Exception:
            pass

    def get(self, kind, ticker, fetch, ok, newest_filed=None, now=None):
        """Use for `kind` of `ticker`: the cached answer while RULES[kind] allows it,
        else `fetch()`, stored when `ok(answer)`. A failed refetch never falls back to
        the expired entry: an expired statement is exactly what this guards against."""
        rule, now = RULES[kind], time.time() if now is None else now
        key = f"{kind}:{ticker}"
        with self._lock:
            entry = self._load().get(key)
        why = stale_reason(entry, rule, newest_filed, now) if entry else "no cache"
        if entry and why is None:
            filing = f", newest SEC filing {newest_filed}" if rule.filing_bound and newest_filed else ""
            return Use(entry["data"], f"cache {_day(entry['at'])}{filing}", True)
        data = fetch()
        if ok(data):
            with self._lock:
                self._load()[key] = {"at": now, "data": data}
                self._save(now)
        return Use(data, "live" if why == "no cache" else f"refetched: {why}", False)


CACHE = FmpCache(os.path.join(config.CACHE_DIR, "fmp_cache.json"))
