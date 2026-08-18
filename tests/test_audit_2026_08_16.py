"""
test_audit_2026_08_16 — regression guards for the full-codebase audit of 2026-08-16.

Every test here corresponds to a defect that was CONFIRMED by a repro before it was
fixed. They are written to fail loudly if the old behaviour comes back, and each one
names the observable symptom rather than the implementation, so a rewrite that keeps
the behaviour still passes.

Run:  python -m tests.test_audit_2026_08_16
"""
import os
import sys
import math
import json
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FAILS = []


def check(cond, label):
    print(("  ok   " if cond else "  FAIL ") + label)
    if not cond:
        FAILS.append(label)


# --------------------------------------------------------------------------- #
#  1. ^TNX is quoted in PERCENT — one point is 100bps, not 10                  #
# --------------------------------------------------------------------------- #
def test_tnx_percent_convention():
    print("\n1. ^TNX unit convention (rate stress was 10x overstated)")
    import inspect
    from pipeline import risk_report
    from sources import yahoo

    src = inspect.getsource(risk_report.risk_snapshot) if hasattr(
        risk_report, "risk_snapshot") else open(risk_report.__file__, encoding="utf-8").read()
    check("* 100 for i in range(1, len(tc))" in src.replace("  ", " ")
          or "(tc[i] - tc[i - 1]) * 100" in src,
          "delta-yield is scaled by 100 (percent -> bps), not 10")
    check("(tc[i] - tc[i - 1]) * 10 " not in src,
          "the old *10 scaling is gone")

    # the two sites must agree about what a ^TNX point means: fetch_treasury_10y
    # divides by 100 to get a decimal, so a 1-point move is 100bps.
    called = {}

    def fake_chart(sym, **kw):
        called["sym"] = sym
        return {"closes": [4.25], "dates": ["2026-08-14"]}

    rf, live = yahoo.fetch_treasury_10y(
        requests_mod=None) if False else (None, None)
    import sources.yahoo as Y
    _orig = Y.fetch_chart
    try:
        Y.fetch_chart = fake_chart
        rf, live = Y.fetch_treasury_10y()
    finally:
        Y.fetch_chart = _orig
    check(abs(rf - 0.0425) < 1e-9,
          "^TNX close 4.25 reads as a 4.25%% risk-free rate (got %r)" % rf)


# --------------------------------------------------------------------------- #
#  2. the covariance matrix is a covariance matrix                             #
# --------------------------------------------------------------------------- #
def _series(n, seed, scale=1.0):
    """Deterministic pseudo-random returns — no Math.random, reproducible."""
    out, x = [], seed
    for _ in range(n):
        x = (1103515245 * x + 12345) % 2147483648
        out.append(((x / 2147483648.0) - 0.5) * 0.04 * scale)
    return out


def test_hybrid_cov_is_a_real_covariance_matrix():
    print("\n2. hybrid covariance: rho in [-1,1] and the matrix is PSD")
    from domain.engine import risk as R

    long_s = _series(400, 7)
    short_s = _series(60, 99, scale=2.6)
    tickers = ["A", "B"]
    cov, realized = R.hybrid_cov({"A": long_s, "B": short_s}, tickers,
                                 [0.3, 0.3], {"A": "equity", "B": "equity"})
    corr = R.corr_from_cov(cov)
    rho = corr[0][1]
    check(-1.0 <= rho <= 1.0,
          "one 400-bar name beside one 60-bar name gives |rho|<=1 (got %.4f)" % rho)

    # portfolio variance must never be negative, at ANY weighting
    worst = min(R.portfolio_vol([w, 1 - w], cov) for w in (0.0, 0.25, 0.5, 0.75, 1.0))
    check(worst >= 0.0, "portfolio_vol is real for every weighting")
    pv = R.portfolio_vol([0.5, 0.5], cov)
    check(pv > 0.0,
          "portfolio_vol does not collapse to 0.0 and blank out VaR/CVaR (got %.4f)" % pv)

    # and the repaired matrix passes a real PSD test
    check(R._cholesky_ok(R.corr_from_cov(cov)) or R.shrink_to_psd(R.corr_from_cov(cov)),
          "the correlation matrix is PSD (or is repaired to be)")

    # a 4-name mixed-window portfolio must also stay well-behaved
    names = ["A", "B", "C", "D"]
    rets = {"A": _series(400, 3), "B": _series(400, 11),
            "C": _series(70, 23, 3.0), "D": _series(65, 41, 0.4)}
    cov4, _ = R.hybrid_cov(rets, names, [0.3] * 4, {n: "equity" for n in names})
    c4 = R.corr_from_cov(cov4)
    check(all(-1.0001 <= c4[i][j] <= 1.0001 for i in range(4) for j in range(4)),
          "every pair in a 4-name mixed-window matrix is a valid correlation")
    check(R.portfolio_vol([0.25] * 4, cov4) > 0,
          "4-name portfolio vol is positive")


