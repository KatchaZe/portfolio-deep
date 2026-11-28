"""test_cyclical_round4: one fair value for a cyclical at a peak (E105), returns on the
capped margin (E106), a class that today's margin does not decide and a value with no
step at the peak line (E107). Fixtures in tests/fixtures/cyclical: MU (watchlist, cached
companyfacts, no consensus), MUC (MU with a five-year consensus path, built here) and SYN
(synthetic: MU's non-SEC fields on a $15B firm at 45% vs a 13% normal, terminal ROIC
below the sector ceiling)."""
import copy
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from domain import company_type as CT                      # noqa: E402
from domain.engine import deep_v82 as E                   # noqa: E402
from domain.engine.deep_v82 import DeepV82Engine           # noqa: E402
from domain.facts import FinancialFacts                    # noqa: E402
from tests._harness import check, run_suite                # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "cyclical")
FIELDS = set(FinancialFacts.__dataclass_fields__)
RANK = {"NOT RATED": -1, "SELL / AVOID": 0, "HOLD": 1, "HOLD / Accumulate": 2, "BUY": 3}
MUC_PATH = [[0.45, 25], [0.15, 25], [0.08, 12], [0.06, 12], [0.05, 12]]
LADDER = [round(0.3 + 0.05 * i, 2) for i in range(35)]


def _raw(t):
    if t == "MUC":
        d = json.load(open(os.path.join(FIX, "MU.json")))
        d["rev_growth_path"] = MUC_PATH
        return d
    return json.load(open(os.path.join(FIX, t + ".json")))


def _facts(t, **over):
    d = _raw(t)
    d.update(over)
    ff = FinancialFacts(**{k: v for k, v in d.items() if k in FIELDS})
    ff.flags = []
    return ff


def _value(t, **over):
    return DeepV82Engine().evaluate(_facts(t, **over), rf=0.045)


def _line(t):
    return CT.peak_line(E.cycle_of(_facts(t)))


def ladder(t, value=_value):
    """[(x, margin, at_peak, FV, P, composite, rating, type)] with today's operating
    income scaled x0.30..x2.00 at the fixture's price; shared with audit2's A24 guard."""
    oi, rev = _raw(t)["operating_income"], _raw(t)["revenue"]
    out = []
    for x in LADDER:
        v = value(t, operating_income=oi * x)
        c = (v.dcf.get("sources") or {}).get("cycle") or {}
        out.append((x, oi * x / rev, bool(c.get("at_peak")), v.anchor_value, v.P, v.composite,
                    v.recommendation, v.company_type))
    return out


def fv_jumps(rows, tol=0.05):
    return [(a[0], b[0], a[3], b[3]) for a, b in zip(rows, rows[1:])
            if not (a[3] and b[3]) or abs(b[3] / a[3] - 1) > tol]


def peak_improvements(rows):
    """Steps into or within the peak (the larger margin at or above the line) where P,
    composite or rating rise. Below the line a higher margin is a better year, not a
    peak, and is valued like any firm's (the DCF starts at it)."""
    return [(a[0], b[0]) for a, b in zip(rows, rows[1:])
            if b[2] and ((b[4] or 0) > (a[4] or 0) + 1e-9 or (b[5] or 0) > (a[5] or 0) + 1e-9
                         or RANK[b[6]] > RANK[a[6]])]


def test_E105_one_fair_value_for_MU():
    v = _value("MU")
    check(abs(v.anchor_value - 440.32) < 0.5, "MU FV $440.32 (start held to 1.5x normal)", str(v.anchor_value))
    km = v.key_metrics
    check("peak_scored_fv" not in km and "mos_scored_pct" not in km, "no second, hidden FV", str(sorted(km)))
    check(not hasattr(E, "_peak_scored_value"), "the peak-held FV helper is gone")
    cases = {c["name"]: c["value"] for c in v.scenarios["cases"]}
    check(abs(cases["Base"] - v.anchor_value) < 0.01, "Base == the FV on the card", str(cases))
    check(0 < cases["Bear"] <= cases["Base"] <= cases["Bull"], "Bear <= Base <= Bull, Bear above $0", str(cases))
    mc = v.scenarios["story_mc"]
    check(mc["p10"] > 0, "MC p10 above $0 (Bear/MC shifted by the cap, not a negative lease offset)", str(mc["p10"]))
    check(v.recommendation != "NOT RATED", "MU stays rated", v.recommendation)
    c = v.key_metrics["cycle"]
    check(c["at_peak"] and c.get("peak_line_pct", 0) - 0.01 < c.get("start_margin_pct", 99) < c.get("peak_line_pct", 0) + 2,
          "the card shows the capped start (the line plus the lease add-back)", str(c))
    check(any("valued from" in fl for fl in v.flags), "the flag says the start is capped")
    for x in (0.6, 1.0, 1.5, 2.0):
        w = _value("MU", operating_income=_raw("MU")["operating_income"] * x)
        check(abs(w.anchor_value - 440.32) < 0.5, f"MU OI x{x}: the same FV", str(w.anchor_value))


