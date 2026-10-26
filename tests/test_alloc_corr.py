"""test_alloc_corr — the Allocation (Risk Desk) and Correlation tabs, plan 2026-10-25
(A1-A14 / D1-D7). Each check is written against a defect found on the live portfolio
and was watched failing on the code before the fix. Offline: fetch_returns is faked."""
import datetime as dt
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from domain import pricing
from domain import risk as R
from pipeline import prices, risk_report


def _dates(n, end="2026-10-23"):
    d, out = dt.date.fromisoformat(end), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d -= dt.timedelta(days=1)
    return out[::-1]


def _provider(series):
    """series: {ticker: [returns]} -> a fetch_returns fake with dates on every series."""
    def fetch_returns(tickers, *a, **kw):
        out = {}
        for t in tickers:
            r = series.get(t)
            if r is None:
                random.seed(sum(map(ord, t)))
                r = [random.gauss(0.0004, 0.015) for _ in range(250)]
            out[t] = {"returns": r, "dates": _dates(len(r)), "n": len(r),
                      "source": "test", "as_of": "2026-10-23", "vol": None}
        return out, 0, {"cache": "test"}
    return fetch_returns


def _store(spec):
    """spec: {ticker: dict(sector=, industry=, beta=, price=, shares=)}"""
    s = {"holdings": {}, "facts": {}, "momentum": {}, "updated": {}, "results": {}}
    for t, v in spec.items():
        s["holdings"][t] = {"shares": v.get("shares", 10), "avg_cost": v.get("avg_cost", 50.0)}
        s["facts"][t] = {"price": v.get("price", 100.0), "sector": v.get("sector"),
                         "industry": v.get("industry"), "currency": "USD", "beta": v.get("beta", 1.0)}
    return s


def _build(spec, series=None):
    p, _ = risk_report.build(_store(spec), "", 0, 250, 20.0, 1.0, False,
                             fetch_returns=_provider(series or {}), fetch_long=lambda t: {})
    return p


# --------------------------------------------------------------------------- A6
def test_semiconductor_multiplier_reads_the_industry():
    w = {"NV": 1.0}
    loss = {r["key"]: r["loss_pct"] for r in R.stress_test(
        w, {"NV": 1.0}, {"NV": "Technology"}, industries={"NV": "Semiconductors"})}
    assert abs(loss["tech_derate"] - (-12 * 2.6)) < 0.05, loss
    soft = {r["key"]: r["loss_pct"] for r in R.stress_test(
        w, {"NV": 1.0}, {"NV": "Technology"}, industries={"NV": "Software - Infrastructure"})}
    assert abs(soft["tech_derate"] - (-12 * 2.2)) < 0.05, soft
    print("A6 semiconductor x2.6 from the industry OK:", loss["tech_derate"], soft["tech_derate"])


def test_payload_stress_uses_industry():
    p = _build({"NV": dict(sector="Technology", industry="Semiconductors"),
                "MS": dict(sector="Technology", industry="Software - Infrastructure")})
    td = next(r for r in p["stress"]["hypothetical"] if r["key"] == "tech_derate")
    # 50/50, beta 1: semis -31.2, software -26.4 -> -28.8
    assert abs(td["loss_pct"] - (-28.8)) < 0.15, td
    print("A6 payload tech de-rating", td["loss_pct"])


# --------------------------------------------------------------------------- A7
def test_missing_sector_is_flagged():
    p = _build({"AA": dict(sector="Healthcare"), "BB": dict(sector=None)})
    assert p["meta"]["missing_sector"] == ["BB"], p["meta"].get("missing_sector")
    print("A7 missing sector flagged OK")