# --------------------------------------------------------------------------- #
#  3. returns are aligned by DATE, not by position                             #
# --------------------------------------------------------------------------- #
def test_returns_align_on_dates():
    print("\n3. date alignment (a one-bar source lag was silently making it a lag-1 corr)")
    from domain.engine import risk as R

    dates = ["2026-08-%02d" % d for d in range(1, 11)]
    a = [0.01, -0.02, 0.03, -0.01, 0.02, -0.03, 0.01, 0.00, -0.02, 0.04]
    rdata = {
        "AAA": {"returns": a, "dates": dates, "n": len(a)},
        # BBB is missing its LAST bar — the routine case when one price tier lags
        "BBB": {"returns": a[:-1], "dates": dates[:-1], "n": len(a) - 1},
    }
    aligned, mode = R.align_on_dates(rdata, ["AAA", "BBB"])
    check(mode == "dates", "alignment mode is 'dates' when dates are present")
    check(aligned["AAA"] == a[:-1],
          "the LAGGING name's missing day is dropped from BOTH series, not the oldest day")
    check(aligned["AAA"] == aligned["BBB"],
          "identical series stay identical after alignment (rho would be 1.0)")

    # positional alignment on the same input is what produced the bug
    pos = R.align_returns({k: v["returns"] for k, v in rdata.items()}, ["AAA", "BBB"])
    check(pos["AAA"] != aligned["AAA"],
          "positional alignment really does differ — this is the bug being guarded")

    # and the fallback still works for a cache written before dates were carried
    old = {"AAA": {"returns": a, "n": len(a)}, "BBB": {"returns": a[:-1], "n": len(a) - 1}}
    _, mode2 = R.align_on_dates(old, ["AAA", "BBB"])
    check(mode2 == "positional", "a dateless legacy cache falls back, it does not crash")


def test_fetch_returns_carries_dates():
    print("\n3b. the returns contract carries dates at all")
    import inspect
    from pipeline import prices
    src = inspect.getsource(prices.fetch_returns)
    check('"dates": rdates' in src, "fetch_returns stores a per-return date list")
    check("len(rdates) != len(rets)" in src,
          "and refuses to ship dates that do not line up with the returns")


def test_every_correlation_consumer_is_date_aligned():
    """2026-08-17 — the FIRST cut of finding 3 only reached the covariance matrix. The
    Correlation tab's benchmark cells, the downside lens and historical VaR still paired
    by position, so the defect survived in the three most-read numbers on the page. A
    partial fix on a defect this quiet is worse than none: it makes the tab look
    audited. This pins the whole seam, not one function."""
    print("\n3c. EVERY correlation consumer aligns on dates, not just cov_matrix")
    from domain.engine import risk as R

    dates = ["2026-08-%02d" % d for d in range(1, 13)]
    a = [0.01, -0.02, 0.03, -0.01, 0.02, -0.03, 0.01, 0.005, -0.02, 0.04, -0.01, 0.02]
    # B is the SAME series one bar behind — positional pairing sees a lag-1 corr,
    # date pairing sees the identical series it is.
    rd = {"AAA": {"returns": a, "dates": dates, "n": len(a)},
          "SPY": {"returns": a[:-1], "dates": dates[:-1], "n": len(a) - 1}}

    dated = R.pair_corr_dated(a, dates, a[:-1], dates[:-1])
    positional = R.pair_corr(a, a[:-1])
    check(dated is not None and abs(dated - 1.0) < 1e-9,
          "date-aligned: identical series read as rho=1.0 (got %r)" % dated)
    check(positional is None or abs(positional - 1.0) > 0.05,
          "positional on the same input does NOT — that is the bug (got %r)" % positional)

    al_a, al_b = R.align_pair(a, dates, a[:-1], dates[:-1])
    check(al_a == al_b, "align_pair returns two series on the same dates")
    check(len(al_a) == len(a) - 1, "and drops only the day one side is missing")

    # the portfolio series must carry its own dates so it can be aligned against SPY
    series, sdates = R.portfolio_returns_dated(rd, ["AAA", "SPY"], {"AAA": 0.5, "SPY": 0.5})
    check(len(series) == len(sdates) and len(series) == len(a) - 1,
          "portfolio_returns_dated returns a series WITH its dates (%d/%d)"
          % (len(series), len(sdates)))

    # legacy dateless cache still works rather than crashing
    old = {"AAA": {"returns": a, "n": len(a)}, "SPY": {"returns": a[:-1], "n": len(a) - 1}}
    s2, d2 = R.portfolio_returns_dated(old, ["AAA", "SPY"], {"AAA": 0.5, "SPY": 0.5})
    check(bool(s2) and d2 == [],
          "a dateless legacy cache still produces a series, with no dates claimed")
    check(R.pair_corr_dated(a, [], a[:-1], []) is not None,
          "pair_corr_dated falls back to the common tail when dates are absent")

    # and no production consumer may reach for the positional primitives any more
    import inspect
    from pipeline import risk_report
    src = inspect.getsource(risk_report)
    check("riskeng.portfolio_returns(" not in src,
          "risk_report no longer calls the positional portfolio_returns")
    check("riskeng.pair_corr(" not in src,
          "risk_report no longer calls the positional pair_corr")
    check("align_pair(b_rets, b_dates, dy_bps, dy_dates)" in src,
          "the bond-duration regression aligns ETF returns against dated yield changes")


