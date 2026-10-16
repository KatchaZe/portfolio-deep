"""
test_frontend — proves the shipped frontend (index.html plus the /static files it
loads) actually carries the new UI and stays wired to the backend payload. Catches
three things run_tests.py otherwise misses:
  1. the frontend reverted / is an old build (new functions + element ids absent)
  2. backend adds a payload key the frontend forgot to consume
  3. build-stamp drift (DASH_BUILD != config.BUILD)
Pure/offline — reads the files, no browser needed.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from tests.frontend_source import frontend_source

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML = frontend_source()


def test_new_ui_present():
    required = [
        "function renderMarket", "function renderBenchmark", "function renderPhilosophy",
        "function garpBadge", 'id="mktRegime"', 'id="benchmark"', 'id="philosophy"',
        "renderMarket(d.market)", "renderBenchmark(d.benchmark)", "renderPhilosophy(d.philosophy)",
        "Downside Risk (S2/S4)", "function checkBuild", "function renderScreen", 'id="garpScreen"',
        # Philosophies UI upgrade (Tier 1-3, build 2026-07-03b)
        "function valueGauge", "function moatChip", "function guardBadges", "function dispAction",
        "function renderSynth", "function renderAssumptions", "function riskStory",
        "function portMoS", "function garpQuadrant", "function renderCashStance",
        'id="cmdStrip"', 'id="assumeBanner"', 'id="stripSynth"', 'id="cashStance"',
        "crash guard", "Portfolio Story",
        # REV-16 (S20): pre-profit forward valuation panel
        "function youngPanel", "Pre-profit forward value (S20)", "youngPanel(x)",
        # REV-21: valuation vs pricing must sit side by side, band included
        "function pricedInLine", "pricedInLine(x)", "margin ±5pp",
    ]
    missing = [t for t in required if t not in HTML]
    assert not missing, ("frontend missing new UI tokens: %s" % missing)
    print("new UI tokens present OK")


def test_backend_keys_referenced():
    keys = ["d.market", "d.benchmark", "d.philosophy", "d.rate_risk", "d.downside",
            "garp_score", "garp_candidate", "net_upside_pct", "x.pead",
            "erp_as_of", "market_pe_as_of", "crash_guard", "reversal",
            # REV-16/21: young-DCF payload + the sensitivity band that makes the
            # implied CAGR honest for a pre-profit filer
            "young_dcf", "rev_sensitivity", "terminal_margin_anchored"]
    missing = [k for k in keys if k not in HTML]
    assert not missing, ("backend payload keys not consumed by the frontend: %s" % missing)
    print("backend keys referenced OK")


def test_build_stamp_matches_config():
    m = re.search(r'DASH_BUILD\s*=\s*"([^"]+)"', HTML)
    assert m, "DASH_BUILD not found in the frontend"
    assert m.group(1) == config.BUILD, (
        "BUILD drift: frontend DASH_BUILD=%s but config.BUILD=%s — bump both together"
        % (m.group(1), config.BUILD))
    print("build stamp matches config OK (%s)" % config.BUILD)


def _newest_behaviour_change():
    """Date and subject of the newest fix/feat/perf commit under domain/ pipeline/ sources/.
    None when git is unavailable (e.g. a deploy without .git)."""
    import subprocess
    try:
        out = subprocess.run(
            ["git", "log", "--no-merges", "--format=%cs %s", "-E",
             "--grep=^(fix|feat|perf)[(:!]", "--", "domain", "pipeline", "sources"],
            cwd=ROOT, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    line = out.stdout.splitlines()[0] if out.returncode == 0 and out.stdout else ""
    return (line[:10], line[11:]) if line else None


def _newest_dated_comment():
    newest, where = None, None
    for pkg in ("domain", "pipeline", "sources"):
        for root, _, files in os.walk(os.path.join(ROOT, pkg)):
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                p = os.path.join(root, fn)
                with open(p, encoding="utf-8") as fh:
                    for i, line in enumerate(fh, 1):
                        for d in re.findall(r"20\d\d-\d\d-\d\d", line):
                            if newest is None or d > newest:
                                newest, where = d, f"{os.path.relpath(p, ROOT)}:{i}"
    return (newest, where) if newest else None


def test_build_stamp_is_not_older_than_the_engine():
    """The stamp must identify the code that produced the numbers, so a reader can tell
    whether a score moved because the DATA changed or because the ENGINE did. The newest
    behaviour-changing commit (fix/feat/perf) under the computing packages must not be
    newer than the build stamp. Without git, dated comments are the fallback evidence."""
    newest = _newest_behaviour_change() or _newest_dated_comment()
    assert newest, "no evidence of the engine's age (no git history, no dated comment)"
    stamp = config.BUILD[:10]
    assert stamp >= newest[0], (
        "BUILD %s is older than the newest behaviour change %s (%s) — bump config.BUILD "
        "and the frontend DASH_BUILD (static/js/app.js) so the dashboard says which engine "
        "produced its numbers" % (config.BUILD, newest[0], newest[1]))
    print("build stamp is current OK (%s >= newest change %s)" % (stamp, newest[0]))


if __name__ == "__main__":
    from tests._harness import run_suite
    run_suite(globals(), "ALL test_frontend PASSED")
