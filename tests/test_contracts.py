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
import re
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


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


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


def test_every_fx_site_is_registered_and_complete():
    """L2. The FX check knew ONE filename. DQ2 added a second conversion block in
    refresh.py and left `eps_gaap` out of it, so TSM's fallback EPS stayed in TWD next
    to a USD ADR price — an $8,612 fair value and a BUY, invisible to a checker that
    only ever opened normalize.py. What is policed now is the SITE LIST itself."""
    print("C1c every currency conversion in the codebase is declared")
    unknown = contracts.unregistered_fx_sites(ROOT)
    check(not unknown,
          f"no unregistered FX conversion site ({len(contracts.FX_SITES)} declared)",
          f"{unknown} — add it to domain/contracts.FX_SITES with its required fields")
    for rel in contracts.FX_SITES:
        missing = contracts.unconverted_money_at(ROOT, rel)
        check(not missing, f"{rel} converts every money field it moves", str(missing))
    # the refresh.py site must stay DERIVED — a hand-typed list is how L2 happened
    required, derived = contracts._fallback_money(ROOT)
    check(derived, "the fallback's FX list is derived from UNIT, not typed out again")
    check("eps_gaap" in required,
          "and eps_gaap is in it — the field that was actually missed (L2)")
    check("shares_diluted" not in required,
          "while shares_diluted is excluded BY UNIT, not by hand (it is a COUNT)")


def test_the_fx_site_check_fires():
    """Mutation: both ways L2 could come back. Nothing is written into the working
    tree — the mutated source is handed to the checker, and the new-site probe lives
    in a throwaway directory."""
    print("C1d and that check is shown to go red")
    src = _read("pipeline", "dataquality.py")
    hand_typed = src.replace("for k in FALLBACK_MONEY:",
                             'for k in ("revenue", "net_income"):')
    check(hand_typed != src, "mutation applied (the loop line reads as expected)")
    missing = contracts.unconverted_money_at(ROOT, "pipeline/dataquality.py",
                                             {"pipeline/dataquality.py": hand_typed})
    check("eps_gaap" in missing,
          "typing the list out again re-exposes eps_gaap (the TSM defect)", str(missing))
    # and FALLBACK_MONEY must stay derived, not become a literal
    dq = _read("pipeline", "dataquality.py")
    literal = re.sub(r"FALLBACK_MONEY\s*=\s*.*?\n\n",
                     'FALLBACK_MONEY = ("revenue", "net_income")\n\n', dq, flags=re.S)
    _, derived = contracts._fallback_money(ROOT, {"pipeline/dataquality.py": literal})
    check(not derived, "a hand-typed FALLBACK_MONEY is refused")
    # a brand-new conversion site must not slip in quietly — probe an isolated tree
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "pipeline"))
        with open(os.path.join(d, "pipeline", "brand_new.py"), "w", encoding="utf-8") as fh:
            fh.write('def go(ff, fx):\n    if ff.currency != "USD":\n'
                     "        ff.revenue = ff.revenue * fx\n")
        check(contracts.unregistered_fx_sites(d) == ["pipeline/brand_new.py"],
              "an unregistered conversion site is reported",
              str(contracts.unregistered_fx_sites(d)))


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


def test_a_rejected_forward_eps_is_not_swapped_for_a_worse_one():
    """L1, found live on TSM 2026-08-10.

    The sanity gate ran on the CONSENSUS forward EPS and not on the SEC-derived value
    that replaced it, so a number rejected at an implied P/E of 1.5x was swapped for
    one at 1.09x. fv_peg came out at $8,612 against a $422 price, the Price pillar
    scored a maximum 5.0 on that "discount", and the row read BUY.

    Numbers below are TSM's real ones: EPS reported in TWD, price quoted in USD."""
    print("L1 the replacement forward EPS must clear the same gate")
    from pipeline import validate                                   # noqa: E402
    from domain.facts import FinancialFacts                         # noqa: E402

    def resolve(**kw):
        f = FinancialFacts("TSM", price=422.44, growth_lt=0.25, **kw)
        validate._resolve_forward_eps(f)
        return f

    f = resolve(forward_eps=277.21, eps_gaap=309.35)
    check(f.forward_eps is None,
          "a fallback that also fails the gate is refused, not substituted",
          f"forward_eps = {f.forward_eps}")
    check(any("rejected too" in x for x in f.flags),
          "and the card says BOTH were rejected", str(f.flags))

    f = resolve(eps_gaap=309.35)                       # no consensus at all
    check(f.forward_eps is None,
          "the no-consensus path is gated too (it used to take the value unchecked)",
          f"forward_eps = {f.forward_eps}")

    f = resolve(forward_eps=1.5, eps_gaap=9.6)         # same EPS, correct currency
    check(f.forward_eps == round(9.6 * 1.25, 2),
          "a SOUND fallback is still used — the gate rejects units, not fallbacks",
          f"forward_eps = {f.forward_eps}")

    f = resolve(forward_eps=12.0, eps_gaap=309.35)     # consensus fine, ignore the rest
    check(f.forward_eps == 12.0, "a passing consensus is untouched", str(f.forward_eps))