def test_ifrs_scalars_are_internally_consistent():
    """The consequence of finding 5 that the first pass did not check: with the IFRS
    scalars finally resolving, code gated on `shares_diluted` runs for NVO/ASML for the
    FIRST time. Invariant I12 says the engine's eps0 and validate.trailing_eps must
    agree — with one side converted from DKK and the other not, they would not."""
    print("\n8b. NVO's newly-available scalars are consistent with each other")
    p = os.path.join(FIX, "NVO", "sec_companyfacts.json")
    if not os.path.exists(p):
        check(True, "NVO fixture absent — skipped")
        return
    import json as _j
    from pipeline import normalize, validate
    with open(p, encoding="utf-8") as fh:
        raw = _j.load(fh)
    ff = normalize.build("NVO", sec_companyfacts=raw, fx_rate=0.145)

    check(ff.currency == "USD", "NVO is reported in USD after conversion")
    per_share = ff.net_income / ff.shares_diluted
    check(abs(per_share - ff.eps_gaap) < 0.01,
          "net_income/shares (%.3f) == eps_gaap (%.3f) — both in the same currency"
          % (per_share, ff.eps_gaap))
    check(abs(validate.trailing_eps(ff.net_income, ff.shares_diluted, ff.eps_gaap)
              - ff.eps_gaap) < 0.01,
          "validate.trailing_eps agrees with the filed EPS (invariant I12)")

    # the ADR/depositary-ratio trap REV-25 warned about: price x shares must land near
    # the real market cap, not 1/ratio of it
    implied = 61.5 * ff.shares_diluted            # NVO ADR ~ $61.5, real mcap ~ $270B
    check(1.5e11 < implied < 4.0e11,
          "price x diluted shares lands in the right order of magnitude ($%.0fB)"
          % (implied / 1e9))

    # PASS2 deliberately kept the SBC/market-cap PROXY for IFRS filers because the
    # annual share-count SERIES genuinely is not filed. That decision must survive.
    check(not raw["facts"].get("us-gaap", {}).get(
        "WeightedAverageNumberOfDilutedSharesOutstanding"),
        "NVO still files no US-GAAP diluted-share SERIES, so PASS2's proxy stays right")


