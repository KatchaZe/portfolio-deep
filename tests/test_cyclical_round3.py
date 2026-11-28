"""test_cyclical_round3: a bigger peak never buys a better rating (E98), the stepped
MoS cap (E99), TSLA below its normal margin (E100-E101) and the acquisition series the
through-cycle growth reads (E102). Fixtures in tests/fixtures/cyclical: MU (watchlist,
cached companyfacts) and TSLA (holding: store facts rebuilt on its cached companyfacts)."""
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from domain.engine import deep_v82 as E                   # noqa: E402
from domain.engine.deep_v82 import DeepV82Engine           # noqa: E402
from domain.facts import FinancialFacts                    # noqa: E402
from tests._harness import check, run_suite                # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "cyclical")
FIELDS = set(FinancialFacts.__dataclass_fields__)
RANK = {"SELL / AVOID": 0, "HOLD": 1, "HOLD / Accumulate": 2, "BUY": 3}


def _facts(t, **over):
    d = json.load(open(os.path.join(FIX, t + ".json")))
    d.update(over)
    ff = FinancialFacts(**{k: v for k, v in d.items() if k in FIELDS})
    ff.flags = []
    return ff


def _value(t, **over):
    return DeepV82Engine().evaluate(_facts(t, **over), rf=0.045)


def _mu_oi():
    return json.load(open(os.path.join(FIX, "MU.json")))["operating_income"]


def peak_ladder(value=_value):
    """[(scale, P, composite, rating)] for MU at its fixed $707.70 with today's operating
    income scaled; shared with audit2's A22 guard."""
    oi = _mu_oi()
    out = []
    for x in (0.75, 1.0, 1.15, 1.5):
        v = value("MU", operating_income=oi * x)
        out.append((x, v.P, v.composite, v.recommendation, v.anchor_value))
    return out


def ladder_is_monotone(rows):
    return all(b[1] <= a[1] and b[2] <= a[2] and RANK[b[3]] <= RANK[a[3]]
               for a, b in zip(rows, rows[1:]))


def test_E98_a_bigger_peak_never_raises_P_composite_or_rating():
    rows = peak_ladder()
    check(ladder_is_monotone(rows), "MU OI x0.75 / 1.0 / 1.15 / 1.5 at $707.70: P, composite and "
          "rating never rise", str(rows))


def test_E98_premium_side_low_bands_retired_by_E107():
    v = _value("MU")
    check(v.uncertainty["rating"] != "Low", "the wider range is still shown as risk", str(v.uncertainty))
    notes = v.subscores["breakdown"]
    base = next(n for n in notes if n.startswith("P base("))
    check("จุดพีค" not in base, "E107: P reads the firm's own bands (the Low-band switch was a cliff at the line)", base)
    check(v.P <= 1.25 + 1e-9, "MU P stays 1.0 + FCF 0.25 at MoS -61%", str(v.P))


def test_E98_peak_mc_never_above_the_bull_margin():
    v = _value("MU")
    cases = {c["name"]: c for c in v.scenarios["cases"]}
    st = v.scenarios["story_mc"]["story"]["margin_pct"]
    check(st[2] <= cases["Bull"]["margin_pct"] + 1e-9, "MC upper margin is the Bull (75th pct) margin",
          f"{st} vs Bull {cases['Bull']['margin_pct']}")
    check(st[0] <= cases["Bear"]["margin_pct"], "MC lower end still reaches the trough", str(st))
    mc = v.scenarios["story_mc"]
    check(mc["p50"] <= v.anchor_value * 1.05, "MC p50 no longer sits above the normalized FV",
          f"p50 {mc['p50']} FV {v.anchor_value}")
    check(abs(v.anchor_value - 440.32) / 440.32 < 0.01, "primary FV $440.32 +/-1% (E105: start held to the line)",
          str(v.anchor_value))


def test_E98_peak_held_value_is_the_card_value_E105():
    v = _value("MU")
    check("peak_scored_fv" not in v.key_metrics, "E105: no second FV; the card's FV is held to the line")
    nv = _value("NVDA")
    check("peak_scored_fv" not in nv.key_metrics, "a standard firm has none either")


def test_E99_stepped_cap_constants_and_boundaries():
    check((E.MOS_CAP_ACCUMULATE, E.MOS_CAP_HOLD) == (-0.25, -0.50), "named -25% / -50% steps")
    check(not hasattr(E, "MOS_HOLD_CAP"), "the one-step constant is gone")
    for c in (4.2, 3.5):
        top = E.recommendation(c)
        check(E.recommendation(c, mos=-0.249) == top, f"{c} at -24.9%: {top}")
        check(E.recommendation(c, mos=-0.251) == "HOLD / Accumulate", f"{c} at -25.1%: Accumulate")
        check(E.recommendation(c, mos=-0.499) == "HOLD / Accumulate", f"{c} at -49.9%: Accumulate")
        check(E.recommendation(c, mos=-0.501) == "HOLD", f"{c} at -50.1%: HOLD")
    check(E.recommendation(2.5, mos=-0.9) == "HOLD" and E.recommendation(1.5, mos=-0.9) == "SELL / AVOID",
          "never lifts")


