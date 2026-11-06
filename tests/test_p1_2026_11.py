"""
P1 fixes, 2026-11 (methodology review against Morningstar / Barra / Vanguard).

P1-2  the 40% FMP-sector cap is enforced: an over-cap sector is trimmed to the cap,
      pro rata, and freed weight never flows into a sector at the cap.
P1-3  the width of the value range sets the uncertainty rating, and the rating sets the
      margin of safety the Price pillar, the advice line and the ADD gate ask for.
P1-4  the implied return: the WACC at which the anchor's own DCF is worth the price.
P1-5  realized performance: the NAV log nets deposits out (TWR), compares with SPY,
      and reports tracking error / IR once there are enough periods.
"""
import json
import os

import config
from domain import advice
from domain import risk as R
from domain.engine import deep_v82 as E
from domain.engine import uncertainty as U


def _rows(weights):
    return [{"ticker": t, "capital_pct": w * 100, "abs_risk_share_pct": w * 100}
            for t, w in weights.items()]


def _val(net_mos, rating="BUY"):
    return {"rating": rating, "net_mos": net_mos, "price": 10.0, "avg_cost": 10.0, "tax_rate": 0.15}


def test_sector_over_cap_is_trimmed_to_the_cap_pro_rata():
    w = {"NVDA": 0.18, "AVGO": 0.18, "MSFT": 0.18, "LLY": 0.18, "XOM": 0.18}
    sec = {"NVDA": "Technology", "AVGO": "Technology", "MSFT": "Technology",
           "LLY": "Healthcare", "XOM": "Energy"}
    sizing = R.position_sizing(_rows(w), sec, sector_cap=0.40, risk_share_cap=1.0)
    by = {r["ticker"]: r for r in sizing}
    for t in ("NVDA", "AVGO", "MSFT"):
        assert by[t]["binding_cap"].startswith("sector cap (Technology 54%"), by[t]
        assert abs(by[t]["hard_max_pct"] - 13.3) < 0.05, by[t]
        assert by[t]["action"] == "TRIM", by[t]
    assert by["LLY"]["binding_cap"] == "single-name cap", by["LLY"]
    value = {t: _val(0.20) for t in w}
    reb = R.rebalance(w, sizing, value=value, total_value=10000, sector_cap=0.40)
    tech = sum(v for t, v in reb["proposed_weights"].items() if sec[t] == "Technology")
    assert tech <= 0.40 + 1e-3, reb["proposed_weights"]
    print("P1-2 over-cap sector trimmed pro rata to 40% OK:", round(tech, 4))


def test_freed_weight_never_lifts_a_sector_over_the_cap():
    w = {"BIG": 0.30, "T1": 0.20, "T2": 0.15, "H1": 0.05}
    sec = {"BIG": "Energy", "T1": "Technology", "T2": "Technology", "H1": "Healthcare"}
    value = {"BIG": _val(0.0, "HOLD"), "T1": _val(0.40), "T2": _val(0.40), "H1": _val(0.20)}
    sizing = R.position_sizing(_rows(w), sec, single_name_cap=0.20, sector_cap=0.40,
                               risk_share_cap=1.0, value=value)
    reb = R.rebalance(w, sizing, value=value, total_value=10000, sector_cap=0.40)
    pw = reb["proposed_weights"]
    tech = pw["T1"] + pw["T2"]
    assert tech <= 0.40 + 1e-6, pw
    assert pw["H1"] > 0.05, pw                       # the rest still goes where value is
    print("P1-2 freed weight respects sector headroom OK: tech", round(tech, 4))


def test_name_in_a_full_sector_is_not_added_to():
    w = {"T1": 0.25, "T2": 0.15, "H1": 0.05}
    sec = {"T1": "Technology", "T2": "Technology", "H1": "Healthcare"}
    value = {"T1": _val(0.40), "T2": _val(0.40), "H1": _val(0.40)}
    by = {r["ticker"]: r for r in R.position_sizing(_rows(w), sec, sector_cap=0.40,
                                                    risk_share_cap=1.0, value=value)}
    assert "sector Technology" in (by["T2"]["add_blocker"] or ""), by["T2"]
    assert by["H1"]["add_blocker"] is None, by["H1"]
    print("P1-2 full sector blocks ADD OK")


def test_unknown_sector_and_non_equity_are_not_pooled():
    w = {"A": 0.25, "B": 0.25, "TLT": 0.25, "GLD": 0.25}
    sec = {"A": "Unknown", "B": "Unknown", "TLT": "Financial Services", "GLD": "Financial Services"}
    sizing = R.position_sizing(_rows(w), sec, single_name_cap=0.30, sector_cap=0.40,
                               risk_share_cap=1.0, asset_class={"TLT": "bond", "GLD": "gold"})
    assert all(not r["binding_cap"].startswith("sector") for r in sizing), sizing
    print("P1-2 Unknown / non-equity not pooled OK")