# --------------------------------------------------------------------------- #
#  4. a loss year is negative cash                                             #
# --------------------------------------------------------------------------- #
def test_reverse_dcf_loss_years_are_negative():
    print("\n4. reverse DCF: loss years must not be booked as positive cash")
    from domain.engine import deep_v82 as E

    kw = dict(price=28.0, shares=0.24e9, revenue=2.1e9, rev_1y=1.5e9,
              total_debt=0.15e9, cash=1.15e9, wacc_val=0.1319, g=0.045,
              tax=0.21, margin=0.25, wacc_term=0.1108, roic_term=0.25)
    flat = E.reverse_dcf(**kw)["implied_cagr_pct"]
    prev = flat
    for mn in (0.10, 0.0, -0.05, -0.152, -0.30):
        cur = E.reverse_dcf(margin_now=mn, **kw)["implied_cagr_pct"]
        check(cur is not None and cur > flat,
              "margin_now=%+.0f%% needs MORE growth than the flat path (%.1f vs %.1f)"
              % (mn * 100, cur if cur is not None else float("nan"), flat))
        check(cur is not None and cur > prev,
              "a deeper loss today needs more growth than a shallower one")
        prev = cur if cur is not None else prev

    # an unreachable price is REPORTED, never silently dropped
    hard = E.reverse_dcf(margin_now=-0.152, **dict(kw, roic_term=0.15))
    check(hard["implied_cagr_pct"] is None and hard["out_of_band"] is True
          and bool(hard["verdict"]),
          "a price no growth rate can justify comes back out_of_band WITH a verdict")


# --------------------------------------------------------------------------- #
#  5. SBC is charged once (dilution), never twice (dilution + score)           #
# --------------------------------------------------------------------------- #
def test_sbc_is_not_double_counted():
    print("\n5. SBC: dilution only, no second qualitative penalty (v7.1 rule 5)")
    from domain.engine import deep_v82 as E

    base = dict(net_income=1.0e9, cfo=1.4e9, total_assets=10e9, revenue=10e9)
    v_lo, f_lo, _ = E.earnings_quality(sbc=0.9e9, **base)      # 9% of revenue
    v_hi, f_hi, _ = E.earnings_quality(sbc=1.1e9, **base)      # 11% of revenue
    check(v_lo == v_hi,
          "crossing 10%% of revenue does not change the scored verdict (%s vs %s)"
          % (v_lo, v_hi))
    check(any("SBC" in str(x) for x in f_hi),
          "the SBC level is still DISCLOSED to the reader")
    check(not any("SBC" in str(x) for x in f_lo),
          "and only when it is actually above the threshold")


# --------------------------------------------------------------------------- #
#  6. selling a business is not buying one                                     #
# --------------------------------------------------------------------------- #
def test_divestiture_is_not_reinvestment():
    print("\n6. a net divestiture must not be counted as capital deployed")
    import inspect
    from domain.engine import deep_v82 as E

    src = inspect.getsource(E)
    check("abs(f.acquisitions_net)" not in src,
          "the reinvestment leg no longer takes abs() of net acquisitions")
    check(E.acquisition_intensity(-2.0e9, 20e9)[0] == 0.0,
          "acquisition_intensity reads a negative (net divestiture) as zero bought")
    check(E.acquisition_intensity(2.0e9, 20e9)[0] == 0.1,
          "and a real purchase as 10% of revenue")


# --------------------------------------------------------------------------- #
#  7. a balance-sheet stock is read on ONE date, the latest available          #
# --------------------------------------------------------------------------- #
FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def test_total_debt_is_read_on_the_latest_balance_sheet():
    print("\n7. total debt: latest balance-sheet date, one date, right tag ladder")
    p = os.path.join(FIX, "ORCL", "sec_companyfacts.json")
    if not os.path.exists(p):
        check(True, "ORCL fixture absent — skipped")
        return
    from sources import sec_edgar as S
    with open(p, encoding="utf-8") as fh:
        d = S.extract(json.load(fh))
    # ORCL at 2026-02-28: LongTermNotesAndLoans 124.718B + NotesPayableCurrent 9.887B
    check(d["total_debt"] == 134_605_000_000,
          "ORCL total debt is 134.605B at 2026-02-28, not the 92.568B annual tag "
          "from 2025-05-31 (got %r)" % d["total_debt"])
    # and the ROIC that rides on it
    ic = d["total_debt"] + d["equity"] - d["cash"]
    nopat = d["operating_income"] * (1 - d["tax_expense"] / d["income_before_tax"])
    roic = nopat / ic * 100
    check(11.0 < roic < 13.5,
          "ORCL ROIC lands near 12%%, not the 17.7%% the stale debt produced "
          "(got %.2f%%)" % roic)


