"""
live_check — the ONE check that talks to the real network. Writes nothing.

Everything else in the safety net is offline: contracts read source, invariants read
the stored rows, replay re-scores what is already on disk. None of them execute
`refresh.analyze_row`, which is why `pipeline/refresh.py` measured 0% coverage and
`sources/fmp.py` 8% on the day the stale-financials fallback was written. Code that
has never run is not code that works.

This calls the same `analyze_row` the app calls, on a few tickers, and prints what
came back next to what is currently stored — so the first live run of a changed
engine is READ, not merely committed.

It commits nothing: `analyze_row` is documented ephemeral (network only, no store),
and the stored side is read straight off disk so not even a Drive pull is triggered.

    python -m tests.live_check                 # MSFT TSM NVO — the three that matter
    python -m tests.live_check AAPL NVDA
    python -m tests.live_check NVO --why       # the inputs each fair value was built on
    python -m tests.live_check --all           # every stored ticker (costs quota)

Why those three by default:
    MSFT  a clean, complete filer — the trend strip and every new leg should populate
    TSM   SEC companyfacts stop at FY2024 (20 months stale) — exercises the DQ2
          fallback: FMP is tried FIRST, and if it lands the SEC series must be CLEARED
    NVO   reports in DKK — exercises the FX path on the new dated series
"""
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config                                    # noqa: E402
from sources import yahoo, damodaran             # noqa: E402
from pipeline import refresh                     # noqa: E402
from domain import trend as trend_mod            # noqa: E402

DEFAULT = ["MSFT", "TSM", "NVO"]
STORE = os.path.join(config.DATA_DIR, "portfolio.json")


