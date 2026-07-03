"""
test_sec_stale_cache — H1 fix: when the SEC network fetch fails, an EXPIRED
disk cache must be served (flagged "_stale_cache") instead of failing the
ticker; with no cache at all the error still propagates. Offline/synthetic.
"""
import os
import sys
import json
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sources import sec_edgar


class _BoomRequests:
    @staticmethod
    def get(*a, **k):
        raise RuntimeError("network down")


def main():
    with tempfile.TemporaryDirectory() as d:
        cik = "0001045810"
        p = os.path.join(d, f"companyfacts_{cik}.json")
        with open(p, "w") as fh:
            json.dump({"facts": {"us-gaap": {}}}, fh)
        os.utime(p, (0, 0))                      # make the cache LOOK expired
        out = sec_edgar.fetch_companyfacts(cik, "ua test@example.com",
                                           requests_mod=_BoomRequests,
                                           cache_dir=d, ttl_hours=12)
        assert out.get("_stale_cache") is True, "expired cache must be served, flagged"
        assert "facts" in out
        print("stale-cache-served OK")

        # no cache at all -> the network error must still propagate
        try:
            sec_edgar.fetch_companyfacts("0000000001", "ua test@example.com",
                                         requests_mod=_BoomRequests,
                                         cache_dir=d, ttl_hours=12)
            raise AssertionError("should have raised")
        except RuntimeError:
            print("no-cache-still-raises OK")
    print("\nALL SEC-STALE-CACHE TESTS PASSED ✅")


if __name__ == "__main__":
    main()