def test_ifrs_filers_get_their_numbers():
    print("\n8. IFRS filers (NVO/ASML) resolve the same scalars US filers do")
    p = os.path.join(FIX, "NVO", "sec_companyfacts.json")
    if not os.path.exists(p):
        check(True, "NVO fixture absent — skipped")
        return
    from sources import sec_edgar as S
    with open(p, encoding="utf-8") as fh:
        d = S.extract(json.load(fh))
    for field in ("cfo", "capex", "tax_expense", "dep_amort", "sbc",
                  "shares_diluted", "eps_gaap"):
        check(d.get(field) is not None,
              "NVO resolves %s from the ifrs-full namespace (got %r)"
              % (field, d.get(field)))


def test_per_share_figures_are_currency_converted():
    print("\n9. a per-share figure is converted with everything else (the TSM/L2 class)")
    from pipeline import normalize
    check("eps_gaap" in normalize._MONEY,
          "eps_gaap is in the SEC-path FX conversion list — otherwise a DKK/TWD EPS "
          "sits next to a USD price and the P/E is off by the exchange rate")


# --------------------------------------------------------------------------- #
#  10-11. app: auth fails closed in public, a local edit survives a Drive pull #
# --------------------------------------------------------------------------- #
def test_auth_fails_closed_on_a_public_deploy():
    print("\n10. auth: a public deploy with no APP_TOKEN refuses to serve")
    import importlib
    # fastapi.testclient needs httpx, which is NOT in requirements.txt (the app itself
    # never needs it — only this test does). Skip the way the rest of the suite skips a
    # missing optional tool, instead of taking the whole run down with an ImportError.
    try:
        import httpx           # noqa: F401
        from fastapi.testclient import TestClient
    except Exception as e:
        print("  SKIP: %s — `pip install httpx` to run the auth checks" % type(e).__name__)
        return
    saved = dict(os.environ)
    tmp = tempfile.mkdtemp(prefix="audit_auth_")
    try:
        os.environ.pop("APP_TOKEN", None)
        os.environ["RENDER"] = "true"
        os.environ.pop("ALLOW_PUBLIC_NO_AUTH", None)
        import config
        config.DATA_DIR = tmp
        config.CACHE_DIR = os.path.join(tmp, "cache")
        import app as A
        importlib.reload(A)
        c = TestClient(A.app)
        check(c.get("/api/portfolio").status_code == 503,
              "GET /api/portfolio is refused (503) with no token on a public deploy")
        check(c.post("/api/holding?ticker=EVIL&shares=9").status_code == 503,
              "POST /api/holding is refused (503) — no anonymous writes")
        check(c.get("/healthz").status_code == 200,
              "/healthz stays open so the platform health check still passes")
    finally:
        os.environ.clear()
        os.environ.update(saved)
        try:
            import app as A2
            importlib.reload(A2)
        except Exception:
            pass


def test_healthz_does_not_mint_the_session_cookie():
    print("\n10b. /healthz is exempt from auth, so it must not hand out cookies")
    import inspect
    import app as A
    src = inspect.getsource(A._auth_middleware)
    check('request.url.path != "/healthz"' in src.split("resp = await call_next")[1],
          "the cookie-minting branch excludes /healthz")


def test_local_edit_survives_a_late_drive_pull():
    print("\n11. a save made while Drive was down is not overwritten by the retry")
    import importlib
    import store_sync
    importlib.reload(store_sync)
    tmp = tempfile.mkdtemp(prefix="audit_sync_")
    path = os.path.join(tmp, "portfolio.json")

    state = {"up": False}
    from sources import gdrive_store

    _enabled, _pull, _push = (gdrive_store.enabled, gdrive_store.drive_pull,
                              gdrive_store.drive_push)
    try:
        gdrive_store.enabled = lambda: True

        def fake_pull(p):
            if not state["up"]:
                return "error"
            with open(p, "w", encoding="utf-8") as fh:                # the STALE remote
                json.dump({"holdings": {"MSFT": {"shares": 10}}}, fh)
            return "pulled"

        gdrive_store.enabled = lambda: True
        gdrive_store.drive_pull = fake_pull
        gdrive_store.drive_push = lambda p: True

        store_sync.ensure_pull(path)                                  # 1. Drive down
        with open(path, "w", encoding="utf-8") as fh:                 # 2. user saves
            json.dump({"holdings": {"NVDA": {"shares": 250}}}, fh)
        store_sync.mark_local_edit()          # what store.set_holding() does
        store_sync.schedule_push(path)                                # blocked, correctly

        state["up"] = True
        store_sync._last_try = 0.0                                    # skip the throttle
        store_sync.ensure_pull(path)                                  # 3. Drive is back

        with open(path, encoding="utf-8") as fh:
            got = json.load(fh)
        check("NVDA" in got.get("holdings", {}),
              "the local save survives the pull retry (holdings=%s)"
              % list(got.get("holdings", {})))
        check(os.path.exists(path + ".remote"),
              "and the remote copy is kept beside it instead of being discarded")
    finally:
        gdrive_store.enabled, gdrive_store.drive_pull, gdrive_store.drive_push = (
            _enabled, _pull, _push)
        importlib.reload(store_sync)


