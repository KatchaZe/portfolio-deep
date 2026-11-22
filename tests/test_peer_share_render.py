"""test_peer_share_render: the market-share box of the คู่แข่ง & Moat tab, rendered in node.

Every stored holding (data/portfolio.json) renders twice: as stored, and with its peer panel
rebuilt offline by the current engine (tools/panel_probe: stored peer points, cached
Damodaran list and SEC frames), which carries the `chart` block the box draws from. Each
rebuilt panel renders in both views. The checks read the numbers back out of the HTML:
dumbbell dots against the engine's share_then/share_now, the others row against 100% minus
the shown rows, the stacked bands' heights against the members' yearly shares, and the
rank against target_rank. Skips when node, the store or the cache is missing.
"""
import copy
import datetime as dt
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import rows as rows_mod             # noqa: E402
from tests.frontend_source import frontend_files  # noqa: E402

MODULES = ("format.js", "card_signals.js", "card_peer.js")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORE = os.path.join(ROOT, "data", "portfolio.json")
KEYS = ("ticker", "peer_panel", "moat_trend", "key_metrics", "uncertainty", "sec_sic")
FINANCIAL = ("UNH", "ELV")
BAD = ("undefined", "NaN", "Infinity", "[object Object]")
H = 180.0


def _js():
    shipped = {os.path.basename(p): p for p in frontend_files()}
    assert all(m in shipped for m in MODULES), "index.html no longer loads the peer tab modules"
    return "\n".join(open(shipped[m], encoding="utf-8").read() for m in MODULES)


def _rows():
    import config
    from pipeline import peers
    from tools import panel_probe
    with open(STORE, encoding="utf-8") as fh:
        s = json.load(fh)
    uni = panel_probe._offline_universe(config.CACHE_DIR)
    out = {}
    for t in s["holdings"]:
        r = s["results"].get(t) or {}
        fd = s["facts"].get(t) or {}
        row = rows_mod.valuation_row(t, fd, r, {}, {}, rev_surprises=None, has_new_schema=True, fmp_on=False)
        out["live_" + t] = {k: row.get(k) for k in KEYS}
        as_of = (r.get("peer_panel") or {}).get("as_of")
        if as_of:
            panel, moat = peers.panel_and_moat(panel_probe._facts(fd, t),
                                               SimpleNamespace(key_metrics=r.get("key_metrics") or {}),
                                               copy.deepcopy(r.get("relative") or {}), uni, dt.date.fromisoformat(as_of))
            out["head_" + t] = dict(out["live_" + t], peer_panel=panel, moat_trend=moat)
    return out


RUN = r"""
const out={panes:{},share:{}};
for(const k in ROWS){const x=ROWS[k], p=x.peer_panel||{};
  out.panes[k]=peerPane(x,'c-'+k);
  for(const v of ['sub','all'])out.share[k+'|'+v]={html:ppShare(p,x,v),table:ppTable(p,null,ppViewOf(p,v),x),view:ppViewOf(p,v)};}
// three cards on one page
out.page=['head_NVDA','head_UNH','head_TMDX'].map(k=>peerPane(ROWS[k],'c-'+k)).join('');
// the disclosure: aria-expanded and the region's hidden move together
const reg={hidden:true}, attrs={'aria-expanded':'false','aria-controls':'r1'};
const btn={getAttribute:a=>attrs[a],setAttribute:(a,v)=>{attrs[a]=v;}}, doc={getElementById:i=>i==='r1'?reg:null};
const st=[]; for(let i=0;i<3;i++){ppDiscClick(btn,doc); st.push([attrs['aria-expanded'],reg.hidden]);}
out.disc=st;
// the box's toggle redraws the share box and the table together
var CARD_ROWS={'c-T':ROWS.head_TMDX}; const drawn={}; let foc=null;
const grid={dataset:{ppcard:'c-T'},querySelector:q=>q==='.pp-share'||q==='.pp-tbl'?{set outerHTML(h){(drawn[q]=drawn[q]||[]).push(h)}}:{focus(){foc=q}}};
ppViewClick({dataset:{pview:'all'},closest:()=>grid}); ppViewClick({dataset:{pview:'sub'},closest:()=>grid});
out.toggle={drawn,foc,view:PP_VIEW['c-T']};
console.log(JSON.stringify(out));
"""


