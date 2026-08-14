"""
test_invariants — things that must be true of EVERY row, on every ticker, always.

Not a unit test of any one function. Each check below is an identity that holds across
TWO code paths, and every one of them was a real defect first:

  I1  the two "actual revenue growth" figures on a card must be the same number
      (P3-1: the Demand pillar said 14.9% while the reverse DCF printed 29.8%)
  I2  spread must equal ROIC - WACC, on the ROIC the card actually shows
      (D3: the trend delta was built from a raw ROIC against an R&D-adjusted one)
  I3  the anchor fair value must be a value some method actually produced
  I4  the anchor must sit inside the range shown next to it
  I5  the GARP score on the screen must equal the pillars it claims to combine
  I6  upside% must equal (anchor - price)/price
  I7  the watchlist row and the holdings row must carry the same fields
      (`2df2ce2 watchlist parity` — the schema is written twice, 300 lines apart)
  I8  a surprise list must be read newest-by-DATE, whatever order the source sent
      (B6: Yahoo returns newest-first, everything else oldest-first)
  I9  no pillar may score above 5 or below 0, and the composite must lie between the
      pillars that produced it

WHY A SEPARATE FILE
-------------------
Unit tests pin what a function DOES. These pin what the SYSTEM must never do. Three
review rounds found 9 of 16 defects by running real data and noticing two numbers that
should agree did not — this is that method, written down so it runs every time instead
of being reinvented each round.

Run: python -m tests.test_invariants
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from domain import pead                            # noqa: E402
from domain.engine.deep_v82 import DeepV82Engine    # noqa: E402
from domain.facts import FinancialFacts             # noqa: E402
from pipeline import screen                         # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
STORE = os.path.join(ROOT, "data", "portfolio.json")
RF = 0.045
EPS = 0.02          # percentage points: two figures within this are the same number
FAIL = []


def check(ok, label, detail=""):
    print(("  ok   " if ok else "  FAIL ") + label + (("  -> " + detail) if detail and not ok else ""))
    if not ok:
        FAIL.append(label)


def _rows():
    """(ticker, facts, valuation) for every stored ticker. Empty when no store."""
    if not os.path.exists(STORE):
        return []
    with open(STORE, encoding="utf-8") as fh:
        store = json.load(fh)
    fields = set(FinancialFacts.__dataclass_fields__)
    engine = DeepV82Engine()
    out = []
    for t, fd in sorted((store.get("facts") or {}).items()):
        kw = {k: v for k, v in fd.items() if k in fields}
        kw["flags"] = []
        ff = FinancialFacts(**kw)
        try:
            out.append((t, ff, engine.evaluate(ff, rf=RF)))
        except Exception as e:
            FAIL.append(f"{t} crashed: {type(e).__name__}: {e}")
    return out


ROWS = _rows()


def _note(v, prefix):
    return next((n for n in v.subscores.get("breakdown", []) if n.startswith(prefix)), "")


# --------------------------------------------------------------------------
def test_growth_agrees_across_paths():
    """I1 — P3-1. The Demand band and the reverse-DCF 'actual' describe the same
    quantity for the same company; a card cannot show two of them."""
    print("I1 the two 'actual revenue growth' figures agree")
    bad = []
    for t, ff, v in ROWS:
        rd = v.reverse_dcf or {}
        shown = rd.get("actual_1y_pct")
        m = re.search(r"D base\(growth (-?\d+)%\)", _note(v, "D base"))
        if shown is None or not m:
            continue
        # the D note rounds to whole percent, so compare at that resolution
        if abs(float(m.group(1)) - round(shown)) > 1:
            bad.append(f"{t}: D {m.group(1)}% vs RevDCF {shown}%")
    check(not bad, f"same growth on both paths ({len(ROWS)} tickers)", "; ".join(bad))


def test_spread_identity():
    """I2 — D3. spread is a derived value; it must reconcile to the ROIC on the card."""
    print("I2 spread == ROIC - WACC, on the ROIC actually displayed")
    bad = []
    for t, ff, v in ROWS:
        km = v.key_metrics or {}
        roic = km.get("roic_adj_pct") if km.get("roic_adj_pct") is not None else km.get("roic_pct")
        w, sp = km.get("wacc_pct"), km.get("spread_pct")
        if None in (roic, w, sp):
            continue
        if abs((roic - w) - sp) > EPS:
            bad.append(f"{t}: {roic} - {w} != {sp}")
    check(not bad, "reconciles on every ticker", "; ".join(bad))


def test_anchor_is_a_real_method_output():
    """I3 — the anchor must be a number some valuation method produced, not a residue."""
    print("I3 the anchor fair value came from a method that ran")
    bad = []
    for t, ff, v in ROWS:
        if v.anchor_value is None or v.anchor_method == "Terminal-Anchored Reverse DCF":
            continue
        mc = (v.young_dcf or {}).get("monte_carlo") or {}
        cands = [x for x in (v.fv_peg, v.fv_fvp, mc.get("p50")) if x]
        if not any(abs(v.anchor_value - c) < 0.01 for c in cands):
            bad.append(f"{t}: anchor {v.anchor_value} not in {cands}")
    check(not bad, "every anchor traces to PEG / FVP / young-DCF p50", "; ".join(bad))


def test_anchor_inside_its_range():
    """I4 — a range printed beside a point value must contain it."""
    print("I4 range_low <= anchor <= range_high")
    bad = [f"{t}: {v.range_low} / {v.anchor_value} / {v.range_high}"
           for t, ff, v in ROWS
           if v.range_low and v.range_high and v.anchor_value
           and not (v.range_low - 0.01 <= v.anchor_value <= v.range_high + 0.01)]
    check(not bad, "the anchor is always inside the band shown with it", "; ".join(bad))


def test_garp_equals_its_pillars():
    """I5 — the screen recombines pillars the engine already scored; they cannot drift."""
    print("I5 GARP score == E_econ + P")
    bad = []
    for t, ff, v in ROWS:
        g = screen.garp_score(v.E_econ, v.P)
        if g is None:
            # must be because a pillar really is missing, not because it was dropped
            if v.E_econ is not None and v.P is not None:
                bad.append(f"{t}: both pillars present but no GARP score")
            continue
        if abs(g - (v.E_econ + v.P)) > 1e-9:
            bad.append(f"{t}: {g} != {v.E_econ}+{v.P}")
    check(not bad, "screen and engine agree", "; ".join(bad))


def test_upside_identity():
    """I6 — the number the card leads with is arithmetic on two others."""
    print("I6 upside% == (anchor - price) / price")
    bad = []
    for t, ff, v in ROWS:
        if not (v.anchor_value and ff.price):
            continue
        want = (v.anchor_value - ff.price) / ff.price * 100
        m = re.search(r"([-+]?\d+)% upside", v.verdict or "")
        if m and abs(float(m.group(1)) - want) > 1.0:
            bad.append(f"{t}: verdict says {m.group(1)}%, arithmetic says {want:.0f}%")
    check(not bad, "the verdict line reconciles to price and anchor", "; ".join(bad))


def test_row_schema_parity():
    """I7 — the row dict is built twice: analyze_row (watchlist) and portfolio_view
    (holdings). Every field the watchlist emits must exist on the holdings path, or one
    tab silently lacks a feature the other has. This has shipped once already."""
    print("I7 watchlist and holdings rows carry the same fields")
    src = open(os.path.join(ROOT, "pipeline", "refresh.py"), encoding="utf-8").read()

    def keys(fn):
        i = src.index(f"def {fn}(")
        m = re.search(r"\ndef ", src[i + 10:])
        j = i + 10 + (m.start() if m else len(src))
        return set(re.findall(r'"([a-z_][a-z0-9_]*)":', src[i:j]))

    watch, hold = keys("analyze_row"), keys("portfolio_view")
    missing = sorted(watch - hold)
    check(not missing, f"holdings path has every watchlist field ({len(watch)} checked)",
          str(missing))


def test_surprise_lists_are_read_by_date():
    """I8 — B6. Source order is not chronology; the newest quarter is a date question."""
    print("I8 the newest quarter is picked by date, not list position")
    bad = []
    for t, ff, v in ROWS:
        rows = ff.earnings_surprises or getattr(ff, "eps_surprises_backfill", None) or []
        qs = [r.get("quarter") for r in rows if isinstance(r, dict) and r.get("quarter")]
        if len(qs) < 2:
            continue
        want = max(qs)
        got = (pead.latest(rows) or {}).get("quarter")
        if got != want:
            bad.append(f"{t}: latest={got} but newest quarter on file is {want}")
    check(not bad, f"every stored track record resolves to its newest quarter", "; ".join(bad))
    # and the helper must be order-blind, not merely correct on today's data
    a = [{"quarter": "2026-03-31", "grade": "beat"}, {"quarter": "2025-06-30", "grade": "miss"}]
    check(pead.latest(a) == pead.latest(list(reversed(a))),
          "the same rows in either order give the same answer")


def test_composite_is_a_function_of_the_pillars_shown():
    """I13. The composite must be reproducible from the four pillars printed beside it.

    AXON's pillars weight to 1.3250000000000002 and REGN's to 2.575 — both exactly on
    the .005 rounding edge, where the last bits of a float decide whether the card reads
    1.32 or 1.33. Those bits shift with any upstream change that alters no reported
    value, so replay showed a composite moving while all four pillars were
    byte-identical: a contradiction on its face, and an hour spent hunting a cause that
    lived in the float rather than in the logic. Same property I2 demands of ROIC."""
    print("I13 composite reproduces from the pillars on the card")
    from domain.engine import deep_v82 as _E
    bad = []
    for t, ff, v in ROWS:
        if v.composite is None:
            continue
        again = _E.composite({"D": v.D, "E_exec": v.E_exec, "E_econ": v.E_econ, "P": v.P})
        if again is None or abs(round(again, 2) - round(v.composite, 2)) > 1e-9:
            bad.append(f"{t}: shown {v.composite} vs {again}")
    check(not bad, f"every composite recomputes from its own pillars ({len(ROWS)} rows)",
          "; ".join(bad))

    # The two rows that produced the phantom drift, weighting to exactly 1.325 and
    # 2.575. What is asserted is the PROPERTY, not the digit: pinning "1.33" here was
    # my own version of the same mistake, since which side of the boundary a
    # representation lands on is an artifact of binary, not a fact about the company.
    # What must be true is that noise BELOW the printed precision cannot move what is
    # printed.
    for pil in (dict(D=3.25, E_exec=1.5, E_econ=0.5, P=0.75),
                dict(D=0.5, E_exec=2.25, E_econ=3.0, P=3.75)):
        base = round(_E.composite(pil), 2)
        check(round(_E.composite(dict(pil)), 2) == base,
              f"{base} is reproducible from the same pillars")
        for eps in (1e-13, -1e-13, 5e-14):
            jitter = {k: v + eps for k, v in pil.items()}
            check(round(_E.composite(jitter), 2) == base,
                  f"jitter {eps:+g} below the reported precision cannot move {base}",
                  str(round(_E.composite(jitter), 2)))


def test_scores_stay_in_band():
    """I9 — the pillars are 0-5 by definition and the composite is a weighted mean of
    them, so it cannot escape their min/max. An adjustment that overflows shows up here
    before it shows up as a recommendation nobody can explain."""
    print("I9 pillars in [0,5] and the composite between them")
    bad = []
    for t, ff, v in ROWS:
        pillars = [x for x in (v.D, v.E_exec, v.E_econ, v.P) if x is not None]
        for name, x in (("D", v.D), ("E_exec", v.E_exec), ("E_econ", v.E_econ), ("P", v.P)):
            if x is not None and not (0 - 1e-9 <= x <= 5 + 1e-9):
                bad.append(f"{t}.{name}={x}")
        if v.composite is not None and pillars:
            if not (min(pillars) - 1e-6 <= v.composite <= max(pillars) + 1e-6):
                bad.append(f"{t}: composite {v.composite} outside pillars {pillars}")
    check(not bad, f"all {len(ROWS)} rows in band", "; ".join(bad))


def test_roic_basis_is_declared_and_consistent():
    """I10 — G4. D3 was two ROICs subtracted across different bases: the current one
    R&D-capitalized, the prior one raw, reported as a 25.4pp collapse in returns that
    never happened. The basis is now recorded on every row, and the rule is that a trend
    may only be reported when BOTH sides could be measured the same way — `_roic_fy`
    returns None rather than mix, and the row says so."""
    print("I10 the ROIC basis is recorded, and no trend crosses two bases")
    bad = []
    for t, ff, v in ROWS:
        km = v.key_metrics or {}
        basis = km.get("roic_basis")
        if km.get("roic_pct") is None and basis is None:
            continue                                     # nothing measured, nothing to declare
        if basis not in ("raw+leases", "rd_capitalized+leases"):
            bad.append(f"{t}: basis={basis!r}")
            continue
        # if the headline is R&D-capitalized, a reported trend must not have been built
        # from a raw prior year — the engine expresses that by skipping with a flag
        trend_note = _note(v, "E_econ 1y-ROIC-trend")
        skipped = any("ROIC trend skipped" in fl for fl in v.flags)
        if trend_note and skipped:
            bad.append(f"{t}: reports a trend AND says it was skipped")
    check(not bad, f"basis declared on every measurable row ({len(ROWS)} checked)",
          "; ".join(bad))
    bases = {(v.key_metrics or {}).get("roic_basis") for _, _, v in ROWS}
    check(len(bases - {None}) >= 1, "at least one basis is actually in use", str(bases))


def test_contract_scanner_is_clean_and_declared():
    """I11 — the clock/unit contract covers the schema and the production modules pass.
    Duplicated from test_contracts on purpose: this file is the one a reviewer runs, and
    a contract that is only checked in its own test is easy to forget exists."""
    print("I11 clock/unit contract holds across the schema and the engine")
    from domain import contracts
    check(not contracts.undeclared_fields(FinancialFacts), "every field declares a clock",
          str(contracts.undeclared_fields(FinancialFacts)))
    check(not contracts.undeclared_units(FinancialFacts), "every field declares a unit",
          str(contracts.undeclared_units(FinancialFacts)))
    with open(os.path.join(ROOT, "pipeline", "normalize.py"), encoding="utf-8") as fh:
        unconverted = contracts.unconverted_money(fh.read())
    check(not unconverted, "every money field is FX-converted", str(unconverted))
    hits = contracts.scan_all(ROOT)
    check(not hits, "no clock or unit crossing in production code",
          "; ".join(f"{f}:{h[0]} {h[3]}" for f, hs in hits.items() for h in hs))


# --------------------------------------------------------------------------
# MUTATION SELF-TEST
# --------------------------------------------------------------------------
def test_the_invariants_can_actually_fail():
    """An invariant that cannot go red is decoration.

    Every check above passes today, which proves nothing on its own — a check with a
    typo in it also passes. So the two invariants that correspond to defects we actually
    shipped are exercised against those defects, put back in memory for the duration.
    If a refactor ever makes one of them structurally unable to fire, this notices.

    (This is the reason the 46-suite wall was green while P3-1 was live: nobody had
    asked the tests to prove they could detect it.)"""
    print("SELF the invariants are shown to catch the defects they were written for")

    # P3-1: drop the growth the engine passes in, so the reverse DCF recomputes
    # `actual_1y_pct` from a TTM over a fiscal year two periods back — the old behaviour.
    import domain.engine.deep_v82 as eng
    original = eng.reverse_dcf

    def without_supplied_growth(*a, **kw):
        kw.pop("actual_growth", None)
        return original(*a, **kw)

    eng.reverse_dcf = without_supplied_growth
    try:
        broken = _rows()
        bad = []
        for t, ff, v in broken:
            shown = (v.reverse_dcf or {}).get("actual_1y_pct")
            m = re.search(r"D base\(growth (-?\d+)%\)", _note(v, "D base"))
            if shown is not None and m and abs(float(m.group(1)) - round(shown)) > 1:
                bad.append(t)
    finally:
        eng.reverse_dcf = original
    check(len(bad) >= 5, "I1 goes red when P3-1 is reintroduced",
          f"caught only {len(bad)} tickers — the check may no longer be able to fire")

    # B6: read the surprise list by position instead of by date.
    #
    # 2026-08-11: this used to hunt the STORE for a list where rows[-1] is not the newest
    # quarter. After the ordering fix landed, `reconcile_earnings` normalises every list
    # on write, so all 21 came back oldest-first and the mutation had nothing left to
    # bite — the self-test reported "caught []" and went red. A guard that can only fire
    # while the bug is still in the data is not a guard on the CODE. It builds its own
    # newest-first input now, which is the shape Yahoo actually returns.
    newest_first = [{"quarter": "2026-06-30", "grade": "beat"},
                    {"quarter": "2026-03-31", "grade": "miss"},
                    {"quarter": "2025-12-31", "grade": "beat"}]
    original_latest = pead.latest
    pead.latest = lambda rows: (rows[-1] if rows else None)
    try:
        bad2 = []
        broke = (pead.latest(newest_first) or {}).get("quarter")
        if broke != "2026-06-30":
            bad2.append(f"positional read picked {broke}")
        for t, ff, v in ROWS:                    # and the real rows, if any still differ
            rows = ff.earnings_surprises or getattr(ff, "eps_surprises_backfill", None) or []
            qs = [r.get("quarter") for r in rows if isinstance(r, dict) and r.get("quarter")]
            if len(qs) >= 2 and (pead.latest(rows) or {}).get("quarter") != max(qs):
                bad2.append(t)
    finally:
        pead.latest = original_latest
    check((pead.latest(newest_first) or {}).get("quarter") == "2026-06-30",
          "and the real pead.latest reads a newest-first list correctly")
    check(len(bad2) >= 1, "I8 goes red when the ordering fault is reintroduced",
          f"caught {bad2} — expected at least the Yahoo-sourced names")

    # REV-1: a money field dropped out of the FX conversion list. The SBC of an IFRS
    # filer stayed in DKK and was divided by a USD market cap.
    from domain import contracts
    with open(os.path.join(ROOT, "pipeline", "normalize.py"), encoding="utf-8") as fh:
        norm = fh.read()
    check(contracts.unconverted_money(norm.replace('          "sbc",\n', '')) == ["sbc"],
          "the FX check goes red when a money field leaves the conversion list",
          str(contracts.unconverted_money(norm.replace('          "sbc",\n', ''))))

    # G2/G3: the scanner must see a comparison and a call-site argument, not only
    # division. Both were blind spots in the first version.
    import tempfile

    def _scan(src):
        p = tempfile.mktemp(suffix=".py")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(src)
        try:
            return contracts.scan_source(p)
        finally:
            os.unlink(p)

    check(bool(_scan("def g(f):\n    a=f.revenue_annuals\n    return f.revenue > a[1]\n")),
          "the scanner goes red on a cross-clock COMPARISON (G2)")
    check(bool(_scan("def g(f):\n    return reverse_dcf(1, 2, 3, rev_1y=f.revenue)\n")),
          "the scanner goes red on a cross-clock CALL ARGUMENT (G3)")
    check(bool(_scan("def g(f):\n    return f.revenue / f.eps_gaap\n")),
          "the scanner goes red on a cross-UNIT division (G1)")


def main():
    if not ROWS:
        print("no stored facts — invariants need data/portfolio.json; skipping")
        return
    print(f"running system invariants over {len(ROWS)} stored tickers\n")
    for t in (test_growth_agrees_across_paths, test_spread_identity,
              test_composite_is_a_function_of_the_pillars_shown,
              test_anchor_is_a_real_method_output, test_anchor_inside_its_range,
              test_garp_equals_its_pillars, test_upside_identity, test_row_schema_parity,
              test_surprise_lists_are_read_by_date, test_scores_stay_in_band,
              test_roic_basis_is_declared_and_consistent,
              test_contract_scanner_is_clean_and_declared,
              test_the_invariants_can_actually_fail):
        t()
    print()
    if FAIL:
        print("test_invariants FAILED:", "; ".join(FAIL))
        sys.exit(1)
    print("test_invariants OK - every cross-path identity holds")


if __name__ == "__main__":
    main()
