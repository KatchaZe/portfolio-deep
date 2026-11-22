"""test_pct_units: the dashboard shows a gap between two percentages as %, never "pp"
(the user's standing preference since 2026-10-27). RENDERS, in node, the คู่แข่ง & Moat
tab (every case test_peer_panel_render builds, stored and synthetic) and the DEEP drawer
(deepDrawer + trendStrip) for every stored ticker, and looks for "pp" as a word in the
HTML, attributes included. CSS classes (pp-grid) and engine field names never reach the
text, so the pattern skips a "pp" joined to a letter, digit, "_" or "-" on the right."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import rows as rows_mod                      # noqa: E402
from tests import test_peer_panel_render as peer          # noqa: E402
from tests.frontend_source import frontend_files           # noqa: E402

PP_UNIT = re.compile(r"(?<![A-Za-z_\-])pp(?![A-Za-z0-9_\-])")
MODULES = ("format.js", "card_signals.js", "card_value.js", "card_drawer.js", "card_peer.js")


def _js():
    shipped = {os.path.basename(p): p for p in frontend_files()}
    return "\n".join(open(shipped[m], encoding="utf-8").read() for m in MODULES)


def _drawer_rows():
    if not os.path.exists(peer.STORE):
        return {}
    with open(peer.STORE, encoding="utf-8") as fh:
        s = json.load(fh)
    out = {}
    for t, r in (s.get("results") or {}).items():
        out[t] = rows_mod.valuation_row(t, (s.get("facts") or {}).get(t) or {}, r, {}, {},
                                        rev_surprises=None, has_new_schema=True, fmp_on=False)
    return out


RUN = r"""
const out={};
for(const k in PEER)out['peer '+k]=peerPane(PEER[k],'c-'+k)+moatChip(PEER[k]);
for(const k in DRAWER)out['drawer '+k]=deepDrawer(DRAWER[k])+trendStrip(DRAWER[k]);
// an engine note in the English the engine writes must still read as %
out['notes']=ppNotes({flags:['gross margin 18pp above the peer median (FY0)']},{flags:['S3: +2.5 pp']});
console.log(JSON.stringify(out));
"""


def _render(peer_cases, drawer_rows):
    js = _js() + "\nconst PEER=%s;\nconst DRAWER=%s;\n" % (json.dumps(peer_cases), json.dumps(drawer_rows)) + RUN
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as fh:
        fh.write(js)
        path = fh.name
    try:
        r = subprocess.run([shutil.which("node"), path], capture_output=True, text=True, timeout=120)
        assert r.returncode == 0, "render threw in node:\n" + r.stderr[-1500:]
        return json.loads(r.stdout)
    finally:
        os.unlink(path)


def test_no_pp_unit_in_the_moat_tab_or_the_drawer():
    if not shutil.which("node"):
        print("SKIP: node not available")
        return
    drawer = _drawer_rows()
    out = _render(peer._cases(), drawer)
    hits = {k: sorted({html[max(0, m.start() - 30):m.end() + 10] for m in PP_UNIT.finditer(html)})[:3]
            for k, html in out.items() if PP_UNIT.search(html)}
    assert not hits, "rendered 'pp' as a unit: %s" % json.dumps(hits, ensure_ascii=False)[:1500]
    assert "18%" in out["notes"] and "+2.5%" in out["notes"], out["notes"]
    print("no 'pp' unit in the moat tab (%d cases) or the drawer (%d stored tickers) OK"
          % (sum(k.startswith("peer ") for k in out), len(drawer)))


def test_drawer_trend_has_no_gross_margin_for_an_insurer():
    """D3 (R25): the 5-year strip drops its Gross margin row for a financial SIC and says why."""
    if not shutil.which("node"):
        return
    drawer = {t: r for t, r in _drawer_rows().items() if t in ("UNH", "ELV", "NVDA")}
    if not drawer:
        print("SKIP: no stored UNH/ELV/NVDA")
        return
    out = _render({}, drawer)
    for t in ("UNH", "ELV"):
        if t in drawer:
            h = out["drawer " + t]
            assert 'aria-label="Gross margin' not in h, t
            assert "ไม่แสดง Gross margin: gross margin ไม่ใช่ตัวชี้วัดของธุรกิจนี้" in h, t
    if "NVDA" in drawer:
        assert 'aria-label="Gross margin' in out["drawer NVDA"], "a non-financial strip keeps its row"
    print("drawer strip: no GM row for insurers OK")


def test_no_pp_unit_in_the_shipped_text():
    """Every static string the page ships: JS literals and index.html text. The two
    exceptions are where the code reads the engine's own "pp" (pp2pct, the English
    evidence pattern, the trend row's unit) to turn it into %."""
    reads_engine = ("function pp2pct", "pp above the peer median", "su0==='pp'", "pp over (", "pp vs the median")
    bad = []
    for path in frontend_files():
        for i, line in enumerate(open(path, encoding="utf-8"), 1):
            if any(r in line for r in reads_engine):
                continue
            if PP_UNIT.search(line):
                bad.append("%s:%d %s" % (os.path.basename(path), i, line.strip()[:120]))
    assert not bad, "user-visible 'pp':\n" + "\n".join(bad)
    print("no 'pp' unit in the shipped text OK")


if __name__ == "__main__":
    from tests._harness import run_suite
    run_suite(globals(), "ALL test_pct_units PASSED")