def _render(rows):
    js = _js() + "\nconst ROWS=%s;\n" % json.dumps(rows, default=str) + RUN
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as fh:
        fh.write(js)
        path = fh.name
    try:
        r = subprocess.run([shutil.which("node"), path], capture_output=True, text=True, timeout=120)
        assert r.returncode == 0, "card_peer.js threw in node:\n" + r.stderr[-1500:]
        return json.loads(r.stdout)
    finally:
        os.unlink(path)


def _text(h):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", h)))


def _basis(x, view):
    s = (x["peer_panel"] or {}).get("industry_share") or {}
    return s.get("sub_group") if view == "sub" else s


def _dots(h):
    return [(html.unescape(a), float(b), float(c), float(d), float(e), float(f)) for a, b, c, d, e, f in re.findall(
        r'data-label="([^"]+)" data-then="([\d.]+)" data-now="([\d.]+)" data-cx0="([\d.]+)" data-cx1="([\d.]+)" data-xmax="([\d.]+)"', h)]


def _bands(h):
    """label -> heights per year as a share of the plot, read from the drawn polygons."""
    out = []
    for pts, lab in re.findall(r'<polygon points="([^"]+)" class="pp-band[^"]*"[^>]*data-label="([^"]+)"', h):
        xy = [tuple(map(float, p.split(","))) for p in pts.split()]
        n = len(xy) // 2
        top, bot = xy[:n], xy[n:][::-1]
        out.append((html.unescape(lab), [(bot[i][1] - top[i][1]) / H for i in range(n)], [t[1] for t in top]))
    return out


def _sym(m):
    return "/".join(m["symbols"]) or "CIK %s" % m["cik"]


def check_view(k, x, view, r):
    """One rendered share box against the engine block it was drawn from."""
    h = r["html"]
    t = x["ticker"]
    for bad in BAD:
        assert bad not in h + r["table"], (k, view, bad)
    assert not re.search(r"(?<![A-Za-z_\-])pp(?![A-Za-z0-9_\-])", _text(h)), (k, view, "a 'pp' unit in visible text")
    b = _basis(x, r["view"])
    c = (b or {}).get("chart")
    if not c or not c.get("members") or 'class="pp-defs"' not in h:
        assert "ไม่มีส่วนแบ่ง" in h or "refresh รอบหน้า" in h or "คำนวณส่วนแบ่งไม่ได้" in h or "ไม่คำนวณส่วนแบ่ง" in h, (k, view, "no chart and no reason")
        return 0
    ms = c["members"]
    named = {_sym(m): m for m in ms}
    dots = _dots(h)
    matched = [m for m in ms if m["matched"]]
    if not matched:
        assert not dots and "ยังวาดกราฟไม่ได้" in h and "<polygon" not in h, (k, view)
        return 1
    shown_then = shown_now = 0.0
    for lab, th, nw, a, z, xm in dots:
        assert abs(a - th / xm * 100) < 0.01 and abs(z - nw / xm * 100) < 0.01, (k, view, lab, "dot position")
        if lab.startswith("อื่น ๆ"):
            o = (th, nw)
            continue
        m = named[lab]
        assert abs(th - m["share_then"]) < 5e-7 and abs(nw - m["share_now"]) < 5e-7, (k, view, lab, th, nw, m)
        shown_then += th
        shown_now += nw
    if dots and dots[-1][0].startswith("อื่น ๆ"):
        assert abs(o[0] - (1 - shown_then)) < 2e-6 and abs(o[1] - (1 - shown_now)) < 2e-6, (k, view, "others = 100% - shown")
        n_rest = int(re.match(r"อื่น ๆ (\d+)", dots[-1][0]).group(1))
        assert n_rest == len(matched) - (len(dots) - 1), (k, view, "others count")
    elif dots:
        assert abs(shown_then - 1) < 1e-4 and abs(shown_now - 1) < 1e-4, (k, view, "no others row: shown rows are the whole matched set")
    if matched:
        assert "ชุดเทียบ: %d จาก %s ราย" % (len(matched), b["n_members"]) in h and len(matched) == b["matched_n"], (k, view)
    bands = _bands(h)
    assert bands, (k, view, "no stacked bands")
    yrs = c["years"]
    for i in range(len(yrs)):
        assert abs(sum(hs[i] for _, hs, _ in bands) - 1) < 1e-3, (k, view, "bands sum to 100%", yrs[i])
        assert abs(bands[-1][2][i]) < 0.05, (k, view, "the top band reaches 100%")
    for lab, hs, _ in bands:
        if lab in named:
            want = [s or 0 for s in named[lab]["share"]]
            assert all(abs(a - w) < 1e-3 for a, w in zip(hs, want)), (k, view, lab, hs, want)
    tr = c["target_rank"]
    if tr.get("rank"):
        assert "%s อันดับ %s จาก %s" % (t, tr["rank"], tr["of"]) in _text(h), (k, view, "rank")
        row = h.split('<tr class="pp-target">')[1]
        assert re.match(r'<td class="num">(\d+)</td>', row).group(1) == str(tr["rank"]), (k, view, "table rank")
    m = re.search(r'<div class="pp-db" role="img" aria-label="([^"]+)"', h)
    tm = next((m_ for m_ in matched if t in m_["symbols"]), None)
    if tm:
        lab = html.unescape(m.group(1))
        assert lab.startswith(t + " ส่วนแบ่ง") and "อันดับ %s จาก %s" % (tr["rank"], tr["of"]) in lab, (k, view, lab)
    return 1