def _stored():
    """Read the store off disk — no store.load(), so no Drive pull, no state change."""
    try:
        with open(STORE, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _n(v, nd=2):
    return "—" if v is None else (f"{v:,.{nd}f}" if isinstance(v, (int, float)) else str(v))


def _delta(new, old, nd=2):
    if new is None or old is None:
        return f"{_n(new, nd)}   (เดิม {_n(old, nd)})"
    if isinstance(new, (int, float)) and isinstance(old, (int, float)):
        d = new - old
        mark = "  ← ขยับ" if abs(d) > 10 ** -nd else ""
        return f"{_n(new, nd)}   (เดิม {_n(old, nd)}, {d:+.{nd}f}){mark}"
    return f"{new}   (เดิม {old})" + ("  ← ขยับ" if new != old else "")


def _point(p, unit):
    """A row's points carry {"fy","end","v"} — v is RAW dollars for a money row and
    already a percent for a pct row. Printing them alike would silently show
    2.8e+11 next to 45.6 and read as corruption."""
    v = p.get("v")
    if v is None:
        return f"{p.get('fy', '?')}:—"
    return f"{p.get('fy', '?')}:" + (f"{v / 1e9:,.1f}" if unit == "money" else f"{v:,.1f}")


def _strip(t5):
    if not t5:
        print("    แถบเทรนด์ 5 ปี: ไม่มี  <- ตรวจว่าทำไม (งบเก่า / ซีรีส์ขาด / fallback ล้างทิ้ง)")
        return
    print(f"    แถบเทรนด์ {t5.get('years')} ปี  ({t5.get('source')})"
          + ("  [ไม่ครบ]" if t5.get("partial") else ""))
    for r in t5.get("rows") or []:
        unit = r.get("unit")
        pts = r.get("points") or []
        suffix = " $B" if unit == "money" else (" %" if unit == "pct" else "")
        head = f"      {(r.get('label') or '?') + suffix:<24}"
        vals = "  ".join(_point(p, unit) for p in pts[-5:])
        smry = r.get("summary")
        tail = (f"   {r.get('summary_label') or 'สรุป'} {smry:+,.1f}"
                if isinstance(smry, (int, float)) else "   สรุป —")
        print(head + vals + tail + f"   {r.get('direction') or ''}")
        if r.get("note"):
            print(f"        ↳ {r['note']}")


def main(argv):
    names = [a.upper() for a in argv if not a.startswith("-")] or DEFAULT
    why = "--why" in argv          # dump the inputs a fair value was actually built on
    s = _stored()
    if "--all" in argv:
        names = sorted((s.get("results") or {}).keys())
    if not config.FMP_API_KEY:
        print("!! ไม่มี FMP_API_KEY — เส้นทาง fallback งบเก่า (DQ2) จะไม่ถูกทดสอบเลย\n")

    rf, rf_live = yahoo.fetch_treasury_10y()
    # same shared table the real refresh fetches once — otherwise a single-ticker
    # run would score against the bundled snapshot and disagree with the app (REVIEW-5)
    roc = damodaran.fetch_roc_table(cache_dir=config.CACHE_DIR,
                                    user_agent=config.SEC_USER_AGENT)
    print(f"rf (10Y) = {rf * 100:.2f}%  live={rf_live}   tickers: {', '.join(names)}\n")

    total_calls, failed = 0, []
    for t in names:
        print("=" * 78)
        print(t)
        print("=" * 78)
        try:
            ff, val, calls = refresh.analyze(t, rf, config.FMP_API_KEY,
                                             rf_live=rf_live, roc_table=roc)
        except Exception as e:                       # a live failure is a RESULT here
            failed.append(t)
            print(f"  !! ล้มเหลว: {type(e).__name__}: {e}\n")
            continue
        total_calls += calls
        old = (s.get("results") or {}).get(t) or {}

        print(f"  ราคา {_n(ff.price)}  ค่าเงิน {ff.currency}  งบล่าสุด FY{ff.fiscal_year}"
              f"  ความเชื่อมั่น {ff.confidence} ({ff.confidence_tier})  FMP calls {calls}")
        print(f"  คะแนนรวม     {_delta(val.composite, old.get('composite'))}")
        print(f"  คำแนะนำ      {_delta(val.recommendation, old.get('recommendation'))}")
        print(f"  anchor FV    {_delta(val.anchor_value, old.get('anchor_value'))}"
              f"   [{val.anchor_method}]")
        for k, label in (("D", "D  อุปสงค์"), ("E_exec", "E  การดำเนินงาน"),
                         ("E_econ", "E  เศรษฐศาสตร์"), ("P", "P  ราคา")):
            print(f"    {label:<18}{_delta(getattr(val, k), old.get(k))}")
        rd = val.reverse_dcf or {}
        print(f"  โต 1 ปีจริง  {_n(rd.get('actual_1y_pct'), 1)}%"
              f"   ตลาดคิด {_n(rd.get('implied_cagr_pct'), 1)}%   {rd.get('verdict')}")

        km = val.key_metrics or {}
        if km.get("roic_basis"):
            print(f"  ฐาน ROIC     {km['roic_basis']}")
        if km.get("fv_disagreement_x"):
            print(f"  !! สองวิธีต่างกัน {km['fv_disagreement_x']}x")
        _strip(trend_mod.build(ff))
        if why:
            print("  ── inputs ──")
            for k in ("growth_lt", "forward_eps", "eps_gaap", "net_income",
                      "shares_diluted", "beta", "revenue"):
                print(f"    {k:<18}{getattr(ff, k, None)}")
            print("  ── key_metrics ──")
            for k in sorted(km):
                print(f"    {k:<28}{km[k]}")
            for line in (val.subscores or {}).get("breakdown") or []:
                print(f"    · {line}")

        data_flags = [f for f in (ff.flags or []) if f.startswith("DATA[")]
        other = [f for f in (ff.flags or []) if not f.startswith("DATA[")]
        for f in data_flags:
            print(f"  ** {f}")
        for f in other:
            print(f"     {f}")
        print()

    print("=" * 78)
    print(f"FMP calls ที่ใช้ไปรอบนี้: {total_calls}"
          + (f"   ล้มเหลว: {', '.join(failed)}" if failed else ""))
    print("ไม่มีการเขียนลง store — ตัวเลขข้างบนยังไม่ได้ commit")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
