"""
test_young_panel_render (REV-16) — actually RENDER the S20 card panel.

test_frontend only proves the token exists in index.html. That has never caught a
panel that throws, prints `undefined`, or divides by zero on a real payload — and
this one does arithmetic on six optional fields. So: pull the functions straight
out of index.html, run them in node against payloads the real engine produced, and
assert on the HTML that comes back.

Skips (does not fail) when node is unavailable.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from domain.engine.deep_v82 import DeepV82Engine   # noqa: E402
from domain.facts import FinancialFacts            # noqa: E402
from tests.test_young_dcf import PREPROFIT         # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML = os.path.join(ROOT, "index.html")

# the card helpers under test + the ones they call
WANT = ["youngPanel", "pricedInLine", "upsideCell", "methodShort", "anchorCell"]

SHIM = r"""
// minimal stand-ins for the dashboard globals these helpers touch
function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;')
  .replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
function f(v,d){return v==null?'&mdash;':'$'+Number(v).toFixed(d==null?2:d);}
function pct(v){return v==null?'&mdash;':(v>=0?'+':'')+v+'%';}
function consensusBadge(){return '';}
"""


def _js(n):
    """Render a float the way JS String(Number) does — 38.0 prints as '38'."""
    return ("%g" % n) if float(n) != int(n) else str(int(n))


def _extract(src, name):
    """Pull `function name(...) { ... }` out by brace matching."""
    m = re.search(r"^function %s\(" % re.escape(name), src, re.M)
    assert m, "index.html has no function %s" % name
    i = src.index("{", m.start())
    depth, j, in_s, esc_c = 0, i, None, False
    while j < len(src):
        c = src[j]
        if in_s:
            if esc_c:
                esc_c = False
            elif c == "\\":
                esc_c = True
            elif c == in_s:
                in_s = None
        elif c in "'\"`":
            in_s = c
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return src[m.start():j + 1]
        j += 1
    raise AssertionError("unbalanced braces extracting %s" % name)


def _row(**over):
    """A display row as pipeline/refresh builds it, from a REAL engine run."""
    kw = dict(PREPROFIT)
    kw.update(over.pop("facts", {}))
    ff = FinancialFacts(over.pop("ticker", "YOUNGCO"), **kw)
    v = DeepV82Engine().evaluate(ff, rf=0.045)
    up = ((v.anchor_value - ff.price) / ff.price * 100) if v.anchor_value else None
    row = {"ticker": ff.ticker, "price": ff.price, "anchor_method": v.anchor_method,
           "anchor_value": v.anchor_value, "fv_peg": v.fv_peg, "fv_fvp": v.fv_fvp,
           "upside_pct": round(up, 1) if up is not None else None, "net_upside_pct": None,
           "young_dcf": v.young_dcf or None,
           "rev_implied_cagr": (v.reverse_dcf or {}).get("implied_cagr_pct"),
           "rev_actual_1y": (v.reverse_dcf or {}).get("actual_1y_pct"),
           "rev_verdict": (v.reverse_dcf or {}).get("verdict"),
           "rev_sensitivity": (v.reverse_dcf or {}).get("sensitivity"),
           "terminal_margin_anchored": (v.key_metrics or {}).get("terminal_margin_anchored"),
           "terminal_margin_label": next((fl for fl in (v.flags or [])
                                          if "terminal margin" in fl), None),
           "forward_eps_n": 0, "forward_eps": ff.forward_eps}
    row.update(over)
    return row


def test_panel_renders():
    node = shutil.which("node")
    if not node:
        print("SKIP: node not available")
        return
    src = open(HTML, encoding="utf-8").read()
    js = SHIM + "\n".join(_extract(src, n) for n in WANT)

    promoted = _row()
    assert promoted["young_dcf"]["promote"] is True, "fixture should promote"
    blocked = _row(ticker="WIDECO", facts=dict(
        revenue=0.35e9, cash=0.30e9, cfo=-0.40e9, operating_income=-0.45e9,
        total_assets=0.5e9, equity=0.30e9))
    assert blocked["young_dcf"]["promote"] is False, "fixture should be blocked"
    profitable = _row(ticker="PROFITCO", young_dcf=None, anchor_value=160.0,
                      upside_pct=14.3, anchor_method="Fundamental PEG")

    cases = {"promoted": promoted, "blocked": blocked, "profitable": profitable}
    js += "\nconst CASES=%s;\nconst out={};" % json.dumps(cases)
    js += """