def check_a11y(h, where):
    for tag in re.findall(r"<svg\b[^>]*>", h):
        assert ('role="img"' in tag and "aria-label=" in tag) or 'aria-hidden="true"' in tag, (where, tag)
    for i in re.findall(r'aria-controls="([^"]+)"', h):
        assert h.count('id="%s"' % i) == 1, (where, "aria-controls target", i)
    for b in re.findall(r'<button type="button" class="pp-disc"[^>]*>', h):
        assert 'aria-expanded="false"' in b, (where, b)


def test_share_box_on_every_holding():
    if not shutil.which("node") or not os.path.exists(STORE):
        print("SKIP: node or data/portfolio.json missing")
        return
    import config
    if not os.path.exists(os.path.join(config.CACHE_DIR, "damodaran_indname.json")):
        print("SKIP: no cached Damodaran list")
        return
    rows = _rows()
    out = _render(rows)
    n_views = 0
    for key, r in out["share"].items():
        k, view = key.split("|")
        n_views += check_view(k, rows[k], view, r)
        check_a11y(r["html"], key)
    heads = [k for k in rows if k.startswith("head_")]
    assert len(heads) >= 20 and n_views >= 2 * len(heads) - 15, (len(heads), n_views)
    for k, h in out["panes"].items():
        for bad in BAD:
            assert bad not in h, (k, bad)
        check_a11y(h, k)
        x = rows[k]
        sub = ((x["peer_panel"] or {}).get("industry_share") or {}).get("sub_group") or {}
        pressed = re.findall(r'data-pview="(\w+)" aria-pressed="true"', h)
        want = ["sub"] if sub.get("sic") and not sub.get("reason") else []
        assert pressed == want, (k, pressed, "the toggle defaults to the same business when it exists")
        if want:
            assert h.count("data-pview=") == 2 and h.index("data-pview=") < h.index('pp-tbl"'), \
                (k, "one toggle, in the share box heading")
    for t in FINANCIAL:
        for k in ("live_" + t, "head_" + t):
            blob = out["panes"][k] + "".join(out["share"]["%s|%s" % (k, v)]["table"] for v in ("sub", "all"))
            cells = re.findall(r'data-col="gross_margin_fy">(.*?)</td>', blob)
            assert cells and not any(re.search(r"\d", _text(c)) for c in cells), (k, "a GM number for a financial target")
            assert "GM median" not in blob and "median คู่แข่ง SIC" not in blob, k
    page = out["page"]
    ids = re.findall(r'\bid="([^"]+)"', page)
    assert len(ids) == len(set(ids)), "duplicate ids when three cards share a page: %s" % sorted({i for i in ids if ids.count(i) > 1})[:5]
    refs = set(re.findall(r"url\(#([^)]+)\)", page))
    assert refs and refs <= set(ids), ("pattern or marker reference without its element", sorted(refs - set(ids))[:5])
    assert len([i for i in ids if re.search(r"-[hvdgx]$", i)]) == 3 * 5, "five patterns per card"
    assert out["disc"] == [["true", False], ["false", True], ["true", False]], out["disc"]
    tg = out["toggle"]
    assert len(tg["drawn"][".pp-share"]) == 2 and len(tg["drawn"][".pp-tbl"]) == 2, "one click redraws both"
    sh, tb = tg["drawn"][".pp-share"], tg["drawn"][".pp-tbl"]
    assert 'data-pview="all" aria-pressed="true"' in sh[0] and "ทั้งกลุ่ม Damodaran:" in sh[0]
    assert "แสดง" not in tb[0].split("</caption>")[0] and "SIC 3845 เท่านั้น" in tb[1], "the table follows the toggle"
    assert 'data-pview="sub" aria-pressed="true"' in sh[1] and tg["foc"] == '[data-pview="sub"]' and tg["view"] == "sub"
    print("share box: %d rows, %d drawn views checked against the engine; ids unique over 3 cards" % (len(rows), n_views))