# --------------------------------------------------------------------------- A10
def test_marginal_uses_signed_risk_like_section_3():
    random.seed(7)
    aa = [random.gauss(0.0005, 0.02) for _ in range(250)]
    bb = [0.8 * x + random.gauss(0, 0.008) for x in aa]
    hedge = [-0.9 * x + random.gauss(0, 0.004) for x in aa]   # a true diversifier
    p = _build({"AA": dict(sector="Technology", shares=10), "BB": dict(sector="Technology", shares=10),
                "HG": dict(sector="Energy", shares=3)}, {"AA": aa, "BB": bb, "HG": hedge})
    signed = {r["ticker"]: r["signed_risk_pct"] for r in p["capital_vs_risk"]["covariance_based"]}
    for m in p["correlation"]["marginal"]:
        assert m["risk_pct"] == signed[m["ticker"]], (m, signed)
        assert abs((m["risk_pct"] - m["cap_pct"]) - m["diff_pp"]) < 0.11, m
    assert signed["HG"] < 0, signed      # the case where abs and signed disagree
    print("A10 one %risk definition OK")


# --------------------------------------------------------------------------- A11
def test_rolling_gap_is_none_not_zero():
    a = [0.0] * 70 + [0.01, -0.01] * 20          # flat window first, then moving
    b = [0.005 * ((i % 3) - 1) for i in range(len(a))]
    roll = R.rolling_corr(a, b, 60)
    assert roll[0] is None, roll[:3]
    assert 0.0 not in roll[:5], roll[:5]
    assert any(v is not None for v in roll), roll
    print("A11 rolling gap None OK")


# --------------------------------------------------------------------------- A12
def test_risk_returns_drop_todays_partial_bar():
    today = pricing.ny_today()
    ds = _dates(70, end=(dt.date.fromisoformat(today) - dt.timedelta(days=1)).isoformat())
    ds = [d for d in ds if d < today] + [today]
    closes = [100.0 + i for i in range(len(ds))]
    rec = prices._returns_record({"closes": closes, "dates": ds}, "test")
    assert rec["as_of"] < today and today not in rec["dates"], rec["as_of"]
    assert rec["n"] == len(ds) - 2, rec["n"]
    print("A12 T-1 on risk returns OK")


# --------------------------------------------------------------------------- A1 / D1
def test_stress_runs_on_the_valuation_beta():
    s = _store({"PF": dict(sector="Healthcare", beta=0.28), "GL": dict(sector=None, beta=0.10)})
    s["results"]["PF"] = {"key_metrics": {"beta": 1.19, "beta_source": "bottom-up: Drugs (Pharmaceutical)"}}
    s["results"]["GL"] = {"key_metrics": {"beta": 1.00}}      # a non-equity never takes it
    import config
    saved = config.ASSET_CLASS_MAP
    config.ASSET_CLASS_MAP = {**saved, "GL": "gold"}
    s["momentum"]["PF"] = {"v2": {"beta_calc": 0.35}}
    try:
        p, _ = risk_report.build(s, "", 0, 250, 20.0, 1.0, False, fetch_returns=_provider({}), fetch_long=lambda t: {})
    finally:
        config.ASSET_CLASS_MAP = saved
    bc = p["meta"]["beta_check"]
    assert bc["PF"]["used"] == 1.19 and bc["PF"]["source"].startswith("bottom-up"), bc["PF"]
    assert bc["PF"]["diverge"] is True, bc["PF"]                  # 1.19 vs measured 0.35
    assert bc["GL"]["used"] == 0.10 and "regression" in bc["GL"]["source"], bc["GL"]
    assert all("downside" in v for v in bc.values()), bc
    sell = next(r for r in p["stress"]["hypothetical"] if r["key"] == "broad_selloff")
    w = {t: r["capital_pct"] / 100 for t, r in ((r["ticker"], r) for r in p["capital_vs_risk"]["covariance_based"])}
    want = -20 * (w["PF"] * 1.19 + w["GL"] * 0.10)
    assert abs(sell["loss_pct"] - want) < 0.15, (sell, want)
    assert abs(p["snapshot"]["port_beta"] - (w["PF"] * 1.19 + w["GL"] * 0.10)) < 0.01, p["snapshot"]
    print("D1 stress on bottom-up beta OK:", sell["loss_pct"], p["snapshot"]["port_beta"])


