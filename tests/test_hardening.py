"""
test_hardening — production-hardening fixes from REVIEW.md:
  * clean_ticker (M4) strips junk like a stray Thai vowel
  * store.save is atomic + round-trips, and a process LOCK exists (C2)
  * sec_edgar.fetch_companyfacts caches to disk + throttle hook (H2)
Offline/synthetic — no network.
"""
import os
import sys
import json
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import store
from sources import sec_edgar


def test_clean_ticker():
    assert store.clean_ticker("ืNVDA") == "NVDA"      # stray Thai vowel removed
    assert store.clean_ticker("  msft ") == "MSFT"
    assert store.clean_ticker("brk.b") == "BRK.B"
    assert store.clean_ticker("aapl\n") == "AAPL"
    assert store.clean_ticker("ก") == ""               # all-junk -> empty
    print("clean_ticker OK")


def test_atomic_save_roundtrip(tmp):
    config.DATA_DIR = tmp
    store.PATH = os.path.join(tmp, "portfolio.json")
    s = store.load()                       # fresh defaults
    store.set_holding(s, "nvda", 10, 100.0)
    store.save(s)
    # no leftover temp files, live file present
    leftovers = [f for f in os.listdir(tmp) if f.endswith(".tmp")]
    assert leftovers == [], leftovers
    again = store.load()
    assert again["holdings"]["NVDA"]["shares"] == 10
    assert isinstance(store.LOCK, type(threading.RLock())), "store.LOCK must be a re-entrant lock"
    print("atomic save + roundtrip + LOCK OK")


class _FakeResp:
    def __init__(self, payload):
        self._p = payload
    def raise_for_status(self):
        pass
    def json(self):
        return self._p


class _FakeRequests:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0
    def get(self, *a, **k):
        self.calls += 1
        return _FakeResp(self.payload)


def test_companyfacts_cache(tmp):
    fake = _FakeRequests({"facts": {"hello": 1}})
    a = sec_edgar.fetch_companyfacts("1045810", "UA", requests_mod=fake,
                                     cache_dir=tmp, ttl_hours=1, min_interval=0)
    b = sec_edgar.fetch_companyfacts("1045810", "UA", requests_mod=fake,
                                     cache_dir=tmp, ttl_hours=1, min_interval=0)
    assert a == b == {"facts": {"hello": 1}}
    assert fake.calls == 1, f"second call should hit cache, not network (calls={fake.calls})"
    assert os.path.exists(os.path.join(tmp, "companyfacts_0001045810.json"))
    print("companyfacts disk cache OK (network called once)")


if __name__ == "__main__":
    test_clean_ticker()
    with tempfile.TemporaryDirectory() as d:
        test_atomic_save_roundtrip(d)
    with tempfile.TemporaryDirectory() as d:
        test_companyfacts_cache(d)
    print("\nALL HARDENING TESTS PASSED")
