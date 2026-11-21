"""test_peer_panel_render: RENDER the คู่แข่ง & Moat tab in node.

Loads format.js, card_signals.js and card_peer.js as index.html ships them and runs
peerPane / moatChip / ppTable on display rows built by pipeline/rows from the real
store (data/portfolio.json, when present: NVDA improving with a full table, UNH eroding,
ASML foreign with no share, ABBV with no table, SPCX with no label), the same rows with
moat_trend rebuilt by the current engine (tools/moat_replay: measures, trends, watch),
and synthetic payloads (an empty object, every hint type). Skips when node is missing.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import rows as rows_mod          # noqa: E402
from tests.frontend_source import frontend_files   # noqa: E402

MODULES = ("format.js", "card_signals.js", "card_peer.js")
STORE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "portfolio.json")
TICKERS = ("NVDA", "UNH", "ASML", "ABBV", "SPCX")
BAD = ("undefined", "NaN", "[object Object]", "Infinity")

SYNTH = {"ticker": "SYN", "key_metrics": {"spread_pct": 12.0}, "uncertainty": {"rating": "High"},
         "peer_panel": {"as_of": "2026-11-20", "industry": "Beverage (Soft)", "flags": [],
                        "table": {"stats": {}, "target_vs_median": {}, "rows": [
                            {"symbol": "SYN", "is_target": True, "revenue_ttm": 2e9, "cagr3_fy": 0.1,
                             "gross_margin_fy": 0.5, "op_margin_ttm": 0.2, "ev_sales": 3.0, "ev_ebit": 15.0,
                             "pe": 20.0, "rank3": {"scale": 1, "growth": None, "margin": 2}, "outlier": []},
                            {"symbol": "GAPCO", "is_target": False, "revenue_ttm": None, "cagr3_fy": None,
                             "gross_margin_fy": None, "op_margin_ttm": None, "ev_sales": None, "ev_ebit": None,
                             "pe": None, "rank3": {}, "outlier": []}]},
                        "industry_share": None},
         "moat_trend": {"label": "stable", "n_signals": 2, "watch": ["ROIC − WACC รายปี"], "flags": [],
                        "signals": [{"name": "spread_slope", "value": 0.4, "direction": 0, "window": "FY2021..FY2025"},
                                    {"name": "share_change", "value": 0.2, "direction": 1, "window": "CY2022..CY2025"}],
                        "source_hints": [
                            {"type": "efficient_scale", "strength": "moderate", "trend": -1,
                             "evidence": "top 5 filers hold 93% of industry revenue",
                             "measures": [{"metric": "top5_share", "value": 0.93, "period": "CY2025"},
                                          {"metric": "share_change_pp", "value": -0.4, "period": "CY2022..CY2025"},
                                          {"metric": "n_members", "value": 12, "period": "CY2025"}]},
                            {"type": "cost_advantage", "strength": "moderate", "trend": None,
                             "evidence": "operating margin 10pp above the peer median (TTM)",
                             "measures": [{"metric": "opm_gap_pp", "value": 10.0, "period": "TTM"},
                                          {"metric": "gm_gap_pp", "value": -3.0, "period": "FY2025"}]}]}}


def _js():
    shipped = {os.path.basename(p): p for p in frontend_files()}
    missing = [m for m in MODULES if m not in shipped]
    assert not missing, "index.html no longer loads %s" % missing
    return "\n".join(open(shipped[m], encoding="utf-8").read() for m in MODULES)


def _row(t, s, moat=None):
    r = (s["results"] or {}).get(t) or {}
    if moat is not None:
        r = dict(r, moat_trend=moat)
    row = rows_mod.valuation_row(t, (s.get("facts") or {}).get(t) or {}, r, {}, {},
                                 rev_surprises=None, has_new_schema=True, fmp_on=False)
    return {k: row[k] for k in ("ticker", "peer_panel", "moat_trend", "key_metrics", "uncertainty")}


def _xss():
    """Every string the store can put in the tab carries markup; none may reach innerHTML raw."""
    ev = '<img src=x onerror=alert(1)>'
    x = json.loads(json.dumps(SYNTH))
    x["uncertainty"] = {"rating": ev}
    pp, mt = x["peer_panel"], x["moat_trend"]
    pp.update(as_of=ev, industry=ev, flags=[ev])
    pp["table"]["rows"][0]["symbol"] = ev
    pp["table"]["rows"][1]["symbol"] = "<script>alert(2)</script>"
    pp["industry_share"] = {"years": [2023, 2024, 2025], "target_share": {"2023": .1, "2024": .1, "2025": .1},
                            "top5_share": {"2023": .5, "2024": .5, "2025": .5}, "hhi": {}, "coverage": {"2025": .9},
                            "n_members": ev, "flags": [ev], "matched_n": 5, "share_change_pp": 0.0, "top5_change_pp": 0.0}
    mt.update(watch=[ev], flags=[ev, "S1: " + ev])
    mt["signals"][0]["window"] = ev
    for h in mt["source_hints"]:
        h["type"] = ev
        h["evidence"] = ev
        for m in h["measures"]:
            m["period"] = ev
    mt["source_hints"].append({"type": "scale", "strength": "weak", "trend": 0, "evidence": ev,
                               "measures": [{"metric": "industry_share", "value": .2, "period": ev}]})
    return x


def _cases():
    noex = dict(SYNTH, key_metrics={"spread_pct": -3.9},
                moat_trend=dict(SYNTH["moat_trend"], source_hints=[],
                                flags=["hints: no excess return (ROIC − WACC ≤ 0)"]))
    cases = {"EMPTY": {}, "SYN": SYNTH, "SYN_NOEX": noex, "XSS": _xss()}
    if os.path.exists(STORE):
        from tools import moat_replay
        with open(STORE, encoding="utf-8") as fh:
            s = json.load(fh)
        new = {t: n for t, (_o, n) in moat_replay.rebuild(s).items()}
        for t in TICKERS:
            cases[t] = _row(t, s)
            cases[t + "_new"] = _row(t, s, new[t])
    return cases


RUN = r"""
const out={};
for(const k in CASES){const x=CASES[k];
  out[k]={pane:peerPane(x,'c-'+k),chip:moatChip(x),arrow:moatArrow(x),
          asc:ppTable((x.peer_panel||{}),{k:'pe',dir:1}),sym:ppTable((x.peer_panel||{}),{k:'symbol',dir:1})};}
