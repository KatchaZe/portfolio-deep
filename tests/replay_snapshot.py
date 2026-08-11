"""
replay_snapshot — run the DEEP engine over every stored ticker and diff the result
against a committed baseline. Offline; no network.

WHY THIS EXISTS
---------------
Across three review rounds, 9 of 16 defects were found the same way: run the real data
through, print two numbers that should agree, and notice they don't. None were found by
reading code, and the 46-suite test wall was green before every round — unit tests pin
the behaviour a function HAS, which is no help when the behaviour itself was wrong.

The harness that actually found things lived in /tmp and was rebuilt from scratch every
round. This is that harness, committed, so a change that moves any score on any real
company has to be looked at on purpose instead of discovered three weeks later.

HOW TO USE IT
-------------
    python -m tests.replay_snapshot              # diff against the baseline, non-zero on drift
    python -m tests.replay_snapshot --update     # accept the current output as the new baseline
    python -m tests.replay_snapshot --verbose    # print every value, not just the drift

The baseline is `tests/replay_baseline.json`, committed alongside the code. Updating it
is a deliberate act and shows up in the diff, which is the whole point: the review
question stops being "did anything change?" and becomes "here is exactly what changed,
on which company, and is that what you meant?".

A drift is NOT automatically a failure of the code — most of the time it is the intended
effect of a fix. It IS always a failure of an unexamined change.
"""
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from domain.engine.deep_v82 import DeepV82Engine     # noqa: E402
from domain.facts import FinancialFacts              # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BASELINE = os.path.join(HERE, "replay_baseline.json")
STORE = os.path.join(ROOT, "data", "portfolio.json")
RF = 0.045

# What gets compared. Scores and the recommendation first, because those are what the
# user acts on; the pillar subscores next, because they say WHY a recommendation moved;
# the reverse-DCF pair last, because a shift there means the market-expectation read
# changed even when the verdict did not.
TRACKED = ("composite", "recommendation", "D", "E_exec", "E_econ", "P",
           "anchor_method", "anchor_value", "range_low", "range_high",
           "eq_verdict", "implied_cagr_pct", "actual_1y_pct", "rev_verdict",
           "roic_pct", "wacc_pct", "spread_pct", "growth_pct", "terminal_roic_pct")
# floats this close are the same number — guards against a last-digit wobble creating
# noise that trains everyone to ignore the report
TOL = 1e-6


def _round(v):
    return round(v, 6) if isinstance(v, float) else v


