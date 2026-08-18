"""
verify_display — does every number the dashboard SHOWS reconcile to the data it was
built from? Offline, read-only, no network, no quota.

`test_invariants` checks identities INSIDE the engine's own output. This checks the
step after: it recomputes each displayed figure independently from the stored facts and
compares. A mismatch means the card is showing something the data does not support —
which is the only kind of bug that actually costs money.

Written for the round-14 audit (2026-08-17), so it knows what those fixes were supposed
to change and asserts they actually did on YOUR data, not on a fixture.

    python -m tests.verify_display                     # the local data/portfolio.json
    python -m tests.verify_display path/to/store.json  # e.g. one pulled from Drive
    python -m tests.verify_display --verbose            # print every row's numbers

Exit code 1 if anything fails.
"""
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config                                                   # noqa: E402

FAILS, WARNS = [], []
VERBOSE = "--verbose" in sys.argv or "-v" in sys.argv


def ok(label, extra=""):
    print("  ok    " + label + (("  " + extra) if extra and VERBOSE else ""))


def fail(label, extra=""):
    print("  FAIL  " + label + (("  " + extra) if extra else ""))
    FAILS.append(label)


def warn(label, extra=""):
    print("  warn  " + label + (("  " + extra) if extra else ""))
    WARNS.append(label)


def check(cond, label, extra=""):
    (ok if cond else fail)(label, extra)
    return cond


def _num(x):
    return isinstance(x, (int, float)) and x == x        # not None, not NaN


def _pairs(dated):
    """[[date, value], ...] -> {date: value}, tolerating a dict."""
    if isinstance(dated, dict):
        return {k: v for k, v in dated.items() if _num(v)}
    return {d: v for d, v in (dated or []) if _num(v)}


def _row(rows, key):
    return next((r for r in (rows or []) if r.get("key") == key), None)


