"""
Run every test suite.  python run_tests.py

A suite is any tests/test_*.py; each runs in its own `python -m` subprocess, so each
must end in an `if __name__ == "__main__":` block (checked below, since a suite
without one imports cleanly, runs nothing and exits 0). Other modules in tests/ are
helpers or manual tools unless listed in EXTRA. EXTRA are the system-level guards
(see docs/REVIEW_PROCESS.md) and run last.

test_extract / test_engine need the captured fixtures in tests/fixtures/
(run `python -m tools.capture` once, or commit the fixtures to the repo).
"""
import ast
import os
import subprocess
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
SUITE_FILES = sorted(Path(HERE, "tests").glob("test_*.py"))
EXTRA = ["tests.replay_snapshot", "tests.golden_snapshot", "tests.audit2"]
TESTS = ["tests." + p.stem for p in SUITE_FILES] + EXTRA


def _has_main_block(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return any(isinstance(node, ast.If)
               and ast.unparse(node.test) == "__name__ == '__main__'"
               for node in tree.body)


def main():
    failed = ["tests.%s (no __main__ block, so it runs nothing)" % p.stem
              for p in SUITE_FILES if not _has_main_block(p)]
    for t in TESTS:
        print("=" * 60)
        print("RUN", t)
        print("=" * 60)
        r = subprocess.run([sys.executable, "-m", t], cwd=HERE)
        if r.returncode != 0:
            failed.append(t)
    print("\n" + "=" * 60)
    if failed:
        print("FAILED:", ", ".join(failed))
        sys.exit(1)
    print("ALL TEST SUITES PASSED OK")


if __name__ == "__main__":
    main()
