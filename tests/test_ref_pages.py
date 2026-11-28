"""test_ref_pages: the How to and Ref tabs stay true to the dashboard and the code.

1. Every label tools/ui_inventory.py finds on the dashboard has a Ref entry, or sits in
   docs/ref/content.CHROME with a reason. A new label without an entry fails here.
2. The Ref quotes the code's thresholds: each number below is read from the module
   that runs it, so changing a constant fails here until the Ref text follows.
3. index.html carries the block tools/build_ref.py generates (no hand edits).
4. The page works: search, collapse and the TOC / How to links, run in node on the
   generated markup through static/js/ref_page.js."""
import datetime as dt
import html
import inspect
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config                                          # noqa: E402
from docs.ref.content import CHROME, SECTIONS, entries, entry   # noqa: E402
from docs.ref.howto import TASKS                       # noqa: E402
from docs.ref.model import Entry                       # noqa: E402
from tools import build_ref                            # noqa: E402
from tools.ui_inventory import inventory, strip_docs   # noqa: E402
from tests.frontend_source import frontend_files       # noqa: E402

INDEX = os.path.join(ROOT, "index.html")


def _page():
    return open(INDEX, encoding="utf-8").read()


def test_every_dashboard_label_has_a_ref_entry():
    es = entries()
    missing = ["%s:%d [%s] %s" % (l.file, l.line, l.kind, l.text) for l in inventory()
               if l.text not in CHROME and not any(e.covers_label(l) for e in es)]
    assert not missing, ("labels with no Ref entry (add one to docs/ref/content*.py, or to CHROME "
                         "with a reason):\n" + "\n".join(missing))
    print("every dashboard label has a Ref entry OK (%d labels)" % len(inventory()))


def test_the_inventory_finds_a_new_label():
    """the coverage check is only as good as the inventory: a new chip must show up in it"""
    from tools import ui_inventory as UI
    js = "const x=`<span class=\"chip\">Brand-new gauge</span>`;"
    found = [t for text, _ in UI.js_literals(js) for _, t in UI.labels_in(text)]
    assert found == ["Brand-new gauge"], found
    lab = UI.Label("Brand-new gauge", "class:chip", "x.js", 1, "card")
    assert not any(e.covers_label(lab) for e in entries()), "a made-up label must be uncovered"
    print("a new label is found and reported uncovered OK")


def test_excludes_and_covers_are_live():
    labels = inventory()
    texts = {l.text for l in labels}
    stale = [c for c in CHROME if c not in texts]
    assert not stale, "CHROME names labels the dashboard no longer shows: %s" % stale
    shipped = html.unescape("\n".join(strip_docs(open(p, encoding="utf-8").read()) if p.endswith(".html")
                                      else open(p, encoding="utf-8").read() for p in frontend_files()))
    engine = "\n".join(open(os.path.join(d, f), encoding="utf-8").read()
                       for d in (os.path.join(ROOT, "domain"), os.path.join(ROOT, "domain", "risk"),
                                 os.path.join(ROOT, "pipeline"))
                       for f in os.listdir(d) if f.endswith(".py"))
    dead = []
    for e in entries():
        for c in e.covers:
            probe = Entry(e.id, "\x01", e.kind, "-", "-", covers=(c,))
            if any(probe.covers_label(l) for l in labels):
                continue
            if c.startswith(("re:", "kind:")):
                dead.append((e.id, c))
            elif not all(part.strip() in shipped or part.strip() in engine for part in c.split("…")):
                dead.append((e.id, c))
    assert not dead, "covers that match nothing the app shows: %s" % dead
    print("CHROME excludes and Ref covers all name live labels OK")


def test_ids_are_unique_and_every_entry_is_complete():
    ids = [e.id for e in entries()]
    assert len(ids) == len(set(ids)), sorted({i for i in ids if ids.count(i) > 1})
    thin = [e.id for e in entries() if not (e.what and e.where)]
    assert not thin, "entries without a meaning or a place on screen: %s" % thin
    print("%d Ref entries, unique ids, each with meaning and place OK" % len(ids))


