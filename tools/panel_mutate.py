"""Mutation spot-check for the peer-panel guards: break one line in a scratch copy of the
repo, run the suite, and report which tests went red and with what message.

    python3 -m tools.panel_mutate            # the unit-suite mutants
    python3 -m tools.panel_mutate --pp1      # also the PP1 invariant mutant (needs data/portfolio.json)

A mutant is KILLED only when the named guard fails with an assertion (an exception is
reported as ERROR: red, but not the predicted disagreement). The working tree is not touched."""
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PP, MT, FU = "domain/peer_panel.py", "domain/moat_trend.py", "pipeline/fundamentals.py"
SUITE = ["-m", "unittest", "tests.test_peer_panel"]
MUTANTS = [
    # (id, file, old, new, guard expected red)
    ("M1 FY3 by list position (alone: the date check still guards)", PP,
     "win = trend._window(rev, 4)", "win = sorted(rev)[-4:]", "test_E3_fy3_never_reaches"),
    ("M1b FY3 without the date check (alone: trend._window still guards)", PP,
     "if len(win) == 4 and FY3_GAP[0] <= _days(fy0, win[0]) <= FY3_GAP[1]:", "if len(win) == 4:", "test_E3_fy3_never_reaches"),
    ("M1c both FY3 guards removed", PP,
     ("win = trend._window(rev, 4)", "if len(win) == 4 and FY3_GAP[0] <= _days(fy0, win[0]) <= FY3_GAP[1]:"),
     ("win = sorted(rev)[-4:]", "if len(win) == 4:"), "test_E3_fy3_never_reaches"),
    ("M2 gross profit borrowed from another fiscal year", PP,
     '"gross_margin_fy": _ratio(fy0.get("gross_profit"), r0)',
     '"gross_margin_fy": _ratio(fy0.get("gross_profit") or fy1.get("gross_profit"), r0)', "test_E14"),
    ("M3 non-USD peers kept", PP, 'if (p.get("currency") or "USD") != "USD":', "if False:", "test_E8"),
    ("M4 lagging row kept", PP, "elif ref_fy0 and fy0 and _days(ref_fy0, fy0) > FY_LAG_DAYS:", "elif False:", "test_E4"),
    ("M5 stats include the target", PP, "st = {m: stats(r[m] for r in peer_rows) for m in METRICS}",
     "st = {m: stats(r[m] for r in rows) for m in METRICS}", "test_R3"),
    ("M6 one signal is enough for a label", MT, "if len(signals) < LABEL_AT:", "if not signals:", "test_E5_label"),
    ("M7 S2 on a zero base", MT, "if b <= 0:", "if b < 0:", "test_E6_S2"),
    ("M8 coverage boundary off by one", PP, "if cov < COVERAGE_MIN:", "if cov <= COVERAGE_MIN:", "test_coverage_threshold"),
    ("M9 frames CY by majority of days", PP, "CY_SHIFT_DAYS = 180", "CY_SHIFT_DAYS = 183", "test_R10_frames_cy"),
    ("M10 the negative band never votes", MT, "    if value < -band:\n        return -1\n", "", "both_sides"),
    ("M11 no-usable-peers return is silent", PP, '        flags.append("no usable peers: no table")\n', "", "test_E13"),
    ("M12 S4 on shrinking revenue", MT, "if r0 <= 0 or r3 <= 0 or r0 <= r3:", "if r0 <= 0 or r3 <= 0:", "test_E6_S4"),
    ("M14 share change over each year's own filers (unmatched sets)", PP,
     "tn, tt = sum(now[c] for c in both), sum(then[c] for c in both)",
     "tn, tt = sum(now.values()), sum(then.values())", "test_R13_share_change_on_the_matched_set"),
    ("M15 a null metric is silent", PP, "        flags += null_flags(tbl, target, peers)\n", "",
     "test_F3_a_null_metric"),
    ("M16 a failed panel build leaves {}", FU, "        panel_failed(val, e)\n", "        pass\n",
     "test_a_panel_failure"),
]
PP1 = ("M13 the panel nudges the Price pillar", FU,
       "        val.peer_panel, val.moat_trend = peers.panel_and_moat(ff, val, block, universe, today)\n",
       "        val.peer_panel, val.moat_trend = peers.panel_and_moat(ff, val, block, universe, today)\n"
       "        val.P = (val.P or 0) + 0.1\n", "PP1")


def copy_repo(dst):
    shutil.copytree(ROOT, dst, ignore=shutil.ignore_patterns(".git", "__pycache__", "cache"))


def run(cwd, args):
    # no bytecode: a same-size edit inside one second would otherwise run the stale .pyc
    r = subprocess.run([sys.executable, "-B"] + args, cwd=cwd, capture_output=True, text=True, timeout=170,
                       env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    return r.returncode, r.stdout + r.stderr


def red(out):
    """[(kind, test, last line of its traceback)] from unittest output."""
    hits = []
    for m in re.finditer(r"^(FAIL|ERROR): (\S+) .*?\n-+\n(.*?)(?=\n={10,}|\n-{10,}\nRan)", out, re.S | re.M):
        hits.append((m.group(1), m.group(2), m.group(3).strip().splitlines()[-1][:150]))
    return hits


def main(argv):
    tmp = tempfile.mkdtemp(prefix="panel_mutate_")
    work = os.path.join(tmp, "repo")
    copy_repo(work)
    code, out = run(work, SUITE)
    assert code == 0, "the unmutated suite must be green:\n" + out[-800:]
    survived = 0
    for name, path, old, new, guard in MUTANTS:
        p = os.path.join(work, path)
        src = open(p, encoding="utf-8").read()
        mut = src
        for o, n in zip(*((old, new) if isinstance(old, tuple) else ((old,), (new,)))):
            assert mut.count(o) == 1, f"{name}: anchor matches {mut.count(o)} times in {path}"
            mut = mut.replace(o, n)
        open(p, "w", encoding="utf-8").write(mut)
        try:
            _c, out = run(work, SUITE)
        finally:
            open(p, "w", encoding="utf-8").write(src)
        hits = red(out)
        named = [h for h in hits if guard in h[1]]
        verdict = ("KILLED" if any(h[0] == "FAIL" for h in named) else
                   "ERROR (red by exception)" if named else
                   "KILLED BY ANOTHER TEST" if hits else
                   "REDUNDANT GUARD" if "(alone:" in name else "SURVIVED")
        survived += verdict == "SURVIVED"
        print(f"{verdict:26s} {name}  [expected {guard}]")
        for kind, test, line in hits:
            print(f"      {kind} {test}: {line}")
    if "--pp1" in argv:
        name, path, old, new, guard = PP1
        p = os.path.join(work, path)
        src = open(p, encoding="utf-8").read()
        assert src.count(old) == 1, name
        c0, base = run(work, ["-m", "tests.test_invariants"])
        open(p, "w", encoding="utf-8").write(src.replace(old, new))
        _c, out = run(work, ["-m", "tests.test_invariants"])
        pick = lambda o: [l.strip() for l in o.splitlines() if "FAIL" in l and ("composite, pillars" in l or "panel" in l)]
        print(f"{'KILLED' if pick(out) and not pick(base) else 'SURVIVED':26s} {name}  [expected {guard}]")
        for l in pick(out):
            print("      " + l[:220])
        survived += not (pick(out) and not pick(base))
    shutil.rmtree(tmp, ignore_errors=True)
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
