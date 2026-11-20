"""Run sources.sec_edgar.extract over every cached companyfacts file and write one JSON.

usage: python3 -B tools/sec_cache_extract.py APP_ROOT CACHE_DIR OUT.json
APP_ROOT is the checkout whose extract runs (a worktree of another commit works), so the
same files can be extracted before and after a change and diffed with --diff A.json B.json.
"""
import glob
import json
import os
import sys


def run(root, cache, out_path):
    sys.path.insert(0, os.path.abspath(root))
    from sources import sec_edgar
    out = {}
    for f in sorted(glob.glob(os.path.join(cache, "companyfacts_*.json"))):
        t = os.path.basename(f)[len("companyfacts_"):-len(".json")]
        with open(f) as fh:
            cf = json.load(fh)
        try:
            out[t] = sec_edgar.extract(cf)
        except Exception as e:                      # one bad file must not hide the rest
            out[t] = {"_error": repr(e)}
    with open(out_path, "w") as fh:
        json.dump(out, fh, sort_keys=True, default=str)
    print(len(out), "files ->", out_path)


def _same(a, b):
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b))
    return a == b


def diff(a_path, b_path):
    a, b = json.load(open(a_path)), json.load(open(b_path))
    rows = []
    for t in sorted(set(a) | set(b)):
        fa, fb = a.get(t, {}), b.get(t, {})
        for k in sorted(set(fa) | set(fb)):
            if not _same(fa.get(k), fb.get(k)):
                rows.append((t, k, fa.get(k), fb.get(k)))
    return rows


if __name__ == "__main__":
    if sys.argv[1] == "--diff":
        for t, k, va, vb in diff(sys.argv[2], sys.argv[3]):
            s = lambda v: json.dumps(v, default=str)[:160]
            print(f"{t}\t{k}\t{s(va)}\t{s(vb)}")
    else:
        run(*sys.argv[1:4])