def test_index_html_carries_the_generated_block():
    page = _page()
    assert build_ref.splice(page) == page, "index.html is out of date: python -B -m tools.build_ref"
    assert "howto_figs" not in page
    print("index.html How to / Ref block matches tools/build_ref.py OK")


def test_howto_links_and_toc_resolve():
    page = _page()
    ids = set(re.findall(r'\bid="([^"]+)"', page))
    hrefs = re.findall(r'href="#((?:ref|refsec|howto)-[^"]+)"', page)
    broken = sorted({h for h in hrefs if h not in ids})
    assert not broken, "links to missing ids: %s" % broken
    for s in SECTIONS:
        assert 'href="#refsec-%s"' % s.id in page, "TOC lacks section %s" % s.id
    linked = {i for _, _, _, steps in TASKS for _, refs in steps for i in refs}
    assert linked <= {e.id for e in entries()}
    assert len(TASKS) == 7 and len(linked) >= 30, (len(TASKS), len(linked))
    print("How to (%d tasks, %d Ref links) and TOC resolve OK" % (len(TASKS), len(linked)))


def _has(eid, *wants):
    t = entry(eid).text()
    miss = [w for w in wants if w not in t]
    assert not miss, "Ref entry %s does not say %s" % (eid, miss)


def test_ref_thresholds_equal_the_code():
    from domain import moat_trend as MT, peer_panel as PP, trend as TR, ledger as L
    from domain.engine import deep_v82 as E, uncertainty as U
    from domain.risk import portfolio as RP, targets as TG, stress as S
    from pipeline import batch, margin_track, market_valuation as mv, portfolio as PF
    from sources import fmp
    from sources.damodaran_snapshot import ERP_HISTORY
    g = lambda x: f"{x:g}"
    pc = lambda x: f"{x * 100:g}"
    # moat trend S1-S4 and the label rule
    _has("s1", f"±{g(MT.SPREAD_SLOPE_PP)}%/ปี", f"อย่างน้อย {MT.MIN_SLOPE_YEARS} ปี", "Theil-Sen")
    _has("s2", f"±{pc(MT.SHARE_REL)}% ของเดิม", f"อย่างน้อย {MT.SUB_MIN_MATCHED} ราย", f"≥{pc(PP.COVERAGE_MIN)}%")
    _has("s3", f"±{g(MT.GM_GAP_PP)}%", f"อย่างน้อย {PP.GM_MIN_COMPARATORS} ราย", "SIC 3 หลัก")
    _has("s4", f"±{g(MT.INC_PP)}%", f"อย่างน้อย {pc(MT.INC_MIN_GROWTH)}% ใน 3 ปี")
    _has("moat-trend", f"อย่างน้อย {MT.LABEL_AT} สัญญาณ", f"≥+{MT.LABEL_AT}", f"≤−{MT.LABEL_AT}")
    lo, hi = PP.FINANCIAL_SIC
    _has("rules-abc", f"อย่างน้อย {PP.GM_MIN_COMPARATORS} ราย", f"อย่างน้อย {MT.SUB_MIN_MATCHED} ราย", f"SIC {lo}–{hi}")
    _has("moat-sources", f"≥{g(MT.INTANGIBLE_PP[0])}% อ่อน", f"≥{g(MT.INTANGIBLE_PP[1])}% ปานกลาง",
         f"ส่วนแบ่ง ≥{pc(MT.SCALE_SHARE[0])}% อ่อน", f"≥{pc(MT.SCALE_SHARE[1])}% ปานกลาง",
         f"Top-5 ≥{pc(MT.EFFICIENT_TOP5[0])}%", f"Top-5 ≥{pc(MT.EFFICIENT_TOP5[1])}%",
         f"±{g(MT.EFFICIENT_SHARE_PP)}%", f"ไม่เกิน {MT.EFFICIENT_MAX_MEMBERS} ราย",
         f"OPM สูงกว่า median ≥{g(MT.COST_OPM_PP)}%", f"GM ไม่เกิน median +{g(MT.COST_GM_PP)}%")
    _has("top5-hhi", f"±{g(MT.TOP5_TREND_PP)}%", f"ไม่เกิน {PP.TOP_N} ราย")
    _has("peer-table", f"IQR {g(PP.IQR_K)} เท่า", f"อย่างน้อย {PP.SUB_MEDIAN_MIN} ราย")
    # incremental ROIC window
    _has("inc-roic", f"อย่างน้อย {TR.INC_MIN_YEARS} year-end", f"INC_MIN_YEARS = {TR.INC_MIN_YEARS}",
         f"ไม่เกิน {TR.MAX_YEARS} year-end", f"อย่างน้อย {pc(TR.MIN_IC_DELTA_FRAC)}%", "NOPAT เป็นบวก")
    _has("trend-strip", f"≤{g(TR.CAGR_FLAT_PCT)}%/ปี", f"≤{g(TR.MARGIN_FLAT_PP)}%")
    # FMP quota
    per = batch.FMP_CALLS_PER_TICKER
    utc_noon = dt.datetime(2026, 11, 23, 12, 0, tzinfo=dt.timezone.utc)
    _has("quota", f"cap {config.QUOTA_CAP} ครั้ง/วัน", f"หุ้นละไม่เกิน {per} ครั้ง", f"กันไว้ {config.QUOTA_RESERVE} ครั้ง",
         f"รีเซ็ต {fmp.reset_time_bkk(utc_noon)} น.", f"÷ {per}")
    for eid in ("run-refresh", "watch-run"):
        _has(eid, f"ไม่เกิน {per} ครั้ง")
    # rating, weights, MoS and uncertainty bands
    assert [E.recommendation(c) for c in (4, 3.99, 3, 2.99, 2, 1.99)] == [
        "BUY", "HOLD / Accumulate", "HOLD / Accumulate", "HOLD", "HOLD", "SELL / AVOID"]
    assert E.recommendation(4.5, mos=E.MOS_CAP_HOLD - 0.01) == "HOLD"
    assert E.recommendation(4.5, mos=E.MOS_CAP_ACCUMULATE - 0.01) == "HOLD / Accumulate"
    _has("rating", *(E.mos_cap_flag(line, top) for line, top in E.MOS_CAPS),
         f"{E.CYCLE_PEAK_MARGIN_X}× ปกติ",
         "composite ≥4 = BUY", "3–4 = HOLD / Accumulate", "2–3 = HOLD", "<2 = SELL / AVOID",
         f">{g(E.TERMINAL_SHARE_UNRATABLE)}%",
         *(f"{k}×{pc(w)}%" for k, w in E.WEIGHTS.items()))
    for r in ("Low", "Medium", "High", "Very High", "Extreme"):
        b = U.BANDS[r]
        _has("mos", f"{r} {pc(b[0])} / {pc(b[1])} / {pc(b[2])} / {pc(b[3])}%".replace("-", "−"))
    b = U.BANDS["Unrated"]
    _has("mos", f"ไม่มี rating {pc(b[0])} / {pc(b[1])} / {pc(b[2])} / {pc(b[3])}%".replace("-", "−"))
    for cut, r in U.WIDTH_CUTS:
        _has("uncertainty", f"{r} <{cut:.2f}")
    _has("whatif", *(f"{r} {pc(U.BANDS[r][1])}%" for r in ("Low", "Medium", "High", "Very High", "Extreme")),
         f"ไม่มี rating {pc(U.BANDS['Unrated'][1])}%")
    # DCF length and caps
    hi_, tot = E.MOAT_PERIODS["wide"]
    _has("dcf", f"WACC + {pc(E.WIDE_SPREAD)}%", f"โต {hi_} ปี + fade ถึงปีที่ {tot}", f"≤{pc(E.GROWTH_CAP)}%",
         f"− {pc(E.WACC_G_MIN_SPREAD)}%", f"เกิน {g(E.DCF_TERMINAL_HEAVY)}%", f"ต่ำกว่า {pc(E.TERMINAL_MARGIN_FLOOR)}%",
         f"มี ≥{E.NORMALIZE_MIN_YEARS - 1}" if False else "ประวัติไม่ถึง 4 ปี")
    for at, name in E.ACCELERATION_VERDICTS[:3]:
        _has("revdcf", name, f"{g(at)}×")
    _has("sbc", f"ไม่เกิน {pc(E.SBC_DILUTION_CAP)}%/ปี", f"เกิน {pc(E.SBC_DILUTION_FLAG_AT)}%", f"ย้อนดู {E.DILUTION_LOOKBACK_YEARS} ปี")
    # earnings quality: the Ref's cut-offs are where the verdict flips
    eq = lambda cfo, ni=100.0, ta=1e9: E.earnings_quality(ni, cfo, ta, None, 1000.0)[1]
    assert any("cash conversion" in f for f in eq(79.0)) and not any("cash conversion" in f for f in eq(80.0))
    assert any("accruals" in f for f in eq(0.0, ni=100.0, ta=1999.0)) and not any("accruals" in f for f in eq(0.0, ni=100.0, ta=2000.0))
    _has("eq-chip", "cash conversion ≥0.8", "accruals ≤5%", "เกิน 10%", "เกิน 15%")
    # signals, regime and overlay
    _has("earn-row", f"±{g(margin_track.THRESHOLD_PP)}%")
    # Sector scout (display only)
    from domain import relative as REL, scout as SC
    from pipeline import scout_job as SJ
    from tools import scout_fetch as SF
    _has("scout-tiers", f"{g(SC.W['value'] * 100)}% Value", f"{g(SC.W['quality'] * 100)}% Quality",
         f"{g(SC.W['moat'] * 100)}% Moat", f"≥ ${g(SC.REV_MIN / 1e6)}M", f"≥{pc(SC.COVER_MIN)}%",
         *(f"{w}%" for w in SC.QW.values()), f"ไม่เกิน {SC.PEN_CAP}")
    _has("scout-excl", f"ต่ำกว่า {pc(REL.MCAP_SALES_FLOOR)}% ของรายได้", f"เกิน ${g(REL.MCAP_FLOOR_REVENUE / 1e6)}M",
         f"เก่ากว่า {SF.STALE_BASE_DAYS} วัน")
    _has("scout-flags", f"เกิน {g(SC.RECOVERY_PP)} จุด")
    _has("scout-job", f"เก็บไว้ {SF.SEC_TTL_DAYS} วัน", f"ครั้งละ {SJ.SCOUT_WORKERS} บริษัท")
    stale = inspect.signature(PF._momentum_meta).parameters["stale_after"].default
    for eid in ("momentum", "banner-mom"):
        _has(eid, f"{stale} วัน")
    _has("banner-assume", f"ERP_STALE_MONTHS = {config.ERP_STALE_MONTHS}")
    elo, ehi, _, y0, y1 = mv.erp_bands(ERP_HISTORY)
    _has("tile-market", f"{elo * 100:.2f}%", f"{ehi * 100:.2f}%", f"{y0}–{y1}")
    conf = open(os.path.join(ROOT, "pipeline", "validate.py"), encoding="utf-8").read()
    m = re.search(r'"green" if ff.confidence >= (\d+) else "yellow" if ff.confidence >= (\d+)', conf)
    _has("confidence", f"เขียว ≥{m.group(1)}", f"เหลือง ≥{m.group(2)}", f"แดง <{m.group(2)}")
    # Risk Desk
    _has("sizing", f"Sector cap {pc(RP.SECTOR_CAP)}% (band {pc(RP.SECTOR_CAP + RP.SECTOR_BAND)}%)",
         f"เกิน {pc(RP.SECTOR_CAP + RP.SECTOR_BAND)}%", f"pricing asset {pc(config.PRICING_ASSET_CAP)}%")
    _has("targets", f"band {pc(TG.BAND_TRIGGER)} / {pc(TG.BAND_DEST)}%", f"{g(TG.TILT_PER_PP)} ต่อ 1%",
         f"{g(TG.TILT_MIN)}–{g(TG.TILT_MAX)}",
         *(f"{r} {g(TG.UNCERTAINTY_SCALE[r])}" for r in ("Low", "Medium", "Very High", "Extreme")))
    _has("rebalance", f"เกิน {pc(TG.BAND_TRIGGER)}%", f"ห่าง {pc(TG.BAND_DEST)}%")
    _has("stress", f"ช่วงละ {S.BOOT_BLOCK} วัน", f"{S.BOOT_SIMS:,} รอบ")
    # ledger, tax, costs
    _has("twr-xirr", f"ครบ {L.XIRR_MIN_DAYS} วัน")
    _has("tax", f"ภาษี capital gains {pc(config.CAPGAINS_TAX_RATE)}%", f"ภาษีปันผล {pc(config.DIVIDEND_WITHHOLDING)}%")
    _has("ledger-txn", f"{pc(config.DIVIDEND_WITHHOLDING)}%")
    _has("upside", f"{config.TRADING_COST_BPS / 100:g}%", f"TRADING_COST_BPS = {config.TRADING_COST_BPS}")
    print("Ref thresholds equal the code constants OK")