def test_valuation_beta_label_matches_the_beta_used():
    """Stored portfolio: a row whose Ke runs on the regression beta (early-stage rule)
    must not be labelled bottom-up. TMDX/RKLB/HIMS/AXON/CELH were."""
    import json
    import config
    from domain.engine.deep_v82 import DeepV82Engine
    from domain.facts import FinancialFacts
    path = os.path.join(config.DATA_DIR, "portfolio.json")
    if not os.path.exists(path):
        print("label check skipped: no stored portfolio"); return
    store = json.load(open(path, encoding="utf-8"))
    fields = set(FinancialFacts.__dataclass_fields__)
    eng, bad, n = DeepV82Engine(), [], 0
    for t, fd in sorted((store.get("facts") or {}).items()):
        f = FinancialFacts(**{k: v for k, v in fd.items() if k in fields})
        km = eng.evaluate(f, rf=0.05).key_metrics or {}
        if f.beta and f.beta_unlevered_industry and km.get("beta") == round(f.beta, 2):
            n += 1
            if km.get("beta_source", "").startswith("bottom-up"):
                bad.append(t)
    assert not bad, bad
    print("valuation beta label OK on", n, "regression-beta rows")


# --------------------------------------------------------------------------- A13 / D6
def test_cash_scales_loss_but_not_composition():
    spec = {"AA": dict(sector="Technology", beta=1.2), "BB": dict(sector="Healthcare", beta=0.8)}
    s0 = _store(spec)
    p0, _ = risk_report.build(s0, "", 0, 250, 20.0, 1.0, False, fetch_returns=_provider({}), fetch_long=lambda t: {})
    s1 = _store(spec)
    s1["cash"] = {"usd": p0["snapshot"]["total_value"]}      # cash = invested -> 50%
    p1, _ = risk_report.build(s1, "", 0, 250, 20.0, 1.0, False, fetch_returns=_provider({}), fetch_long=lambda t: {})
    a, b = p0["snapshot"], p1["snapshot"]
    assert a["cash_pct"] == 0.0 and b["cash_pct"] == 50.0, (a["cash_pct"], b["cash_pct"])
    assert abs(b["port_vol_pct"] - a["port_vol_pct"] / 2) <= 0.1, (a, b)
    assert abs(b["port_beta"] - a["port_beta"] / 2) <= 0.002, (a, b)
    assert b["invested_vol_pct"] == a["port_vol_pct"], (a, b)
    for k in ("hypothetical", "historical"):
        for r0, r1 in zip(p0["stress"][k], p1["stress"][k]):
            assert abs(r1["loss_pct"] - r0["loss_pct"] / 2) <= 0.1, (r0, r1)
    assert abs(p1["stress"]["var"]["var99_pct"] - p0["stress"]["var"]["var99_pct"] / 2) <= 0.1
    assert abs(p1["stress"]["var_hist"]["var95_pct"] - p0["stress"]["var_hist"]["var95_pct"] / 2) <= 0.1
    # the invested book's composition is not diluted by cash
    assert p1["correlation"]["avg_pairwise"] == p0["correlation"]["avg_pairwise"]
    assert p1["diversification"]["enb"] == p0["diversification"]["enb"]
    assert p1["allocation"]["by_sector"] == p0["allocation"]["by_sector"]
    assert p1["suitability"]["binding_drawdown_pct"] > p0["suitability"]["binding_drawdown_pct"]
    print("D6 cash halves loss, keeps composition OK:", a["port_vol_pct"], "->", b["port_vol_pct"])


def test_set_cash_validates_and_view_totals():
    import store
    from pipeline import portfolio as pview
    s = _store({"AA": dict(sector="Technology")})
    for bad in (-1, 2e9):
        try:
            store.set_cash(s, bad)
            raise AssertionError(f"accepted {bad}")
        except ValueError:
            pass
    store.set_cash(s, 1000)
    assert store.cash_usd(s) == 1000.0
    t = pview.view(s)["totals"]
    assert t["cash"] == 1000.0 and abs(t["total_value"] - (t["market_value"] + 1000)) < 0.01, t
    print("D6 set_cash + view totals OK:", t["cash_pct"], "%")


