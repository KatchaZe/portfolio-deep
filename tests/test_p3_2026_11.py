"""
test_p3_2026_11 — P3 round 1: investor tax, risk estimation, the transaction ledger.

  1 capital-gains tax 0 (gains stay abroad), dividend withholding 15%, docs agree
  2 Ledoit-Wolf: a known matrix, delta in [0,1], diagonal kept, PSD; wired into the
    Risk Desk with the sample matrix still behind the Correlation tab
  3 block bootstrap VaR: seeded, drift-free, the 1-year tail is wider than one day's
  4 ledger: average cost, realized P&L, implied deposits, splits, dividends, XIRR,
    the since-start identity NAV - start - flows = sum of per-name P&L
  5 store: start from the book, holdings/cash derived, old editors refused, a bad sell
    leaves the store untouched; NAV flows are the ledger's deposits (late ones too)
  6 API endpoints, called as functions on a temp store
"""
import json
import os
import random
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import store
from domain import costs
from domain import ledger as L
from domain.risk import bootstrap_var, historical_var, ledoit_wolf
from tests._harness import check, run_suite

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _fresh():
    return json.loads(json.dumps(store._DEFAULT))


def test_tax():
    print("1 tax: Thai resident, gains kept abroad")
    check(config.CAPGAINS_TAX_RATE == 0.0, "capital-gains tax 0")
    check(config.DIVIDEND_WITHHOLDING == 0.15, "US dividend withholding at the treaty 15%")
    check(costs.net_upside(30.0, config.CAPGAINS_TAX_RATE, config.TRADING_COST_BPS) == 29.8,
          "net upside = gross - 0.2% cost, no tax")
    html = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
    js = open(os.path.join(ROOT, "static", "js", "card_value.js"), encoding="utf-8").read()
    check("~15%" not in html and "~15%" not in js, "no page still says the tax is ~15%")


def _series(n_names, T, seed=1):
    r = random.Random(seed)
    f = [r.gauss(0, 0.01) for _ in range(T)]
    return {f"S{i}": [0.8 * f[t] + r.gauss(0, 0.012) * (1 + i / 10) for t in range(T)]
            for i in range(n_names)}


def test_ledoit_wolf():
    print("2 Ledoit-Wolf shrinkage")
    data = _series(6, 300)
    tk = sorted(data)
    cov, d = ledoit_wolf(data, tk)
    check(0.0 <= d <= 1.0, "delta in [0, 1]", str(d))
    T = 300
    for i, t in enumerate(tk[:2]):
        x = data[t]
        m = sum(x) / T
        var = sum((v - m) ** 2 for v in x) / T * 252
        check(abs(cov[i][i] - var) < 1e-12, f"{t}: own variance kept")
    # PSD: x' C x >= 0 for random directions
    r = random.Random(4)
    ok = all(sum(v[i] * cov[i][j] * v[j] for i in range(6) for j in range(6)) >= -1e-15
             for v in ([r.gauss(0, 1) for _ in range(6)] for _ in range(200)))
    check(ok, "positive semi-definite on 200 random directions")
    # known answer, pinned from the published covCor algorithm (numpy port, 2026-11-11)
    check(abs(d - PINNED_DELTA) < 1e-9, "delta matches the reference implementation", f"{d!r}")
    check(ledoit_wolf({"A": [0.01, 0.02]}, ["A"]) == (None, None), "one name -> None")
    src = open(os.path.join(ROOT, "pipeline", "risk_report.py"), encoding="utf-8").read()
    check("_crisis(cov_raw," in src and "_correlation(rdata, tickers, weights, book, cov_raw," in src,
          "Correlation tab and crisis regime read the sample matrix")


PINNED_DELTA = 0.6352458135810516   # numpy port of Ledoit & Wolf's covCor.m on _series(6, 300)