def test_no_pp_unit_in_the_docs():
    from tests.test_pct_units import PP_UNIT
    text = " ".join(e.text() for e in entries()) + " ".join(
        a for _, t, s, steps in TASKS for a in [t, s] + [x for x, _ in steps])
    hits = [m.group(0) for m in PP_UNIT.finditer(text)]
    assert not hits and "~15%" not in text, hits
    print("no 'pp' unit in How to / Ref OK")


# --- the page in node: a small DOM built from the generated markup ---------------------
DOM = r"""
class El{constructor(id,cls,extra){this.id=id||'';this.cls=cls||[];Object.assign(this,extra||{});this.hidden=!!this.hidden;
  this.open=!!this.open;this.children=[];this.parent=null;this.dataset=this.dataset||{};this.listeners={};this.style=this.style||{};this.value=this.value||'';this.focused=false;this.scrolled=false;}
  add(c){c.parent=this;this.children.push(c);return c;}
  all(){return this.children.flatMap(c=>[c,...c.all()]);}
  querySelectorAll(sel){const k=sel.split(' ').pop().slice(1);return this.all().filter(e=>e.cls.includes(k));}
  querySelector(sel){return sel==='summary'?this.summary:this.querySelectorAll(sel)[0];}
  matches(sel){return this.cls.includes(sel.slice(1));}
  closest(sel){let e=this;while(e){if(e.matches&&e.matches(sel))return e;e=e.parent;}return null;}
  addEventListener(t,f){this.listeners[t]=f;}
  scrollIntoView(){this.scrolled=true;} focus(){this.focused=true;}}
const byId={};
const root=new El('p5',[],{style:{display:'none'}}); byId.p5=root;
for(const s of STRUCT){const sec=root.add(new El('refsec-'+s.id,['refsec']));byId[sec.id]=sec;sec.summary=new El('',[]);
  for(const e of s.entries){const en=sec.add(new El('ref-'+e.id,['refent'],{dataset:{q:e.q}}));byId[en.id]=en;}}
for(const id of ['refq','refcount','refempty','refOpenAll','refCloseAll','refClear'])byId[id]=new El(id,[],{hidden:id==='refempty'});
const document={getElementById:id=>byId[id]||null,querySelectorAll:sel=>root.querySelectorAll(sel),addEventListener(){}};
const window={addEventListener(){}}; const location={hash:''};
let shown=null; function showTab(n){shown=n;root.style.display='block';}
"""

