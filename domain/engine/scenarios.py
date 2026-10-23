"""
Scenarios and priced-in inputs — two cross-checks that re-run the primary model.

Damodaran: a point value hides the story; show what the price has to believe, and the
value across the stories that could happen. Both run the SAME model the anchor came
from (fcff_dcf.value for an operating company, young_dcf.going_concern for a
pre-profit one) with one input changed. Neither moves the anchor or the Price pillar
(user decision 2026-10-22: cross-check only).

priced_in(...)   the input the price implies, the others held:
                 - target operating margin at consensus growth
                 - first-year growth at the model's margin, as year-10 revenue x today
scenarios(...)   Bear / Base / Bull from the firm's own history, 25/50/25:
                 operating firm  margin = min / model / max of its last 5 years
                                 growth = consensus -/+ max(2pp, 20% of the year)
                 pre-profit      margin = industry / model / 2x industry
                                 first-year growth -/+ 15pp
                 expected value, and the Bull probability the price implies with
                 Bear:Base held 1:2.

Pure: no I/O.
"""
from . import fcff_dcf, young_dcf

PROBS = (0.25, 0.50, 0.25)
GROWTH_SHIFT_MIN, GROWTH_SHIFT_SHARE = 0.02, 0.20
YOUNG_GROWTH_SHIFT = 0.15
MARGIN_LO, MARGIN_HI = 0.005, 0.80
HISTORY_YEARS, HISTORY_MIN = 5, 3
BEAR_MARGIN_FLOOR = 0.5


def _bisect(fn, target, lo, hi, n=60):
    """x in [lo, hi] with fn(x) = target for an increasing fn; None when target is
    outside [fn(lo), fn(hi)] (returned as ('below'|'above', None))."""
    flo, fhi = fn(lo), fn(hi)
    if flo is None or fhi is None:
        return None, "not computable"
    if fhi <= flo:
        # value falls as the input rises: on this margin and capital need, more
        # growth destroys value (return on new capital below the cost of capital)
        return None, "decreasing"
    if target < flo:
        return None, "below"
    if target > fhi:
        return None, "above"
    for _ in range(n):
        mid = (lo + hi) / 2
        v = fn(mid)
        if v is None or v < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2, None


def _shift(path, sign):
    return [g + sign * max(GROWTH_SHIFT_MIN, GROWTH_SHIFT_SHARE * abs(g)) for g in path]


def _dcf(kw, **over):
    out = fcff_dcf.value(**dict(kw, **over))
    return out["per_share"] if out and out.get("per_share") else (0.0 if out else None)


def _rev10(out):
    return out["path"][-1]["revenue"] if out and out.get("path") else None


def _implied_bull(vals, price):
    bear, base, bull = vals
    rest = (bear + 2 * base) / 3
    if bull <= price:
        return None, "every scenario is worth less than the price"
    if rest >= price:
        return 0.0, "Bear and Base alone cover the price"
    return round((price - rest) / (bull - rest), 3), None


def _pack(names, values, inputs, price, basis):
    exp = sum(p * v for p, v in zip(PROBS, values))
    pb, why = _implied_bull(values, price)
    return {"basis": basis, "probs": list(PROBS),
            "cases": [dict(name=n, value=round(v, 2), **i) for n, v, i in zip(names, values, inputs)],
            "expected": round(exp, 2),
            "mos": round((exp - price) / exp, 4) if exp > 0 else None,
            "implied_bull_prob": pb, "implied_bull_note": why,
            # where growth destroys value (RKLB at an 8.65% margin: return on new
            # capital below its cost) the slower Bear story is worth MORE than Base
            "growth_destroys_value": values[0] > values[1]}


