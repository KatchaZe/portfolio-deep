"""Second-pass audit — read-only. Hammers the code added in REV-16..REV-28 with
degenerate and boundary inputs, looking for behaviour that is CORRECT AS CODED but
distorts the answer. Nothing here modifies the repo.
"""
import os
import sys

APP = os.environ.get("APP_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)

from domain.engine import deep_v82 as E          # noqa: E402
from domain.engine import young_dcf as Y         # noqa: E402
from domain.facts import FinancialFacts as FF    # noqa: E402
from sources import damodaran                    # noqa: E402

RF = 0.045
FOUND = []


def hdr(n, t):
    print("\n" + "=" * 78)
    print(f"[{n}] {t}")
    print("=" * 78)


def flag(sev, msg):
    FOUND.append((sev, msg))
    print(f"  !! {sev}: {msg}")


# ---------------------------------------------------------------- A1  [FIXED: P2-1]
hdr("A1", "dilution: real share-count history vs the old SBC/market-cap proxy")
print("  the proxy measures GROSS grants and moves with the SHARE PRICE:")
for px_mult, lbl in ((1.0, "today"), (0.5, "after a 50% drawdown"), (2.0, "after a double")):
    mcap = 40e9 * px_mult
    print(f"   {lbl:24s} mcap ${mcap/1e9:5.1f}B -> proxy {E.sbc_dilution_rate(1.05e9, mcap)*100:.2f}%/yr")
print("  the engine now prefers the FILED share-count series (net of buybacks):")
buyback = FF("BB", shares_diluted_annuals=[7.43e9, 7.47e9, 7.54e9, 7.61e9], sbc=12e9)
dilutive = FF("DD", shares_diluted_annuals=[4.99e9, 4.92e9, 4.83e9, 4.74e9], sbc=7.6e9)
ifrs = FF("IF", sbc=1e9)
for f_, mc_, lbl in ((buyback, 3100e9, "retires more than it grants"),
                     (dilutive, 1500e9, "genuinely dilutive"),
                     (ifrs, 1000e9, "IFRS, no series filed")):
    r, l = E.dilution_rate(f_, mc_)
    print(f"   {lbl:28s} -> {r*100:5.2f}%/yr   {l}")
if E.dilution_rate(buyback, 3100e9)[0] != 0.0:
    flag("MED", "a net-buyback company is still being charged dilution")
elif "proxy" in (E.dilution_rate(dilutive, 1500e9)[1] or ""):
    flag("MED", "real history exists but the proxy is still being used")
else:
    print("   -> FIXED (P2-1): real history primary, proxy only as an IFRS fallback")

# ---------------------------------------------------------------- A2  [FIXED: P2-3]
hdr("A2", "working_capital_change: partial legs")
full = E.working_capital_change(FF("X", receivables=3e9, receivables_prior=2.2e9,
                                   inventory=1.5e9, inventory_prior=1.2e9,
                                   accounts_payable=1.0e9, accounts_payable_prior=0.9e9))
ap_only = E.working_capital_change(FF("Y", accounts_payable=2.0e9, accounts_payable_prior=1.0e9))
ar_only = E.working_capital_change(FF("Z", receivables=3e9, receivables_prior=2.2e9))
print(f"   all three legs : {full[0]/1e9:+.2f}B   {full[1]}")
print(f"   AP leg only    : {str(ap_only[0]):>8s}    {ap_only[1]}")
print(f"   AR leg only    : {ar_only[0]/1e9:+.2f}B   {ar_only[1]}")
if ap_only[0] is not None:
    flag("MED", "an AP-only delta is still returned; it flips sign and inflates FCFF")
else:
    print("   -> FIXED (P2-3): receivables required; a sign-flipped partial is refused")

# ---------------------------------------------------------------- A3
hdr("A3", "implied() returns the band floor as if it were a measurement")
r = E.reverse_dcf(price=1.0, shares=1e9, revenue=50e9, rev_1y=45e9, total_debt=0,
                  cash=0, wacc_val=0.09, g=0.045, tax=0.21, margin=0.20, roic_term=0.25)
print(f"   a $1 stock on $50B revenue -> implied_cagr_pct = {r['implied_cagr_pct']}"
      f"  verdict {r['verdict']!r}")
flag("LOW", "when even -50%/yr decline is worth more than the price, implied() returns the "
            "band edge (-50.0%) as a normal number. It is a boundary, not an estimate, and "
            "nothing marks it as one — unlike the PE clamp, which does flag its boundary.")

# ---------------------------------------------------------------- A4
hdr("A4", "peak scan resolution (n=40 over a 150pp band = 3.75pp steps)")
base = dict(shares=1e9, revenue=10e9, rev_1y=9e9, total_debt=0, cash=0,
            wacc_val=0.09, g=0.045, tax=0.21, margin=0.15)
misses = 0
for roic in (0.095, 0.10, 0.11, 0.12):
    for p in (13.0, 13.5, 14.0, 14.5, 15.0):
        r = E.reverse_dcf(price=p, roic_term=roic, **base)
        if r.get("implied_cagr_pct") is None:
            misses += 1
print(f"   thin-spread grid: {misses}/20 report 'no growth justifies this price'")
print("   (that IS the right answer near ROIC=WACC; checking the peak is not being missed)")
fine = []
for n_steps in (40, 400):
    lo, hi, m, roic = -0.5, 1.0, 0.15, 0.10
    ev = 14.0 * 1e9

    def pv(x):
        rein = max(0.0, x / roic) if x > 0 else 0.0
        rt = min(0.8, 0.045 / roic)
        p_, rv = 0.0, 10e9
        for t in range(1, 11):
            rv *= (1 + x)
            p_ += rv * m * 0.79 * (1 - rein) / 1.09 ** t
        return p_ + (rv * 1.045 * m * 0.79 * (1 - rt) / 0.045) / 1.09 ** 10
    peak = max(pv(lo + (hi - lo) * i / n_steps) for i in range(n_steps + 1))
    fine.append(peak)
print(f"   peak with n=40 : ${fine[0]/1e9:.4f}B")
print(f"   peak with n=400: ${fine[1]/1e9:.4f}B   (error {abs(fine[1]-fine[0])/fine[1]*100:.3f}%)")
if abs(fine[1] - fine[0]) / fine[1] > 0.005:
    flag("LOW", "the 40-step peak scan under-locates the maximum, so a price just under the "
                "true peak can be misreported as unjustifiable.")
else:
    print("   -> peak location is accurate enough at n=40; no finding")

# ---------------------------------------------------------------- A5
hdr("A5", "young DCF: p_survival step function at the boundaries")
print("   runway -> p_survival is a STEP, so a day either side of a boundary jumps:")
for burn, cash in ((1e9, 5.01e9), (1e9, 4.99e9), (1e9, 3.01e9), (1e9, 2.99e9)):
    p, why = Y.survival_probability(cash, -burn, None, None)
    print(f"    runway {cash/burn:4.2f}y -> p {p:.2f}   ({why})")
flag("LOW", "p_survival steps 0.85 -> 0.75 across a 0.02y difference in runway. Since value "
            "= p x going-concern + (1-p) x distress, a 10pp step moves the fair value by "
            "10% of the gap between going-concern and distress — discontinuously.")

# ---------------------------------------------------------------- A6
hdr("A6", "young DCF terminal share of value")
kw = dict(current_revenue=2.1e9, g_high=0.35, g_stable=RF, horizon=10,
          current_margin=-0.152, target_margin=0.25, sales_to_capital=2.4,
          tax=0.21, wacc=0.1319, roic_stable=0.15, net_debt=-1.0e9,
          shares=0.24e9, annual_dilution=0.0446)
gc = Y.going_concern(dict(kw))
share = gc["pv_terminal"] / gc["firm_value"] * 100
print(f"   PV(interim) ${gc['pv_fcff']/1e9:+.2f}B   PV(terminal) ${gc['pv_terminal']/1e9:.2f}B"
      f"   -> terminal is {share:.1f}% of firm value")
if share > 85:
    print(f"   ({share:.0f}% of the value is terminal — inherent to a cash-burning young")
    print("    company, but it means the answer rests on target_margin. P2-4 now sizes the")
    print("    Monte Carlo band by whether that margin was MEASURED or ASSUMED:)")
    for anch in (True, False):
        span = Y.MC_MARGIN_SPAN_ANCHORED if anch else Y.MC_MARGIN_SPAN_ASSUMED
        mc = Y.monte_carlo(dict(kw, p_survival=0.68, distress_value_per_share=4.17),
                           margin_span=span)
        print(f"     {'anchored' if anch else 'assumed ':9s} +/-{span*100:4.1f}pp -> "
              f"p10 ${mc['p10']:.2f}  p50 ${mc['p50']:.2f}  p90 ${mc['p90']:.2f}  "
              f"band {mc['p90']/mc['p10']:.2f}x")
    if Y.MC_MARGIN_SPAN_ASSUMED <= Y.MC_MARGIN_SPAN_ANCHORED:
        flag("MED", "the assumed-margin band is not wider than the measured one")
    else:
        print("   -> FIXED (P2-4): the gate now judges a band sized by data availability")

# ---------------------------------------------------------------- A7
hdr("A7", "terminal ROIC for a coarse sector after REV-26")
for tk, sec in (("MSFT", "Technology"), ("ZZZZ", "Technology"), ("ZZZZ", "Healthcare"),
                ("ZZZZ", "Utilities"), ("ZZZZ", "Financial Services"), ("ZZZZ", None)):
    v = damodaran.terminal_roic_for(tk, sec)
    print(f"   {tk:5s} {str(sec):20s} -> {('%.2f%%' % (v*100)) if v else 'None'}")
print("   -> a coarse hit can now only RESTRAIN (capped at the 14.92% market average)")

# ---------------------------------------------------------------- A8
hdr("A8", "zero / missing propagation through the whole engine")
cases = {
    "revenue = 0": dict(revenue=0.0),
    "shares = 0": dict(shares_diluted=0.0),
    "price = 0": dict(price=0.0),
    "cash = 0": dict(cash=0.0),
    "everything None": dict(revenue=None, operating_income=None, net_income=None,
                            shares_diluted=None, price=None, forward_eps=None,
                            cfo=None, total_assets=None),
    "beta = 0": dict(beta=0.0),
    "tax fields 0": dict(income_before_tax=0.0, tax_expense=0.0),
}
GOOD = dict(beta=1.1, price=100.0, revenue=20e9, operating_income=4e9, net_income=3e9,
            shares_diluted=1e9, total_debt=5e9, cash=4e9, equity=15e9, capex=1e9,
            dep_amort=0.8e9, interest_expense=0.2e9, income_before_tax=3.7e9,
            tax_expense=0.7e9, forward_eps=3.3, growth_lt=0.12, cfo=4.2e9,
            total_assets=40e9, market_cap=100e9, sbc=0.5e9,
            revenue_annuals=[20e9, 18e9, 16e9, 14e9, 12e9, 11e9],
            operating_income_annuals=[4e9, 3.6e9], equity_prior=13e9, cash_prior=3.5e9,
            total_debt_prior=4.8e9, terminal_roic_sector=0.2295)
eng = E.DeepV82Engine()
for lbl, over in cases.items():
    try:
        v = eng.evaluate(FF("T", **dict(GOOD, **over)), rf=RF)
        print(f"   {lbl:18s} -> {str(v.recommendation):18s} FV {v.anchor_value}"
              f"  comp {v.composite}  ({v.anchor_method})")
    except Exception as ex:
        flag("HIGH", f"{lbl} CRASHES the engine: {type(ex).__name__}: {ex}")

# ---------------------------------------------------------------- A9
hdr("A9", "tax_rate: a real 0% payer vs missing data")
print("   FinancialFacts.tax_rate returns 0.21 whenever the ratio is unusable.")
for ibt, txe, lbl in ((5e9, 0.0, "genuine 0% tax (NOL shield)"),
                      (5e9, None, "tax not filed"),
                      (0.0, 0.5e9, "pretax exactly zero"),
                      (-2e9, 0.1e9, "loss-making")):
    print(f"    {lbl:28s} ibt={str(ibt):8s} txe={str(txe):8s} -> {FF('T', income_before_tax=ibt, tax_expense=txe).tax_rate}")
flag("LOW", "a company genuinely paying ~0% (NOL carryforwards, common in the pre-profit "
            "names the young DCF now values) is handed the 21% marginal rate, understating "
            "its NOPAT and FCFF. The `0 <= r <= 0.6` band accepts 0.0, so this one is "
            "actually handled — but only when tax_expense is filed as exactly 0, not None.")

# ---------------------------------------------------------------- A10
hdr("A10", "growth_lt = 0 vs missing, after REVIEW-1")
for g, lbl in ((0.0, "measured zero growth"), (None, "growth not available")):
    f = FF("T", **dict(GOOD, growth_lt=g))
    v = eng.evaluate(f, rf=RF)
    used = (v.key_metrics or {}).get("growth_pct")
    print(f"   {lbl:24s} -> growth used {used}%   FV {v.anchor_value}")

# ---------------------------------------------------------------- A11  [E30]
hdr("A11", "every SEC value comes from a periodic report (10-K/10-Q/20-F/40-F, 6-K for 20-F filers)")
import glob as _glob                               # noqa: E402
import json as _json                               # noqa: E402
from sources import sec_edgar as _sec              # noqa: E402

# the guard's own list, not the extract's: a guard reading sec_edgar's constant would
# go blind together with it
_ALLOWED = {"10-K", "10-K/A", "10-Q", "10-Q/A", "10-KT", "10-QT", "20-F", "20-F/A", "40-F", "40-F/A"}
_FIXTURES = sorted(_glob.glob(os.path.join(APP, "tests", "fixtures", "*", "sec_companyfacts.json"))
                   + _glob.glob(os.path.join(APP, "tests", "fixtures", "sec_s", "*.json")))


def _leaks():
    """[(fixture, concept, form)] for facts of a non-periodic form that extract read."""
    seen = []
    real = _sec._Xbrl.entries

    def spy(self, concept, prefer=_sec.USD):
        out = real(self, concept, prefer)
        if not self.periodic_only:          # the DATA note's comparison read, never stored
            return out
        foreign = any(str(e.get("form")).startswith(("20-F", "40-F"))
                      for n in self.ns.values() for a in n.get("units", {}).values() for e in a)
        ok = _ALLOWED | ({"6-K", "6-K/A"} if foreign else set())
        seen.extend((concept, e.get("form")) for e in out if e.get("form") not in ok)
        return out
    _sec._Xbrl.entries = spy
    leaks = []
    try:
        for f in _FIXTURES:
            del seen[:]
            with open(f) as fh:
                _sec.extract(_json.load(fh))
            leaks.extend((os.path.relpath(f, APP),) + x for x in sorted(set(seen)))
    finally:
        _sec._Xbrl.entries = real
    return leaks


_found = _leaks()
print(f"  {len(_FIXTURES)} companyfacts fixtures extracted, {len(_found)} non-periodic facts read")
for x in _found[:5]:
    print("   ", x)
if _found:
    flag("HIGH", f"extract read facts from non-periodic forms: {_found[:3]}")
# self-test: with the form filter removed, INGN's DEF 14A must reach extract
_real_periodic = _sec._periodic
_sec._periodic = lambda e, foreign=False: True
try:
    _mutant = _leaks()
finally:
    _sec._periodic = _real_periodic
print(f"  mutation (form filter removed): {len(_mutant)} non-periodic facts read")
if not any(f == "DEF 14A" for _, _, f in _mutant):
    flag("HIGH", "A11 cannot fire: removing the form filter let no DEF 14A fact through")

# ---------------------------------------------------------------- A12  [E38-E43]
hdr("A12", "peer panel: gross margin only within the same business (SPEC R25-R28)")
import re as _re                                   # noqa: E402
import shutil as _shutil                           # noqa: E402
import subprocess as _sp                           # noqa: E402
import tempfile as _tf                             # noqa: E402
import types as _types                             # noqa: E402
from domain import moat_trend as _MT               # noqa: E402
from domain import peer_panel as _PP               # noqa: E402


class _Stale(Exception):
    """A mutation self-test whose source edit no longer applies: it proves nothing."""


def _variant(mod, old=None, new=None, override=None, **attrs):
    """A copy of `mod` for the guards' mutation self-tests: one source edit (old -> new),
    or, more robust, `override` {name: value} replacing a module global after the copy
    runs (functions read globals at call time, so a later edit to the overridden
    function's text cannot make the self-test vacuous). Raises _Stale when `old` is gone,
    which the caller reports as a stale self-test, never as a detection."""
    src = open(mod.__file__, encoding="utf-8").read()
    if old is not None:
        if src.count(old) != 1:
            raise _Stale(f"{mod.__name__}: {old[:60]!r} found {src.count(old)} times")
        src = src.replace(old, new)
    m = _types.ModuleType(mod.__name__ + "_mutant")
    m.__file__ = mod.__file__
    m.__dict__.update(attrs)
    exec(compile(src, mod.__file__, "exec"), m.__dict__)
    m.__dict__.update(override or {})
    return m


def _leg(end, rev, gm, oi=None):
    return {"end": end, "revenue": rev, "gross_profit": None if gm is None else rev * gm, "operating_income": oi}


def _pt(sym, gm0, gm3=None, rev=100.0, opm=0.10):
    gm3 = gm0 if gm3 is None else gm3
    return {"symbol": sym, "currency": "USD", "accounting_standard": "us-gaap", "revenue_ttm": rev,
            "ebit_ttm": rev * opm, "net_income_ttm": rev * opm * .8, "ev": rev * 3, "market_cap": rev * 2.5,
            "period_end": "2026-09-30", "price_date": "2026-11-13",
            "fy0": _leg("2025-12-31", rev, gm0, rev * .2), "fy1": _leg("2024-12-31", rev * .9, gm0),
            "fy3": _leg("2022-12-31", rev * .7, gm3, rev * .7 * .1)}


# the insurer case (UNH-like: GM 88.7% beside distributors), a semiconductor with peers in
# three SICs, and a device maker that needs the 3-digit fallback
_CASES = {
    "INS": ("6324", [("CI", "6324", .22), ("ELV", "6324", .89), ("CNC", "6324", .07), ("MOH", "6324", .13),
                     ("MCK", "5122", .04), ("COR", "5122", .03)], .887),
    "SEMI": ("3674", [("A", "3674", .5), ("B", "3674", .55), ("C", "3674", .6), ("D", "3672", .2),
                      ("E", "3559", .45), ("F", None, .9), ("G", "0000", .9)], .75),
    "DEV": ("3845", [("H", "3845", .6), ("I", "3845", .62), ("J", "3841", .7), ("K", "3851", .9),
                     ("L", "3851", .9), ("M", "3851", .9)], .65),
    "THIN": ("7841", [("N", "7841", .4), ("O", "7372", .8), ("P", "7812", .3)], .45),
    # both edges of the financial range (a bank at 6000, a REIT at 6799) and one past it
    "BANK": ("6000", [("Q", "6000", .5), ("R", "6000", .6), ("S", "6000", .7)], .55),
    "REIT": ("6799", [("U", "6799", .5), ("V", "6799", .6), ("W", "6799", .7)], .55),
    "RE": ("6800", [("X1", "6800", .5), ("X2", "6800", .6), ("X3", "6800", .7)], .55),
}
# R25's range, written here rather than read from peer_panel, so a wrong bound there is seen
_FIN = range(6000, 6800)
_Y5 = [2021, 2022, 2023, 2024, 2025]


def _built(pp=_PP, mt=_MT):
    """{case: (target sic, sics, panel, moat)} built by the given module pair."""
    out = {}
    for k, (tsic, peers_, tgm) in _CASES.items():
        tgt = _pt("T" + k, tgm, tgm - .02)
        pts = [_pt(s, g) for s, _c, g in peers_]
        sics = {"T" + k: tsic, **{s: c for s, c, _g in peers_}}
        members = {"T" + k: 1, **{s: i + 2 for i, (s, _c, _g) in enumerate(peers_)}, "T" + k + "B": 1}
        sics["T" + k + "B"] = tsic
        frames = {y: {c: [10.0 * c, f"{y}-12-31"] for c in set(members.values())} for y in _Y5}
        share = pp.industry_share(members, frames, 1, _Y5, "2026-11-16")
        share["sub_group"] = pp.sub_group(members, frames, 1, _Y5, "2026-11-16", None, None, sics, "T" + k)
        panel = pp.build(tgt, pts, "2026-11-16", "X", "b", share, sics=sics)
        kept = pp.peer_set(tgt, pts, "2026-11-16")[0]
        out[k] = (tsic, sics, panel, mt.build({}, tgt, kept, share, 8.0, 5.0, sics))
    return out


def _live():
    """The same built offline for the store's holdings when the store and caches exist."""
    import config as _cfg
    store = os.path.join(_cfg.DATA_DIR, "portfolio.json")
    if not (os.path.exists(store) and os.path.exists(os.path.join(_cfg.CACHE_DIR, "damodaran_indname.json"))):
        return {}
    import datetime as _dt
    from types import SimpleNamespace as _NS
    from pipeline import peers as _peers
    from tools import panel_probe as _probe
    with open(store, encoding="utf-8") as fh:
        st = _json.load(fh)
    u = _probe._offline_universe(_cfg.CACHE_DIR)
    sics = {t: row[1] for t, row in (u.rows or {}).items()}
    out = {}
    for t in sorted(st.get("holdings") or {}):
        r, fd = (st.get("results") or {}).get(t) or {}, (st.get("facts") or {}).get(t)
        if not fd or not (r.get("peer_panel") or {}).get("as_of"):
            continue
        panel, moat = _peers.panel_and_moat(_probe._facts(fd, t), _NS(key_metrics=r.get("key_metrics") or {}),
                                            r.get("relative") or {}, u,
                                            _dt.date.fromisoformat(r["peer_panel"]["as_of"]))
        out[t] = (sics.get(t), sics, panel, moat)
    return out


def _cross_sic(built):
    """A12a: a GM comparison that includes a comparator of another SIC at the level used."""
    bad = []
    for k, (_ts, sics, panel, moat) in built.items():
        tbl = panel.get("table") or {}
        scopes = [("table", tbl.get("gm_compare") or {})] + [
            ("S3", s.get("comparators") or {}) for s in moat["signals"] if s["name"] == "gm_gap_change"]
        for where, g in scopes:
            lv = g.get("level")
            if not lv:
                continue
            for sym in g.get("symbols") or []:
                c = _PP.sic4(sics.get(sym))
                if c is None or c[:lv] != str(g.get("sic"))[:lv]:
                    bad.append((k, where, sym, c, g.get("sic"), lv))
        listed = set((tbl.get("gm_compare") or {}).get("symbols") or [])
        bad += [(k, "row", r["symbol"]) for r in tbl.get("rows") or [] if r.get("gm_comparator") and r["symbol"] not in listed]
    return bad


def _financial_gm(built):
    """A12b: a financial target with a GM number, S3 or a GM-based hint."""
    bad = []
    for k, (ts, _s, panel, moat) in built.items():
        if _PP.sic4(ts) is None or int(_PP.sic4(ts)) not in _FIN:
            continue
        rows = (panel.get("table") or {}).get("rows") or []
        bad += [(k, "row GM", r["symbol"]) for r in rows if r.get("gross_margin_fy") is not None]
        bad += [(k, "S3") for s in moat["signals"] if s["name"] == "gm_gap_change"]
        bad += [(k, h["type"]) for h in moat["source_hints"] if h["type"] in ("intangible", "cost_advantage")]
        if "gm_gap_change" not in (moat.get("votes") or {}).get("not_applicable", []):
            bad.append((k, "votes"))
    return bad


def _silent_skips(built):
    """A12c: a skipped GM comparison or sub-group with no reason, or no counts."""
    bad = []
    for k, (_ts, _s, panel, moat) in built.items():
        g = (panel.get("table") or {}).get("gm_compare")
        if g and not g.get("level"):
            r = g.get("reason") or ""
            counted = g.get("sic") is None or not g.get("applies") or (
                f"{g.get('n4')} ราย" in r and f"{g.get('n3')} ราย" in r)
            if not r or not counted:
                bad.append((k, "gm", r))
        if g and not g.get("level") and not any(f.startswith("gross margin: ") for f in panel["flags"]):
            bad.append((k, "panel flag"))
        if not any(s["name"] == "gm_gap_change" for s in moat["signals"]) and \
                not any(f.startswith("S3: ") and len(f) > 4 for f in moat["flags"]):
            bad.append((k, "S3 flag"))
        sub = (panel.get("industry_share") or {}).get("sub_group")
        if sub and (sub.get("sic") is None or sub.get("n_members", 0) < 2) and not sub.get("reason"):
            bad.append((k, "sub_group"))
    return bad


def _sub_shares(built):
    """A12d: sub-group shares above 100% or the target counted other than once."""
    bad = []
    for k, (_ts, _s, panel, _m) in built.items():
        sub = (panel.get("industry_share") or {}).get("sub_group") or {}
        if not sub.get("sic"):
            continue
        tot = sum(m["share"] for m in sub.get("members") or [] if m.get("share"))
        if tot > 1 + 1e-9:
            bad.append((k, "sum", round(tot, 4)))
        rows = (panel.get("table") or {}).get("rows") or []
        tsym = next((r["symbol"] for r in rows if r["is_target"]), None)
        n = sum(m["symbols"].count(tsym) for m in sub.get("members") or [])
        # a home-listed target (SIC from Damodaran's global rows, R29) is not a US member
        home = str((panel.get("target_sic") or {}).get("source") or "").startswith("Damodaran global")
        if tsym and n != (0 if home else 1):
            bad.append((k, "target rows", n))
    return bad


_GUARDS = (("A12a", _cross_sic), ("A12b", _financial_gm), ("A12c", _silent_skips), ("A12d", _sub_shares))
_built_now = _built()
_built_now.update(_live())
print(f"  {len(_built_now)} panels ({len(_CASES)} synthetic, {len(_built_now) - len(_CASES)} live holdings rebuilt offline)")
for _name, _g in _GUARDS:
    _found = _g(_built_now)
    print(f"  {_name} {_g.__doc__.split(':')[1].strip()[:70]}: {len(_found)}")
    if _found:
        flag("HIGH", f"{_name} {_found[:3]}")

_MUTANT_EDITS = (
    ("A12a", _cross_sic, dict(old="c is not None and c[:lv] == ts[:lv]]", new="True]")),
    ("A12b", _financial_gm, dict(override={"is_financial": lambda code: False})),
    ("A12b", _financial_gm, dict(override={"FINANCIAL_SIC": (6001, 6798)})),
    ("A12c", _silent_skips, dict(old='out["reason"] = (f"คู่แข่งที่มี', new='out["reason"] = None and (f"คู่แข่งที่มี')),
    ("A12d", _sub_shares, dict(old="total = sum(v for v in rev.values() if _pos(v))", new="total = max(rev.values())")),
    ("A12d", _sub_shares, dict(old="for t, c in list(sm.items()) + list(sf.items()):",
                               new="for t, c in list(sm.items()) + list(sm.items()) + list(sf.items()):")))
_MUTANTS = []
for _name, _g, _edit in _MUTANT_EDITS:
    try:
        _MUTANTS.append((_name, _g, _variant(_PP, **_edit)))
    except _Stale as _e:
        print(f"  mutation for {_name}: STALE ({_e})")
        flag("HIGH", f"{_name} self-test is stale, not a detection: {_e}")
for _name, _g, _pp in _MUTANTS:
    _mt = _variant(_MT, "from domain import peer_panel, trend", "from domain import trend", peer_panel=_pp)
    _hit = _g(_built(_pp, _mt))
    print(f"  mutation for {_name}: {len(_hit)} violation(s) found")
    if not _hit:
        flag("HIGH", f"{_name} cannot fire: its mutant produced no violation")



# ---------------------------------------------------------------- A13  [E50-E55]
hdr("A13", "industry share: every member resolved or named, counted once, on one revenue rule (SPEC R32-R38)")
from domain import frames_revenue as _FR          # noqa: E402
from domain import member_cik as _MC              # noqa: E402


def _a13_resolution(mc=_MC):
    """[(ticker, cik, reason)] for every Damodaran US member of every industry in the offline
    caches, resolved as Universe.members_of does (exact ticker first), with module `mc`."""
    import config as _cfg
    path = os.path.join(_cfg.CACHE_DIR, "damodaran_indname.json")
    if not os.path.exists(path):
        return None
    from pipeline.cik import resolve_cik as _rc
    from tools import panel_probe as _probe
    u = _probe._offline_universe(_cfg.CACHE_DIR)
    sec, titles = u._sec_map
    index = mc.NameIndex(u.frame_names)
    has_rev = {int(c) for yr in u.frames.values() for c, row in yr.items() if row and row[0] and row[0] > 0}
    out = []
    for t, (ind, _sic, ctry) in sorted((u.rows or {}).items()):
        if ctry != "United States":
            continue
        r = mc.resolve(t, u.us_names.get(t), _rc(t)[0], sec, titles, index, has_rev)
        out.append((t, r["cik"], r["reason"]))
    return out


def _a13a(res):
    """A13a: a Damodaran US member with neither a CIK nor a reason (or with both)."""
    return [(t, c, w) for t, c, w in res or [] if (c is None) == (not w)]


def _a13_shares(pp=_PP):
    """Synthetic share groups (share classes, an unresolved member, a non-USD IFRS member)
    through `pp`, plus the live holdings' rebuilt panels (_built_now)."""
    members = {"T": 1, "TB": 1, "P": 2, "Q": 3, "QQ": None, "EU": 4, "GF": 5, "R": 6, "S": 7}
    sics = {"T": "3674", "TB": "3674", "P": "3674", "Q": "3672", "QQ": "3674", "EU": "3674", "GF": "3674",
            "R": "3674", "S": "3674"}
    frames = {y: {1: [50.0, f"{y}-12-31"], 2: [30.0, f"{y}-12-31", "us-gaap:Revenues", None],
                  3: [20.0, f"{y}-12-31", "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
                      "revenues_component"], 6: [5.0, f"{y}-12-31"], 7: [5.0, f"{y}-12-31"]} for y in _Y5}
    s = pp.industry_share(members, frames, 1, _Y5, "2026-11-16", None, None, {"QQ": "ไม่พบชื่อ"}, {4: "EUR"})
    s["sub_group"] = pp.sub_group(members, frames, 1, _Y5, "2026-11-16", None, None, sics, "T",
                                  {"QQ": "ไม่พบชื่อ"}, {4: "EUR"})
    out = [("SYN", members, s), ("SYN/sub", members, s["sub_group"])]
    for k, (_ts, _s, panel, _m) in _built_now.items():
        sh = (panel.get("industry_share") or {})
        if sh:
            out += [(k, None, sh)] + ([(k + "/sub", None, sh["sub_group"])] if sh.get("sub_group", {}).get("sic") else [])
    return out


def _a13b(shares):
    """A13b: a CIK counted twice in a year (chart rows, n_members, or shares past 100%)."""
    bad = []
    for k, _m, s in shares:
        rows = (s.get("chart") or {}).get("members") or []
        ciks = [r["cik"] for r in rows]
        if len(ciks) != len(set(ciks)) or (rows and len(rows) != s.get("n_members")):
            bad.append((k, "rows", len(ciks), len(set(ciks)), s.get("n_members")))
        for i, y in enumerate((s.get("chart") or {}).get("years") or []):
            tot = sum(r["share"][i] or 0 for r in rows)
            if tot > 1 + 1e-4:
                bad.append((k, y, round(tot, 4)))
    return bad


def _a13c(fr=_FR):
    """A13c: a chosen revenue above the filer's Revenues by more than 1% with no note (real frames)."""
    fix = _json.load(open(os.path.join(APP, "tests", "fixtures", "share_frames_2026_11.json"), encoding="utf-8"))
    bad = []
    for y, by in fix["frames"].items():
        ent = {}
        for concept, rows in by.items():
            for d in rows:
                ent.setdefault(d["cik"], {})["us-gaap:" + concept] = {"val": d["val"], "end": d["end"],
                                                                      "accn": d["accn"]}
        for cik, e in ent.items():
            got, rv = fr.choose(e), e.get("us-gaap:Revenues")
            if got and rv and got["val"] > rv["val"] * 1.01 and not got.get("note"):
                bad.append((cik, y, got["val"], rv["val"]))
    return bad


def _a13d(shares):
    """A13d: a share block whose denominator does not report the members it dropped."""
    bad = []
    for k, members, s in shares:
        cov, inc = s.get("coverage") or {}, s.get("coverage_incl_unresolved")
        if inc is None or any(v is not None and inc.get(y) is None for y, v in cov.items()):
            bad.append((k, "coverage_incl_unresolved"))
        if "members_unresolved" not in s or "dropped_weight" not in s or "members_ifrs_other" not in s:
            bad.append((k, "keys"))
            continue
        if members is not None:
            want = sum(1 for t, c in members.items() if not c and (not s.get("sic") or k.endswith("/sub")))
            if s["members_unresolved"] != want:
                bad.append((k, "count", s["members_unresolved"], want))
        n = s["members_unresolved"]
        if n and not any(f"หา CIK ไม่ได้ {n} ราย" in f for f in s.get("flags") or []):
            bad.append((k, "flag", n))
        if s["members_ifrs_other"] and not any("รายงาน IFRS สกุล" in f for f in s.get("flags") or []):
            bad.append((k, "ifrs flag"))
    return bad


def _a13e(shares):
    """A13e [E58]: a counted member with no revenue in any year and no reason, or a
    no-revenue count the flags do not add up to."""
    bad = []
    for k, _m, s in shares:
        named = {c for _t, c, _w in s.get("no_revenue") or []}
        if "no_revenue" not in s:
            bad.append((k, "key"))
            continue
        ifrs = " ".join(f for f in s.get("flags") or [] if "รายงาน IFRS สกุล" in f)
        top = s.get("top5_share") or {}
        if top and all(v is not None for v in top.values()):
            for r in (s.get("chart") or {}).get("members") or []:
                if r["revenue_now"] is None and all(x is None for x in r["share"]) and r["cik"] not in named \
                        and not any(f"{t})" in ifrs or f"{t}," in ifrs for t in r["symbols"]):
                    bad.append((k, "silent", r["symbols"]))
        n = sum(int(m.group(1)) for f in s.get("flags") or []
                for m in [_re.search(r"ไม่มีรายได้ใน SEC frames (\d+) ราย", f)] if m)
        if n != len(named):
            bad.append((k, "flag count", n, len(named)))
    return bad


def _a13_max_of_concepts(e):
    """The rule R35 replaced: the largest concept across filings, no note (TMDX CY2021)."""
    c, v = max(((c, v["val"]) for c, v in e.items()), key=lambda cv: cv[1])
    return {"concept": c, "val": v, "note": None}


_res = _a13_resolution()
_sh = _a13_shares()
for _name, _found in (("A13a", _a13a(_res)), ("A13b", _a13b(_sh)), ("A13c", _a13c()), ("A13d", _a13d(_sh)),
                      ("A13e", _a13e(_sh))):
    print(f"  {_name}: {len(_found)}" + ("" if _res is not None or _name != "A13a" else " (no offline caches: synthetic only)"))
    if _found:
        flag("HIGH", f"{_name} {_found[:3]}")
if _res is not None:
    _n = len(_res)
    _un = sum(1 for _t, _c, _w in _res if _c is None)
    print(f"  {_n} Damodaran US members: {_n - _un} with a CIK, {_un} with a named reason")
_A13_MUT = []
for _name, _mod, _edit, _run in (
        ("A13a", _MC, dict(override={"_out": lambda cik=None, how=None, reason=None:
                                     {"cik": cik, "how": how, "reason": None}}),
         lambda m: _a13a(_a13_resolution(m) if _res is not None else
                         [(t, m.resolve(t, "", None, {}, {}, m.NameIndex({}), set())["cik"],
                           m.resolve(t, "", None, {}, {}, m.NameIndex({}), set())["reason"]) for t in ("ZZ.A", "QX")])),
        ("A13b", _PP, dict(old='"n_members": len(ciks),',
                           new='"n_members": sum(1 for c in members.values() if c),'),
         lambda m: _a13b(_a13_shares(m)[:2])),
        ("A13c", _FR, dict(old='            pick, note = over, note or "revenues_component"',
                           new="            pick = over"), lambda m: _a13c(m)),
        ("A13c", _FR, dict(override={"choose": _a13_max_of_concepts}), lambda m: _a13c(m)),
        ("A13d", _PP, dict(old='        flags.append(f"หา CIK ไม่ได้ {len(no_cik)} ราย (ไม่นับ): {\', \'.join(no_cik[:6])}{more}")',
                           new="        pass"), lambda m: _a13d(_a13_shares(m)[:2])),
        ("A13e", _PP, dict(override={"no_revenue": lambda *a, **k: None}), lambda m: _a13e(_a13_shares(m)[:2])),
        ("A13e", _PP, dict(old='        out["flags"].append(f"ไม่มีรายได้ใน SEC frames {len(ts)} ราย',
                           new='        out["flags"].append(f"ไม่มีรายได้ {len(ts)} ราย'), lambda m: _a13e(_a13_shares(m)[:2]))):
    try:
        _m = _variant(_mod, **_edit)
    except _Stale as _e:
        print(f"  mutation for {_name}: STALE ({_e})")
        flag("HIGH", f"{_name} self-test is stale, not a detection: {_e}")
        continue
    _hit = _run(_m)
    print(f"  mutation for {_name}: {len(_hit)} violation(s) found")
    if not _hit:
        flag("HIGH", f"{_name} cannot fire: its mutant produced no violation")


def _ui_gm_cells(js_patch=None):
    """A12e: the GM cells the tab renders for a financial target whose stored rows still
    carry GM numbers, with gm_compare (R25) and without it (a panel stored before R25,
    the profile's SEC SIC 6324 only: D2). None when node is missing. `js_patch` is JS run
    after the modules (a mutant that redefines a function), so it cannot go stale."""
    node = _shutil.which("node")
    if not node:
        return None
    from tests.frontend_source import frontend_files
    shipped = {os.path.basename(p): p for p in frontend_files()}
    js = "\n".join(open(shipped[m], encoding="utf-8").read() for m in ("format.js", "card_signals.js", "card_peer.js"))
    if js_patch:
        js += "\n" + js_patch
    tbl = {"gm_compare": {"applies": False, "reason": "x", "sic": "6324"},
           "stats": {"gross_margin_fy": {"n": 3, "median": .42}}, "target_vs_median": {},
           "rows": [{"symbol": s, "is_target": s == "UNH", "gross_margin_fy": g, "rank3": {}, "outlier": []}
                    for s, g in (("UNH", .887), ("ELV", .894), ("CI", .218))]}
    old = dict(tbl)
    old.pop("gm_compare")
    xs = [{"peer_panel": {"table": tbl}}, {"sec_sic": ["6324", "x"], "peer_panel": {"table": old}}]
    js += "\nconst XS=%s;console.log(JSON.stringify(XS.map(x=>ppTable(x.peer_panel,null,'all',x)).join('')));" % _json.dumps(xs)
    with _tf.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as fh:
        fh.write(js)
    try:
        r = _sp.run([node, fh.name], capture_output=True, text=True, timeout=60)
    finally:
        os.unlink(fh.name)
    html = _json.loads(r.stdout)
    return [c for c in _re.findall(r'data-col="gross_margin_fy">(.*?)</td>', html)
            if _re.search(r"\d", _re.sub(r"<[^>]+>", "", c))]


_ui = _ui_gm_cells()
if _ui is None:
    flag("LOW", "A12e skipped: node not available")
else:
    print(f"  A12e GM numbers rendered for a financial target: {len(_ui)}")
    if _ui:
        flag("HIGH", f"A12e the tab shows a GM number for a financial target: {_ui[:3]}")
    _ui_m = _ui_gm_cells("ppGmState=()=>({na:false,fin:false,reason:null});")
    print(f"  mutation for A12e (the cell guard removed): {len(_ui_m)} GM number(s) rendered")
    if not _ui_m:
        flag("HIGH", "A12e cannot fire: removing the cell guard rendered no GM number")

# ---------------------------------------------------------------- A14  [E60-E63]
hdr("A14", "debt: never vanishes from a year that files a debt concept; no total summed with its own parts")
_DEBT_FIX = os.path.join(APP, "tests", "fixtures", "sec_debt")


def _a14_nodes(sec):
    out, todo = [], list(sec.DEBT_ALL)
    while todo:
        n = todo.pop()
        out.append(n)
        todo += list(n.parts) + list(n.alt)
    return out


with open(os.path.join(_DEBT_FIX, "expected.json")) as _fh:
    _A14_ABSENT = {(r[0], r[1]) for r in _json.load(_fh)["rows"] if r[2] is None}


def _a14(sec=_sec):
    """A14 over the real trimmed filers: a fiscal year-end whose IC debt is 0 or absent while
    a non-zero debt concept is filed on it (equity and cash filed too), or a year whose
    concepts used hold a total beside one of its own parts (combined beside anything but
    finance leases)."""
    bad = []
    for name in sorted(os.listdir(_DEBT_FIX)):
        if not name.endswith(".json") or name == "expected.json":
            continue
        with open(os.path.join(_DEBT_FIX, name)) as fh:
            cf = _json.load(fh)
        e, x = sec.extract(cf), sec._Xbrl(cf["facts"])
        _x0 = _sec._Xbrl(cf["facts"]); _x0.cik = cf.get("cik")
        _book0 = _x0.debt_book()             # the unmutated relation, so a mutant cannot hide
        ic = e.get("ic_components_dated") or {}
        ends = [r[0] for r in e.get("operating_income_annuals_dated") or []][:12] or sorted(ic)   # the IC window
        eq = x.instant_at_dates(list(sec.EQUITY_TAGS), ends)[0]
        cash = x.instant_at_dates(list(sec.CASH_TAGS), ends)[0]
        for d in ends:
            on = {c: m[d] for c in sec.DEBT_CONCEPTS for m in [x.instant_at_dates([c], [d])[0]] if d in m}
            if (any(on.values()) and d in eq and d in cash and not (ic.get(d) or {}).get("debt")
                    and (name[:-5], d) not in _A14_ABSENT):
                bad.append((name, d, "debt vanished", sorted(c for c, v in on.items() if v)))
            used = set(((ic.get(d) or {}).get("lt_source") or "").split("+")) - {""}
            for n in _a14_nodes(sec):
                parts = {c for p in n.parts + n.alt for c in p.concepts()}
                if used & set(n.totals) and used & parts:
                    bad.append((name, d, "total + its part", sorted(used & set(n.totals)), sorted(used & parts)))
            if used & set(sec.DEBT_COMBINED.totals) and used - set(sec.DEBT_COMBINED.totals) - sec.LEASE_CONCEPTS:
                bad.append((name, d, "combined + other", sorted(used)))
            cur = set(sec.DEBT_CURRENT_TOTAL.concepts()) | set(sec.DEBT_MATURITIES.concepts()) | \
                set(sec.DEBT_NOTES_CURRENT.concepts())
            if "LongTermDebt" in used and used & cur and _book0.ltd_is_total(d):
                bad.append((name, d, "LongTermDebt total + its current maturities", sorted(used)))
    return bad + _a14_values(sec)


def _a14_values(sec):
    """A14 at the value level (E64-E68): extract against the hand-written balance-sheet debt in
    tests/fixtures/sec_debt/expected.json (each row names its filing). A total summed with its
    own part, a concept carried from another year, finance leases counted twice or dropped, a
    principal amount read as debt, and lease-only debt missing from total_debt all show here as
    a wrong number, whatever lt_source says."""
    with open(os.path.join(_DEBT_FIX, "expected.json")) as fh:
        rows = _json.load(fh)["rows"]
    bad, cache = [], {}
    for t, d, want, why, _url in rows:
        if t not in cache:
            with open(os.path.join(_DEBT_FIX, t + ".json")) as fh:
                cache[t] = sec.extract(_json.load(fh))
        e = cache[t]
        if d in ("total_debt", "total_debt_prior"):
            got = e.get(d)
        else:
            got = ((e.get("ic_components_dated") or {}).get(d) or {}).get("debt") if d in (e.get("ic_components_dated") or {}) else None
        if want is None:
            if got is not None:
                bad.append((t, d, "should be absent", got))
            continue
        if got is None or abs(got / 1e6 - want) > max(0.2, 0.001 * abs(want)):
            bad.append((t, d, "value", got and round(got / 1e6, 1), want))
    return bad


_f14 = _a14()
print(f"  A14: {len(_f14)} over {len([n for n in os.listdir(_DEBT_FIX) if n.endswith('.json')]) - 1} filers")
if _f14:
    flag("HIGH", f"A14 {_f14[:3]}")
for _name, _edit in (
        ("A14 vanish", dict(override={"debt_on": lambda on, *a:
                                      (on.get("LongTermDebtNoncurrent"), ("LongTermDebtNoncurrent",))})),
        ("A14 LongTermDebt total+current", dict(override={"_ltd_is_total": lambda relations, d: False})),
        ("A14 M1 combined + DebtCurrent", dict(old="    legs, u_legs = _debt_legs(on, nc, u_nc, tot, u_tot, ltd_is_total)",
                                               new="    v = v + (DEBT_CURRENT_TOTAL.read(on)[0] or 0) if v is not None else v\n"
                                                   "    legs, u_legs = _debt_legs(on, nc, u_nc, tot, u_tot, ltd_is_total)")),
        ("A14 M2 tags carried across years", dict(old="        dd = book.near(debt_dates, d, tol_days)",
                                                  new="        dd = max((e for e in debt_dates if e <= d), default=None)")),
        ("A14 M3 lease-only total_debt None", dict(old="                   or (recent & book.dates(LEASE_CONCEPTS)), reverse=True)",
                                                   new="                   , reverse=True)")),
        ("A14 leases inside the lines added again", dict(old="        self.leases_in_debt = x.cik in FIN_LEASE_IN_DEBT",
                                                         new="        self.leases_in_debt = False")),
        ("A14 principal read as debt", dict(old="    if v is None or (nc is not None and legs is not None and _is_principal(on, v, legs)):",
                                            new="    if v is None:")),
        ("A14 debt-free year dropped", dict(old="            v, used = 0, ()\n",
                                            new="            continue\n")),
        ("A14 total+parts", dict(old="            return nonzero[0][0], (nonzero[0][1],)",
                                 new="            return (nonzero[0][0] + sum(v for got in parts for v, _ in got), "
                                     "(nonzero[0][1],) + tuple(c for got in parts for _, u in got for c in u))"))):
    try:
        _m = _variant(_sec, **_edit)
    except _Stale as _e:
        print(f"  mutation for {_name}: STALE ({_e})")
        flag("HIGH", f"{_name} self-test is stale, not a detection: {_e}")
        continue
    _hit = _a14(_m)
    print(f"  mutation for {_name}: {len(_hit)} violation(s) found, e.g. {_hit[:2]}")
    if not _hit:
        flag("HIGH", f"{_name} cannot fire: its mutant produced no violation")

# ---------------------------------------------------------------- A15  [E76]
hdr("A15", "an IFRS member whose companyfacts carry revenue is never reported as no-revenue")
from domain import frames_revenue as _FR15         # noqa: E402
_IFRS_FIX = os.path.join(APP, "tests", "fixtures", "ifrs", "GFS_companyfacts.json")


def _a15(pp=_PP, fr=_FR15):
    """Members in the no-revenue list whose own companyfacts give USD revenue in a panel
    year: GFS (20-F, IFRS, USD; in no SEC frame) beside two us-gaap filers, read the way
    pipeline.peers.share_frames reads them (candidates -> companyfacts rows -> with_ifrs)."""
    with open(_IFRS_FIX) as fh:
        cf = _json.load(fh)
    ys = [2021, 2022, 2023, 2024, 2025]
    members = {"NVDA": 1045810, "AMD": 2488, "GFS": 1709048}
    frames = {y: {1045810: [100.0 * (y - 2020), f"{y + 1}-01-25", "us-gaap:Revenues", None],
                  2488: [50.0, f"{y}-12-27", "us-gaap:Revenues", None]} for y in ys}
    cand = pp.ifrs_candidates(members, {}, frames, {}, ys)
    ifrs = {c: {"currency": fr.ifrs_currency(cf), "cy": fr.cy_rows_from_facts(cf)} for c in cand
            if c == int(cf["cik"])}
    fr2, other = pp.with_ifrs(frames, {}, ifrs)
    out = pp.industry_share(members, fr2, 1045810, ys, "2026-11-24", {}, {}, {}, other, [], set())
    has = {int(cf["cik"])} if any(r[0] > 0 for y, r in fr.cy_rows_from_facts(cf).items() if y in ys) else set()
    return [r for r in out["no_revenue"] if r[1] in has] + ([] if has else [("fixture", "no revenue read")])


_hit15 = _a15()
print(f"  A15: {len(_hit15)} IFRS member(s) with companyfacts revenue reported as no-revenue")
if _hit15:
    flag("HIGH", f"A15 IFRS member with revenue reported as no-revenue: {_hit15[:3]}")
for _name, _mod, _edit in (
        ("A15 with_ifrs dropped", "pp", dict(override={"with_ifrs": lambda frames, other, ifrs: (frames, other)})),
        ("A15 no candidates", "pp", dict(override={"ifrs_candidates": lambda *a: set()})),
        ("A15 quarter frames read as years", "fr", dict(old='_CY = re.compile(r"^CY(\\d{4})$")',
                                                        new='_CY = re.compile(r"^CY(\\d{4})Q4I$")'))):
    try:
        _m = _variant(_PP if _mod == "pp" else _FR15, **_edit)
    except _Stale as _e:
        print(f"  mutation for {_name}: STALE ({_e})")
        flag("HIGH", f"{_name} self-test is stale, not a detection: {_e}")
        continue
    _hit = _a15(**{_mod: _m})
    print(f"  mutation for {_name}: {len(_hit)} violation(s) found, e.g. {_hit[:1]}")
    if not _hit:
        flag("HIGH", f"{_name} cannot fire: its mutant produced no violation")

hdr("A16-A19", "shared readers after the scout audit: share scale, one-time gains, debt lines, cash (E77-E81)")
from domain import one_time as _OT                 # noqa: E402
from domain import relative as _REL                # noqa: E402
_SHF = os.path.join(APP, "tests", "fixtures", "sec_shares")


def _cf(name):
    with open(os.path.join(_SHF, name + ".json")) as fh:
        return _json.load(fh)


def _a16(sec=_sec, rel=_REL):
    """A share count used is within 2x of the filer's cover page; an implausible market
    cap is refused with a reason (NTNX as the scout read it)."""
    bad = []
    for n in ("LOAR", "MCD"):
        cf = _cf(n)
        cov = sec.cover_shares(cf["facts"].get("dei"))[1]
        s = sec.extract(cf)["shares_diluted"]
        if not s or not 0.5 <= cov / s <= 2:
            bad.append((n, s, cov))
    x = {"currency": "USD", "revenue": 2853545000, "operating_income": 273970000, "shares_diluted": 292183,
         "latest_period_end": "2026-07-31", "revenue_annuals_dated": [["2026-07-31", 1], ["2025-07-31", 1]]}
    p, why = rel.point("NTNX", x, 57.56, "2026-11-23", "2026-11-24", check_base=False)
    if p is not None:
        bad.append(("NTNX", "market cap used", p["market_cap"]))
    return bad


def _a17(sec=_sec, ot=_OT):
    """HCA's FY2021 facility gain (inside its costs and expenses) comes out of that year's
    margin; a TTM gain comes out of EBIT."""
    bad = []
    d = sec.extract(_cf("HCA_gains"))
    f = FF("HCA", revenue_annuals_dated=d["revenue_annuals_dated"], gain_annuals_dated=d["gain_annuals_dated"])
    if ot.annual_addbacks(f).get("2021-12-31", 0) >= 0:
        bad.append(("HCA FY2021", "gain kept in EBIT"))
    g = FF("G", revenue=1000e6, operating_income=300e6, gain_ttm=100e6)
    ot.normalize_operating_income(g)
    if g.operating_income != 200e6:
        bad.append(("TTM gain", g.operating_income))
    # E88: NVST files no total-opex line; GP 400.6 - SG&A 284.2 - R&D 29.3 = 87.1 vs 515.2
    M = 1e6
    nv = sec._opex_line_sums({"SellingGeneralAndAdministrativeExpense": 284.2 * M,
                              "ResearchAndDevelopmentExpense": 29.3 * M}.get)
    if not sec._gain_inside(428.1 * M, 515.2 * M, 650 * M, 400.6 * M, None, None, nv):
        bad.append(("NVST Q3 2026", "gain not placed by the sum of lines"))
    if sec._gain_inside(428.1 * M, 87.1 * M, 650 * M, 400.6 * M, None, None, nv):
        bad.append(("NVST lines explain OI", "gain placed anyway (false hit)"))
    # HXL FY2022: GP 357.1 - SG&A 148.0 - R&D 45.8 + other operating income 11.9 = OI 175.2;
    # the 19.4 gain is that line net of restructuring 7.6. Amortization 6.8 is notes-only.
    hx = sec._opex_line_sums({"SellingGeneralAndAdministrativeExpense": 148.0 * M,
                              "ResearchAndDevelopmentExpense": 45.8 * M,
                              "AmortizationOfIntangibleAssets": 6.8 * M}.get)
    if not sec._gain_inside(19.4 * M, 175.2 * M, 1577.7 * M, 357.1 * M, None, None, hx, 11.9 * M, [7.6 * M]):
        bad.append(("HXL FY2022", "gain inside other operating income not placed"))
    if sec._gain_inside(19.4 * M, 175.2 * M, 1577.7 * M, 357.1 * M, None, None, hx):
        bad.append(("HXL FY2022 no other-operating line", "placed by the amortization coincidence"))
    return bad


def _a18(sec=_sec):
    """Debt is never below the balance-sheet lines filed on its date; a cited extension-concept
    debt date is unread, not partial."""
    bad = []
    d = sec.extract(_cf("QCOM_debt"))
    if d["total_debt"] < 12620e6 + 2492e6:
        bad.append(("QCOM", d["total_debt"]))
    inst = lambda v: {"units": {"USD": [{"end": "2026-03-31", "val": v, "form": "10-Q", "filed": "2026-05-07"}]}}
    flow = lambda v: {"units": {"USD": [{"start": "2026-01-01", "end": "2026-03-31", "val": v, "form": "10-Q",
                                         "filed": "2026-05-07"}]}}
    acu = {"cik": 2098, "facts": {"us-gaap": {"Revenues": flow(45e6), "OperatingIncomeLoss": flow(2e6),
                                              "LongTermDebtNoncurrent": inst(33030e3),
                                              "SecuredDebtCurrent": inst(458e3), "Liabilities": inst(90e6)}}}
    if sec.extract(acu)["total_debt"] is not None:
        bad.append(("ACU", "partial debt read as the total"))
    return bad


def _a19(sec=_sec):
    """Cash is cash and equivalents plus every current marketable-securities leg filed on the date."""
    d = sec.extract(_cf("INTU_cash"))
    return [] if d["cash"] == 4042e6 + 1279e6 else [("INTU", d["cash"])]


for _lbl, _fn, _muts in (
        ("A16", _a16, (("A16 no share-scale check", lambda: dict(sec=_variant(_sec, override={"_check_share_scale": lambda x, out: []}))),
                       ("A16 no market-cap guard", lambda: dict(rel=_variant(_REL, override={"implausible_market_cap": lambda *a, **k: None}))))),
        ("A17", _a17, (("A17 no annual gain read", lambda: dict(sec=_variant(_sec, override={"_gain_annuals": lambda *a: []}))),
                       ("A17 TTM gain ignored", lambda: dict(ot=_variant(_OT, override={"_gain": lambda ff: 0.0}))),
                       ("A17 sum-of-lines off (E88)", lambda: dict(sec=_variant(_sec, override={"_opex_line_sums": lambda get: []}))),
                       ("A17 any positive residual places a gain", lambda: dict(sec=_variant(
                           _sec, old="    if abs(oi - (gp - line_sums[0]) - gain) <= tol:",
                           new="    if abs(oi - (gp - line_sums[0]) - gain) <= tol or oi - (gp - line_sums[0]) >= 0:"))),
                       ("A17 notes-only lines at the loose tolerance (E88 HXL)", lambda: dict(sec=_variant(_sec, override={"FACE_TOL": 0.10}))))),
        ("A18", _a18, (("A18 combined total over the lines", lambda: dict(sec=_variant(_sec, old="legs > v * (1 + TOTAL_TOL)", new="legs > v * 1e9"))),
                       ("A18 extension debt read as partial", lambda: dict(sec=_variant(_sec, override={"DEBT_IN_EXTENSION": {}}))))),
        ("A19", _a19, (("A19 current AFS debt left out", lambda: dict(sec=_variant(
            _sec, old='            "DebtSecuritiesAvailableForSaleExcludingAccruedInterestCurrent",\n', new=""))),))):
    _hit = _fn()
    print(f"  {_lbl}: {len(_hit)} violation(s)")
    if _hit:
        flag("HIGH", f"{_lbl} reader guard: {_hit[:2]}")
    for _name, _mk in _muts:
        try:
            _kw = _mk()
        except _Stale as _e:
            print(f"  mutation for {_name}: STALE ({_e})")
            flag("HIGH", f"{_name} self-test is stale, not a detection: {_e}")
            continue
        _hit = _fn(**_kw)
        print(f"  mutation for {_name}: {len(_hit)} violation(s) found, e.g. {_hit[:1]}")
        if not _hit:
            flag("HIGH", f"{_name} cannot fire: its mutant produced no violation")

hdr("A20", "a cyclical at its peak is never valued as STANDARD; growth firms are never CYCLICAL (E83-E85)")
from domain import company_type as _CT              # noqa: E402
from domain.engine import scenarios as _SC          # noqa: E402
# NVDA / TMDX FY margins and revenue ($B), newest first (tests/test_cyclical_norm.py cites them)
_A20_GROWTH = {
    "NVDA": ([.604, .624, .541, .157, .373, .272, .261, .330, .280, .149],
             [215.938, 130.497, 60.922, 26.974, 26.914, 16.675, 10.918, 9.714, 6.910, 5.010], "Semiconductor"),
    "TMDX": ([.179, .085, -.119, -.336, -1.303, -1.029, -1.254, -1.555],
             [.605, .442, .242, .093, .030, .026, .024, .013], None)}


def _a20_peaks():
    """Synthetic firms that pass rule (a)-(c) by construction: a 10-year history, one
    revenue fall of 15-60% inside the last five years into a -5% loss year, and today's
    margin 1.6-3x its (>= 5%) median."""
    out = []
    for drop in (0.15, 0.3, 0.6):
        for k in (1.6, 2.0, 3.0):
            for pos in (1, 2, 4):
                m = [.12, .15, .10, .08, .14, .11, .13, .09, .16, .12]
                m[pos] = -.05                # the fall takes the margin into a loss
                med = sorted(m[1:] + [0])[5]
                rev = [100.0 * 1.05 ** -i for i in range(10)]
                rev[pos] = rev[pos + 1] * (1 - drop) - 1e-6
                m[0] = k * max(_CT.normalized_margin(m[1:] + [.5]) or med, .12)
                dated = [(f"{2026 - i}-06-30", r) for i, r in enumerate(rev)]
                out.append((f"drop{drop}/x{k}/y{pos}", m, dated))
    return out


def _a20(ct=_CT, sc=_SC):
    bad = []
    for name, m, dated in _a20_peaks():
        if ct.normalized_margin(m) and m[0] > 1.5 * ct.normalized_margin(m):
            got = ct.classify(None, m[0] * dated[0][1], dated[0][1], m, dated)[0]
            if got is not ct.CompanyType.CYCLICAL:
                bad.append((name, "peak firm valued as", got.value))
    for name, (m, rev, ind) in _A20_GROWTH.items():
        dated = [(f"{2026 - i}-01-31", r) for i, r in enumerate(rev)]
        if ct.classify(ind, m[0] * rev[0], rev[0], m, dated)[0] is ct.CompanyType.CYCLICAL:
            bad.append((name, "growth firm valued as cyclical"))
    # the Bear of a cyclical reaches its low years: never the half-model floor
    cyc = ct.cycle_profile(None, [.746, .261, .052, -.363, .315, .227, .140, .315, .493, .289],
                           [(f"{2026 - i}-08-31", r) for i, r in enumerate(
                               [133.2, 37.4, 25.1, 15.5, 30.8, 27.7, 21.4, 23.4, 30.4, 20.3])], .746)
    if cyc is None:
        return bad + [("MU history", "not cyclical")]
    kw = dict(revenue=100.0, margin_now=.746, margin_target=cyc.normal_margin, g_high=.05, g_stable=.03,
              sales_to_capital=1.3, tax_now=.15, tax_marginal=.25, wacc=.10, wacc_stable=.085,
              roic_stable=.2, cash=0, debt=0, shares=1, growth_path=[.05], margin_years=3)
    _, pack = sc.operating(kw, 300.0, None, .746, cycle=cyc)
    case = {c["name"]: c["margin_pct"] for c in pack["cases"]}
    lo_q = ct._quantile([.746, .261, .052, -.363, .315, .227, .140, .315, .493, .289], ct.BEAR_PERCENTILE)
    hi_q = ct._quantile([.746, .261, .052, -.363, .315, .227, .140, .315, .493, .289], ct.BULL_PERCENTILE)
    if abs(case["Bear"] - lo_q * 100) > 0.01:
        bad.append(("cyclical Bear", case["Bear"], f"not the window's lower quartile {lo_q*100:.2f}%"))
    if case["Bull"] > hi_q * 100 + 0.01:
        bad.append(("cyclical Bull", case["Bull"], f"above the window's upper quartile {hi_q*100:.2f}%"))
    return bad


for _name, _mk in (("A20 firm test off", lambda: dict(ct=_variant(_CT, override={"firm_cycle_test": lambda *a: (False, None)}))),
                   ("A20 revenue-fall rule off", lambda: dict(ct=_variant(_CT, override={"CYCLE_REVENUE_DROP": -10.0}))),
                   ("A20 Bear half-floor for cyclicals", lambda: dict(sc=_variant(
                       _SC, old="    if cycle is not None:\n        priced.update", new="    if False:\n        priced.update")))):
    if _name.endswith("firm test off"):
        _hit = _a20()
        print(f"  A20: {len(_hit)} violation(s) over {len(_a20_peaks())} synthetic peaks + {len(_A20_GROWTH)} growth firms")
        if _hit:
            flag("HIGH", f"A20 cyclical guard: {_hit[:2]}")
    try:
        _kw = _mk()
    except _Stale as _e:
        flag("HIGH", f"{_name} self-test is stale, not a detection: {_e}")
        continue
    _hit = _a20(**_kw)
    print(f"  mutation for {_name}: {len(_hit)} violation(s) found, e.g. {_hit[:1]}")
    if not _hit:
        flag("HIGH", f"{_name} cannot fire: its mutant produced no violation")

hdr("A21", "a cyclical is valued on its normal margin x TODAY's revenue, consensus years 1-2 only, organic through-cycle growth, MoS < -50% caps at HOLD (E92-E95)")
_A21_MU = _json.load(open(os.path.join(APP, "tests", "fixtures", "cyclical", "MU.json")))
_A21_HAND = 470.86        # ~/work/v_cyc/tools/hand.py alternative (i'), the verifier's rebuild


def _a21_mu(**over):
    d = dict(_A21_MU, **over)
    ff = FF(**{k: v for k, v in d.items() if k in FF.__dataclass_fields__})
    ff.flags = []
    return E.DeepV82Engine().evaluate(ff, rf=0.045)


def _a21():
    bad = []
    v = _a21_mu()
    inp, path = v.dcf["inputs"], v.dcf["path"]
    g_stable = 0.045
    if abs(path[0]["revenue"] - _A21_MU["revenue"] * (1 + inp["growth_path"][0])) > 1e6:
        bad.append(("M1 base", path[0]["revenue"], "year 1 is not today's revenue grown once"))
    if max(inp["growth_path"]) > g_stable + 1e-9:
        bad.append(("M1 growth", inp["growth_path"], "above the stable rate with no consensus"))
    if abs(v.anchor_value - _A21_HAND) / _A21_HAND > 0.01:
        bad.append(("M1 FV", v.anchor_value, f"not within 1% of hand.py {_A21_HAND}"))
    two = _a21_mu(rev_growth_path=[[0.70, 25], [0.05, 20]])
    five = _a21_mu(rev_growth_path=[[0.70, 25], [0.05, 20], [0.40, 15], [0.30, 12], [0.20, 11]])
    gp = five.dcf["inputs"]["growth_path"]
    if gp[:2] != [0.70, 0.05] or any(abs(x - inp["growth_path"][0]) > 1e-12 for x in gp[2:]):
        bad.append(("M2 path", gp, "consensus past year 2 used"))
    if abs(two.anchor_value - five.anchor_value) > 0.01:
        bad.append(("M2 FV", five.anchor_value, two.anchor_value))
    apps = FF(ticker="APPS")
    apps.revenue_annuals_dated = [["2026-03-31", 565.251e6], ["2025-03-31", 490.506e6], ["2024-03-31", 544.482e6],
                                  ["2023-03-31", 665.920e6], ["2022-03-31", 747.596e6], ["2021-03-31", 313.579e6]]
    apps.acquisitions_annuals_dated = [["2022-03-31", 0.5 * 313.579e6]]
    g, _ = E.through_cycle_growth(apps)
    clean = [565.251 / 490.506, 490.506 / 544.482, 544.482 / 665.920, 665.920 / 747.596]
    want = (clean[0] * clean[1] * clean[2] * clean[3]) ** 0.25 - 1
    if g is None or abs(g - want) > 1e-9:
        bad.append(("M3 organic", g, f"not the clean-year mean {want:.4f}"))
    if E.recommendation(4.5, mos=-0.6) != "HOLD" or E.recommendation(3.5, mos=-0.51) != "HOLD":
        bad.append(("M4 cap", E.recommendation(4.5, mos=-0.6), "MoS -60% not capped at HOLD"))
    hi = _a21_mu(price=1500.0)
    if hi.composite >= 3 and hi.recommendation != "HOLD":
        bad.append(("M4 engine", hi.recommendation, "MU at $1,500 not capped"))
    return bad


def _a21_patch(attrs):
    saved = {k: getattr(E, k) for k in attrs}
    for k, v in attrs.items():
        setattr(E, k, v)
    try:
        return _a21()
    finally:
        for k, v in saved.items():
            setattr(E, k, v)


_hit = _a21()
print(f"  A21: {len(_hit)} violation(s) on MU (FV vs hand.py {_A21_HAND}), consensus 2 vs 5 years, APPS M&A, MoS cap")
if _hit:
    flag("HIGH", f"A21 cyclical valuation guard: {_hit[:2]}")
for _name, _attrs in (
        ("A21 M1 trend slope as growth", {"cycle_growth": lambda f, g: (0.1144, "log-linear trend slope")}),
        ("A21 M2 full consensus path", {"CYCLE_CONSENSUS_YEARS": 5}),
        ("A21 M3 acquisition years counted", {"INORGANIC_ACQ_SHARE": 1e9}),
        ("A21 M4 no MoS cap", {"MOS_HOLD_CAP": -1e9})):
    _hit = _a21_patch(_attrs)
    print(f"  mutation for {_name}: {len(_hit)} violation(s) found, e.g. {_hit[:1]}")
    if not _hit:
        flag("HIGH", f"{_name} cannot fire: its mutant produced no violation")

print("\n" + "=" * 78)
print(f"SUMMARY: {len(FOUND)} findings")
for sev, m in FOUND:
    print(f"  [{sev}] {m[:110]}")

# This file started as a one-off audit; wiring it into run_tests turns it into a guard
# against RE-introducing the class of defect it was written to find. LOW findings are
# accepted-and-documented (see CODE_REVIEW_2026-08-04_PASS2.md section 4), so only
# MED and HIGH fail the build.
_bad = [x for x in FOUND if x[0] in ("MED", "HIGH")]
if _bad:
    print(f"\nFAIL: {len(_bad)} MED/HIGH finding(s) — see above")
    sys.exit(1)
print(f"\nOK: {len(FOUND)} LOW finding(s), all documented and accepted")