RUN = r"""
const out={};
const state=()=>STRUCT.map(s=>({id:s.id,open:byId['refsec-'+s.id].open,hidden:byId['refsec-'+s.id].hidden,
  shown:byId['refsec-'+s.id].children.filter(c=>!c.hidden).length}));
for(const q of QUERIES){byId.refq.value=q;byId.refq.listeners.input();
  out[q]={count:byId.refcount.textContent,empty:!byId.refempty.hidden,secs:state()};}
byId.refq.value='';byId.refq.listeners.input(); out.cleared={count:byId.refcount.textContent,secs:state()};
byId.refOpenAll.listeners.click(); out.openAll=state().every(s=>s.open);
byId.refCloseAll.listeners.click(); out.closeAll=state().every(s=>!s.open);
out.go=refGo('#ref-quota'); out.goState={tab:shown,open:byId['refsec-status'].open,focused:byId['ref-quota'].focused,scrolled:byId['ref-quota'].scrolled};
byId.refq.value='zzzz';byId.refq.listeners.input(); out.goHidden=refGo('#ref-dumbbell');
out.goHiddenState={open:byId['refsec-tab-peer'].open,shown:!byId['ref-dumbbell'].hidden,count:byId.refcount.textContent};
console.log(JSON.stringify(out));
"""


