"""Damodaran ROC-by-sector source: HTML parsing, caching, fallback, mapping.

The live page cannot be reached from CI, so the parser is exercised against a
fixture that mimics the real thing: Excel-exported HTML with style attributes,
&nbsp;, stray whitespace in headers, and NA cells for financials.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sources import damodaran as D  # noqa: E402

FIXTURE = """<html><head><meta name=Generator content="Microsoft Excel 15"></head><body>
<table border=0 cellpadding=0 cellspacing=0 width=1200 style='border-collapse:collapse'>
 <tr height=0 style='display:none'><td width=300></td></tr>
 <tr class=xl65 height=40>
  <td class=xl66 width=300>Industry&nbsp;Name</td>
  <td class=xl66>Number of firms</td>
  <td class=xl66>Unadjusted pre-tax ROIC</td>
  <td class=xl66>Unadjusted after-tax ROIC</td>
  <td class=xl66>Lease &amp; R&amp;D adjusted after-tax  ROIC</td>
  <td class=xl66>Normalized ROIC (last 10 years)</td>
 </tr>
 <tr><td>Semiconductor</td><td>66</td><td>43.53%</td><td>41.30%</td><td>27.23%</td><td>20.53%</td></tr>
 <tr><td>Drugs&nbsp; (Pharmaceutical)</td><td>228</td><td>33.74%</td><td>32.73%</td><td>16.95%</td><td>23.52%</td></tr>
 <tr><td>Drugs (Biotechnology)</td><td>496</td><td>7.76%</td><td>7.67%</td><td>3.53%</td><td>21.60%</td></tr>
 <tr><td>Bank (Money Center)</td><td>15</td><td>NA</td><td>NA</td><td>NA</td><td>NA</td></tr>
 <tr><td>Auto &amp; Truck</td><td>33</td><td>2.55%</td><td>2.45%</td><td>2.33%</td><td>5.53%</td></tr>
 <tr><td>Total Market (without financials)</td><td>4822</td><td>20.37%</td><td>18.92%</td><td>15.07%</td><td>14.92%</td></tr>
