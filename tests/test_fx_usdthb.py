"""USD/THB series for the Correlation tab: Yahoo's London-midnight FX stamps land on
the right date, today's live bar is dropped, and a failed fetch never breaks /api/risk."""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sources import yahoo
from pipeline import risk_report
from tests.test_risk_report import _store, _fake_provider


def _ts(iso):
    return int(dt.datetime.fromisoformat(iso).replace(tzinfo=dt.timezone.utc).timestamp())


# Yahoo THB=X in London summer time: each bar stamped 23:00 UTC the evening before
BST = {"meta": {"gmtoffset": 3600},
       "timestamp": [_ts("2026-10-21T23:00:00"), _ts("2026-10-22T23:00:00"),
                     _ts("2026-10-23T23:00:00"), _ts("2026-10-26T00:00:00"),
                     _ts("2026-10-28T00:00:00")],
       "indicators": {"quote": [{"close": [32.41, None, 32.55, 32.6, 32.7]}]}}


def test_dates_follow_the_exchange_day_not_utc():
    pts = yahoo.parse_fx_closes(BST, today="2026-10-29")
    assert [p["d"] for p in pts] == ["2026-10-22", "2026-10-24", "2026-10-26", "2026-10-28"]
    assert [p["v"] for p in pts] == [32.41, 32.55, 32.6, 32.7]


def test_todays_live_bar_is_not_a_close():
    pts = yahoo.parse_fx_closes(BST, today="2026-10-28")
    assert pts[-1] == {"d": "2026-10-26", "v": 32.6}


def _build(fetch_fx):
    tks = ["AAA", "BBB", "CCC"]
    payload, _ = risk_report.build(_store(tks), "", 0, 250, 20.0, 1.0, False,
                                   fetch_returns=_fake_provider({}), fetch_long=lambda t: {},
                                   fetch_fx=fetch_fx)
    assert payload["status"] == "ok"
    return payload["correlation"]


def test_build_ships_the_contract_the_panel_reads():
    pts = yahoo.parse_fx_closes(BST, today="2026-10-29")
    fx = _build(lambda: pts)["fx_usdthb"]
    assert fx == {"pair": "USD/THB", "as_of": "2026-10-28", "source": "Yahoo THB=X", "points": pts}


def test_failed_or_thin_fetch_leaves_the_key_out():
    def boom():
        raise RuntimeError("yahoo down")
    assert "fx_usdthb" not in _build(boom)
    assert "fx_usdthb" not in _build(lambda: [{"d": "2026-10-27", "v": 32.6}] * 2)
    assert "fx_usdthb" not in _build(None)


if __name__ == "__main__":
    from tests._harness import run_suite
    run_suite(globals(), "ALL test_fx_usdthb PASSED")