def test_findings_and_labels():
    """The words a reader gets: the dumbbell's label states the finding, the rules stay."""
    if not shutil.which("node") or not os.path.exists(STORE):
        print("SKIP: node or data/portfolio.json missing")
        return
    import config
    if not os.path.exists(os.path.join(config.CACHE_DIR, "damodaran_indname.json")):
        print("SKIP: no cached Damodaran list")
        return
    rows = _rows()
    out = _render({k: rows[k] for k in ("head_NVDA", "head_TMDX", "head_UNH", "head_MELI", "head_TSLA", "live_NVDA")})
    n = out["share"]["head_NVDA|sub"]["html"]
    assert 'role="img" aria-label="NVDA ส่วนแบ่งเพิ่มจาก 8.3% เป็น 39.6% อันดับ 1 จาก 60"' in n, \
        re.findall(r'class="pp-db" role="img" aria-label="([^"]+)"', n)
    assert 'aria-label="ส่วนแบ่งรายปี CY2021 ถึง CY2025: NVDA' in n and "กลุ่มอื่น ๆ" in n
    tm = _text(out["share"]["head_TMDX|sub"]["html"])
    assert "TMDX อันดับ 14 จาก 60" in tm and "⋯" not in tm
    assert 'pp-dbr-t pp-dbr-gap' in out["share"]["head_TMDX|sub"]["html"], "a target outside the top 8 gets its own row after a gap"
    u = _text(out["share"]["head_UNH|sub"]["html"])
    assert "Top-5 รวม ไม่ใช้" not in u and "Top-5 รวม CY" in u, "UNH's sub-group has 9 members"
    me = _text(out["share"]["head_MELI|sub"]["html"])
    assert "Top-5 รวม ไม่ใช้" in me and "กลุ่มนี้มี 3 ราย" in me, "a group of 5 or fewer drops Top-5"
    assert "สัญญาณ S2 ใช้ทั้งกลุ่ม Damodaran แทน" in me, "the S2 basis line names the basis S2 used"
    ts = _text(out["share"]["head_TSLA|sub"]["html"])
    assert "ยังวาดกราฟไม่ได้ เพราะ CY2022 มีบริษัทในกลุ่มนี้ที่มีข้อมูลใน SEC 6 จาก 9 ราย (67% ต่ำกว่าเกณฑ์ 70%)" in ts
    for k in ("head_NVDA", "head_TMDX", "head_MELI"):
        h = _text(out["share"][k + "|all"]["html"])
        assert "5 รายใหญ่สุดของ CY2025 ทุกปี" in h and "ไม่มีผลต่อคะแนน" in h and "ดูตารางตัวเลข" in h, k
    lv = _text(out["share"]["live_NVDA|all"]["html"])
    assert "กราฟรายบริษัทจะแสดงหลัง refresh รอบหน้า" in lv and "NVDA ส่วนแบ่ง CY2025" in lv, "a stored panel has no chart block"
    print("share box labels and rules OK")


if __name__ == "__main__":
    from tests._harness import run_suite
    run_suite(globals(), "ALL test_peer_share_render PASSED")