def test_bootstrap_var():
    print("3 block bootstrap VaR")
    r = random.Random(3)
    p = [r.gauss(0.0, 0.012) for _ in range(500)]
    a, b = bootstrap_var(p), bootstrap_var(p)
    check(a == b, "seeded: same history, same answer")
    up = bootstrap_var([x + 0.003 for x in p])
    check(up["var95_pct"] == a["var95_pct"], "drift removed: a bull window does not shrink VaR",
          f"{up['var95_pct']} vs {a['var95_pct']}")
    h = historical_var(p)
    check(a["var95_pct"] > 0 and abs(a["var95_pct"] - h["var95_pct"]) < 0.5 * h["var95_pct"],
          "same order as the sqrt-scaled historical VaR", f"{a['var95_pct']} vs {h['var95_pct']}")
    check(a["var99_pct"] > a["var95_pct"] and a["cvar95_pct"] >= a["var95_pct"], "99 > 95, CVaR >= VaR")
    check(set(a["p_loss"]) == {"10", "20", "30"} and a["p_loss"]["10"] >= a["p_loss"]["30"],
          "loss odds fall with depth")
    check(bootstrap_var(p[:50])["var95_pct"] is None, "under 120 days: no number")


TX = [{"id": 1, "type": "OPEN", "date": "2026-01-02", "ticker": "AAA", "shares": 10, "price": 50, "mkt_price": 80},
      {"id": 2, "type": "OPEN", "date": "2026-01-02", "amount": 1000},
      {"id": 3, "type": "DEPOSIT", "date": "2026-02-01", "amount": 2000, "thb": 66000},
      {"id": 4, "type": "BUY", "date": "2026-02-02", "ticker": "BBB", "shares": 20, "price": 100, "fee": 2},
      {"id": 5, "type": "BUY", "date": "2026-02-03", "ticker": "AAA", "shares": 10, "price": 90, "fee": 1},
      {"id": 6, "type": "SELL", "date": "2026-06-01", "ticker": "AAA", "shares": 5, "price": 120, "fee": 1},
      {"id": 7, "type": "DIVIDEND", "date": "2026-06-15", "ticker": "BBB", "amount": 40},
      {"id": 8, "type": "SPLIT", "date": "2026-07-01", "ticker": "BBB", "ratio": 2},
      {"id": 9, "type": "WITHDRAW", "date": "2026-08-01", "amount": 100, "thb": 3300}]


def test_ledger_math():
    print("4 ledger arithmetic")
    b = L.replay(TX, 0.15)
    h = L.holdings(b)
    check(h["AAA"] == {"shares": 15.0, "avg_cost": 70.05}, "AAA avg cost (500 + 901) / 20, unchanged by the sale", str(h["AAA"]))
    check(h["BBB"] == {"shares": 40.0, "avg_cost": 50.05}, "BBB split 2:1 halves the average", str(h["BBB"]))
    tr = b["trades"][0]
    check(abs(tr["pnl"] - (599 - 5 * 70.05)) < 1e-6, "realized = proceeds - fee - avg x shares", str(tr["pnl"]))
    check(abs(b["cash"] - (1000 + 2000 - 2002 - 901 + 599 + 34 - 100)) < 1e-6, "cash: dividend net of 15%", str(b["cash"]))
    v = L.view(b, {"AAA": 130, "BBB": 55}, "2026-11-11", usdthb=33.0)
    short = L.view(b, {"AAA": 130, "BBB": 55}, "2026-01-20")
    check(short["xirr_pct"] is None and "30" in short["xirr_note"], "no XIRR under 30 days")
    check(v["xirr_pct"] is not None, "XIRR after 30 days")
    flows = sum(f["amount"] for f in b["flows"])
    check(abs(v["since_start_gain"] - (v["nav"] - b["open_value"] - flows)) < 1e-6,
          "sum of per-name since-start P&L = NAV - start value - net deposits")
    check(abs(v["total_gain"] - (v["realized"] + v["unrealized"] + v["dividends_net"])) < 0.01,
          "gain over principal = realized + unrealized + dividends")
    check(v["principal_usd"] == 3400.0 and v["principal_share_pct"] == round(3400 / v["nav"] * 100, 1),
          "principal = opening cost 1,500 + deposits 2,000 - withdrawal 100")
    check(v["thb"]["principal_estimated_usd"] == 1500.0, "opening book has no baht amount: estimated at today's rate")
    check(v["closed"]["n"] == 1 and v["closed"]["win_rate_pct"] == 100.0, "closed-trade stats")
    # implied deposit: a buy the cash cannot pay
    b2 = L.replay([{"id": 1, "type": "BUY", "date": "2026-03-01", "ticker": "X", "shares": 2, "price": 50}])
    check(b2["implied_deposits"] == [{"date": "2026-03-01", "ticker": "X", "amount": 100.0, "id": 1}]
          and b2["cash"] == 0.0 and b2["principal_usd"] == 100.0, "unfunded buy -> implied deposit, listed")
    try:
        L.replay(TX + [{"id": 10, "type": "SELL", "date": "2026-09-01", "ticker": "BBB", "shares": 41, "price": 1}])
        check(False, "overselling raises")
    except L.LedgerError as e:
        check("only 40 held" in str(e), "overselling raises, naming the holding", str(e))
    check(abs(L.xirr([("2025-01-01", -100), ("2026-01-01", 110)]) - 0.10) < 1e-6, "XIRR of +10% in a year")
    check(L.xirr([("2026-01-01", -100)]) is None, "XIRR needs flows both ways")
    for bad in ({"type": "BUY", "date": "2026-01-01", "ticker": "X", "shares": 0, "price": 1},
                {"type": "DEPOSIT", "date": "01/02/2026", "amount": 5},
                {"type": "SPLIT", "date": "2026-01-01", "ticker": "X"}):
        try:
            L.validate(bad)
            check(False, f"rejects {bad}")
        except L.LedgerError:
            check(True, f"rejects {bad['type']} with a bad field")
    log = [{"date": "d1", "nav": 100, "flow": 0}, {"date": "d2", "nav": 80, "flow": 0},
           {"date": "d3", "nav": 140, "flow": 50}, {"date": "d4", "nav": 70, "flow": 0}]
    # index 1 -> 0.8 -> 0.9 (90/80, the deposit netted out) -> 0.45 (70/140): worst -55%
    check(L.max_drawdown(log) == -55.0, "max drawdown on the flow-netted index", str(L.max_drawdown(log)))