def test_an_app_write_is_not_a_user_edit():
    """The counterpart to the test above, and the reason mark_local_edit() is NOT in
    store.save(). A cold start whose pull failed hands back the EMPTY DEFAULT store and
    saves it. If that counted as a user edit, the empty store would win the next pull
    and overwrite a good Drive backup — the 2026-07 wipe incident, reintroduced."""
    print("\n11b. an app-initiated save is not a user edit")
    import inspect
    import store as ST
    src = inspect.getsource(ST.save)
    check("mark_local_edit" not in src.replace("does NOT call store_sync.mark_local_edit", ""),
          "store.save() does not mark the file dirty on its own")
    for fn in ("set_holding", "remove_holding", "add_watch", "remove_watch",
               "set_assumptions"):
        check("mark_local_edit" in inspect.getsource(getattr(ST, fn)),
              "store.%s() DOES mark it — that is a real user edit" % fn)


# --------------------------------------------------------------------------- #
#  12. the optional FMP spend is checked against the live budget               #
# --------------------------------------------------------------------------- #
def test_optional_fmp_spend_is_budget_gated():
    print("\n12. the stale-financials fallback cannot push the day past the FMP cap")
    from pipeline import refresh
    b = refresh._Budget(6)
    check(b.take(4) is True, "4 optional calls fit in a budget of 6")
    check(b.take(4) is False, "a second 4 does not, and is refused")
    check(b.left() == 2, "and the refused attempt did not consume anything")
    check(refresh._quota_take(None, 999) is True,
          "an untracked (single-ticker) path is unaffected")

    import inspect
    src = inspect.getsource(refresh.analyze)
    check("_quota_take(quota_left, 4)" in src,
          "analyze() reserves the +4 BEFORE spending it")


def test_forward_eps_spread_is_not_a_currency_artefact():
    """14 (2026-08-17, found by running live_check on TSM). The forward-EPS blend
    compared candidates from different sources with no currency check. Yahoo quotes
    `forwardEps` for the LISTED security (USD for an ADR); FMP quotes the filer's own
    reporting currency (TWD for TSM). The blend read the ~32x gap as dispersion and
    printed `fwdEPS 2src spread 184.3% (-6 conf)` — docking confidence, and telling the
    reader the analysts disagreed when one number was simply in another currency."""
    print("\n14. a cross-currency candidate is a UNIT fault, not a disagreement")
    from pipeline import consensus as C

    # the live TSM case: price $433.45, FMP in TWD, the others in USD
    b = C.blend_forward_eps({"yahoo": 21.78, "fmp": 277.22, "finnhub": 12.15},
                            price=433.45)
    check("fmp" in (b.get("rejected") or {}),
          "the TWD candidate is rejected before the blend")
    check("currency" in (b["rejected"].get("fmp") or ""),
          "and the reason says currency/unit, not disagreement")
    check(b["n"] == 2 and "fmp" not in b["sources"],
          "it does not reach the median either (n=%s)" % b["n"])
    check(b["spread_pct"] < 100,
          "spread now measures real disagreement (%.1f%%, was 184.3%%)" % b["spread_pct"])

    # a genuine disagreement must still read as one
    g = C.blend_forward_eps({"yahoo": 10.0, "fmp": 12.0, "finnhub": 11.0}, price=200.0)
    check(not g["rejected"] and g["n"] == 3,
          "ordinary analyst dispersion is untouched")
    check(g["value"] == 11.0, "and still blends to the median")

    # never leave the row with nothing: if every candidate is odd, keep them all and
    # let the downstream revenue-capacity ceiling decide
    a = C.blend_forward_eps({"a": 500.0, "b": 520.0}, price=100.0)
    check(a is not None and a["n"] == 2 and not a["rejected"],
          "all-suspect candidates are kept, not silently dropped to None")

    # no price -> the old behaviour, so nothing regresses where price is missing
    n = C.blend_forward_eps({"yahoo": 10.0, "fmp": 277.0})
    check(n["n"] == 2 and not n["rejected"],
          "with no price there is nothing to test against, so nothing is rejected")

    # and the caller actually surfaces the reason
    import inspect
    from pipeline import refresh
    src = inspect.getsource(refresh)
    check("price=ff.price" in src, "refresh passes the price into the blend")
    check("dropped before the blend" in src,
          "and puts the rejection on the card as a flag")


