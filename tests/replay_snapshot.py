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

    if update or not os.path.exists(BASELINE):
        with open(BASELINE, "w", encoding="utf-8") as fh:
            json.dump(current, fh, indent=1, sort_keys=True, ensure_ascii=False)
        print(f"baseline {'updated' if update else 'created'}: {len(current)} tickers -> {BASELINE}")
        print("commit it alongside the change so the diff is reviewable.")
        return 0

    with open(BASELINE, encoding="utf-8") as fh:
        base = json.load(fh)
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
