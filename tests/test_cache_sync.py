"""test_cache_sync — Drive-shared risk cache (build 2026-07-19b):
the local machine pays the FMP quota once and mirrors risk_cache.json to
Drive; another instance (Render) MERGES the shared cache instead of
re-fetching. Offline — gdrive + fetcher + push hook are monkeypatched."""
import os
import sys
import json
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import prices
from sources import gdrive_store
import store_sync


def _entry(n=80):
    return {"returns": [0.001] * n, "vol": 0.1, "source": "fmp",
            "as_of": "2026-07-17", "n": n}


class _Env:
    """Monkeypatch drive pull / push hook / fetcher; restore on exit."""

    def __init__(self, td, remote):
        self.td, self.remote, self.pushes, self.fetches = td, remote, [], []

    def __enter__(self):
        self._orig = (prices.RISK_CACHE_PATH, prices._fetch_one,
                      prices._last_remote_pull, gdrive_store.drive_pull_json,
                      store_sync.schedule_push_named)
        prices.RISK_CACHE_PATH = os.path.join(self.td, "risk_cache.json")
        prices._last_remote_pull = 0.0
        gdrive_store.drive_pull_json = lambda name: self.remote

        def dead_fetch(t, key, may, days):
            self.fetches.append(t)
            return None, "proxy", False
        prices._fetch_one = dead_fetch
        store_sync.schedule_push_named = lambda p, n: self.pushes.append(n)
        return self

    def __exit__(self, *a):
        (prices.RISK_CACHE_PATH, prices._fetch_one, prices._last_remote_pull,
         gdrive_store.drive_pull_json, store_sync.schedule_push_named) = self._orig


def test_remote_merge_and_push():
    tks = ["AAA", "BBB"]
    remote = {"key": prices.holdings_key(tks), "data": {"AAA": _entry()}}
    with tempfile.TemporaryDirectory() as td, _Env(td, remote) as env:
        d, calls, meta = prices.fetch_returns(tks)
        assert d["AAA"]["n"] == 80, "must come from the Drive-shared cache"
        assert d["BBB"]["n"] == 0            # network dead
        assert calls == 0
        assert meta.get("drive_cache_merged") == 1, meta
        assert env.pushes == ["risk_cache.json"], "good data -> mirrored back"
        saved = json.load(open(prices.RISK_CACHE_PATH))
        assert "AAA" in saved["data"] and "BBB" not in saved["data"]
    print("remote merge + push OK")


def test_remote_stale_key_ignored():
    tks = ["AAA", "BBB"]
    remote = {"key": "2020-01-01:deadbeef0000", "data": {"AAA": _entry()}}
    with tempfile.TemporaryDirectory() as td, _Env(td, remote) as env:
        d, calls, meta = prices.fetch_returns(tks)
        assert d["AAA"]["n"] == 0            # stale remote day must be IGNORED
        assert meta.get("drive_cache_merged") in (0, None)
        assert env.pushes == [], "nothing good -> must NOT overwrite remote"
    print("stale remote key ignored OK")


def test_drive_full_hit_no_fetch():
    tks = ["AAA", "BBB"]
    remote = {"key": prices.holdings_key(tks),
              "data": {"AAA": _entry(), "BBB": _entry(90)}}
    with tempfile.TemporaryDirectory() as td, _Env(td, remote) as env:
        d, calls, meta = prices.fetch_returns(tks)
        assert d["AAA"]["n"] == 80 and d["BBB"]["n"] == 90
        assert calls == 0 and env.fetches == [], "full drive hit -> zero fetches"
        assert meta["cache"] == "drive-hit", meta
    print("drive full-hit OK")


def test_disabled_env_noops():
    """FORCE-disable Drive during this test — the machine running the suite may
    have real GDRIVE_* creds (the fix for the leak found 2026-07-20: the old
    version silently hit the real Drive API and would break once
    risk_cache.json exists remotely)."""
    env_keys = ("GDRIVE_SA_JSON", "GDRIVE_FOLDER_ID", "GDRIVE_OAUTH_CLIENT_ID",
                "GDRIVE_OAUTH_CLIENT_SECRET", "GDRIVE_OAUTH_REFRESH_TOKEN")
    saved = {k: os.environ.pop(k, None) for k in env_keys}
    orig = (gdrive_store._enabled, gdrive_store._service, gdrive_store._file_id)
    gdrive_store._enabled = gdrive_store._service = gdrive_store._file_id = None
    try:
        assert gdrive_store.enabled() is False
        assert gdrive_store.drive_pull_json("risk_cache.json") is None
        assert gdrive_store.drive_push_json("risk_cache.json", "/tmp/x.json") is False
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v
        (gdrive_store._enabled, gdrive_store._service, gdrive_store._file_id) = orig
    print("disabled-env no-ops OK")


if __name__ == "__main__":
    test_remote_merge_and_push()
    test_remote_stale_key_ignored()
    test_drive_full_hit_no_fetch()
    test_disabled_env_noops()
    print("ALL test_cache_sync OK")