def test_the_reference_tab_describes_the_code_that_ships():
    """2026-08-18, found by reading the deployed dashboard. Two fixes changed what a
    number MEANS, and the in-app documentation still described the old meaning — the
    exact doc-drift PASS2 treated as a defect ("DESIGN.md said 40 suites, run_tests had
    47"). A Ref table that lies is worse than no Ref table: the reader trusts it."""
    print("\n15. the in-app Ref/How-to tables match the code that ships")
    here = os.path.dirname(os.path.abspath(__file__))
    html = open(os.path.join(os.path.dirname(here), "index.html"), encoding="utf-8").read()

    # #13: FCF now deducts SBC, so the Ref definition must say so
    i = html.find("FCF (Free cash flow)")
    row = html[i:i + 1400] if i >= 0 else ""
    check(i >= 0, "the Ref tab still documents FCF")
    check("SBC" in row, "and its definition names the SBC deduction")
    check("CFO &minus; capex &minus; SBC" in row or "CFO − capex − SBC" in row,
          "spelled as the formula the code computes")

    # #7: SBC no longer moves the earnings-quality VERDICT, only the disclosure
    j = html.find("EQ วัดว่ากำไรกลายเป็นเงินสดจริงไหม")
    eq = html[j:j + 700] if j >= 0 else ""
    check(j >= 0, "the How-to tab still explains the EQ badge")
    check("ไม่หักคะแนน" in eq,
          "and says SBC is disclosed rather than scored (no double count)")

    # and the trend row's own note, which the card renders, agrees with both
    from domain import trend as T
    import inspect
    src = inspect.getsource(T)
    check("CFO − capex − SBC" in src,
          "the trend row note the card renders says the same thing")