def operating(kw, price, margin_history, margin_now_unadjusted=None):
    """(priced_in, scenarios) for an FCFF DCF firm. `kw` is the anchor's own
    fcff_dcf.value() input; `margin_history` the firm's annual margins, newest first
    (one-time add-backs applied), without the lease-interest add-back the DCF makes,
    which is `kw["margin_now"] - margin_now_unadjusted`."""
    if not kw or not price:
        return None, None
    m_model = kw["margin_target"]
    base = fcff_dcf.value(**kw)
    if not base or not base.get("per_share"):
        return None, None
    # priced-in margin at consensus growth
    m_star, why_m = _bisect(lambda m: _dcf(kw, margin_target=m), price, MARGIN_LO, MARGIN_HI)
    # priced-in first-year growth at the model margin (held 5 years, then the model's fade)
    def at_growth(x):
        return _dcf(kw, growth_path=[x] * 5, g_high=x, fade_start_cap=None)
    g_star, why_g = _bisect(at_growth, price, -0.3, 1.0)
    rev10 = None
    if g_star is not None:
        rev10 = _rev10(fcff_dcf.value(**dict(kw, growth_path=[g_star] * 5, g_high=g_star,
                                             fade_start_cap=None)))
    priced = {"margin_pct": round(m_star * 100, 2) if m_star is not None else None,
              "margin_note": why_m,
              "margin_now_pct": round(kw["margin_now"] * 100, 2),
              "margin_model_pct": round(m_model * 100, 2),
              "growth_pct": round(g_star * 100, 1) if g_star is not None else None,
              "growth_note": why_g,
              "rev10_x": round(rev10 / kw["revenue"], 1) if rev10 else None}
    # scenarios: margins from the firm's own last five years (lease adjustment carried)
    lease_adj = (kw["margin_now"] - margin_now_unadjusted) if margin_now_unadjusted is not None else 0.0
    hist = [m for m in (margin_history or [])[:HISTORY_YEARS] if m is not None]
    if len(hist) >= HISTORY_MIN:
        lo = min(min(hist) + lease_adj, kw["margin_now"])
        hi = max(hist) + lease_adj
        basis = "margin = ต่ำสุด / โมเดล / สูงสุดใน 5 ปี (รวม TTM)"
    else:
        lo, hi = 0.8 * m_model, 1.2 * m_model
        basis = "margin = โมเดล ∓20% (ประวัติไม่ถึง 3 ปี)"
    # a loss year or a charge-laden year is not a steady-state story: TMDX's -130%
    # margins from its pre-profit years made every Bear and most Monte Carlo draws
    # worth zero. Bear is floored at half the model margin, and the floor is reported.
    floor = BEAR_MARGIN_FLOOR * m_model
    floored = lo < floor
    lo = max(lo, floor)
    lo, hi = min(lo, m_model), max(hi, m_model)
    if floored:
        basis += " · Bear ไม่ต่ำกว่าครึ่งหนึ่งของ margin โมเดล (ปีขาดทุน/ปีพิเศษไม่ใช่ steady state)"
    path = kw.get("growth_path") or [kw["g_high"]]
    cases = [("Bear", dict(margin_target=lo, growth_path=_shift(path, -1))),
             ("Base", {}),
             ("Bull", dict(margin_target=hi, growth_path=_shift(path, +1)))]
    values, inputs = [], []
    for _, over in cases:
        v = _dcf(kw, **over)
        values.append(v or 0.0)
        g = (over.get("growth_path") or path)
        inputs.append({"margin_pct": round(over.get("margin_target", m_model) * 100, 2),
                       "growth_y1_pct": round(g[0] * 100, 1)})
    pack = _pack([c[0] for c in cases], values, inputs, price,
                 basis + " · growth = consensus ∓ มากกว่าระหว่าง 2pp กับ 20% ของปีนั้น")
    pack["story_mc"] = story_mc_operating(kw, price, lo, hi)
    return priced, pack


def young(ydcf, price, industry_margin=None):
    """(priced_in, scenarios) for the young-company DCF: failure-adjusted value
    p_survival x going concern + (1 - p_survival) x distress, the anchor's own form."""
    d = (ydcf or {}).get("model")
    if not d or not price:
        return None, None
    p, distress = ydcf.get("p_survival") or 0.0, ydcf.get("distress_per_share") or 0.0

    def val(**over):
        g = young_dcf.going_concern(dict(d, **over))
        return None if g is None else p * g["going_concern_per_share"] + (1 - p) * distress
    m_star, why_m = _bisect(lambda m: val(target_margin=m), price, MARGIN_LO, MARGIN_HI)
    g_star, why_g = _bisect(lambda g: val(g_high=g), price, 0.0, 3.0)
    rev10 = None
    if g_star is not None:
        rev10 = young_dcf.going_concern(dict(d, g_high=g_star))["rows"][-1]["revenue"]
    priced = {"margin_pct": round(m_star * 100, 2) if m_star is not None else None,
              "margin_note": why_m,
              "margin_now_pct": round(d["current_margin"] * 100, 2),
              "margin_model_pct": round(d["target_margin"] * 100, 2),
              "growth_pct": round(g_star * 100, 1) if g_star is not None else None,
              "growth_note": why_g,
              "rev10_x": round(rev10 / d["current_revenue"], 1) if rev10 else None}
    ind = industry_margin if (industry_margin and industry_margin > 0) else d["target_margin"]
    lo, hi = min(ind, d["target_margin"]), max(2 * ind, d["target_margin"])
    gh = d["g_high"]
    cases = [("Bear", dict(target_margin=lo, g_high=max(0.0, gh - YOUNG_GROWTH_SHIFT))),
             ("Base", {}),
             ("Bull", dict(target_margin=hi, g_high=gh + YOUNG_GROWTH_SHIFT))]
    values, inputs = [], []
    for _, over in cases:
        v = val(**over)
        values.append(v or 0.0)
        inputs.append({"margin_pct": round(over.get("target_margin", d["target_margin"]) * 100, 2),
                       "growth_y1_pct": round(over.get("g_high", gh) * 100, 1)})
    pack = _pack([c[0] for c in cases], values, inputs, price,
                 "margin = อุตสาหกรรม / โมเดล / 2× อุตสาหกรรม · growth ปีแรก ∓15pp · Base = ค่าจุด (FV หลักคือ p50 ของ Monte Carlo)")
    pack["story_mc"] = story_mc_young(ydcf, price, lo, hi)
    return priced, pack


