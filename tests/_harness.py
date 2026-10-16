"""Shared runner for the suites run_tests.py starts as `python -m tests.<suite>`.

check() records a failure and keeps going, so one run reports every broken check.
A plain `assert` still stops the suite at the first failure, as it always has.
"""
import os
import sys

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