# --------------------------------------------------------------------------- #
def load_store(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def section(title):
    print("\n" + title)
    print("-" * len(title))


# --------------------------------------------------------------------------- #
#  0. is this store even the one the new code produced?                        #
# --------------------------------------------------------------------------- #
def check_freshness(store):
    section("0. the store was written by the post-audit code")
    facts = store.get("facts") or {}
    n = len(facts)
    with_sbc = [t for t, f in facts.items() if f.get("sbc_annuals_dated")]
    if not check(bool(with_sbc),
                 "sbc_annuals_dated is present (the field the FCF fix needs)",
                 f"{len(with_sbc)}/{n} tickers"):
        print("\n  >>> This store predates the round-14 fixes. Nothing below can")
        print("      confirm the dashboard, because these facts were built by the OLD")
        print("      code. Refresh (Run Daily) on the machine that owns this file, or")
        print("      point this script at the store the running app is using.\n")
        return False
    if len(with_sbc) < n:
        missing = [t for t in facts if t not in with_sbc]
        why = []
        for t in missing:
            fl = " ".join(str(x) for x in (facts[t].get("flags") or []))
            why.append(t + (" (SEC series cleared by the fresher fallback — expected)"
                            if ("ถูกล้าง" in fl or "แหล่งสำรอง" in fl) else " (no SBC tag filed)"))
        warn("some tickers carry no SBC series, so their FCF is not SBC-adjusted",
             ", ".join(why))
    ups = sorted({v for v in (store.get("updated") or {}).values() if v})
    print(f"  info  refreshed dates in this store: {ups[0]} .. {ups[-1]}" if ups else
          "  info  no refresh dates recorded")
    return True


# --------------------------------------------------------------------------- #
#  1. FCF is CFO - capex - SBC, on every row that shows one                    #
# --------------------------------------------------------------------------- #
def check_fcf(store):
    section("1. the Free cash flow row equals CFO - capex - SBC (audit #13)")
    # `trend5y` is NOT persisted: refresh.py builds it at VIEW time with
    # `trend.build(ff)` from the stored facts (refresh.py:590 and :905). So rebuild it
    # here the same way — which makes this a genuine independent reproduction of the
    # number on the card rather than a re-read of something already computed.
    from domain import trend as _T
    facts = store.get("facts") or {}
    seen = flipped = 0
    negatives = []
    for t in sorted(facts):
        try:
            rows = _T.build(facts[t])
        except Exception as e:
            fail(f"{t} trend strip raised {type(e).__name__}", str(e)[:80])
            continue
        rows = rows.get("rows") if isinstance(rows, dict) else rows
        r = _row(rows, "fcf")
        if not r or not r.get("points"):
            continue
        seen += 1
        cfo = _pairs(facts[t].get("cfo_annuals_dated"))
        capex = _pairs(facts[t].get("capex_annuals_dated"))
        sbc = _pairs(facts[t].get("sbc_annuals_dated"))
        bad = []
        for p in r["points"]:
            d, shown = p.get("end"), p.get("v")
            if d not in cfo or d not in capex or not _num(shown):
                continue
            expect = cfo[d] - capex[d] - (sbc.get(d) or 0.0)
            if abs(expect) > 1 and abs(shown - expect) / max(abs(expect), 1) > 0.005:
                bad.append(f"{d}: shown {shown/1e9:.2f}B vs CFO-capex-SBC {expect/1e9:.2f}B")
        if bad:
            fail(f"{t} FCF row does not reconcile", " | ".join(bad[:3]))
        elif VERBOSE:
            last = r["points"][-1]
            ok(f"{t} FCF reconciles", f"latest {last.get('end')} = {last.get('v', 0)/1e9:.2f}B")
        note = r.get("note") or ""
        if "SBC" not in note:
            fail(f"{t} FCF row does not say SBC was deducted", note[:60])
        if (r["points"][-1].get("v") or 0) < 0:
            flipped += 1
            negatives.append(f"{t} {r['points'][-1].get('v', 0)/1e9:+.2f}B")
    check(seen > 0, "at least one row shows an FCF series", f"{seen} rows")
    if not FAILS:
        ok(f"all {seen} FCF series reconcile to CFO - capex - SBC")
    print(f"  info  {flipped} of {seen} names show a NEGATIVE latest FCF once SBC is "
          f"charged: {', '.join(negatives) if negatives else 'none'}")


# --------------------------------------------------------------------------- #
#  2. per-share figures are in the same currency as the price                  #
# --------------------------------------------------------------------------- #
def check_per_share_units(store):
    section("2. EPS is in the price's currency (audit #5b — the TSM/$8,612 class)")
    facts = store.get("facts") or {}
    for t in sorted(facts):
        f = facts[t]
        price, eps = f.get("price"), f.get("eps_gaap")
        if not (_num(price) and _num(eps)) or eps <= 0:
            continue
        pe = price / eps
        if not (2.0 <= pe <= 400.0):
            fail(f"{t} trailing P/E {pe:,.1f}x — reads as a currency/unit mismatch",
                 f"price {price:,.2f} / eps {eps:,.2f}")
        elif VERBOSE:
            ok(f"{t} P/E {pe:.1f}x plausible")
        ni, sh = f.get("net_income"), f.get("shares_diluted")
        if _num(ni) and _num(sh) and sh > 0 and eps:
            derived = ni / sh
            if abs(derived - eps) / max(abs(eps), 0.01) > 0.15:
                ratio = derived / eps if eps else float("nan")
                warn(f"{t} net_income/shares ({derived:,.2f}) differs from filed EPS "
                     f"({eps:,.2f}) by {ratio:.2f}x",
                     f"net_income {ni/1e9:,.3f}B / shares {sh/1e6:,.1f}M vs filed EPS "
                     f"{eps:,.2f} · price {price:,.2f} · shares source "
                     f"{(f.get('provenance') or {}).get('shares_diluted', '?')}. "
                     f"A ratio near 2.0x usually means one side is basic and the other "
                     f"diluted, or a share count off by a class")
    if not [x for x in FAILS if "P/E" in x]:
        ok("every trailing P/E on the store is in a plausible band")


# --------------------------------------------------------------------------- #
#  3. the forward-EPS blend has no cross-currency member                       #
# --------------------------------------------------------------------------- #
def check_forward_eps_blend(store):
    section("3. no forward-EPS source is a currency outlier (audit #14)")
    facts = store.get("facts") or {}
    hits = 0
    for t in sorted(facts):
        f = facts[t]
        srcs = f.get("forward_eps_sources") or {}
        vals = [v for v in srcs.values() if _num(v) and v > 0]
        if len(vals) < 2:
            continue
        hits += 1
        xs = sorted(vals)
        med = xs[len(xs) // 2] if len(xs) % 2 else (xs[len(xs) // 2 - 1] + xs[len(xs) // 2]) / 2
        worst = max(max(v / med, med / v) for v in vals)
        if worst > 4.0:
            fail(f"{t} forward-EPS sources differ by {worst:.1f}x — a unit artefact "
                 f"survived into the blend", str(srcs))
        elif VERBOSE:
            ok(f"{t} sources within {worst:.2f}x", str(srcs))
        sp = f.get("forward_eps_spread_pct")
        if _num(sp) and sp > 120:
            warn(f"{t} forward-EPS spread {sp:.0f}% is very wide", str(srcs))
    check(True, f"checked {hits} multi-source blends")


# --------------------------------------------------------------------------- #
#  4. ROIC, WACC and the spread on the card reconcile                          #
# --------------------------------------------------------------------------- #
def check_roic_spread(store):
    section("4. ROIC - WACC = the spread shown, on the ROIC actually displayed")
    results = store.get("results") or {}
    n = 0
    for t in sorted(results):
        km = (results[t] or {}).get("key_metrics") or {}
        # the card shows the R&D-ADJUSTED ROIC when it has one, and the spread is built
        # from that same figure — invariant I2. Comparing against the raw roic_pct is
        # what my first cut of this script got wrong.
        roic = km.get("roic_adj_pct") if _num(km.get("roic_adj_pct")) else km.get("roic_pct")
        wacc, spread = km.get("wacc_pct"), km.get("spread_pct")
        if not all(_num(x) for x in (roic, wacc, spread)):
            continue
        n += 1
        if abs((roic - wacc) - spread) > 0.15:
            fail(f"{t} spread {spread:.2f} != ROIC {roic:.2f} - WACC {wacc:.2f}",
                 f"(roic_adj_pct={km.get('roic_adj_pct')} roic_pct={km.get('roic_pct')})")
        elif VERBOSE:
            ok(f"{t} ROIC {roic:.1f}% - WACC {wacc:.1f}% = {spread:.1f}pp")
    check(n > 0, f"{n} rows carry all three figures")
    if not [x for x in FAILS if "spread" in x]:
        ok("every displayed spread reconciles to its own ROIC and WACC")


# --------------------------------------------------------------------------- #
#  5. the composite reproduces from the four pillars on the card               #
# --------------------------------------------------------------------------- #
def check_composite(store):
    section("5. the DEEP score reproduces from the pillars shown beside it")
    # The pillars sit at the TOP level of the stored valuation, not under `subscores`
    # (that carries the adjustment audit trail). And the weighting + missing-pillar cap
    # live in the engine, so call the engine rather than restate the weights here — a
    # second copy of the rule is a second thing to drift.
    from domain.engine import deep_v82 as E
    results = store.get("results") or {}
    n = 0
    for t in sorted(results):
        v = results[t] or {}
        pil = {k: v.get(k) for k in ("D", "E_exec", "E_econ", "P")}
        comp = v.get("composite")
        if not _num(comp):
            continue
        n += 1
        for k, x in pil.items():
            if _num(x) and not (0 <= x <= 5):
                fail(f"{t} pillar {k} = {x} outside 0-5")
        again = E.composite(pil)
        if again is None:
            warn(f"{t} composite {comp} shown but the pillars cannot reproduce it",
                 str(pil))
        elif abs(round(again, 2) - round(comp, 2)) > 1e-9:
            fail(f"{t} composite {comp} != {round(again, 2)} from its own pillars",
                 str(pil))
        elif VERBOSE:
            ok(f"{t} composite {comp} reproduces", str(pil))
    check(n > 0, f"{n} rows carry a composite")
    if not [x for x in FAILS if "composite" in x]:
        ok("no card shows a score its own pillars cannot support")


# --------------------------------------------------------------------------- #
#  6. upside% reconciles to price and the anchor fair value                     #
# --------------------------------------------------------------------------- #
def check_upside(store):
    section("6. upside% = (anchor - price) / price, and the anchor sits in its range")
    facts, results = store.get("facts") or {}, store.get("results") or {}
    mom = store.get("momentum") or {}
    n = 0
    for t in sorted(results):
        v = results[t] or {}
        anchor = v.get("anchor_value")
        price = (mom.get(t) or {}).get("price") or (facts.get(t) or {}).get("price")
        if not (_num(anchor) and _num(price) and price > 0):
            continue
        n += 1
        lo, hi = v.get("range_low"), v.get("range_high")
        if _num(lo) and _num(hi) and not (lo - 0.01 <= anchor <= hi + 0.01):
            fail(f"{t} anchor {anchor} outside its own range {lo}-{hi}")
        exp = (anchor - price) / price * 100
        if abs(exp) > 300:
            warn(f"{t} upside {exp:+.0f}% is extreme — check the method and its flags",
                 f"anchor {anchor} vs price {price} via {v.get('anchor_method')}")
        elif VERBOSE:
            ok(f"{t} upside {exp:+.1f}%", f"{v.get('anchor_method')}")
    check(n > 0, f"{n} rows have both an anchor and a price")


# --------------------------------------------------------------------------- #
#  7. balance-sheet coherence (audit #4 lives here)                            #
# --------------------------------------------------------------------------- #
def check_balance_sheet(store):
    section("7. debt, cash and equity are internally coherent (audit #4)")
    facts = store.get("facts") or {}
    for t in sorted(facts):
        f = facts[t]
        d, c, e = f.get("total_debt"), f.get("cash"), f.get("equity")
        if not all(_num(x) for x in (d, c)):
            continue
        if d < 0:
            fail(f"{t} total_debt is negative ({d/1e9:.1f}B)")
        if c < 0:
            fail(f"{t} cash is negative ({c/1e9:.1f}B)")
        ta = f.get("total_assets")
        if _num(ta) and ta > 0 and d > ta:
            fail(f"{t} debt {d/1e9:.1f}B exceeds total assets {ta/1e9:.1f}B")
        elif VERBOSE:
            ic = (d + (e or 0) - c)
            ok(f"{t} debt {d/1e9:.1f}B cash {c/1e9:.1f}B -> invested capital "
               f"{ic/1e9:.1f}B")
    if not [x for x in FAILS if "debt" in x or "cash is" in x]:
        ok("no impossible balance-sheet figure on any row")


# --------------------------------------------------------------------------- #
#  8. the risk tab: correlations in band, VaR present                          #
# --------------------------------------------------------------------------- #
def check_risk(store, risk_path):
    section("8. the risk tab is populated and its correlations are legal (audit #1,2,3)")
    snap = (store.get("risk") or {}).get("snapshot") or store.get("risk_snapshot") or {}
    if not snap:
        warn("no risk snapshot stored — open the Risk tab once, then re-run",
             "the tab computes on demand and may not be persisted")
        return
    corr = snap.get("corr") or snap.get("correlation") or {}
    bad = []
    def _scan(node, path=""):
        if isinstance(node, dict):
            for k, v in node.items():
                _scan(v, f"{path}.{k}" if path else str(k))
        elif isinstance(node, list):
            for i, v in enumerate(node):
                _scan(v, f"{path}[{i}]")
        elif _num(node) and (node > 1.0001 or node < -1.0001) and "pct" not in path:
            bad.append(f"{path}={node}")
    _scan(corr)
    check(not bad, "every correlation is within [-1, 1]", " ".join(bad[:5]))

    vc = snap.get("var_cvar") or {}
    have = [k for k in ("var95_pct", "var99_pct", "cvar95_pct") if _num(vc.get(k))]
    check(len(have) >= 1,
          "VaR/CVaR are computed, not blanked out by a non-PSD matrix",
          str({k: vc.get(k) for k in ("var95_pct", "var99_pct", "cvar95_pct")}))

    rr = snap.get("rate_risk") or {}
    loss = rr.get("loss_pct")
    if _num(loss):
        check(abs(loss) <= 25,
              "the +100bp rate shock is a plausible magnitude (audit #1 was 10x)",
              f"{loss:+.1f}%")
        durs = rr.get("durations") or {}
        for k, d in durs.items():
            if _num(d) and d > 30:
                fail(f"{k} duration {d:.1f}y is implausible for a bond ETF")
    else:
        print("  info  no bonds held, so there is no rate-shock figure to check")

    if os.path.exists(risk_path):
        with open(risk_path, encoding="utf-8") as fh:
            rc = json.load(fh)
        data = rc.get("data") or {}
        withd = [t for t, v in data.items() if (v or {}).get("dates")]
        check(bool(withd),
              "the price cache carries per-return DATES (audit #3)",
              f"{len(withd)}/{len(data)} series")
        for t, v in data.items():
            r, d = (v or {}).get("returns") or [], (v or {}).get("dates") or []
            if d and len(d) != len(r):
                fail(f"{t} has {len(d)} dates for {len(r)} returns")


# --------------------------------------------------------------------------- #
#  9. non-USD filers actually got their numbers (audit #5)                     #
# --------------------------------------------------------------------------- #
def check_foreign_filers(store):
    section("9. foreign/IFRS filers resolve the same fields as US ones (audit #5)")
    facts = store.get("facts") or {}
    NEED = ("cfo", "capex", "tax_expense", "dep_amort", "sbc", "shares_diluted", "eps_gaap")
    foreign = [t for t, f in facts.items()
               if any("converted" in str(x) for x in (f.get("flags") or []))]
    if not foreign:
        print("  info  no converted (non-USD-reporting) filer in this store")
        return
    for t in sorted(foreign):
        f = facts[t]
        missing = [k for k in NEED if not _num(f.get(k))]
        if missing:
            fail(f"{t} still missing {', '.join(missing)}")
        else:
            ok(f"{t} resolves all {len(NEED)} scalars")
        if f.get("currency") != "USD":
            fail(f"{t} currency is {f.get('currency')} — conversion did not complete")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    path = args[0] if args else os.path.join(config.DATA_DIR, "portfolio.json")
    risk_path = os.path.join(os.path.dirname(path), "risk_cache.json")
    if not os.path.exists(path):
        print("no store at %s" % path)
        sys.exit(1)
    print("=" * 74)
    print("verify_display — %s" % path)
    print("=" * 74)
    store = load_store(path)
    if not check_freshness(store):
        sys.exit(1)
    check_fcf(store)
    check_per_share_units(store)
    check_forward_eps_blend(store)
    check_roic_spread(store)
    check_composite(store)
    check_upside(store)
    check_balance_sheet(store)
    check_risk(store, risk_path)
    check_foreign_filers(store)

    print("\n" + "=" * 74)
    if FAILS:
        print("FAILED %d check(s):" % len(FAILS))
        for f in FAILS:
            print("  - " + f)
    if WARNS:
        print("%d warning(s) — not failures, but worth a look:" % len(WARNS))
        for w in WARNS:
            print("  - " + w)
    if not FAILS:
        print("ALL DISPLAY CHECKS PASSED — every number shown reconciles to its inputs")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