def _mc(p10, p50, p90):
    return {"story_mc": {"p10": p10, "p50": p50, "p90": p90}}


def test_width_sets_the_rating():
    assert U.assess(_mc(90, 100, 120))["rating"] == "Low"
    assert U.assess(_mc(80, 100, 135))["rating"] == "Medium"
    assert U.assess(_mc(60, 100, 150))["rating"] == "High"
    assert U.assess(_mc(40, 100, 180))["rating"] == "Very High"
    assert U.assess(_mc(10, 100, 300))["rating"] == "Extreme"
    assert U.assess({})["rating"] == "Unrated"
    cases = {"cases": [{"value": 70}, {"value": 100}, {"value": 120}]}
    a = U.assess(cases)
    assert a["rating"] == "Medium" and a["basis"].startswith("Bear"), a
    print("P1-3 width -> rating OK")


def test_price_score_moves_with_uncertainty():
    def p(mos, unc):
        return E._r_price(100.0, 100.0 * (1 - mos), [], uncertainty=unc)
    low, med, unrated = {"rating": "Low"}, {"rating": "Medium"}, None
    assert p(0.25, low) == 5.0 and p(0.25, unrated) == 4.0, (p(0.25, low), p(0.25, unrated))
    assert p(-0.12, med) == 2.0 and p(-0.12, unrated) == 3.0
    assert p(0.45, {"rating": "Extreme"}) == 4.0 and p(0.45, unrated) == 5.0
    print("P1-3 Price bands follow the rating OK")


def test_advice_reads_the_same_bands():
    row = {"upside_pct": 100 / 92 * 100 - 100, "mos_pct": 8.0,
           "key_metrics": {"spread_pct": 5.0}, "eq_verdict": "CLEAN",
           "uncertainty": U.assess(_mc(90, 100, 120))}
    assert advice.action(row) == "ACCUMULATE", advice.action(row)     # 8% clears Low's 5%
    row["uncertainty"] = None
    assert advice.action(row) != "ACCUMULATE", advice.action(row)     # not the 15% default
    print("P1-3 advice uses the row's bands OK")


def test_add_gate_uses_each_names_bar():
    v = _val(0.08)
    assert R.add_blocker(dict(v, add_min_mos=0.05)) is None
    assert R.add_blocker(dict(v, add_min_mos=0.15)).startswith("MoS"), R.add_blocker(dict(v, add_min_mos=0.15))
    assert R.add_blocker(v).startswith("MoS")                           # no rating -> 15%
    print("P1-3 ADD bar per uncertainty OK")


def test_engine_carries_the_rating_on_a_real_fixture():
    v = E.DeepV82Engine().evaluate(_abbv(), rf=0.045)
    assert v.uncertainty.get("rating") in U.BANDS and v.uncertainty["rating"] != "Unrated", v.uncertainty
    line = next(n for n in v.subscores["breakdown"] if n.startswith("P base"))
    assert v.uncertainty["rating"] in line, line
    print("P1-3 engine wiring OK:", v.uncertainty["rating"], v.uncertainty["width"])


def _abbv():
    from pipeline import normalize, validate

    def load(n):
        p = os.path.join(config.FIXTURE_DIR, "ABBV", n + ".json")
        return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None
    ff = normalize.build("ABBV", load("sec_companyfacts"), load("fmp_profile"),
                         load("yahoo_quotesummary"))
    validate.validate(ff)
    return ff


def test_implied_return_equals_wacc_when_price_equals_value():
    ff = _abbv()
    v = E.DeepV82Engine().evaluate(ff, rf=0.045)
    assert v.anchor_method == "FCFF DCF", v.anchor_method
    ff2 = _abbv()
    ff2.price = v.fv_dcf
    er = E.DeepV82Engine().evaluate(ff2, rf=0.045).expected_return
    assert abs(er["firm_irr_pct"] - er["wacc_pct"]) < 0.05, er
    dear = v.expected_return                      # the real price is above the value
    assert v.anchor_value < ff.price and dear["firm_irr_pct"] < dear["wacc_pct"], dear
    assert dear["excess_pct"] < 0, dear
    print("P1-4 implied return: = WACC at fair value, below it when dear OK", dear["equity_irr_pct"])