def input_fingerprint(store_path=STORE):
    """What the replay was RUN ON, so a drift can name its own cause.

    2026-08-11. This check reported 225 moved values across all 21 tickers and it looked
    exactly like a catastrophic engine regression. Nothing in the engine had changed:
    starting the app had pulled a newer `portfolio.json` from Google Drive over the local
    one (`Drive: pulled portfolio.json` in the server log), so the replay was comparing
    NEW FACTS against a baseline captured on OLD ONES.

    The harness is meant to answer "did the CODE move a score". Silently answering "the
    inputs are different" in the same words destroys that: 225 unexplainable lines is
    precisely the kind of report people learn to scroll past. So the inputs are
    fingerprinted and the two causes are told apart before a single value is compared."""
    try:
        with open(store_path, encoding="utf-8") as fh:
            store = json.load(fh)
    except (OSError, ValueError):
        return None
    facts = store.get("facts") or {}
    blob = json.dumps(facts, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return {"tickers": len(facts),
            "sha1": hashlib.sha1(blob).hexdigest()[:16],
            "refreshed": sorted(set((store.get("updated") or {}).values()))[-3:]}


def evaluate_all(store_path=STORE):
    """{ticker: {tracked field: value}} for every ticker with stored facts."""
    with open(store_path, encoding="utf-8") as fh:
        store = json.load(fh)
    fields = set(FinancialFacts.__dataclass_fields__)
    engine = DeepV82Engine()
    out = {}
    for ticker, fd in sorted((store.get("facts") or {}).items()):
        kw = {k: v for k, v in fd.items() if k in fields}
        kw["flags"] = []                     # replay clean; stored flags are output, not input
        try:
            v = engine.evaluate(FinancialFacts(**kw), rf=RF)
        except Exception as e:               # a crash IS a result worth recording
            out[ticker] = {"ERROR": f"{type(e).__name__}: {e}"}
            continue
        km = v.key_metrics or {}
        rd = v.reverse_dcf or {}
        row = {"composite": v.composite, "recommendation": v.recommendation,
               "D": v.D, "E_exec": v.E_exec, "E_econ": v.E_econ, "P": v.P,
               "anchor_method": v.anchor_method, "anchor_value": v.anchor_value,
               "range_low": v.range_low, "range_high": v.range_high,
               "eq_verdict": v.eq_verdict,
               "implied_cagr_pct": rd.get("implied_cagr_pct"),
               "actual_1y_pct": rd.get("actual_1y_pct"),
               "rev_verdict": rd.get("verdict")}
        for k in ("roic_pct", "wacc_pct", "spread_pct", "growth_pct", "terminal_roic_pct"):
            row[k] = km.get(k)
        out[ticker] = {k: _round(row.get(k)) for k in TRACKED}
    return out


def diff(baseline, current):
    """[(ticker, field, before, after)] — plus appeared/disappeared tickers."""
    rows = []
    for t in sorted(set(baseline) | set(current)):
        b, c = baseline.get(t), current.get(t)
        if b is None:
            rows.append((t, "<ticker>", "—", "ใหม่")); continue
        if c is None:
            rows.append((t, "<ticker>", "มีอยู่", "หายไป")); continue
        for k in sorted(set(b) | set(c)):
            x, y = b.get(k), c.get(k)
            if isinstance(x, float) and isinstance(y, float) and abs(x - y) <= TOL:
                continue
            if x != y:
                rows.append((t, k, x, y))
    return rows


def main(argv):
    update = "--update" in argv
    verbose = "--verbose" in argv
    if not os.path.exists(STORE):
        print(f"no store at {STORE} — nothing to replay")
        return 0
    current = evaluate_all()

    if verbose:
        for t, row in current.items():
            print(f"{t:7} " + "  ".join(f"{k}={row.get(k)}" for k in
                                        ("composite", "recommendation", "D", "E_exec", "E_econ", "P")))
        print()

    fp = input_fingerprint()
    if update or not os.path.exists(BASELINE):
        with open(BASELINE, "w", encoding="utf-8") as fh:
            json.dump({"_input": fp, "scores": current}, fh, indent=1,
                      sort_keys=True, ensure_ascii=False)
        print(f"baseline {'updated' if update else 'created'}: {len(current)} tickers -> {BASELINE}")
        print(f"  input: {fp}")
        print("commit it alongside the change so the diff is reviewable.")
        return 0

    with open(BASELINE, encoding="utf-8") as fh:
        raw = json.load(fh)
    # baselines written before the fingerprint existed are plain {ticker: {...}}
    base = raw.get("scores") if isinstance(raw.get("scores"), dict) else raw
    base_fp = raw.get("_input") if isinstance(raw, dict) else None

    if base_fp and fp and base_fp.get("sha1") != fp.get("sha1"):
        print("REPLAY INPUT CHANGED — this run is NOT a code check.\n")
        print(f"  baseline ran on   {base_fp.get('tickers')} tickers  sha1 {base_fp.get('sha1')}"
              f"  refreshed {base_fp.get('refreshed')}")
        print(f"  this run ran on   {fp.get('tickers')} tickers  sha1 {fp.get('sha1')}"
              f"  refreshed {fp.get('refreshed')}\n")
        print("data/portfolio.json is not immutable: a refresh rewrites it, and simply")
        print("STARTING THE APP can replace it with the Google Drive copy. Any score that")
        print("moves now may be new facts, not new code — the two are indistinguishable")
        print("from the diff alone.\n")
        print("  1. confirm the CODE is what you think it is (git status / git diff)")
        print("  2. python -m tests.replay_snapshot --update   to re-pin to these inputs")
        print("  3. from then on a drift means the code moved a score, which is the point")
        return 1

    rows = diff(base, current)
    errs = [t for t, r in current.items() if "ERROR" in r]

    if errs:
        print("ROWS THAT CRASHED:")
        for t in errs:
            print(f"  {t}: {current[t]['ERROR']}")
        print()
    if not rows:
        print(f"replay clean — {len(current)} tickers, no tracked value moved")
        return 1 if errs else 0

    print(f"REPLAY DRIFT — {len(rows)} value(s) across {len({r[0] for r in rows})} ticker(s)")
    print("(ไม่ใช่ error เสมอไป — แต่ทุกบรรทัดต้องอธิบายได้ว่าตั้งใจให้เกิด)\n")
    width = max(len(r[1]) for r in rows)
    last = None
    for t, k, x, y in rows:
        if t != last:
            print(f"  {t}")
            last = t
        print(f"     {k:<{width}}  {str(x):>22}  ->  {y}")
    print(f"\nถ้าตั้งใจ: python -m tests.replay_snapshot --update  แล้ว commit baseline ไปพร้อมกับการแก้")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