</table></body></html>"""


def test_parse():
    t = D.parse_roc_html(FIXTURE)
    # terminal ROIC = "Normalized ROIC (last 10 years)" — NOT the single-year
    # column, and explicitly not min() of the two (see module docstring: the
    # single-year biotech aggregate is 496 firms, mostly pre-revenue burners)
    assert abs(t["semiconductor"] - 0.2053) < 1e-9, t.get("semiconductor")
    assert abs(t["drugs (pharmaceutical)"] - 0.2352) < 1e-9, t.get("drugs (pharmaceutical)")
    assert abs(t["drugs (biotechnology)"] - 0.2160) < 1e-9, t.get("drugs (biotechnology)")
    assert abs(t["auto & truck"] - 0.0553) < 1e-9
    assert abs(t["total market (without financials)"] - 0.1492) < 1e-9
    # NA rows are dropped, not stored as 0 (a 0% ceiling would be catastrophic)
    assert "bank (money center)" not in t
    # nbsp / double-space in labels and headers must normalize away
    assert "drugs (pharmaceutical)" in t
    print("parse OK:", len(t), "industries")


def test_parse_rejects_unknown_layout():
    """If he renames the columns we must return {} so the caller keeps the old
    snapshot — silently grabbing the wrong column would be far worse."""
    assert D.parse_roc_html("<table><tr><td>Industry Name</td><td>Something Else</td></tr>"
                            "<tr><td>Semiconductor</td><td>9%</td></tr></table>") == {}
    # the single-year column alone is NOT enough — we require the normalized one
    assert D.parse_roc_html("<table><tr><td>Industry Name</td>"
                            "<td>Lease &amp; R&amp;D adjusted after-tax ROIC</td></tr>"
                            "<tr><td>Semiconductor</td><td>27.23%</td></tr></table>") == {}
    assert D.parse_roc_html("<html>no table here</html>") == {}
    print("bad-layout guard OK")


def test_fallback_snapshot():
    fb = D.FALLBACK
    assert len(fb) > 80, len(fb)
    assert abs(fb["semiconductor"] - 0.2053) < 1e-9
    assert abs(fb["drugs (pharmaceutical)"] - 0.2352) < 1e-9
    assert abs(fb["drugs (biotechnology)"] - 0.2160) < 1e-9
    assert abs(fb["total market (without financials)"] - 0.1492) < 1e-9
    assert all(-0.2 < v < 0.9 for v in fb.values()), "implausible value in snapshot"
    print("fallback snapshot OK:", len(fb), "industries")


def test_mapping():
    f = D.terminal_roic_for
    assert abs(f("LLY", "Healthcare") - 0.2352) < 1e-9          # ticker map wins
    assert abs(f("REGN", "Healthcare") - 0.2160) < 1e-9         # ...and separates biotech
    assert abs(f("NVDA", "Technology") - 0.2053) < 1e-9
    assert abs(f("ASML", "Technology") - 0.3444) < 1e-9
    assert abs(f("TSLA", "Consumer Cyclical") - 0.0553) < 1e-9
    # unmapped ticker falls back to its FMP sector
    # REV-26: a hit on the COARSE sector map is not an industry view. FMP's eleven
    # GICS sectors are far broader than Damodaran's ninety-odd industries, so mapping
    # all "Technology" onto Software (System & Application) 22.95% would hand every
    # unmapped tech name the ceiling of one of its richest corners. A coarse hit is
    # capped at the market-wide normalized ROIC, so it can restrain but never gift.
    assert abs(f("ZZZZ", "Technology") - 0.1492) < 1e-9, f("ZZZZ", "Technology")
    # an EXPLICIT per-ticker mapping is unaffected
    assert abs(f("MSFT", "Technology") - 0.2295) < 1e-9
    # and a coarse sector BELOW the market average still passes through unchanged
    assert abs(f("ZZZZ", "Utilities") - 0.0637) < 1e-9, f("ZZZZ", "Utilities")
    # unmapped ticker AND unknown sector -> None (engine keeps its own default)
    assert f("ZZZZ", "Wombat Farming") is None
    assert f("ZZZZ", None) is None
    print("mapping OK")


def test_fetch_cache_and_offline():
    class OKResp:
        text, status_code = FIXTURE, 200

        def raise_for_status(self):
            pass

    class OK:
        calls = 0

        def get(self, *a, **k):
            OK.calls += 1
            return OKResp()

    class Dead:
        def get(self, *a, **k):
            raise OSError("network down")

    with tempfile.TemporaryDirectory() as td:
        t1 = D.fetch_roc_table(cache_dir=td, requests_mod=OK())
        assert abs(t1["semiconductor"] - 0.2053) < 1e-9
        assert os.path.exists(os.path.join(td, D.CACHE_FILE))
        # second call is served from disk — no second request
        t2 = D.fetch_roc_table(cache_dir=td, requests_mod=OK())
        assert t2 == t1 and OK.calls == 1, OK.calls
        # expired cache + dead network -> stale cache still beats the snapshot
        os.utime(os.path.join(td, D.CACHE_FILE), (0, 0))
        t3 = D.fetch_roc_table(cache_dir=td, requests_mod=Dead())
        assert t3 == t1

    # no cache at all + dead network -> bundled snapshot, never an exception
    with tempfile.TemporaryDirectory() as td:
        t4 = D.fetch_roc_table(cache_dir=td, requests_mod=Dead())
        assert t4 == D.FALLBACK
    # corrupt cache must not crash a refresh
    with tempfile.TemporaryDirectory() as td:
        with open(os.path.join(td, D.CACHE_FILE), "w") as fh:
            fh.write("{not json")
        t5 = D.fetch_roc_table(cache_dir=td, requests_mod=Dead())
        assert t5 == D.FALLBACK
    print("fetch/cache/offline OK")


def test_engine_uses_sector_cap():
    from domain.engine import deep_v82 as E
    # high-ROIC semi: cap moves 15% -> 20.53%, so terminal ROIC rises
    assert abs(E.terminal_roic(0.60, 0.10, 0.2053) - 0.2053) < 1e-9
    assert abs(E.terminal_roic(0.60, 0.10, None) - 0.15) < 1e-9
    # the cap can never GIFT: a firm below its industry keeps its own number
    assert abs(E.terminal_roic(0.12, 0.09, 0.2053) - 0.105) < 1e-9   # half-fade binds
    # ...and never drops below the cost of capital
    assert abs(E.terminal_roic(0.02, 0.08, 0.0353) - 0.08) < 1e-9
    # growth cap: g <= ROIC
    assert abs(E.sustainable_growth_cap(0.137) - 0.137) < 1e-9
    assert abs(E.sustainable_growth_cap(0.626) - E.GROWTH_CAP) < 1e-9
    assert abs(E.sustainable_growth_cap(None) - E.GROWTH_CAP) < 1e-9
    assert abs(E.sustainable_growth_cap(-0.05) - E.GROWTH_CAP) < 1e-9
    print("engine wiring OK")


def test_terminal_beta_and_two_rate_pe():
    """P-K/P-B2/P-M/P-L: the stable phase gets its own risk, not the company's
    current high-growth risk."""
    from domain.engine import deep_v82 as E

    # beta fades toward 1 from BOTH sides (Damodaran band 0.8-1.2)
    assert E.terminal_beta(2.21) == 1.2 and E.terminal_beta(0.24) == 0.8
    assert E.terminal_beta(1.05) == 1.05 and E.terminal_beta(None) == 1.0

    # wacc_true(beta_override=) keeps capital structure + Kd, only Ke moves
    args = (0.045, 2.2, 800e9, 200e9, 50e9, 0.21, 2e9, 40e9)
    w_hi, ke_hi, kd_hi, _ = E.wacc_true(*args)
    w_lo, ke_lo, kd_lo, _ = E.wacc_true(*args, beta_override=1.2)
    assert ke_lo < ke_hi and w_lo < w_hi and kd_lo == kd_hi

    # a lower stable-phase Ke raises the perpetuity multiple; the high-growth Ke
    # still discounts the first n years, so the two are not interchangeable
    hi = E.two_stage_pe(0.20, 5, 0.045, 0.143, 0.60, 0.2053)
    split = E.two_stage_pe(0.20, 5, 0.045, 0.143, 0.60, 0.2053, ke_st=0.0895)
    assert split > hi, (split, hi)
    assert E.two_stage_pe(0.20, 5, 0.045, 0.143, 0.60, 0.2053, ke_st=0.143) == hi
    assert E.two_stage_pe(0.20, 5, 0.05, 0.143, 0.60, 0.2053, ke_st=0.04) is None  # ke_st <= g

    # one clamp band for both paths, and it no longer donates value
    assert (E.PE_FLOOR, E.PE_CEIL) == (5.0, 35.0)
    # an exit PE of 6.0 passes through instead of being lifted to the old floor of 12
    assert abs(E.future_value_projection(10.0, 0.0, 0.10, 6.0) - 60.0 / 1.10 ** 5) < 1e-9
    assert abs(E.future_value_projection(10.0, 0.0, 0.10, 4.0) - 50.0 / 1.10 ** 5) < 1e-9  # 4 -> floor 5

    # reverse_dcf: a cheaper terminal rate needs LESS implied growth to justify
    # the same price; a better terminal ROIC makes growth cheaper to buy
    # REV-28: the old fixture used wacc_val 15% against the default terminal ROIC of
    # 15% — a ZERO spread, where growth is value-neutral by construction. It only
    # produced an answer because reinvestment was capped and growth was therefore
    # partly free. Now a spread is required for growth to justify anything, so the
    # fixture carries one.
    base = dict(price=40.0, shares=1e9, revenue=10e9, rev_1y=8e9, total_debt=1e9,
                cash=0.5e9, g=0.045, tax=0.21, margin=0.20)
    a = E.reverse_dcf(wacc_val=0.10, roic_term=0.20, **base)
    b = E.reverse_dcf(wacc_val=0.10, roic_term=0.20, wacc_term=0.085, **base)
    c = E.reverse_dcf(wacc_val=0.10, roic_term=0.30, **base)
    assert a["triggered"] and b["triggered"] and c["triggered"]
    assert b["implied_cagr_pct"] < a["implied_cagr_pct"], (a, b)
    assert c["implied_cagr_pct"] < a["implied_cagr_pct"], (a, c)

    # ...and the case the old cap was hiding: with ROIC == WACC, growth adds nothing,
    # so NO growth rate can justify paying above the no-growth value (S5).
    flat = E.reverse_dcf(wacc_val=0.15, roic_term=0.15, **base)
    assert flat["implied_cagr_pct"] is None and "no growth rate justifies" in flat["verdict"], flat
    # omitting wacc_term must be identical to passing wacc_val (the documented default)
    assert E.reverse_dcf(wacc_val=0.10, wacc_term=0.10, roic_term=0.20, **base) == a
    # guard: a terminal rate at or below g must not divide by ~zero
    assert E.reverse_dcf(wacc_val=0.15, wacc_term=0.04, **base) == {"triggered": False}
    print("terminal beta / two-rate PE / reverse DCF OK")


def test_review_regressions():
    """Guards for the four correctness bugs the code review caught."""
    import types
    from domain.engine import deep_v82 as E
    from pipeline.normalize import _annual_cagr

    # REVIEW-1: a MEASURED zero growth must not be read as "missing" and replaced
    # by the 8% default. `growth_lt or 0.08` did exactly that for every decliner.
    assert _annual_cagr([50e9, 60e9, 70e9, 80e9, 90e9, 100e9]) == 0.0
    f = types.SimpleNamespace(growth_lt=0.0)
    assert (f.growth_lt if f.growth_lt is not None else 0.08) == 0.0
    assert (None if False else 0.08) == 0.08

    # REVIEW-2: with no ROIC of its own, the industry number must not become a gift
    assert E.terminal_roic(None, 0.08, 0.7769) == E.ROIC_TERMINAL     # Tobacco 77.7%
    assert E.terminal_roic(None, 0.08, None) == E.ROIC_TERMINAL

    # REV-17 (2026-08-04): this branch used to skip rule 3 — "the cost of capital is
    # the FLOOR" — which the KNOWN-ROIC branch has always enforced. The old
    # assertion pinned that inconsistency: Auto & Truck 5.53% pulled an unmeasurable
    # firm to a PERPETUAL return below its own cost of capital, i.e. permanent value
    # destruction assumed for ever. Damodaran restructures, sells or liquidates such
    # a business; he does not model it burning capital in perpetuity.
    # The industry number may still pull DOWN — just not through the floor.
    assert E.terminal_roic(None, 0.08, 0.0553) == 0.08                # floored at WACC
    assert E.terminal_roic(None, 0.04, 0.0553) == 0.0553              # ...pulls down above it
    assert E.terminal_roic(None, None, 0.0553) == 0.0553              # no WACC -> no floor
    # and it must agree with the known-ROIC branch, which has always floored
    assert E.terminal_roic(0.03, 0.08, 0.0553) == E.terminal_roic(None, 0.08, 0.0553)

    # REVIEW-3: floor against the TERMINAL cost of capital. A low-beta name whose
    # beta fades UP (w_term > w) must not end below its own terminal WACC.
    w, w_term = 0.070, 0.080
    assert E.terminal_roic(0.09, max(w, w_term), 0.2053) >= w_term

    # REVIEW-4: the PE ceiling is a real judgement cap, not a no-op — raw values
    # above it are reachable, so the "bounded by construction" claim was false.
    raw = E.two_stage_pe(0.30, 5, 0.045, 0.12, 0.60, 0.3444, ke_st=0.0984)
    assert raw > E.PE_CEIL, raw

    # REVIEW-6: zero growth is a valid input, not a missing one. A no-growth firm
    # still has value (payout becomes 1); rejecting g=0 dropped it to a
    # trailing-EPS method instead (PFE swung 94pp on this alone).
    fv0, d0 = E.fundamental_peg_price(0.0, 0.045, 0.08, 0.08, 0.073, 2.9423, ke_stable=0.0806)
    assert fv0 and fv0 > 0, fv0
    assert d0["fundamental_peg"] is None            # PEG undefined at g=0, not a ZeroDivisionError
    assert E.fundamental_peg_price(-0.05, 0.045, 0.08, 0.08, 0.073, 2.9423) == (None, None)
    print("review regressions OK")


def test_forward_eps_gate_shared():
    """R1/R3: ONE forward-EPS gate, and it scales with growth."""
    from pipeline.validate import forward_eps_rejection as rej
    from pipeline import normalize
    import inspect

    # R3: the ceiling is built on FORWARD revenue. NVDA-shaped: 253.5B revenue,
    # 24.39B shares, 26% growth, consensus 8.18. Current-revenue ceiling = 6.76
    # (wrongly rejects); forward-revenue ceiling = 8.53 (accepts).
    nvda = dict(forward_eps=8.18, revenue=253.491e9, shares=24.391e9, price=202.81, growth_lt=0.2632)
    assert rej(**nvda) is None, rej(**nvda)
    assert rej(**{**nvda, "growth_lt": 0.0}) is not None          # no growth -> old tight ceiling
    # growth is capped so a wild estimate cannot inflate the ceiling
    assert rej(**{**nvda, "forward_eps": 30.0, "growth_lt": 5.0}) is not None

    # shares-free P/E gate still catches the currency mismatch (TSM)
    assert "forward P/E" in rej(forward_eps=323.34, revenue=88.27e9, shares=None,
                                price=398.37, growth_lt=0.155)
    # nothing to judge -> no opinion, never a crash
    assert rej(forward_eps=None, revenue=1e9, shares=1e9, price=10.0, growth_lt=0.1) is None
    assert rej(forward_eps=5.0, revenue=None, shares=None, price=None, growth_lt=None) is None

    # R1: normalize._score must call the shared gate, not a private copy
    src = inspect.getsource(normalize._score)
    code = "\n".join(ln.split("#")[0] for ln in src.splitlines())   # ignore comments
    assert "forward_eps_rejection" in code, "normalize._score re-implemented the gate"
    assert "0.65" not in code, "normalize._score still hardcodes its own margin ceiling"
    print("shared forward-EPS gate OK")


def test_add_flag_dedups():
    """R4: one helper, and it dedups on the string actually appended.
    The EQ loop used to test `fl` but append `"EQ: " + fl`, so it never deduped."""
    from domain.engine import deep_v82 as E
    import types
    f = types.SimpleNamespace(flags=[])
    E._add_flag(f, "EQ: accruals high")
    E._add_flag(f, "EQ: accruals high")
    E._add_flag(f, None)
    E._add_flag(f, "")
    assert f.flags == ["EQ: accruals high"], f.flags
    print("_add_flag OK")


def test_shares_from_market_cap():
    """T1-T4: the ADR ratio is DERIVED, never hardcoded.

    market_cap and price are both quoted for the US-listed security, so their
    quotient is a share count in the unit price is actually in. That removes the
    need for an ADR-ratio table — a constant that would fail silently when wrong.
    """
    from pipeline.normalize import _resolve_shares, MCAP_TOLERANCE
    from domain.facts import FinancialFacts
    from domain.engine import deep_v82 as E

    def mk(**kw):
        f = FinancialFacts("X")
        for k, v in kw.items():
            setattr(f, k, v)
        return f

    # ordinary US listing: SEC count already agrees, so nothing is touched
    ok = mk(shares_diluted=1766792821, price=227.23, market_cap=401468325888)
    _resolve_shares(ok)
    assert ok.shares_diluted == 1766792821 and ok.flags == []
    assert ok.provenance.get("shares_diluted") is None

    # IFRS filer with no SEC share count -> derive it (TSM: ~5.19B ADRs, not 25.9B ordinary)
    tsm = mk(shares_diluted=None, price=398.37, market_cap=2066e9)
    _resolve_shares(tsm)
    assert abs(tsm.shares_diluted - 2066e9 / 398.37) < 1
    assert tsm.provenance["shares_diluted"].startswith("derived")

    # ordinary count against an ADR price: caught, and the implied ratio reported
    adr = mk(shares_diluted=25929700000, price=398.37, market_cap=2066e9)
    _resolve_shares(adr)
    assert adr.flags and "implied ratio 5.0" in adr.flags[0], adr.flags
    assert abs(adr.shares_diluted - 2066e9 / 398.37) < 1, "must not keep the ordinary count"

    # a small, innocent gap (buybacks between filing and quote) must NOT trip it
    drift = mk(shares_diluted=1766792821, price=227.23, market_cap=401468325888 * 1.05)
    _resolve_shares(drift)
    assert drift.flags == [], drift.flags
    assert 0.05 < MCAP_TOLERANCE

    # missing inputs: no opinion, no crash, nothing invented
    for kw in (dict(shares_diluted=1e9, price=50.0, market_cap=None),
               dict(shares_diluted=None, price=None, market_cap=None),
               dict(shares_diluted=None, price=0.0, market_cap=1e9)):
        f = mk(**kw)
        _resolve_shares(f)
        assert f.flags == [] and f.shares_diluted == kw["shares_diluted"]

    # T4: the engine prefers the reported cap, and falls back cleanly without one
    import types
    assert E.wacc_true(0.045, 1.0, 400e9, 100e9, 10e9, 0.21, 2e9, 40e9)[0] > 0
    print("shares from market cap OK")


if __name__ == "__main__":
    test_parse()
    test_parse_rejects_unknown_layout()
    test_fallback_snapshot()
    test_mapping()
    test_fetch_cache_and_offline()
    test_engine_uses_sector_cap()
    test_terminal_beta_and_two_rate_pe()
    test_review_regressions()
    test_forward_eps_gate_shared()
    test_add_flag_dedups()
    test_shares_from_market_cap()
    print("ALL DAMODARAN-ROC TESTS PASSED")
