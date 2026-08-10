"""
test_contracts — the field-clock contract and the checker that enforces it. Offline.

Three review rounds, one defect family: a value combined with another value measured on
a DIFFERENT clock. P3-1 (TTM ÷ fiscal-year-minus-2), D2 (the same, on margins), D3
(R&D-adjusted ROIC minus raw ROIC), B6 (a list read by position when its order depends
on the source). 48 of 79 fields sit on a clock — 1,128 pairs that can be mismatched, so
the answer cannot be "read more carefully".

Two things are pinned here:

  C1  every FinancialFacts field declares a clock, and every declaration still
      corresponds to a field. Adding a field without answering "measured over what
      period?" fails the build, at the moment the answer is obvious.
  C2  the checker actually fires. Verified against a reconstruction of P3-1 written the
      way it really appeared — through two intermediate variables and a conditional —
      plus a matrix of pairs that must and must not trip it.

C2 is the part that matters. The 46-suite wall was green for weeks while P3-1 was live,
because nobody had asked those tests to prove they could see it.

Run: python -m tests.test_contracts
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from domain import contracts                     # noqa: E402
from domain.facts import FinancialFacts          # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAIL = []


def check(ok, label, detail=""):
    print(("  ok   " if ok else "  FAIL ") + label + (("  -> " + detail) if detail and not ok else ""))
    if not ok:
        FAIL.append(label)


def _scan(src):
    p = tempfile.mktemp(suffix=".py")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(src)
    try:
        return contracts.scan_source(p)
    finally:
        os.unlink(p)


# --------------------------------------------------------------------------
def test_every_field_declares_a_clock():
    print("C1 the contract covers the schema exactly")
    missing = contracts.undeclared_fields(FinancialFacts)
    check(not missing,
          "every FinancialFacts field declares a clock",
          f"undeclared: {missing} — add them to domain/contracts.CLOCK")
    stale = contracts.stale_declarations(FinancialFacts)
    check(not stale, "no clock is declared for a field that no longer exists", str(stale))
    n = sum(1 for v in contracts.CLOCK.values() if v != contracts.STATIC)
    check(n >= 40, f"{n} fields carry a real clock (the exposure being policed)")


def test_production_code_is_clean():
    print("C2 no cross-clock arithmetic in the modules that do the maths")
    hits = contracts.scan_all(ROOT)
    detail = "; ".join(f"{f}:{ln} {a} vs {b} — {why} [{kind}]"
                       for f, hs in hits.items() for ln, a, b, why, kind in hs)
    check(not hits, f"clean across {len(contracts.DEFAULT_TARGETS)} modules", detail)


P31_AS_IT_REALLY_LOOKED = '''
def evaluate(f):
    ann = f.revenue_annuals or []
    rev_1y = ann[1] if len(ann) > 1 else None
    # ... 167 lines of unrelated work ...
    a1 = f.revenue / rev_1y - 1
    return a1
'''


def test_the_checker_catches_p3_1():
    """The whole point. Written the way it actually appeared: an alias, a conditional
    subscript, and the division far away from both."""
    print("C2b the checker fires on a faithful reconstruction of P3-1")
    hits = _scan(P31_AS_IT_REALLY_LOOKED)
    check(len(hits) == 1, "exactly one violation reported", str(hits))
    if hits:
        _, a, b, why, kind = hits[0]
        check({a, b} == {"revenue", "revenue_annuals"} and "TTM" in why and "FY" in why,
              "and it names both fields and both clocks", str(hits[0]))
        check(kind == "binop", "reported as a division/subtraction", kind)


def test_every_field_declares_a_unit():
    print("C1b the unit contract covers the schema, and money is always converted")
    missing = contracts.undeclared_units(FinancialFacts)
    check(not missing, "every field declares a unit", str(missing))
    with open(os.path.join(ROOT, "pipeline", "normalize.py"), encoding="utf-8") as fh:
        src = fh.read()
    check(not contracts.unconverted_money(src),
          "every MONEY field is FX-converted in normalize",
          str(contracts.unconverted_money(src)))
    # REV-1 reproduced: drop sbc from the conversion list and the check must notice
    check(contracts.unconverted_money(src.replace('          "sbc",\n', '')) == ["sbc"],
          "and the check goes red when a money field is dropped (REV-1)")
    check(set(contracts.FX_EXEMPT) <= set(contracts.UNIT),
          "every FX exemption names a real field", str(contracts.FX_EXEMPT))


def test_matrix():
    print("C2c pairs that must and must not trip the checker")
    cases = [
        ("TTM / FY via an inline subscript", True,
         "def g(f):\n    a = f.revenue_annuals\n    return f.cfo / a[0]\n"),
        ("FY / TTM (either direction)", True,
         "def g(f):\n    o = f.operating_income_annuals\n    return o[1] / f.revenue\n"),
        ("QUARTER - FY", True,
         "def g(f):\n    q = f.eps_quarters\n    r = f.rnd_annuals\n    return q - r[0]\n"),
        ("the same pair OUTSIDE its allowed function", True,
         "def other(f):\n    a = f.revenue_annuals\n    r = a[1]\n    return f.revenue / r - 1\n"),
        ("TTM / INSTANT — that is just ROIC", False,
         "def g(f):\n    return f.operating_income / f.equity\n"),
        ("TTM / TTM — the operating margin", False,
         "def g(f):\n    return f.operating_income / f.revenue\n"),
        ("FY / FY — a margin within one year", False,
         "def g(f):\n    a=f.revenue_annuals\n    o=f.operating_income_annuals\n    return o[0]/a[0]\n"),
        ("SPOT / FORWARD — a forward P/E", False,
         "def g(f):\n    return f.price / f.forward_eps\n"),
        ("the documented exception, inside reverse_dcf", False,
         "def reverse_dcf(f):\n    a=f.revenue_annuals\n    r=a[1]\n    return f.revenue / r - 1\n"),
        # G2 — a comparison across clocks reads even more innocently than a division
        ("TTM > FY as a COMPARISON", True,
         "def g(f):\n    a=f.revenue_annuals\n    return f.revenue > a[1]\n"),
        ("TTM > INSTANT — a cash-vs-flow test, fine", False,
         "def g(f):\n    return f.cfo > f.cash\n"),
        # G1 — units
        ("MONEY / PER_SHARE — total over per-share", True,
         "def g(f):\n    return f.revenue / f.eps_gaap\n"),
        ("MONEY / SHARES — that is how per-share is built", False,
         "def g(f):\n    return f.net_income / f.shares_diluted\n"),
        # G3 — the cross-function hole, closed at the call site
        ("a TTM passed into a parameter that means 'prior fiscal year'", True,
         "def g(f):\n    return reverse_dcf(1, 2, 3, rev_1y=f.revenue)\n"),
        ("the right clock passed into the same parameter", False,
         "def g(f):\n    a=f.revenue_annuals\n    return reverse_dcf(1, 2, 3, rev_1y=a[1])\n"),
    ]
    for label, want, src in cases:
        got = bool(_scan(src))
        check(got == want, f"{'catches' if want else 'allows'}: {label}",
              f"expected {'a hit' if want else 'no hit'}, got {got}")


def test_allowed_list_is_scoped_and_short():
    """An exception list is only meaningful while it is small, and a GLOBAL exception
    for the very pair being policed is a hole — the first version of ALLOWED whitelisted
    ("revenue", "revenue_annuals") everywhere and the checker walked past P3-1."""
    print("C2d the exception list stays narrow")
    check(len(contracts.ALLOWED) <= 5,
          f"{len(contracts.ALLOWED)} exception(s) — keep it under 5", str(list(contracts.ALLOWED)))
    unscoped = [k for k in contracts.ALLOWED if k[2] is None]
    check(not unscoped, "no exception is global; each names the function it applies to",
          str(unscoped))
    check(all(isinstance(v, str) and len(v) > 30 for v in contracts.ALLOWED.values()),
          "and each carries a real reason, not a shrug")


def main():
    for t in (test_every_field_declares_a_clock, test_every_field_declares_a_unit,
              test_production_code_is_clean,
              test_the_checker_catches_p3_1, test_matrix, test_allowed_list_is_scoped_and_short):
        t()
    print()
    if FAIL:
        print("test_contracts FAILED:", "; ".join(FAIL))
        sys.exit(1)
    print("test_contracts OK - clocks declared, enforced, and the enforcer is proven to fire")


if __name__ == "__main__":
    main()