def test_E105_mos_cap_and_P_read_the_card_fv():
    v = _value("MU")
    mos = (v.anchor_value - _raw("MU")["price"]) / v.anchor_value
    fl = [x for x in v.flags if "จำกัดคำแนะนำ" in x]
    check(all("peak-held" not in x for x in fl), "the cap flag names no second FV", str(fl))
    base = next(n for n in v.subscores["breakdown"] if n.startswith("P base("))
    check(f"{mos*100:+.0f}%" in base, "P's MoS is the card's MoS", base)
    rd = v.reverse_dcf
    check(rd.get("model") == "ginzu", "reverse DCF solves the same capped model", str(rd.get("model")))


def test_E106_terminal_roic_and_econ_flat_above_the_line():
    for t in ("SYN", "MU"):
        oi = _raw(t)["operating_income"]
        vals = [_value(t, operating_income=oi * x) for x in (0.6, 1.0, 2.0)]
        rs = [w.dcf["inputs"]["roic_stable"] for w in vals]
        check(max(rs) - min(rs) < 1e-9, f"{t}: terminal ROIC the same at x0.6 / 1.0 / 2.0", str(rs))
        ec = [w.E_econ for w in vals]
        check(max(ec) - min(ec) < 1e-9, f"{t}: E_econ the same at x0.6 / 1.0 / 2.0", str(ec))
    syn = _value("SYN")
    check(syn.dcf["inputs"]["roic_stable"] < _facts("SYN").terminal_roic_sector - 0.01,
          "SYN sits below the sector ROIC ceiling (the case MU hid)", str(syn.dcf["inputs"]["roic_stable"]))
    check(any("ROIC measured on EBIT" in fl for fl in syn.flags), "the flag says ROIC reads the capped EBIT")
    oi = _raw("MU")["operating_income"]
    a = _value("MU", operating_income=oi * 0.65, price=525.0)
    b = _value("MU", operating_income=oi * 0.70, price=525.0)
    check(RANK[b.recommendation] <= RANK[a.recommendation] and b.composite <= a.composite + 1e-9,
          "MU at $525: x0.70 never rates above x0.65", f"{a.recommendation} {a.composite} -> {b.recommendation} {b.composite}")


def test_E107_class_does_not_read_todays_margin():
    for t in ("MU", "SYN"):
        rows = ladder(t)
        check(all(r[7] == "cyclical" for r in rows), f"{t} cyclical at every step x0.30..x2.00",
              str([(r[0], r[7]) for r in rows if r[7] != "cyclical"]))
    m = E._margin_history(_facts("MU"))
    revd = sorted(_facts("MU").revenue_annuals_dated, reverse=True)
    oi, rev = _raw("MU")["operating_income"], revd[0][1]
    check(CT.classify("Semiconductor", 0.2 * rev, rev, m, revd)[0] is CT.CompanyType.CYCLICAL,
          "MU at 20% today (under its normal) is still CYCLICAL")
    never = [m[i] if i >= 3 else min(m[i], 0.20) for i in range(len(m))]
    check(CT.classify("Semiconductor", oi, rev, never, revd)[0] is CT.CompanyType.STANDARD,
          "(c) a fall whose margin never came back to normal: STANDARD (a decline, not a cycle)")


def test_E107_no_step_at_the_peak_line():
    for t in ("MU", "MUC", "SYN"):
        line, base = _line(t), _facts(t)
        out = []
        for e in (-1e-6, 1e-6):
            v = _value(t, operating_income=(line + e) * base.revenue)
            sc, mc = {c["name"]: c["value"] for c in v.scenarios["cases"]}, v.scenarios["story_mc"]
            out.append((v.anchor_value, sc["Bear"], sc["Bull"], mc["p10"], mc["p50"], mc["p90"],
                        v.uncertainty["rating"], v.P, v.E_econ, v.composite, v.recommendation))
        a, b = out
        same = all(abs(x - y) <= 0.001 * max(abs(x), 1) if isinstance(x, float) else x == y
                   for x, y in zip(a, b))
        check(same, f"{t}: FV, Bear, Bull, MC, uncertainty, P, E_econ, rating equal just under / over the line",
              f"{a} vs {b}")


def test_E107_ladder_continuous_and_never_better_on_a_bigger_peak():
    for t in ("MU", "MUC", "SYN"):
        rows = ladder(t)
        check(not fv_jumps(rows), f"{t}: no FV step > 5% x0.30..x2.00", str(fv_jumps(rows)))
        check(not peak_improvements(rows), f"{t}: P, composite, rating never rise as the peak grows",
              str(peak_improvements(rows)))
        above = [r[3] for r in rows if r[2]]
        check(above and max(above) - min(above) < 0.01, f"{t}: one FV at and above the line", str(above[:3]))


def test_E107_price_sweep_never_better_at_a_higher_price():
    for t in ("MU", "MUC", "SYN", "TSLA"):
        p0 = _raw(t)["price"]
        prev = None
        for s in [0.5 + 0.05 * i for i in range(21)]:
            r = _value(t, price=p0 * s).recommendation
            if prev is not None and r != "NOT RATED" and prev != "NOT RATED":
                check(RANK[r] <= RANK[prev], f"{t} x{s:.2f} price: {prev} -> {r}")
            prev = r


if __name__ == "__main__":
    run_suite(globals(), "ALL test_cyclical_round4 PASSED")
