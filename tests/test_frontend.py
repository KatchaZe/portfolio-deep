"""
test_frontend — proves index.html actually carries the new UI and stays wired to
the backend payload. Catches three things run_tests.py otherwise misses:
  1. index.html reverted / is an old build (new functions + element ids absent)
  2. backend adds a payload key the frontend forgot to consume
  3. build-stamp drift (index.html DASH_BUILD != config.BUILD)
Pure/offline — reads the file, no browser needed.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()


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
    assert not missing, ("index.html missing new UI tokens: %s" % missing)
    print("new UI tokens present OK")


def test_backend_keys_referenced():
    keys = ["d.market", "d.benchmark", "d.philosophy", "d.rate_risk", "d.downside",
            "garp_score", "garp_candidate", "net_upside_pct", "x.pead",
            "erp_as_of", "market_pe_as_of", "crash_guard", "reversal",
            # REV-16/21: young-DCF payload + the sensitivity band that makes the
            # implied CAGR honest for a pre-profit filer
            "young_dcf", "rev_sensitivity", "terminal_margin_anchored"]
    missing = [k for k in keys if k not in HTML]
    assert not missing, ("backend payload keys not consumed by index.html: %s" % missing)
    print("backend keys referenced OK")


def test_build_stamp_matches_config():
    m = re.search(r'DASH_BUILD\s*=\s*"([^"]+)"', HTML)
    assert m, "DASH_BUILD not found in index.html"
    assert m.group(1) == config.BUILD, (
        "BUILD drift: index.html DASH_BUILD=%s but config.BUILD=%s — bump both together"
        % (m.group(1), config.BUILD))
    print("build stamp matches config OK (%s)" % config.BUILD)


if __name__ == "__main__":
    test_new_ui_present()
    test_backend_keys_referenced()
    test_build_stamp_matches_config()
    print("\nALL test_frontend PASSED")
