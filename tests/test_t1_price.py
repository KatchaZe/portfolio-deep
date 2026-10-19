"""test_t1_price — the valuation price is the last completed session's close (T-1).

Run: python -m tests.test_t1_price
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from domain import pricing                       # noqa: E402
from tests._harness import check, run_suite      # noqa: E402

DATES = ["2026-10-14", "2026-10-15", "2026-10-16", "2026-10-19"]
CLOSES = [100.0, 101.0, 102.0, 109.5]            # 2026-10-19 is a partial, live bar


def test_t1_close():
    print("T1 the last session before today in New York")
    check(pricing.t1_close(CLOSES, DATES, "2026-10-19") == (102.0, "2026-10-16"),
          "Monday: Friday's close, today's partial bar dropped")
    check(pricing.t1_close(CLOSES, DATES, "2026-10-20") == (109.5, "2026-10-19"),
          "Tuesday: Monday's close")
    check(pricing.t1_close([], [], "2026-10-19") == (None, None), "no bars, no price")
    c, d, v = pricing.completed_bars(CLOSES, DATES, "2026-10-19", [1, 2, 3, 4])
    check(c == CLOSES[:3] and d == DATES[:3] and v == [1, 2, 3], "every series cut to the same length")


def test_ny_date():
    print("T1b 'today' is the New York date, not UTC or Bangkok")
    # 07:00 Bangkok Tuesday = 00:00 UTC Tuesday = Monday evening in New York
    utc = dt.datetime(2026, 10, 20, 0, 0, tzinfo=dt.timezone.utc)
    check(pricing.ny_today(utc) == "2026-10-19", "Monday evening in New York", pricing.ny_today(utc))


def test_fundamentals_use_t1():
    print("T1c the valuation price and market cap move to the T-1 close")
    from domain.facts import FinancialFacts
    from pipeline import fundamentals
    from sources import yahoo
    ff = FinancialFacts("X")
    ff.set("price", 110.0, "fmp")
    ff.market_cap = 110e9
    orig, today = yahoo.fetch_chart, pricing.ny_today
    yahoo.fetch_chart = lambda *a, **k: {"closes": CLOSES, "dates": DATES}
    pricing.ny_today = lambda now=None: "2026-10-19"
    try:
        fundamentals._t1_price(ff, "X")
    finally:
        yahoo.fetch_chart, pricing.ny_today = orig, today
    check(ff.price == 102.0 and "2026-10-16" in ff.provenance["price"], "price = Friday's close",
          f"{ff.price} {ff.provenance.get('price')}")
    check(abs(ff.market_cap - 102e9) < 1, "market cap scaled with it", str(ff.market_cap))
    g = FinancialFacts("Y")
    g.set("price", 50.0, "fmp")
    yahoo.fetch_chart = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("blocked"))
    try:
        fundamentals._t1_price(g, "Y")
    finally:
        yahoo.fetch_chart = orig
    check(g.price == 50.0 and any("live quote" in x for x in g.flags),
          "chart down: the live quote stays and the card says so")


def test_row_carries_the_close_date():
    print("T1d the card row says which session its price closed on")
    from pipeline import rows
    f = {"price": 101.0, "provenance": {"price": "yahoo close 2026-10-16 (T-1)"}}
    v = {"anchor_value": 120.0}
    kw = dict(rev_surprises=[], has_new_schema=True, fmp_on=False)
    r = rows.valuation_row("X", f, v, {"price": 102.0}, {"price_date": "2026-10-16"}, **kw)
    check(r["price_date"] == "2026-10-16", "momentum price: date from the composite series", str(r["price_date"]))
    r2 = rows.valuation_row("X", f, v, {}, {}, **kw)
    check(r2["price_date"] == "2026-10-16", "no momentum: date from the facts' close", str(r2["price_date"]))


if __name__ == "__main__":
    run_suite(globals(), "ALL test_t1_price PASSED")