# --------------------------------------------------------------------------- A5 / D4
def test_currency_follows_revenue_not_listing():
    from domain import exposure
    w = {"ASML": 0.5, "MELI": 0.5}
    mixes = {"ASML": {"NL": 1.0}, "MELI": {"Brazil": 53, "Mexico": 22, "Argentina": 21, "Latin America": 4}}
    ccy, reg, non_us = exposure.revenue_exposure(w, mixes)
    got = {r["label"]: r["value"] for r in ccy}
    assert got["EUR"] == 50.0 and abs(got["BRL"] - 26.5) < 0.1 and "USD" not in got, got
    assert non_us == {"ASML": 1.0, "MELI": 1.0}, non_us
    ccy2, _, nu2 = exposure.revenue_exposure({"X": 1.0}, {})
    assert ccy2 == [{"label": "Unknown", "value": 100.0}] and nu2["X"] is None, ccy2
    pc = exposure.portfolio_crp({"A": 0.6, "B": 0.4}, {"A": 0.04, "B": None})
    assert pc == {"crp_pct": 2.4, "weight_without_crp_pct": 40.0}, pc
    print("D4 revenue currency OK:", got)


def test_payload_currency_is_not_all_usd_for_adrs():
    spec = {"ASML": dict(sector="Technology"), "MSFT": dict(sector="Technology")}
    s = _store(spec)
    s["facts"]["ASML"].update(country="NL", accounting_standard="ifrs", crp=0.0)
    s["facts"]["MSFT"].update(country="US", accounting_standard="ifrs", crp=0.0)   # no 10-K lookup
    p, _ = risk_report.build(s, "", 0, 250, 20.0, 1.0, False, fetch_returns=_provider({}), fetch_long=lambda t: {})
    cur = {r["label"]: r["value"] for r in p["allocation"]["by_currency"]}
    assert cur.get("EUR", 0) > 0 and cur.get("USD", 0) < 100, cur
    assert p["allocation"]["by_listing_currency"] == [{"label": "USD", "value": 100.0}]
    print("D4 payload currency by revenue OK:", cur)


def test_exposure_mix_is_the_one_the_crp_was_priced_on():
    """Stored portfolio: re-pricing each name's CRP from the mix the Risk Desk reads
    must reproduce the CRP the valuation stored. Two numbers that must agree."""
    import json
    import config
    from sources import country_risk as cr
    path = os.path.join(config.DATA_DIR, "portfolio.json")
    if not os.path.exists(path):
        print("crp parity skipped: no stored portfolio"); return
    s = json.load(open(path, encoding="utf-8"))
    tks = list(s.get("holdings") or {})
    mixes, src = risk_report._revenue_mixes(s, tks)
    table = cr.fetch_crp_table(cache_dir=config.CACHE_DIR)
    bad = []
    for t in tks:
        f = s["facts"].get(t) or {}
        if f.get("crp") is None:
            continue
        c, _ = cr.crp_for(t, f.get("country"), table,
                          mix=None if src.get(t) == "domicile" else mixes.get(t), source=src.get(t))
        if c is None or abs(round(c, 5) - f["crp"]) > 1e-5:
            bad.append((t, f["crp"], c, src.get(t)))
    assert not bad, bad
    print("D4 exposure mix = CRP mix on", len(tks), "holdings")


# --------------------------------------------------------------------------- A4 / D3
def _one_factor(n=500, crisis=(200, 260), seed=3):
    """market + idiosyncratic noise; inside `crisis` the market falls hard and its
    variance dominates, the textbook 'correlation rises in a crash'."""
    random.seed(seed)
    mkt, names = [], {"A": [], "B": [], "C": []}
    for i in range(n):
        inside = crisis[0] <= i < crisis[1]
        m = random.gauss(-0.006 if inside else 0.0005, 0.03 if inside else 0.008)
        mkt.append(m)
        for k in names:
            names[k].append(m + random.gauss(0, 0.012))
    return mkt, names