for(const k in CASES){
  const x=CASES[k];
  out[k]={panel:youngPanel(x),upside:upsideCell(x),anchor:anchorCell(x),
          method:methodShort(x.anchor_method)};
}
console.log(JSON.stringify(out));
"""
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as fh:
        fh.write(js)
        path = fh.name
    try:
        r = subprocess.run([node, path], capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, "panel threw in node:\n" + r.stderr[-1500:]
        out = json.loads(r.stdout)
    finally:
        os.unlink(path)

    p = out["promoted"]
    assert "Pre-profit forward value (S20)" in p["panel"]
    assert "survival 68%" in p["panel"], p["panel"][:400]
    mc = promoted["young_dcf"]["monte_carlo"]
    for v in (mc["p10"], mc["p50"], mc["p90"]):
        assert ("$%s" % v) in p["panel"], (v, p["panel"][:600])
    assert "ข้อมูลเสริม" not in p["panel"], "a promoted value must not be labelled supplementary"
    assert p["method"] == "YoungDCF", p["method"]
    assert "YoungDCF" in p["anchor"], p["anchor"]

    b = out["blocked"]
    assert "ข้อมูลเสริม" in b["panel"], "a blocked value MUST say it is not the anchor"
    assert blocked["young_dcf"]["blocked_reason"][:30] in b["panel"]
    assert "ตีมูลค่าไม่ได้" in b["upside"], b["upside"]

    assert out["profitable"]["panel"] == "", "profitable names get no S20 panel"

    # REV-21: the market's implied CAGR must SURVIVE promotion — it answers a
    # different question from the young DCF (pricing vs valuation) and used to
    # vanish the moment an anchor appeared.
    for k in ("promoted", "blocked"):
        panel = out[k]["panel"]
        imp = cases[k]["rev_implied_cagr"]
        assert "price-in" in panel, "%s lost the priced-in block" % k
        assert ("<b>%s%%</b>" % _js(imp)) in panel, (k, imp, panel[-700:])
        # and never as a bare point estimate: the margin band has to be next to it
        s = cases[k]["rev_sensitivity"]
        lo, hi = min(s["bull"], s["bear"]), max(s["bull"], s["bear"])
        assert ("%s–%s%%" % (_js(lo), _js(hi))) in panel, (k, s, panel[-700:])
        # a pre-profit filer's terminal margin is assumed -> it must say so
        assert cases[k]["terminal_margin_anchored"] is False
        assert "margin สมมติ" in panel, k
    print("REV-21 priced-in block kept + banded OK")

    # the failure that motivated this test: optional fields silently rendering junk
    for k, v in out.items():
        blob = v["panel"] + v["upside"] + v["anchor"]
        for bad in ("undefined", "NaN", "[object Object]", "Infinity"):
            assert bad not in blob, "%s rendered %r:\n%s" % (k, bad, blob[:600])
    print("S20 panel renders OK (promoted / blocked / profitable, no undefined|NaN)")


def test_panel_survives_missing_fields():
    """Half the payload is optional. A partial young_dcf must degrade, not throw."""
    node = shutil.which("node")
    if not node:
        print("SKIP: node not available")
        return
    src = open(HTML, encoding="utf-8").read()
    js = SHIM + "\n".join(_extract(src, n) for n in WANT)
    partial = [
        {"price": 10, "young_dcf": {}},
        {"price": 10, "young_dcf": {"promote": False, "monte_carlo": None}},
        {"price": None, "young_dcf": {"promote": True, "monte_carlo": {"p10": 5, "p50": 5, "p90": 5},
                                      "going_concern_per_share": 6, "distress_per_share": 1,
                                      "failure_adjusted_per_share": 5, "p_survival": 0.8}},
        {"price": 10, "young_dcf": {"promote": True, "monte_carlo": {"p10": None, "p90": None}}},
    ]
    js += "\nconst P=%s;\nfor(const x of P){youngPanel(x);upsideCell(x);}\nconsole.log('ok');" % json.dumps(partial)
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as fh:
        fh.write(js)
        path = fh.name
    try:
        r = subprocess.run([node, path], capture_output=True, text=True, timeout=60)
        assert r.returncode == 0 and "ok" in r.stdout, (
            "panel threw on a partial payload:\n" + r.stderr[-1200:])
    finally:
        os.unlink(path)
    print("S20 panel survives partial payloads OK")


if __name__ == "__main__":
    test_panel_renders()
    test_panel_survives_missing_fields()
    print("\nALL test_young_panel_render PASSED")