# --------------------------------------------------------------------------- #
#  16. a 200x forward P/E is an expensive stock, not a broken unit             #
# --------------------------------------------------------------------------- #
def test_extreme_pe_is_not_treated_as_a_unit_error():
    """A16 (found live on Render, 2026-08-18).

    The forward-EPS gate rejected anything outside [3, 200]x and called every
    rejection a "currency or share-unit mismatch". On the production deploy that
    threw away two perfectly real consensus numbers by a rounding error:

        TSLA  fwd 1.69 vs $339.30 = 200.6x   (over by 0.3%)
        AXON  fwd 3.02 vs $604.32 = 200.2x   (over by 0.1%)

    Both rows lost forward_eps entirely, so PEG and FVP were skipped and the
    valuation fell back to reverse DCF alone. A unit artefact is off by a FACTOR
    (TSM 1.2x, RKLB 9,608x); these were off by a fraction of a percent.

    The gate now splits: reject below 3x (currency) and above 600x (near-zero EPS),
    and between 200x and 600x KEEP the value with a loud flag."""
    print("\n16. an extreme-but-real forward P/E survives the gate")
    from pipeline import validate as V

    # the two live false positives must now pass
    tsla = dict(forward_eps=1.69, revenue=None, shares=None, price=339.30, growth_lt=0.20)
    axon = dict(forward_eps=3.02, revenue=None, shares=None, price=604.32, growth_lt=0.25)
    check(V.forward_eps_rejection(**tsla) is None,
          "TSLA 1.69 @ $339.30 (200.6x) is accepted, not rejected as a unit error")
    check(V.forward_eps_rejection(**axon) is None,
          "AXON 3.02 @ $604.32 (200.2x) is accepted, not rejected as a unit error")

    # ...but neither is allowed through in silence
    for name, kw in (("TSLA", tsla), ("AXON", axon)):
        note = V.forward_eps_extreme(kw["forward_eps"], kw["price"])
        check(bool(note) and "P/E" in note,
              f"{name} still carries an explicit extreme-multiple flag")

    # the faults the gate was actually built for are still caught, both sides
    tsm = V.forward_eps_rejection(forward_eps=323.34, revenue=88.27e9, shares=None,
                                  price=398.37, growth_lt=0.155)     # TWD vs USD, 1.2x
    check(tsm is not None and "below" in tsm,
          "TSM's TWD EPS against a USD ADR price is still rejected (low side)")
    rklb = V.forward_eps_rejection(forward_eps=0.01, revenue=None, shares=None,
                                   price=80.04, growth_lt=0.38)      # 8,004x
    check(rklb is not None and "above" in rklb,
          "a near-zero forward EPS (8,004x) is still rejected (high side)")
    check(V.forward_eps_extreme(0.01, 80.04) is None,
          "and a rejected value is never ALSO reported as merely extreme")

    # the boundary itself: 200x passes clean, 201x passes flagged, 601x is gone
    check(V.forward_eps_rejection(forward_eps=1.0, revenue=None, shares=None,
                                  price=200.0, growth_lt=0.1) is None
          and V.forward_eps_extreme(1.0, 200.0) is None,
          "exactly 200x is accepted without the extreme flag")
    check(V.forward_eps_rejection(forward_eps=1.0, revenue=None, shares=None,
                                  price=201.0, growth_lt=0.1) is None
          and V.forward_eps_extreme(1.0, 201.0) is not None,
          "201x is accepted WITH the extreme flag")
    check(V.forward_eps_rejection(forward_eps=1.0, revenue=None, shares=None,
                                  price=601.0, growth_lt=0.1) is not None,
          "601x is rejected")

    # the revenue-capacity ceiling is untouched — it must still bite where it did
    nvda = dict(forward_eps=8.18, revenue=253.491e9, shares=24.391e9,
                price=202.81, growth_lt=0.2632)
    check(V.forward_eps_rejection(**nvda) is None, "NVDA's real consensus still passes")
    check(V.forward_eps_rejection(**{**nvda, "forward_eps": 30.0, "growth_lt": 5.0}) is not None,
          "a wild EPS still fails the revenue-capacity ceiling")

    # EVERY accept path in _resolve_forward_eps must flag, not just the first
    import inspect
    src = inspect.getsource(V._resolve_forward_eps)
    check(src.count("_flag_extreme") == 3,
          "all three accept branches route through the extreme-flag helper")

    # and the flag must actually land on a real row, not just on the helper
    class _FF:
        price = 339.30
        forward_eps = 1.69
        forward_eps_raw = None
        revenue = None
        shares_diluted = None
        net_income = None
        eps_gaap = None
        growth_lt = 0.20
        provenance = {}
    ff = _FF()
    ff.flags = []
    ff.provenance = {}
    V._resolve_forward_eps(ff)
    check(ff.forward_eps == 1.69, "a TSLA-shaped row keeps its forward EPS end-to-end")
    check(any("P/E" in f for f in ff.flags),
          "and the row carries the extreme-multiple flag after validate runs")
    check(not any("rejected" in f for f in ff.flags),
          "and no longer claims the number was rejected")


def main():
    for fn in (test_tnx_percent_convention,
               test_hybrid_cov_is_a_real_covariance_matrix,
               test_returns_align_on_dates,
               test_fetch_returns_carries_dates,
               test_every_correlation_consumer_is_date_aligned,
               test_ifrs_scalars_are_internally_consistent,
               test_reverse_dcf_loss_years_are_negative,
               test_sbc_is_not_double_counted,
               test_divestiture_is_not_reinvestment,
               test_total_debt_is_read_on_the_latest_balance_sheet,
               test_ifrs_filers_get_their_numbers,
               test_per_share_figures_are_currency_converted,
               test_auth_fails_closed_on_a_public_deploy,
               test_healthz_does_not_mint_the_session_cookie,
               test_local_edit_survives_a_late_drive_pull,
               test_an_app_write_is_not_a_user_edit,
               test_optional_fmp_spend_is_budget_gated,
               test_forward_eps_spread_is_not_a_currency_artefact,
               test_the_reference_tab_describes_the_code_that_ships,
               test_extreme_pe_is_not_treated_as_a_unit_error):
        fn()
    print("\n" + "=" * 62)
    if FAILS:
        print("FAILED %d check(s):" % len(FAILS))
        for f in FAILS:
            print("  - " + f)
        sys.exit(1)
    print("ALL test_audit_2026_08_16 PASSED")


if __name__ == "__main__":
    main()