def test_crisis_window_is_the_worst_episode():
    mkt, _ = _one_factor()
    ds = _dates(len(mkt))
    days, dd = R.crisis_window(mkt, ds)
    assert dd <= -0.10 and len(days) >= 40, (dd, len(days))
    inside = set(ds[200:260])
    assert len(days & inside) / len(inside) > 0.7, "the window must cover the crash"
    calm = [abs(x) * 0.1 + 0.0001 for x in mkt]               # never draws down 10%
    assert R.crisis_window(calm, ds) == (set(), 0.0), R.crisis_window(calm, ds)
    print("D3 crisis window OK:", dd, len(days), "days")


def test_measured_crisis_raises_correlation_in_a_crash():
    mkt, names = _one_factor()
    ds = _dates(len(mkt))
    tks = ["A", "B", "C"]
    series = {**{t: (names[t][-250:], ds[-250:]) for t in tks}}      # the 'normal' window
    long = {t: (names[t], ds) for t in tks}
    long["SPY"] = (mkt, ds)

    def fetch_long(tickers):
        return {t: long[t] for t in tickers if t in long}
    p, _ = risk_report.build(_store({t: dict(sector="Technology") for t in tks}), "", 0, 250, 20.0, 1.0,
                             False, fetch_returns=_provider({**{t: series[t][0] for t in tks},
                                                             **{r: [0.001 * ((i % 5) - 2) for i in range(60)]
                                                                for r in ("SPY", "QQQ", "GLD", "IBIT")}}),
                             fetch_long=fetch_long)
    ci = p["diversification"]["crisis"]
    assert ci["method"] == "measured" and ci["assumed_pairs"] == 0, ci
    av = p["correlation"]["avg_pairwise"]
    assert av["crisis"] > av["normal"] + 0.1, av
    assert p["diversification"]["enb_crisis"] < p["diversification"]["enb"], p["diversification"]
    # down days alone would have shown the opposite: the shared fall is their mean
    days_down = {d for x, d in zip(mkt, ds) if x <= -0.015}
    rho_down, _ = R.crisis_pair_corr(*long["A"], *long["B"], days_down)
    rho_win, _ = R.crisis_pair_corr(*long["A"], *long["B"], R.crisis_window(mkt, ds)[0])
    assert rho_win > rho_down, (rho_win, rho_down)
    # the yardsticks' history (the last 60 days) misses the crash: never called measured
    story = p["correlation"]["philosophy"]["story_normal"] + p["correlation"]["philosophy"]["story_crisis"]
    assert not p["correlation"]["benchmark"]["portfolio"]["GLD"]["crisis_measured"]
    assert "Gold" not in story and "BTC" not in story, story
    print("D3 measured crisis corr OK:", av, "down-days-only", round(rho_down, 2), "window", round(rho_win, 2))


def test_crisis_falls_back_to_the_floor_without_history():
    tks = ["A", "B"]
    p, _ = risk_report.build(_store({t: dict(sector="Technology") for t in tks}), "", 0, 250, 20.0, 1.0,
                             False, fetch_returns=_provider({}), fetch_long=lambda t: {})
    ci = p["diversification"]["crisis"]
    assert ci["method"] == "assumed", ci
    assert p["correlation"]["avg_pairwise"]["crisis"] >= 0.9 - 1e-9, p["correlation"]["avg_pairwise"]
    assert all(not c["crisis_measured"] for c in p["correlation"]["benchmark"]["portfolio"].values())
    print("D3 assumed floor fallback OK")


# --------------------------------------------------------------------------- A8 A9 / D5
def test_story_says_only_what_was_measured():
    from domain import diversification as D
    base = dict(n_holdings=5, enb=3.0, enb_crisis=2.0, eff_n=4.5, top_sector="Technology",
                top_sector_wt=60, top_sector_corr=0.6, avg_pairwise=0.3, avg_pairwise_crisis=0.5)
    none = D.diversification_philosophy(**base)
    text = none["story_normal"] + none["story_crisis"] + " ".join(p["value"] for p in none["pillars"])
    assert "Gold" not in text and "BTC" not in text, text      # nothing measured -> nothing claimed
    meas = D.diversification_philosophy(**base, ballast=("ELV", -0.05),
                                        refs_crisis={"GLD": 0.25, "IBIT": 0.45},
                                        crisis_info={"method": "measured", "first_day": "2022-01-03",
                                                     "last_day": "2022-10-12", "market_drawdown_pct": -25.4,
                                                     "window_days": 197})
    assert "Gold 0.25" in meas["story_normal"], meas["story_normal"]
    assert "BTC" not in meas["story_normal"], meas["story_normal"]          # 0.45 is not a ballast
    assert "2022-01-03" in meas["story_crisis"] and "ELV" in meas["story_crisis"], meas["story_crisis"]
    assert "~1" not in meas["story_crisis"], meas["story_crisis"]
    print("A9 story from measured numbers OK")


