"""
P1 fixes, 2026-11 (methodology review against Morningstar / Barra / Vanguard).

P1-2  the 40% FMP-sector cap is enforced: an over-cap sector is trimmed to the cap,
      pro rata, and freed weight never flows into a sector at the cap.
"""
from domain import risk as R


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


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("ALL P1 2026-11 TESTS PASSED")
