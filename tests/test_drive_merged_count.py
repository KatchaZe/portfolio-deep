"""meta.drive_cache_merged must count the names actually taken from the Drive copy,
not every entry in it. Holding A locally while Drive held A and B reported 2."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import prices


def _entry(src="yahoo"):
    return {"returns": [0.01] * 300, "dates": [], "vol": 0.2, "source": src,
            "as_of": "2026-10-15", "n": 300}


def _run(local, remote, tickers, fetched=None):
    saved = (prices._local_good, prices._remote_good, prices._fetch_missing,
             prices._save, prices._mirror_to_drive)
    prices._local_good = lambda key, tickers: dict(local)
    prices._remote_good = lambda key: dict(remote)
    prices._fetch_missing = lambda todo, *a, **k: ({t: _entry() for t in todo}, 0)
    prices._save = lambda *a, **k: None
    prices._mirror_to_drive = lambda *a, **k: None
    try:
        return prices.fetch_returns(tickers, "", 0, 250)[2]
    finally:
        (prices._local_good, prices._remote_good, prices._fetch_missing,
         prices._save, prices._mirror_to_drive) = saved


def test_counts_only_names_taken_from_drive():
    meta = _run({"A": _entry()}, {"A": _entry(), "B": _entry()}, ["A", "B"])
    assert meta["cache"] == "drive-hit", meta
    assert meta["drive_cache_merged"] == 1, meta


def test_unrelated_drive_entries_are_not_counted():
    meta = _run({}, {"A": _entry(), "Z": _entry(), "Y": _entry()}, ["A", "B"])
    assert meta["cache"] == "partial", meta
    assert meta["drive_cache_merged"] == 1, meta


if __name__ == "__main__":
    from tests._harness import run_suite
    run_suite(globals(), "ALL test_drive_merged_count OK")