def test_gap_coverage_counts_a_foreign_business():
    w = {"TSM": 0.5, "MSFT": 0.5}
    assert R.gap_coverage(w, {}) == 0.0
    assert R.gap_coverage(w, {}, {"TSM": 1.0, "MSFT": 0.49}) == 0.2
    assert R.gap_coverage(w, {}, {"TSM": 0.3, "MSFT": 0.49}) == 0.0
    sc = R.diversification_score(1.5, 1.2, 3, 2, 2, 0.2)
    assert sc["tag"].startswith("[JUDG]"), sc
    print("D5 intl by revenue + JUDG tag OK")


# --------------------------------------------------------------------------- A2 A3 / D2
def _sizing_rows(weights):
    rc = [{"ticker": t, "capital_pct": w * 100, "abs_risk_share_pct": w * 100} for t, w in weights.items()]
    return rc


def test_rebalance_adds_only_where_the_value_is_and_parks_the_rest_in_cash():
    w = {"A": 0.40, "B": 0.10, "C": 0.05, "D": 0.05, "E": 0.40}
    value = {"A": {"rating": "HOLD", "net_mos": 0.0, "price": 120.0, "avg_cost": 100.0, "tax_rate": 0.15},
             "B": {"rating": "BUY", "net_mos": 0.30, "price": 10.0, "avg_cost": 10.0, "tax_rate": 0.15},
             "C": {"rating": "SELL / AVOID", "net_mos": 0.50, "price": 10.0, "avg_cost": 10.0, "tax_rate": 0.15},
             "D": {"rating": "BUY", "net_mos": 0.10, "price": 10.0, "avg_cost": 10.0, "tax_rate": 0.15},
             "E": {"rating": "HOLD", "net_mos": None, "price": 10.0, "avg_cost": 10.0, "tax_rate": 0.15}}
    sizing = R.position_sizing(_sizing_rows(w), {}, single_name_cap=0.20, risk_share_cap=1.0, value=value)
    act = {r["ticker"]: r["action"] for r in sizing}
    assert act["C"].startswith("HOLD (ไม่เพิ่ม: rating"), act
    assert act["D"].startswith("HOLD (ไม่เพิ่ม: MoS"), act
    reb = R.rebalance(w, sizing, value=value, total_value=10000, cost_bps=20)
    tr = {t["ticker"]: t for t in reb["trades"]}
    assert tr["A"]["action"] == "TRIM" and tr["A"]["to_pct"] == 20.0, tr["A"]
    assert set(tr) <= {"A", "B", "E"} and "C" not in tr and "D" not in tr, tr      # never add C/D
    assert tr["B"]["action"] == "ADD" and tr["B"]["to_pct"] == 16.0, tr["B"]      # up to its soft max
    assert reb["to_cash_pct"] == 34.0 and reb["cash_pct_after"] == 34.0, reb       # 40 freed - 6 added
    assert abs(sum(reb["proposed_weights"].values()) + reb["cash_pct_after"] / 100 - 1) < 1e-3, reb
    # tax only on the gain: 20% of $10,000 = $2,000 sold at 120 over 100 cost -> gain 333.33
    assert abs(tr["A"]["realized_gain_usd"] - 333.33) < 0.01 and abs(tr["A"]["tax_usd"] - 50.0) < 0.01, tr["A"]
    assert tr["A"]["cost_usd"] == 2.0 and tr["B"]["tax_usd"] == 0.0, (tr["A"], tr["B"])
    print("D2 value-aware rebalance OK:", reb["costs"], "to cash", reb["to_cash_pct"])


