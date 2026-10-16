"""Shared runner for the suites run_tests.py starts as `python -m tests.<suite>`.

check() records a failure and keeps going, so one run reports every broken check.
A plain `assert` still stops the suite at the first failure, as it always has.
"""
import hashlib
import json
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


# The golden and ui baselines are LOCAL (gitignored): they are built from data/, which
# holds the owner's holdings and changes on every refresh or Drive pull. So a baseline
# records the inputs it was pinned on, and a check on different inputs says so instead
# of reporting a drift that code did not cause.
def data_fingerprint(root):
    digest = hashlib.sha1()
    data_dir = os.path.join(root, "data")
    for base, dirs, files in os.walk(data_dir):
        dirs.sort()
        for name in sorted(files):
            path = os.path.join(base, name)
            digest.update(os.path.relpath(path, data_dir).encode("utf-8"))
            with open(path, "rb") as fh:
                digest.update(hashlib.sha1(fh.read()).digest())
    return digest.hexdigest()[:16]


def save_pinned_inputs(stamp_path, root):
    with open(stamp_path, "w", encoding="utf-8") as fh:
        json.dump({"data_sha1": data_fingerprint(root)}, fh)
        fh.write("\n")


def baseline_gate(name, baseline_path, stamp_path, root):
    """None when the check may run; else (exit_code, message) to stop with."""
    if not os.path.exists(baseline_path):
        return 0, (f"{name}: no local baseline at {os.path.relpath(baseline_path, root)} "
                   f"(it is gitignored because it holds portfolio data); skipped. "
                   f"Pin one on a commit you trust: python -m tests.{name} --update")
    try:
        with open(stamp_path, encoding="utf-8") as fh:
            pinned = json.load(fh).get("data_sha1")
    except (OSError, ValueError):
        pinned = None
    current = data_fingerprint(root)
    if pinned != current:
        return 1, (f"{name.upper()} INPUT CHANGED: data/ is not what the baseline was pinned on "
                   f"(pinned {pinned}, now {current}), so a drift would not be a code check. "
                   f"Confirm the code is what you think (git status), then re-pin: "
                   f"python -m tests.{name} --update")
    return None
