"""test_peer_panel_th (D9): every English reason the คู่แข่ง & Moat tab shows renders in Thai.

The engine stores reasons in English (flags, notes, exclusions, hint evidence); card_peer.js
translates them at render time with its PP_TH table and marks any reason no entry matches
with data-untranslated. This suite fails on any such mark in:
  1. every reason TEMPLATE the panel's code can emit, found by reading the source (flags /
     notes appended, `return None, "<reason>"`, a "reason" value), each rendered with
     sample values for its placeholders, so a new English flag fails here before any
     ticker hits it;
  2. the tab rendered for every case test_peer_panel_render builds, and for every holding
     in data/portfolio.json, stored and rebuilt offline.
Skips when node is missing."""
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import test_peer_panel_render as R      # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCES = ("domain/peer_panel.py", "domain/moat_trend.py", "domain/relative.py", "pipeline/peers.py",
           "sources/damodaran_companies.py")
# placeholders whose sample a number cannot stand in for, by the expression's text
NAMED = (("reason", "ไม่มีรหัส SIC ของหุ้นนี้ในรายชื่อ Damodaran"), ("basis", "report revenue"),
         ("window", "CY2022..CY2025"), ("cy", "CY2025"))
UNTR = re.compile(r'data-untranslated="1" lang="en">([^<]*)')


def _sample(node):
    """A string from a str constant or f-string, each placeholder replaced by a sample
    value its format and the text before it suggest (CY2025, SIC 3711, 67%, +1.0)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if not isinstance(node, ast.JoinedStr):
        return None
    out = ""
    for v in node.values:
        if isinstance(v, ast.Constant):
            out += str(v.value)
            continue
        spec = ast.unparse(v.format_spec) if v.format_spec is not None else ""
        expr = ast.unparse(v.value)
        named = next((val for key, val in NAMED if re.search(r"\b%s\b" % key, expr)), None)
        if named is not None:
            out += named
        elif "%" in spec:
            out += "67%"
        elif "+" in spec:
            out += "+1" if ".0f" in spec else "+1.0"
        elif out.endswith(("CY", "SIC ")):
            out += "3711" if out.endswith("SIC ") else "2025"
        elif out.endswith("FY"):
            out += "2025"
        else:
            out += "1"
    return out


def _strings(node):
    """The candidate reason strings an expression can evaluate to (a conditional gives both)."""
    if isinstance(node, ast.IfExp):
        return _strings(node.body) + _strings(node.orelse)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        a, b = _strings(node.left), _strings(node.right)
        return [x + y for x in a for y in b] if a and b else a or b
    s = _sample(node)
    return [s] if s is not None else []


def templates():
    """{sample: 'file:line'} for every reason the panel code emits."""
    out = {}
    for rel in SOURCES:
        tree = ast.parse(open(os.path.join(ROOT, rel), encoding="utf-8").read())
        for n in ast.walk(tree):
            got = []
            if isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "append" and n.args:
                tgt = ast.unparse(n.func.value)
                if tgt.endswith(("flags", "notes", 'out["flags"]', "flags")):
                    got = _strings(n.args[0])
            elif isinstance(n, ast.Return) and isinstance(n.value, ast.Tuple) and len(n.value.elts) >= 2:
                if isinstance(n.value.elts[0], ast.Constant) and n.value.elts[0].value is None:
                    for e in n.value.elts[1:]:
                        got += _strings(e)
            elif isinstance(n, ast.Dict):
                for k, v in zip(n.keys, n.values):
                    if isinstance(k, ast.Constant) and k.value in ("reason", "evidence", "basis_reason"):
                        got += _strings(v)
            for g in got:
                if re.search(r"[A-Za-z]{3}", g):
                    out.setdefault(g, f"{rel}:{n.lineno}")
    return out


def _node(js):
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as fh:
        fh.write(js)
        path = fh.name
    try:
        r = subprocess.run([shutil.which("node"), path], capture_output=True, text=True, timeout=120)
        assert r.returncode == 0, r.stderr[-1500:]
        return json.loads(r.stdout)
    finally:
        os.unlink(path)


def test_every_reason_template_has_a_thai_entry():
    if not shutil.which("node"):
        print("SKIP: node not available")
        return
    t = templates()
    assert len(t) > 60, "the source scan found too few reasons: %d" % len(t)
    out = _node(R._js() + "\nconst T=%s;\nconsole.log(JSON.stringify(T.map(s=>ppReason(s))));" % json.dumps(list(t)))
    bad = [f"{src}: {s}" for (s, src), h in zip(t.items(), out) if UNTR.search(h)]
    assert not bad, "English reasons with no PP_TH entry:\n  " + "\n  ".join(bad)


def test_the_marker_fires_on_an_unmapped_reason():
    if not shutil.which("node"):
        return
    out = _node(R._js() + "\nconsole.log(JSON.stringify([ppReason('S2: a brand new english reason'),"
                          "ppReason('CY2022: 6 of 9 members report revenue (67% < 70%): share not shown')]))")
    assert UNTR.search(out[0]) and not UNTR.search(out[1]), out


def _store_cases():
    if not os.path.exists(R.STORE):
        return {}
    import config
    from tools import panel_probe
    from pipeline import peers
    import datetime as dt
    from types import SimpleNamespace
    with open(R.STORE, encoding="utf-8") as fh:
        s = json.load(fh)
    u = panel_probe._offline_universe(config.CACHE_DIR) if os.path.exists(
        os.path.join(config.CACHE_DIR, "damodaran_indname.json")) else None
    cases = {}
    for t in s.get("holdings") or {}:
        r = (s.get("results") or {}).get(t) or {}
        cases[t] = R._row(t, s)
        as_of = (r.get("peer_panel") or {}).get("as_of")
        if u is None or not as_of or not (s.get("facts") or {}).get(t):
            continue
        panel, moat = peers.panel_and_moat(panel_probe._facts(s["facts"][t], t),
                                           SimpleNamespace(key_metrics=r.get("key_metrics") or {}),
                                           r.get("relative") or {}, u, dt.date.fromisoformat(as_of))
        cases[t + "_rebuilt"] = dict(cases[t], peer_panel=panel, moat_trend=moat)
    return cases


def test_no_english_reason_renders_on_any_case():
    if not shutil.which("node"):
        return
    cases = dict(R._cases(), **_store_cases())
    cases.pop("XSS", None)                 # markup in every field, not a reason
    js = R._js() + "\nconst CASES=%s;\n" % json.dumps(cases) + r"""
const out={};
for(const k in CASES){const x=CASES[k];
  out[k]=peerPane(x,'c-'+k)+ppTable(x.peer_panel||{},null,'sub',x);}
console.log(JSON.stringify(out));"""
    out = _node(js)
    bad = {k: sorted(set(UNTR.findall(h)))[:4] for k, h in out.items() if UNTR.search(h)}
    assert not bad, "untranslated English in the tab: %s" % json.dumps(bad, ensure_ascii=False, indent=1)
    print("no untranslated reason in %d rendered cases" % len(out))


if __name__ == "__main__":
    from tests._harness import run_suite
    run_suite(globals(), "ALL test_peer_panel_th PASSED")