def test_rebalance_sale_at_a_loss_pays_no_tax_and_sell_rated_trims():
    w = {"S": 0.30, "K": 0.70}
    value = {"S": {"rating": "SELL / AVOID", "net_mos": -0.4, "price": 50.0, "avg_cost": 80.0, "tax_rate": 0.15},
             "K": {"rating": "HOLD", "net_mos": 0.05, "price": 10.0, "avg_cost": 10.0, "tax_rate": 0.15}}
    sizing = R.position_sizing(_sizing_rows(w), {}, single_name_cap=0.30, risk_share_cap=1.0, value=value)
    reb = R.rebalance(w, sizing, value=value, total_value=1000, cost_bps=20)
    s = {t["ticker"]: t for t in reb["trades"]}.get("S")
    assert s, reb["trades"]
    assert s["action"] == "TRIM" and s["to_pct"] == 24.0 and "SELL" in s["priority"], s
    assert s["realized_gain_usd"] < 0 and s["tax_usd"] == 0.0, s
    assert reb["cash_pct_after"] == 46.0, reb      # K 70->30, S 30->24, nobody qualifies -> cash
    print("D2 loss sale untaxed, SELL above soft max trimmed OK")


def test_live_book_never_adds_to_a_sell_rated_name():
    p = _build({"PF": dict(sector="Healthcare", shares=1), "MS": dict(sector="Technology", shares=30),
                "XX": dict(sector="Energy", shares=30), "YY": dict(sector="Utilities", shares=30)})
    assert any(r["current_pct"] < 5 for r in p["position_sizing"]), p["position_sizing"]
    for r in p["position_sizing"]:
        assert not r["action"].startswith("ADD"), r            # no fair value on file -> no ADD
    print("D2 payload sizing without FV never says ADD OK")


# --------------------------------------------------------------------------- A14 / D7
def _wi_store():
    random.seed(11)
    aa = [random.gauss(0.0005, 0.02) for _ in range(250)]
    bb = [0.9 * x + random.gauss(0, 0.006) for x in aa]
    hedge = [-0.6 * x + random.gauss(0, 0.004) for x in aa]
    btc = [1.5 * x + random.gauss(0, 0.03) for x in aa]
    s = _store({"AA": dict(sector="Technology", shares=10), "BB": dict(sector="Technology", shares=10)})
    for t in ("HG", "IBIT"):
        s["facts"][t] = {"price": 100.0, "sector": "Other", "currency": "USD", "beta": 0.5}
    return s, {"AA": aa, "BB": bb, "HG": hedge, "IBIT": btc}


def test_whatif_zero_buy_changes_nothing():
    s, series = _wi_store()
    r = risk_report.whatif(s, [], fetch_returns=_provider(series), fetch_long=lambda t: {})
    assert r["delta"] and all(v == 0 for v in r["delta"].values() if v is not None), r["delta"]
    assert r["names"] == [] and r["spent_usd"] == 0
    print("D7 empty what-if = no change OK")


def test_whatif_a_diversifier_raises_enb_and_a_pricing_asset_is_flagged():
    s, series = _wi_store()
    r = risk_report.whatif(s, [{"ticker": "HG", "amount": 400}], fetch_returns=_provider(series),
                           fetch_long=lambda t: {})
    assert r["delta"]["enb"] > 0 and r["delta"]["port_vol_pct"] < 0, r["delta"]
    hg = r["names"][0]
    assert hg["ticker"] == "HG" and hg["risk_pct"] < hg["capital_pct"], hg
    assert hg["add_blocker"] == "ไม่มี fair value", hg
    import config
    saved = config.ASSET_CLASS_MAP
    config.ASSET_CLASS_MAP = {**saved, "IBIT": "crypto"}
    try:
        r2 = risk_report.whatif(s, [{"ticker": "IBIT", "amount": 400}], fetch_returns=_provider(series),
                                fetch_long=lambda t: {})
    finally:
        config.ASSET_CLASS_MAP = saved
    ib = r2["names"][0]
    assert ib["pricing_asset"] and ib["over_pricing_cap"], ib
    print("D7 what-if diversifier dENB", r["delta"]["enb"], "· IBIT over the 5% cap OK")