def _structure(page):
    """sections and entries (id, search text) as the generated markup ships them"""
    block = page[page.find('id="p5"'):]
    out = []
    for m in re.finditer(r'<details class="refsec" id="refsec-([^"]+)">(.*?)</details>', block, re.S):
        ents = [{"id": i, "q": html.unescape(q)} for i, q in
                re.findall(r'<article class="refent" id="ref-([^"]+)" tabindex="-1" data-q="([^"]*)"', m.group(2))]
        out.append({"id": m.group(1), "entries": ents})
    return out


def test_search_collapse_and_links_work_in_node():
    if not shutil.which("node"):
        print("SKIP: node not available")
        return
    struct = _structure(_page())
    assert [s["id"] for s in struct] == [s.id for s in SECTIONS]
    queries = ["dumbbell", "QUOTA", "roic wacc", "ไม่มีคำนี้แน่นอน"]
    js = ("const STRUCT=%s;const QUERIES=%s;\n" % (json.dumps(struct, ensure_ascii=False), json.dumps(queries, ensure_ascii=False))
          + DOM + open(os.path.join(ROOT, "static", "js", "ref_page.js"), encoding="utf-8").read() + RUN)
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as fh:
        fh.write(js)
    try:
        r = subprocess.run([shutil.which("node"), fh.name], capture_output=True, text=True, timeout=60)
    finally:
        os.unlink(fh.name)
    assert r.returncode == 0, r.stderr[-1500:]
    out = json.loads(r.stdout.strip().splitlines()[-1])
    total = sum(len(s["entries"]) for s in struct)
    for q in queries:
        words = q.lower().split()
        want = {s["id"]: sum(all(w in e["q"] for w in words) for e in s["entries"]) for s in struct}
        got = out[q]
        n = sum(want.values())
        assert got["count"] == (f"พบ {n} จาก {total} รายการ"), (q, got["count"], n)
        assert got["empty"] == (n == 0), q
        for s in got["secs"]:
            assert s["shown"] == want[s["id"]], (q, s)
            assert s["open"] == (want[s["id"]] > 0) and s["hidden"] == (want[s["id"]] == 0), (q, s)
    assert out["dumbbell"]["secs"][[s.id for s in SECTIONS].index("tab-peer")]["open"]
    assert sum(1 for s in out["ไม่มีคำนี้แน่นอน"]["secs"] if not s["hidden"]) == 0
    assert out["cleared"]["count"] == f"ทั้งหมด {total} รายการ"
    assert all(not s["open"] and not s["hidden"] and s["shown"] for s in out["cleared"]["secs"]), "clearing restores the closed page"
    assert out["openAll"] and out["closeAll"]
    assert out["go"] and out["goState"] == {"tab": 5, "open": True, "focused": True, "scrolled": True}, out["goState"]
    assert out["goHidden"] and out["goHiddenState"]["open"] and out["goHiddenState"]["shown"], out["goHiddenState"]
    assert out["goHiddenState"]["count"] == f"ทั้งหมด {total} รายการ", "a link out of a filtered view clears the search"
    print("Ref search, collapse, open/close all and links work in node OK (%d entries)" % total)


if __name__ == "__main__":
    from tests._harness import run_suite
    run_suite(globals(), "ALL test_ref_pages PASSED")