def test_per_share_unit_gate_closes_every_method_at_once():
    """L4. Gating one consumer moves the fault to the next one: L1 closed the PEG path
    and the Future Value Projection — which builds its own EPS from net_income/shares —
    printed $5,881 against TSM's $421 instead. The verdict is now reached ONCE, at the
    source, and read where per-share value is created."""
    print("L4 a per-share unit mismatch stops every per-share valuation, not one")
    from pipeline import validate                                   # noqa: E402
    from domain.facts import FinancialFacts                         # noqa: E402
    from domain.engine import deep_v82 as E                         # noqa: E402

    def tsm(**kw):
        base = dict(ticker="TSM", price=421.42, beta=1.1, growth_lt=0.25,
                    revenue=3.0e12, operating_income=1.5e12, net_income=1.6e12,
                    shares_diluted=5.19e9, eps_gaap=309.35, equity=4.0e12,
                    total_debt=1.0e12, cash=2.0e12, cfo=2.0e12, total_assets=7.0e12,
                    market_cap=2.19e12, forward_eps=277.21)
        base.update(kw)
        return FinancialFacts(**base)

    f = tsm()
    validate.validate(f)
    check(f.per_share_unit_ok is False, "the mismatch is detected at the source")
    check(any("หน่วยต่อหุ้น" in x for x in f.flags), "and stated on the card")
    check(f.confidence < 50, f"and the row is no longer trustworthy ({f.confidence})")

    v = E.DeepV82Engine().evaluate(f, rf=0.047)
    check(v.fv_peg is None, "PEG produces nothing (was $8,612)", str(v.fv_peg))
    check(v.fv_fvp is None, "Future Value produces nothing (was $5,881)", str(v.fv_fvp))
    check(not v.young_dcf, "young-company DCF produces nothing (was $8,504 p50)",
          str((v.young_dcf or {}).get("monte_carlo")))
    check(v.anchor_value is None, "no anchor at all", str(v.anchor_value))
    check(v.range_low is None and v.range_high is None,
          "and no range either", f"{v.range_low}-{v.range_high}")
    check("unit mismatch" in (v.anchor_method or ""), "the method says why", v.anchor_method)
    check(v.P is None, "the Price pillar is unmeasurable, not zero and not five", str(v.P))
    check(v.recommendation != "BUY", f"so the row cannot read BUY ({v.recommendation})")

    # a correctly-converted TSM must be entirely unaffected
    ok = tsm(revenue=93.7e9, operating_income=46.9e9, net_income=50.0e9,
             eps_gaap=9.63, equity=125e9, total_debt=31e9, cash=62e9, cfo=62e9,
             total_assets=219e9, market_cap=2.19e11, forward_eps=12.0)
    validate.validate(ok)
    check(ok.per_share_unit_ok is True, "a correctly-converted row is untouched")
    check(ok.forward_eps == 12.0, "and keeps its consensus EPS", str(ok.forward_eps))

    # the boundary, both sides
    check(validate.per_share_unit_mismatch(100.0, 40.0) is not None, "P/E 2.5x rejected")
    check(validate.per_share_unit_mismatch(100.0, 33.0) is None, "P/E 3.03x accepted")
    check(validate.per_share_unit_mismatch(100.0, -5.0) is None,
          "a LOSS has no P/E — never rejected on this test")
    check(validate.per_share_unit_mismatch(100.0, 0.2) is None,
          "a trough year (P/E 500x) is not a unit fault")
    check(validate.per_share_unit_mismatch(None, 40.0) is None, "no price -> silent")


def test_trailing_eps_matches_what_the_engine_actually_uses():
    """I12. The gate must test the number production builds on, not a lookalike of it.
    Checking a reconstruction is how a checker ends up green while the real path is
    wrong — the failure this whole file exists to remove."""
    print("I12 the gate's EPS is the engine's EPS")
    from pipeline import validate                                   # noqa: E402
    from domain.facts import FinancialFacts                         # noqa: E402
    cases = [
        ("net income over diluted shares", dict(net_income=50e9, shares_diluted=5e9,
                                                eps_gaap=99.0), 10.0),
        ("falls back to the filed EPS", dict(net_income=None, shares_diluted=5e9,
                                             eps_gaap=9.5), 9.5),
        ("a break-even year is a measurement", dict(net_income=0.0, shares_diluted=5e9,
                                                    eps_gaap=99.0), 0.0),
        ("no shares -> the filed EPS", dict(net_income=50e9, shares_diluted=None,
                                            eps_gaap=9.5), 9.5),
    ]
    for label, kw, want in cases:
        f = FinancialFacts("T", **kw)
        got = validate.trailing_eps(f.net_income, f.shares_diluted, f.eps_gaap)
        check(got == want, f"{label}: {got}", f"expected {want}")
    src = _read("domain", "engine", "deep_v82.py")
    check("eps0 = earn / f.shares_diluted" in src and "eps0 = f.eps_gaap" in src,
          "and the engine still builds eps0 the same two ways (else this test is stale)")


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
              test_every_fx_site_is_registered_and_complete, test_the_fx_site_check_fires,
              test_production_code_is_clean,
              test_the_checker_catches_p3_1, test_matrix,
              test_a_rejected_forward_eps_is_not_swapped_for_a_worse_one,
              test_per_share_unit_gate_closes_every_method_at_once,
              test_trailing_eps_matches_what_the_engine_actually_uses,
              test_allowed_list_is_scoped_and_short):
        t()
    print()
    if FAIL:
        print("test_contracts FAILED:", "; ".join(FAIL))
        sys.exit(1)
    print("test_contracts OK - clocks declared, enforced, and the enforcer is proven to fire")


if __name__ == "__main__":
    main()