# --------------------------------------------------------------------------- #
#  Story Monte Carlo (Damodaran item 6)                                        #
# --------------------------------------------------------------------------- #
# The 25/50/25 scenarios are three points. The story Monte Carlo samples the same
# story inputs continuously, between the same Bear and Bull bounds, and answers "what
# share of the stories is worth the price". It is not the young-company band (that
# one samples around ONE story to measure our input noise). Seeded: the same facts
# give the same answer. Cross-check only.
MC_SIMS, MC_SEED, MC_BINS = 1000, 20261023, 20
S2C_SPREAD, WACC_SPREAD, SURVIVAL_SPREAD = 0.25, 0.01, 0.20


def _tri(rnd, lo, mode, hi):
    lo, hi = min(lo, mode), max(hi, mode)
    return mode if hi - lo < 1e-12 else rnd.triangular(lo, hi, mode)


def _summary(vals, price, story):
    vals = sorted(vals)
    n = len(vals)
    if n == 0:
        return None
    q = lambda p: vals[min(n - 1, int(p * (n - 1)))]
    top = q(0.99) or 1.0
    width = top / MC_BINS if top > 0 else 1.0
    bins = [0] * MC_BINS
    for v in vals:
        bins[min(MC_BINS - 1, max(0, int(v / width)))] += 1
    above = sum(1 for v in vals if v >= price) / n
    return {"n": n, "p10": round(q(0.10), 2), "p50": round(q(0.50), 2), "p90": round(q(0.90), 2),
            "mean": round(sum(vals) / n, 2), "prob_above_price": round(above, 3),
            "price_percentile": round(1 - above, 3), "bins": bins, "bin_width": round(width, 4),
            "story": story}


def story_mc_operating(kw, price, margin_lo, margin_hi, sims=MC_SIMS, seed=MC_SEED):
    """Monte Carlo over an FCFF DCF story: target margin triangular(lo, model, hi),
    growth shifted by u x the scenario step (u triangular -1..+1), sales-to-capital
    x triangular(0.75, 1, 1.25), WACC (both phases) +/- 1pp."""
    import random
    if not kw or not price:
        return None
    rnd = random.Random(seed)
    path = kw.get("growth_path") or [kw["g_high"]]
    step = [max(GROWTH_SHIFT_MIN, GROWTH_SHIFT_SHARE * abs(g)) for g in path]
    vals = []
    for _ in range(sims):
        u = _tri(rnd, -1.0, 0.0, 1.0)
        dw = _tri(rnd, -WACC_SPREAD, 0.0, WACC_SPREAD)
        over = dict(margin_target=_tri(rnd, margin_lo, kw["margin_target"], margin_hi),
                    growth_path=[g + u * s for g, s in zip(path, step)],
                    sales_to_capital=kw["sales_to_capital"] * _tri(rnd, 1 - S2C_SPREAD, 1.0, 1 + S2C_SPREAD),
                    wacc=kw["wacc"] + dw,
                    wacc_stable=max(kw["wacc_stable"] + dw, kw["g_stable"] + 0.01))
        v = _dcf(kw, **over)
        if v is not None:
            vals.append(v)
    return _summary(vals, price, {
        "margin_pct": [round(margin_lo * 100, 2), round(kw["margin_target"] * 100, 2), round(margin_hi * 100, 2)],
        "growth": "consensus -/+ max(2pp, 20%)", "sales_to_capital": "x0.75-1.25", "wacc": "+/-1pp"})


def story_mc_young(ydcf, price, margin_lo, margin_hi, sims=MC_SIMS, seed=MC_SEED):
    """Monte Carlo over a young-company story: target margin triangular(industry,
    model, 2x industry), first-year growth +/-15pp, sales-to-capital x0.75-1.25,
    survival +/-20 points; value = p x going concern + (1 - p) x distress."""
    import random
    d = (ydcf or {}).get("model")
    if not d or not price:
        return None
    rnd = random.Random(seed)
    p0, distress = ydcf.get("p_survival") or 0.0, ydcf.get("distress_per_share") or 0.0
    vals = []
    for _ in range(sims):
        p = min(1.0, max(0.0, _tri(rnd, p0 - SURVIVAL_SPREAD, p0, p0 + SURVIVAL_SPREAD)))
        over = dict(target_margin=_tri(rnd, margin_lo, d["target_margin"], margin_hi),
                    g_high=max(0.0, _tri(rnd, d["g_high"] - YOUNG_GROWTH_SHIFT, d["g_high"],
                                         d["g_high"] + YOUNG_GROWTH_SHIFT)),
                    sales_to_capital=d["sales_to_capital"] * _tri(rnd, 1 - S2C_SPREAD, 1.0, 1 + S2C_SPREAD))
        g = young_dcf.going_concern(dict(d, **over))
        if g is not None:
            vals.append(p * g["going_concern_per_share"] + (1 - p) * distress)
    return _summary(vals, price, {
        "margin_pct": [round(margin_lo * 100, 2), round(d["target_margin"] * 100, 2), round(margin_hi * 100, 2)],
        "growth": "first-year -/+15pp", "sales_to_capital": "x0.75-1.25", "survival": "+/-20 points"})