def test_whatif_spends_recorded_cash_first():
    s, series = _wi_store()
    s["cash"] = {"usd": 1000.0}
    r = risk_report.whatif(s, [{"ticker": "AA", "amount": 600}], fetch_returns=_provider(series),
                           fetch_long=lambda t: {})
    # $1,000 cash + $2,000 invested; $600 moves from cash into AA: 400 / 3000, not 1000 / 3600
    assert r["after"]["cash_pct"] == round(400 / 3000 * 100, 1), r["after"]
    print("D7 cash spent first OK:", r["before"]["cash_pct"], "->", r["after"]["cash_pct"])


# --------------------------------------------------------------------------- portfolio MoS
def test_portfolio_mos_is_value_against_price_of_the_whole_book():
    """Two names: $100 at MoS +50% (FV $200) and $100 at MoS -920% (FV $9.8). The book's
    fair value is $209.8 against $200 at price: MoS +4.7%, not the -435% an average of
    ratios gives."""
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        print("portfolio MoS check skipped: no node"); return
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "static", "js", "risk.js"), encoding="utf-8").read()
    fn = src[src.index("function portMoS()"):]
    fn = fn[:fn.index("\nfunction ")]
    js = ("const window={__last:{rows:[{market_value:100,mos_pct:50,net_upside_pct:80},"
          "{market_value:100,mos_pct:-920,net_upside_pct:-90}]}};"
          "function mosOf(x){return x.mos_pct;}\n" + fn + "\nconsole.log(JSON.stringify(portMoS()));")
    out = subprocess.run([node, "-e", js], capture_output=True, text=True, timeout=30)
    import json
    m = json.loads(out.stdout.strip())
    assert abs(m["gross"] - 4.7) < 0.1 and m["net"] == -5.0, m
    print("portfolio MoS on the whole book OK:", m)


def test_rebalance_is_idempotent_with_cash():
    """Carry out the plan, run it again: nothing left to do. Caps on the invested book
    while freed weight went to cash put every name back over its cap on the next run."""
    w = {"A": 0.40, "B": 0.10, "C": 0.05, "D": 0.05, "E": 0.40}
    value = {t: {"rating": "BUY" if t == "B" else "HOLD", "net_mos": 0.30 if t == "B" else 0.0,
                 "price": 10.0, "avg_cost": 10.0, "tax_rate": 0.15} for t in w}
    cash = 0.0
    for run in range(3):
        rows = [{"ticker": t, "capital_pct": x * (1 - cash) * 100, "abs_risk_share_pct": x * 100}
                for t, x in w.items()]
        sizing = R.position_sizing(rows, {}, single_name_cap=0.20, risk_share_cap=1.0, value=value)
        reb = R.rebalance(w, sizing, value=value, cash_frac=cash, total_value=10000, cost_bps=20)
        if run == 0:
            assert not reb["no_trade"], reb
        else:
            assert reb["no_trade"], (run, reb["trades"])
        pw, cash = reb["proposed_weights"], reb["cash_pct_after"] / 100
        inv = sum(pw.values())
        w = {t: x / inv for t, x in pw.items()}
    print("D2 rebalance idempotent with cash OK: cash", cash)


def test_regression_beta_is_not_labelled_bottom_up():
    b, src = risk_report._risk_beta("equity", {"beta": 2.43, "beta_regression": 2.43,
                                                "beta_source": "bottom-up: Healthcare IT"}, 2.43, 2.56)
    assert b == 2.43 and src.startswith("regression"), src
    b2, src2 = risk_report._risk_beta("equity", {"beta": 1.19, "beta_regression": 0.28,
                                                  "beta_source": "bottom-up: Drugs"}, 0.28, 0.35)
    assert src2.startswith("bottom-up"), src2
    print("beta label by what it is OK")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("ALL ALLOC/CORR TESTS PASSED")