def test_E99_flag_names_the_step_one_decimal_never_across_the_line():
    check(E.mos_text(-0.50283, E.MOS_CAP_HOLD) == "MoS −50.3%", "one decimal, unicode minus")
    check(E.mos_text(-0.50004, E.MOS_CAP_HOLD) == "MoS −50.1%", "just past -50% never prints -50.0%")
    check(E.mos_text(-0.24996, E.MOS_CAP_ACCUMULATE) == "MoS −24.9%", "just inside -25% never prints -25.0%")
    v = _value("MU", price=650.0)
    fl = [x for x in v.flags if "จำกัดคำแนะนำ" in x]
    check(len(fl) <= 1, "one cap flag", str(fl))
    v = _value("MU", price=2000.0)
    fl = [x for x in v.flags if "จำกัดคำแนะนำ" in x]
    check(fl and "−50%" in fl[0] and "ไม่เกิน HOLD" in fl[0] and "Accumulate" not in fl[0].split(":")[0],
          "the -50% step is named", str(fl))


def test_E99_price_sweep_moves_one_notch_at_a_time():
    prev, seen = None, []
    for p in range(300, 1500, 10):
        r = _value("MU", price=float(p)).recommendation
        if prev is not None:
            check(abs(RANK[r] - RANK[prev]) <= 1, f"${p - 10} -> ${p}: {prev} -> {r}")
        if r != prev:
            seen.append((p, r))
        prev = r
    check(len(seen) >= 2, "the sweep crosses at least one cap", str(seen))
    for p in (600, 650, 700, 706, 707.7, 710):
        r = _value("MU", price=float(p)).recommendation
        check(RANK[r] <= RANK["HOLD / Accumulate"], f"MU ${p}: at most HOLD / Accumulate", r)


def test_E100_consensus_years_1_2_rule_only_at_a_peak():
    v = _value("TSLA")
    check(v.key_metrics["cycle"]["at_peak"] is False, "TSLA is not at a peak (4.1% vs normal 8.5%)")
    g = v.dcf["inputs"]["growth_path"]
    check(len(g) == 5 and any(abs(x - 0.045) > 1e-9 for x in g[2:]),
          "TSLA keeps its consensus past year 2", str(g))
    check(not any("consensus ถึงปีที่ 2" in fl for fl in v.flags), "no years-1-2 flag below the peak")
    check(not v.key_metrics["cycle"].get("consensus_years"), "no margin held for consensus years")
    check(v.recommendation == E.NOT_RATED and any("terminal value is 119.8%" in fl for fl in v.flags),
          "the rules as written: TSLA's terminal share over 100% is NOT RATED", v.recommendation)
    mu = _value("MU", rev_growth_path=[[0.70, 25], [0.05, 20], [0.40, 15]])
    check(mu.dcf["inputs"]["growth_path"][:3] == [0.70, 0.05, 0.045], "MU at a peak still uses years 1-2 only")
    check(mu.key_metrics["cycle"]["consensus_years"] == 2, "MU holds today's margin for those 2 years")


def test_E101_through_cycle_growth_spans_the_real_years():
    ff = _facts("TSLA")
    g, note = E.through_cycle_growth(ff)
    revd = sorted(ff.revenue_annuals_dated, reverse=True)
    want = (revd[0][1] / revd[-1][1]) ** (1 / 7) - 1
    check(abs(g - want) < 1e-6 and abs(g - 0.236) < 0.002, "TSLA 2018 -> 2025 is 7 years: ~23.6%/yr",
          f"{g} vs {want}")
    years = E._organic_years(ff)
    gap = next(y for y in years if y[0] == "2021-12-31")
    check(gap[3] == 3, "the 2018 -> 2021 gap spans 3 years", str(gap))
    v = _value("TSLA")
    check(any("through-cycle organic 23.6%/yr over 7 years" in fl for fl in v.flags),
          "the flag states the real span", str([fl for fl in v.flags if "through-cycle" in fl]))


def test_E101_cross_checks_above_fv_carry_no_bear_label():
    v = _value("TSLA")
    for c in v.scenarios.get("cycle_checks") or []:
        if c["value"] > v.anchor_value:
            check("Bear" not in c["label"] and "ค่าเทียบรอบวัฏจักร" in c["label"],
                  f"{c['name']} {c['value']} above FV {v.anchor_value}: neutral label", c["label"])
        else:
            check(c["label"].startswith("Bear cross-check"), f"{c['name']} below FV keeps Bear", c["label"])
    mu = _value("MU")
    check(all(c["label"].startswith("Bear cross-check") for c in mu.scenarios["cycle_checks"]),
          "MU's checks below its FV stay Bear")


def test_E102_acquisitions_from_real_companyfacts():
    from pipeline import normalize
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cf = json.load(open(os.path.join(root, "tests", "fixtures", "AVGO", "sec_companyfacts.json")))
    ff = normalize.build("AVGO", cf)
    acq = dict(ff.acquisitions_annuals_dated)
    check(acq.get("2024-11-03", 0) > 20e9, "AVGO FY2024 VMware cash from PaymentsToAcquireBusinesses",
          str(acq.get("2024-11-03")))
    years = {d: b for d, _, b, *_ in E._organic_years(ff)}
    check(years.get("2024-11-03") is True and years.get("2025-11-02") is False,
          "FY2024 is an acquisition year, FY2025 is not", str(years))
    g, note = E.through_cycle_growth(ff)
    check(note and "2024-11-03" in note, "through-cycle growth leaves FY2024 out", str(note))


def test_E102_no_acquisition_series_is_flagged():
    v = _value("MU")
    check(any("ไม่มีข้อมูลการซื้อกิจการ" in fl for fl in v.flags),
          "a cyclical with no acquisition series says growth may include M&A")
    v = _value("TSLA")
    check(not any("ไม่มีข้อมูลการซื้อกิจการ" in fl for fl in v.flags), "TSLA has the series: no flag")


if __name__ == "__main__":
    run_suite(globals(), "ALL test_cyclical_round3 PASSED")
