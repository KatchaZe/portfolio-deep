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
    members = {"T": 1, "TB": 1, "P": 2, "Q": 3, "QQ": None, "EU": 4}
    sics = {"T": "3674", "TB": "3674", "P": "3674", "Q": "3672", "QQ": "3674", "EU": "3674"}
    frames = {y: {1: [50.0, f"{y}-12-31"], 2: [30.0, f"{y}-12-31", "us-gaap:Revenues", None],
                  3: [20.0, f"{y}-12-31", "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
                      "revenues_component"]} for y in _Y5}
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


def _a13_max_of_concepts(e):
    """The rule R35 replaced: the largest concept across filings, no note (TMDX CY2021)."""
    c, v = max(((c, v["val"]) for c, v in e.items()), key=lambda cv: cv[1])
    return {"concept": c, "val": v, "note": None}


_res = _a13_resolution()
_sh = _a13_shares()
for _name, _found in (("A13a", _a13a(_res)), ("A13b", _a13b(_sh)), ("A13c", _a13c()), ("A13d", _a13d(_sh))):
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
                           new="        pass"), lambda m: _a13d(_a13_shares(m)[:2]))):
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
