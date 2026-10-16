"""Shared runner for the suites run_tests.py starts as `python -m tests.<suite>`.

check() records a failure and keeps going, so one run reports every broken check.
A plain `assert` still stops the suite at the first failure, as it always has.
"""
import os
import sys
import time

FAIL = []


def check(ok, label, detail=""):
    print(("  ok   " if ok else "  FAIL ") + label + (("  -> " + detail) if detail and not ok else ""))
    if not ok:
        FAIL.append(label)


def run_suite(ns, ok_msg):
    """Run the module's own test_* functions in definition order, then report."""
    module = ns["__name__"]
    for name, fn in list(ns.items()):
        if name.startswith("test_") and callable(fn) and getattr(fn, "__module__", None) == module:
            fn()
    print()
    if FAIL:
        suite = os.path.splitext(os.path.basename(ns["__file__"]))[0]
        print(suite + " FAILED:", "; ".join(FAIL))
        sys.exit(1)
    print(ok_msg)


def age_data_files(data_dir, frozen_at):
    """Give every file under data_dir one mtime, 90 days before frozen_at.

    Each cache TTL compares a file's mtime with the clock. A snapshot runs under a
    frozen clock, so a fresh clone or a plain `cp -r` (mtimes later than the frozen
    instant) would flip cached reads into fetches and the snapshot would drift."""
    stamp = time.mktime(time.strptime(frozen_at, "%Y-%m-%d %H:%M:%S")) - 90 * 86400
    for base, _dirs, files in os.walk(data_dir):
        for name in files:
            os.utime(os.path.join(base, name), (stamp, stamp))