def _store_with_book():
    store.today = lambda: "2026-11-11"          # the app's clock, pinned: the dates below are fixed
    s = _fresh()
    store.set_holding(s, "AAA", 10, 50.0)
    store.set_cash(s, 1000)
    s["momentum"] = {"AAA": {"price": 80.0, "price_date": "2026-11-10"}}
    return s


def test_store_ledger():
    print("5 store: ledger drives holdings, cash and NAV flows")
    import store_sync
    store_sync.mark_local_edit = lambda: None
    s = _store_with_book()
    try:
        store.ledger_save(s, {"type": "DEPOSIT", "date": "2026-11-11", "amount": 1})
        check(False, "a first entry over an existing book is refused")
    except L.LedgerError:
        check(s["holdings"]["AAA"]["shares"] == 10, "a first entry over an existing book is refused, book kept")
    store.ledger_start(s, usdthb=33.0)
    tx = s["ledger"]["txns"]
    check([t["type"] for t in tx] == ["OPEN", "OPEN"] and tx[0]["price"] == 50.0 and tx[0]["mkt_price"] == 80.0,
          "OPEN at average cost, with today's close", str(tx))
    for fn, args in ((store.set_holding, ("AAA", 5, 1.0)), (store.set_cash, (5,)), (store.remove_holding, ("AAA",))):
        try:
            fn(s, *args)
            check(False, f"{fn.__name__} refused")
        except L.LedgerError:
            check(True, f"{fn.__name__} refused once the ledger runs")
    store.ledger_save(s, {"type": "BUY", "date": store.today(), "ticker": "bbb", "shares": "4", "price": 100, "fee": 1})
    check(s["holdings"]["BBB"]["shares"] == 4.0 and s["cash"]["usd"] == 599.0, "a buy moves holdings and cash")
    before = json.dumps(s, sort_keys=True)
    try:
        store.ledger_save(s, {"type": "SELL", "date": store.today(), "ticker": "BBB", "shares": 9, "price": 100})
    except L.LedgerError:
        pass
    check(json.dumps(s, sort_keys=True) == before, "a rejected sell leaves the store as it was")
    store.ledger_save(s, {"type": "SELL", "date": store.today(), "ticker": "BBB", "shares": 4, "price": 110})
    check("BBB" not in s["holdings"], "sold out -> leaves the book")
    try:
        store.ledger_save(s, {"type": "DEPOSIT", "date": "2000-01-01", "amount": 5})
        check(False, "a date before the ledger started is refused")
    except L.LedgerError as e:
        check("ก่อนวันเริ่ม" in str(e), "a date before the ledger started is refused")
    sid = s["ledger"]["txns"][-1]["id"]
    store.ledger_delete(s, sid)
    check(s["holdings"]["BBB"]["shares"] == 4.0, "deleting the sell restores the holding")

    # NAV flows: deposits counted once, a late-typed one in the next period
    from pipeline import performance as P
    s["momentum"]["BBB"] = {"price": 100.0, "price_date": "2026-11-10"}
    s["market"] = {"spy_close": 500.0, "as_of": "2026-11-10"}
    P.record(s)
    store.ledger_save(s, {"type": "DEPOSIT", "date": "2026-11-11", "amount": 300})
    s["market"] = {"spy_close": 505.0, "as_of": "2026-11-11"}
    for t in ("AAA", "BBB"):
        s["momentum"][t]["price_date"] = "2026-11-11"
    e = P.record(s)
    check(e["flow"] == 300.0, "a deposit is the period's flow", str(e))
    s["ledger"]["started"] = "2026-11-01"          # the late deposit below falls after the start
    store.ledger_save(s, {"type": "DEPOSIT", "date": "2026-11-05", "amount": 50})   # typed in late
    s["market"] = {"spy_close": 510.0, "as_of": "2026-11-12"}
    for t in ("AAA", "BBB"):
        s["momentum"][t]["price_date"] = "2026-11-12"
    e = P.record(s)
    check(e["flow"] == 50.0, "a back-dated deposit lands in the next period, once", str(e))
    e = P.record(s)
    check(e["flow"] == 50.0, "re-recording the same day does not count it twice", str(e))
    # Bangkok enters a deposit dated a day after the New York close the NAV is priced on
    store.ledger_save(s, {"type": "DEPOSIT", "date": "2026-11-13", "amount": 200})
    e = P.record(s)                                   # still the 2026-11-12 close
    check(e["flow"] == 250.0, "a deposit dated after the close in use is that period's flow", str(e))
    from domain import performance as perf_dom
    r = [x for x in perf_dom.periods(s["performance"]["nav"])][-1][1]
    check(abs(r) < 0.05, "and does not show as a return", f"{r:.4f}")
    v = P.ledger_view(s)
    check(v["active"] and v["nav"] > 0 and "max_drawdown_pct" in v["performance"], "ledger view")