def test_implied_return_falls_as_price_rises():
    from domain.engine import scenarios as S
    from domain.engine import fcff_dcf
    kw = dict(revenue=1000.0, margin_now=0.2, margin_target=0.2, g_high=0.08, g_stable=0.03,
              sales_to_capital=2.0, tax_now=0.21, tax_marginal=0.25, wacc=0.09,
              wacc_stable=0.085, roic_stable=0.12, cash=0.0, debt=0.0, shares=100.0)
    fair = fcff_dcf.value(**kw)["per_share"]
    r_lo, _ = S.implied_return(kw, fair * 1.3)
    r_eq, _ = S.implied_return(kw, fair)
    r_hi, _ = S.implied_return(kw, fair * 0.7)
    assert r_lo < r_eq < r_hi and abs(r_eq - 0.09) < 1e-4, (r_lo, r_eq, r_hi)
    print("P1-4 monotone in price OK", round(r_lo, 4), round(r_eq, 4), round(r_hi, 4))


def _store(prices, spy, day, cash=0.0):
    import copy
    import store
    s = copy.deepcopy(store._DEFAULT)
    s["market"] = {"spy_close": spy, "as_of": day}
    s["momentum"] = {t: {"price": p, "price_date": day} for t, p in prices.items()}
    s["cash"] = {"usd": cash}
    return s


def _day(s, prices, spy, day):
    s["market"] = {"spy_close": spy, "as_of": day}
    for t, p in prices.items():
        s["momentum"][t] = {"price": p, "price_date": day}


def test_deposit_is_not_a_return():
    import store
    from pipeline import performance as P
    s = _store({"A": 10.0}, 100.0, "2026-11-02")
    store.set_holding(s, "A", 10, 10.0)              # starting book: not a flow
    assert P.record(s)["flow"] == 0.0
    store.set_holding(s, "A", 20, 10.0)              # bought 10 more with new money
    store.set_cash(s, 50)                            # and deposited 50
    _day(s, {"A": 10.0}, 101.0, "2026-11-03")
    e = P.record(s)
    assert e["nav"] == 250.0 and e["flow"] == 150.0, e
    v = P.view(s)
    assert v["twr_pct"] == 0.0 and v["spy_pct"] == 1.0 and v["active_pp"] == -1.0, v
    print("P1-5 deposits netted out of the TWR OK")


def test_sale_and_removal_are_outflows_and_the_return_counts():
    import store
    from pipeline import performance as P
    s = _store({"A": 10.0, "B": 20.0}, 100.0, "2026-11-02")
    store.set_holding(s, "A", 10, 10.0)
    store.set_holding(s, "B", 5, 20.0)
    P.record(s)                                      # NAV 200
    store.remove_holding(s, "B")                     # took 100 out
    _day(s, {"A": 11.0}, 100.0, "2026-11-03")
    e = P.record(s)
    assert e["nav"] == 110.0 and e["flow"] == -100.0, e
    assert P.view(s)["twr_pct"] == 5.0, P.view(s)    # (110 + 100) / 200 - 1
    print("P1-5 removal is an outflow; the A gain is the return OK")


def test_mixed_day_nav_is_not_recorded():
    import store
    from pipeline import performance as P
    s = _store({"A": 10.0}, 100.0, "2026-11-02")
    store.set_holding(s, "A", 10, 10.0)
    P.record(s)
    s["market"] = {"spy_close": 102.0, "as_of": "2026-11-03"}   # A still on the 2nd
    assert P.record(s) is None and len(s["performance"]["nav"]) == 1
    _day(s, {"A": 10.5}, 102.0, "2026-11-03")
    store.set_cash(s, 30)
    e1 = P.record(s)
    store.set_cash(s, 50)                            # a second deposit the same day
    e2 = P.record(s)
    assert e2 is e1 and e2["flow"] == 50.0 and len(s["performance"]["nav"]) == 2, s["performance"]
    print("P1-5 mixed-day NAV skipped; same-day entry accumulates flows OK")


def test_tracking_error_needs_enough_periods():
    from domain import performance as D
    import datetime as dt
    log, nav, spy = [], 100.0, 100.0
    d0 = dt.date(2026, 1, 1)
    for i in range(25):
        log.append({"date": (d0 + dt.timedelta(days=i)).isoformat(), "nav": nav, "flow": 0.0, "spy": spy})
        nav *= 1.002 if i % 2 else 0.999
        spy *= 1.001
    out = D.summary(log)
    assert out["tracking_error_pct"] and out["tracking_error_pct"] > 0, out
    assert out["information_ratio"] is not None, out
    short = D.summary(log[:5])
    assert "tracking_error_pct" not in short and short["note"], short
    print("P1-5 TE/IR from", out["n_periods"], "periods OK:", out["tracking_error_pct"], out["information_ratio"])


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("ALL P1 2026-11 TESTS PASSED")
