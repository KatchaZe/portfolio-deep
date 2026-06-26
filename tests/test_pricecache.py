"""
test_pricecache — R4 long-price disk cache: write + fresh-read + expiry + stale
fallback + missing-key safety. Pure/offline (temp dir + injected clock).
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import pricecache


def test_write_read_fresh_stale():
    with tempfile.TemporaryDirectory() as d:
        pay = {"closes": [1.0, 2.0, 3.0], "source": "yahoo", "dividend_adjusted": True}
        assert pricecache.write("NVDA", pay, cache_dir=d, now=1000.0) is True

        # fresh within ttl
        assert pricecache.read_fresh("NVDA", ttl_hours=18, cache_dir=d, now=1000.0 + 3600) == pay
        # expired (older than ttl) -> None
        assert pricecache.read_fresh("NVDA", ttl_hours=1, cache_dir=d, now=1000.0 + 7200) is None
        # read_any returns regardless of age
        assert pricecache.read_any("NVDA", cache_dir=d) == pay
        # unknown ticker is safe (no file)
        assert pricecache.read_fresh("ZZZZ", cache_dir=d, now=1000.0) is None
        assert pricecache.read_any("ZZZZ", cache_dir=d) is None
        # ticker sanitisation doesn't crash on odd symbols
        assert pricecache.write("BRK.B", pay, cache_dir=d, now=1000.0) is True
        assert pricecache.read_any("BRK.B", cache_dir=d) == pay
    print("pricecache write/read/fresh/stale OK")


if __name__ == "__main__":
    test_write_read_fresh_stale()
    print("\nALL test_pricecache PASSED")