// the sort button toggles the same column and redraws only the table
var CARD_ROWS={'c-NVDA':CASES.NVDA||CASES.SYN};
const drawn=[]; const grid={dataset:{ppcard:'c-NVDA'},querySelector:q=>q==='.pp-tbl'?{set outerHTML(h){drawn.push(h)}}:{focus(){}}};
const btn=k=>({dataset:{psort:k},closest:()=>grid});
ppSortClick(btn('pe')); ppSortClick(btn('pe')); ppSortClick(btn('symbol'));
out.clicks={sort:PP_SORT['c-NVDA'],drawn:drawn.map(h=>(h.match(/aria-sort="(\w+)"/)||[])[1]||null)};
console.log(JSON.stringify(out));
"""


def _render(cases):
    node = shutil.which("node")
    js = _js() + "\nconst CASES=%s;\n" % json.dumps(cases) + RUN
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as fh:
        fh.write(js)
        path = fh.name
    try:
        r = subprocess.run([node, path], capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, "card_peer.js threw in node:\n" + r.stderr[-1500:]
        return json.loads(r.stdout)
    finally:
        os.unlink(path)


def _order(html):
    body = html.split("<tbody>")[1].split("</tbody>")[0]
    return [re.sub(r"<[^>]+>", "", m).split(" ")[0] for m in re.findall(r'<th scope="row">(.*?)</th>', body)]


def test_tab_renders_every_shape():
    if not shutil.which("node"):
        print("SKIP: node not available")
        return
    cases = _cases()
    out = _render(cases)
    for k, v in out.items():
        if k == "clicks":
            continue
        blob = v["pane"] + v["chip"] + v["asc"]
        for bad in BAD:
            assert bad not in blob, "%s rendered %r:\n%s" % (k, bad, blob[:800])
        for need in ("แนวโน้ม moat", "เกณฑ์ชั่วคราว · ยังไม่ backtest", "สัญญาณ 4 ตัว",
                     "ส่วนแบ่งรายได้ในอุตสาหกรรม", "วัดจากงบย้อนหลัง", "ความไม่แน่นอน",
                     "ไม่มีผลต่อคะแนน DEEP หรือ Fair Value", 'class="pp-grid"'):
            assert need in v["pane"], (k, need)
    e = out["EMPTY"]["pane"]
    assert "ข้อมูลไม่พอ" in e and "หุ้นนี้ไม่มีกลุ่มอุตสาหกรรมของ Damodaran" in e
    assert "ข้อมูลคู่แข่งยังไม่ถูกสร้างในรอบ refresh นี้" in e
    assert out["EMPTY"]["arrow"] == "" and out["EMPTY"]["chip"] == ""
    syn = out["SYN"]["pane"]
    for need in ("ขนาดตลาดที่พอดี (efficient scale)", "ความได้เปรียบด้านต้นทุน", "93%", "↘</span> อ่อนลง",
                 "แนวโน้มยังไม่ทราบ", "ติดตามไตรมาสหน้า:", "efficient_scale: top 5 filers hold 93%",
                 "ความกว้าง moat</span><b>กว้าง</b>", "<b>High</b>"):
        assert need in syn, need
    xs = out["XSS"]
    for blob in (xs["pane"], xs["asc"], xs["sym"]):
        assert "<img" not in blob and "<script" not in blob and "onerror=alert(1)>" not in blob.replace("&lt;img", ""), \
            "store markup reached the DOM raw"
    assert "&lt;img src=x onerror=alert(1)&gt;" in xs["pane"] and "&lt;script&gt;alert(2)&lt;/script&gt;" in xs["sym"]
    ne = out["SYN_NOEX"]["pane"]
    assert "ยังไม่มีกำไรส่วนเกิน (ROIC ≤ WACC) จึงไม่นับเป็นแหล่ง moat" in ne
    assert "ความได้เปรียบด้านต้นทุน" not in ne and "ขนาดตลาดที่พอดี" not in ne.split("หมายเหตุข้อมูล")[0]
    assert "ยังไม่มีกำไรส่วนเกิน" not in syn
    gap = syn.split('<th scope="row">GAPCO</th>')[1].split("</tr>")[0]
    assert gap.count(">—</td>") == 7 and "0.0%" not in gap, "a missing metric is a dash, never a zero"
    assert "กลุ่มบน" in syn and "กลุ่มกลาง" in syn
    assert "แนวโน้ม ทรงตัว" in out["SYN"]["chip"] and "→" in out["SYN"]["chip"]
    if "NVDA" not in cases:
        print("peer tab renders (synthetic only: no data/portfolio.json) OK")
        return
    store_checks(cases, out)
    print("peer tab renders on NVDA UNH ASML ABBV SPCX (stored + replayed) and synthetic shapes OK")


def store_checks(cases, out):
    n = out["NVDA"]["pane"]
    rows = cases["NVDA"]["peer_panel"]["table"]["rows"]
    assert "↗</span><span>ดีขึ้น" in n and "แนวโน้ม ดีขึ้น" in out["NVDA"]["chip"]
    assert n.split("<tbody>")[1].split("</tbody>")[0].count("<tr") == len(rows) == 6
    assert "median คู่แข่ง" in n.split("<tfoot>")[1] and "ไม่ใช่ market share จริง" in n
    assert "CY2025" in n and 'role="img" aria-label="ส่วนแบ่ง CY2021' in n
    assert ("(นอกกรอบ)" in n) == any(r["outlier"] for r in rows)
    assert "แข็งขึ้น" in out["NVDA_new"]["pane"] and "ติดตามไตรมาสหน้า:" in out["NVDA_new"]["pane"]
    assert "gross margin สูงกว่า median คู่แข่ง 18 pp" in n, "stored English evidence is translated"
    want = [r["symbol"] for r in sorted(rows, key=lambda r: (r["pe"] is None, r["pe"] or 0))]
    assert _order(out["NVDA"]["asc"]) == want, (_order(out["NVDA"]["asc"]), want)
    assert 'aria-sort="ascending"><button type="button" data-psort="pe"' in out["NVDA"]["asc"]
    assert _order(out["NVDA"]["sym"]) == sorted(r["symbol"] for r in rows)
    assert out["clicks"] == {"sort": {"k": "symbol", "dir": 1}, "drawn": ["descending", "ascending", "ascending"]}, out["clicks"]
    assert "↘</span><span>เสื่อม" in out["UNH"]["pane"] and "แนวโน้ม เสื่อม" in out["UNH"]["chip"]
    a = out["ASML"]["pane"]
    assert "ไม่พบรายได้ของหุ้นนี้ใน SEC frames" in a and "ตารางคู่แข่ง" in a
    b = out["ABBV"]["pane"]
    assert "มีคู่แข่งที่ใช้ได้ 4 ราย ต้องมีอย่างน้อย 5 ราย" in b and "ต่ำกว่าเกณฑ์ 70%" in b
    assert "ข้อมูลไม่พอ" in out["ABBV_new"]["pane"], "R21 leaves ABBV one signal: no label"
    s = out["SPCX"]
    assert "ข้อมูลไม่พอ" in s["pane"] and s["arrow"] == "" and "แนวโน้ม" not in s["chip"]


if __name__ == "__main__":
    from tests._harness import run_suite
    run_suite(globals(), "ALL test_peer_panel_render PASSED")