def test_api():
    print("6 API endpoints on a temp store")
    import app
    import store_sync
    from sources import gdrive_store
    gdrive_store.enabled = lambda: False
    with tempfile.TemporaryDirectory() as d:
        config.DATA_DIR, store.PATH = d, os.path.join(d, "portfolio.json")
        s = _store_with_book()
        store.save(s)
        body = lambda r: json.loads(r.body) if hasattr(r, "body") else r
        r = body(app.api_ledger_start())
        check(r["ledger"]["active"] and r["ledger"]["principal_usd"] == 1500.0, "start", str(r["ledger"].get("principal_usd")))
        r = app.api_ledger_txn(app.Txn(type="DEPOSIT", date="2026-11-11", amount=200, thb=6600))
        check(body(r)["ledger"]["cash"] == 1200.0, "deposit via API")
        r = app.api_ledger_txn(app.Txn(type="SELL", date="2026-11-11", ticker="AAA", shares=99, price=1))
        check(r.status_code == 422 and "only 10 held" in body(r)["error"], "oversell -> 422 with the reason")
        r = app.api_set_cash(5)
        check(r.status_code == 409, "old cash editor -> 409 once the ledger runs")
        r = app.api_ledger_delete(999)
        check(r.status_code == 422, "unknown id -> 422")
        store_sync.wait_push(timeout=5.0)


def test_docs_quote_the_numbers_the_code_runs():
    print("7 How-to / Ref carry this round's numbers")
    from domain.risk import stress as S
    html = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
    for want in (f"ภาษี capital gains {config.CAPGAINS_TAX_RATE * 100:g}%",
                 f"ภาษีปันผล {config.DIVIDEND_WITHHOLDING * 100:g}%",
                 f"ครบ {L.XIRR_MIN_DAYS} วัน", f"ช่วงละ {S.BOOT_BLOCK} วัน", f"{S.BOOT_SIMS:,} รอบ",
                 "6) หน้า Performance", "Ledoit-Wolf δ"):
        check(want in html, f"index.html says: {want}")


if __name__ == "__main__":
    run_suite(globals(), "ALL P3 (TAX / RISK / LEDGER) TESTS PASSED")
