"""
ui_snapshot — pin what the dashboard renders in a real browser, then prove a change kept it.

golden_snapshot pins every API body; this pins the page built from them. Chromium loads
the dashboard, every request it makes is answered in-process by the FastAPI app (via
TestClient, network cut, secrets removed, server clock frozen) and every request that
would leave the app is refused and recorded. The browser clock is frozen too. For each
view the harness records the visible text, the markup of the visible panel, a digest of
computed styles and layout, console errors and failed requests.

    python -m tests.ui_snapshot             # diff against tests/ui_baseline.json.gz
    python -m tests.ui_snapshot --twice     # capture twice, prove the capture is deterministic
    python -m tests.ui_snapshot --update    # re-pin (only when the UI is meant to change)
"""
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

from tests._harness import age_data_files, baseline_gate, save_pinned_inputs

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
INPUTS = os.path.join(HERE, "ui_baseline.inputs.json")
BASELINE = os.path.join(HERE, "ui_baseline.json.gz")   # gzip: the panel markup is ~2.5 MB raw
FROZEN_AT = "2026-10-15 05:00:00"
BROWSER_NOW = "2026-10-15T05:00:00Z"   # same instant as the server clock
ORIGIN = "http://dashboard.local"
SECRET_ENV = ("FMP_API_KEY", "FINNHUB_API_KEY", "ALPHAVANTAGE_API_KEY", "APP_TOKEN",
              "GDRIVE_TOKEN_JSON", "GDRIVE_FOLDER_ID", "GOOGLE_APPLICATION_CREDENTIALS",
              "RENDER", "RENDER_EXTERNAL_URL", "PUBLIC_DEPLOY", "WEB_CONCURRENCY")
DEAD_PROXY = "http://127.0.0.1:9"
HOP_HEADERS = {"content-length", "content-encoding", "transfer-encoding", "connection"}

# Computed-style properties whose change means the page looks different. Layout
# (offset box) is included so a CSS file that fails to load, or loads in a different
# order, cannot pass on text alone. No element reports its top: one taller panel above
# would move every box below it.
STYLE_PROBE = r"""
(root) => {
  const props = ['display','visibility','color','backgroundColor','fontSize','fontWeight',
                 'textAlign','whiteSpace','borderTopColor','borderTopWidth','paddingTop',
                 'paddingLeft','marginTop','gridTemplateColumns'];
  const out = [];
  for (const el of [root, ...root.querySelectorAll('*')]) {
    if (el.tagName === 'SCRIPT' || el.tagName === 'STYLE') continue;
    const cs = getComputedStyle(el);
    out.push([el.tagName, ...props.map(p => cs[p]), el.offsetLeft, el.offsetWidth,
              el.offsetHeight].join('|'));
  }
  return out.join('\n');
}
"""
FIXTURE_PORTFOLIO = r"""
const d = await (await fetch('/api/portfolio')).json();
const r = d.rows;
Object.assign(r[0], {ticker: 'TLT'});
Object.assign(r[1], {ticker: 'GLD'});
r[2].momentum_v2 = Object.assign({}, r[2].momentum_v2 || {}, {mom_label: 'Strong', crash_guard: true,
  dividend_adjusted: false, stale: true, age_days: 9, mom_bucket: 'Top', mom_rank_pct: 0.91,
  quality: {bars: 250, dropped_nonpos: 1, dropped_spikes: 1}});
r[2].action = 'BUY';
r[3].key_metrics = Object.assign({}, r[3].key_metrics || {}, {fv_disagreement_x: 3.2, spread_pct: 4.1});
Object.assign(r[3], {forward_eps_n: 3, forward_eps: 5.1, forward_eps_low: 4.2, forward_eps_high: 6.3,
  forward_eps_spread_pct: 41, forward_eps_sources: {fmp: 5.0, yahoo: 5.2, finnhub: 5.1}});
Object.assign(r[4], {earnings_surprises: [], rev_surprises: [], margin_trend: [], change: -1.25,
  earn_status: {eps: {reason: 'stale-facts', detail: 'old schema'}, rev: {}, mgn: {reason: 'stale-facts'}},
  pl: -1234.5, pl_pct: -12.3, eq_verdict: 'LOW', advice: null, flags: []});
Object.assign(r[5], {pead: {bias: 'down', strength: 'strong', quarter: 'Q2 2026', note: 'x'}});
d.market = Object.assign({}, d.market, {regime: 'risk_off', dist_pct: -4.2,
  valuation: Object.assign({}, (d.market || {}).valuation, {regime: 'expensive', tilt: 'raise cash'})});
d.benchmark = Object.assign({}, d.benchmark, {excess_pp: -3.4, portfolio_12m: 5.1, market_12m: 8.5});
d.philosophy = Object.assign({}, d.philosophy, {running: 'GARP', fit_ok: false, notes: ['drift']});
d.totals = Object.assign({}, d.totals, {pl: -2500, pl_pct: -4.4});
d.momentum_meta = {stale: true, as_of: null, stale_count: 1};
renderPortfolio(d);
"""
FIXTURE_RISK = r"""
const d = JSON.parse(JSON.stringify(__riskCache.data));
const top = ((d.capital_vs_risk || {}).covariance_based || [])[0] || {ticker: 'MSFT'};
d.rebalance = {no_trade: false, after: {port_vol_pct: 18.2, top_risk: 'MSFT', stress_worst_pct: -31.5},
  trades: [{ticker: top.ticker, action: 'TRIM', from_pct: 14.1, to_pct: 10, amount_pct: -4.1, priority: 'Must'},
           {ticker: 'TLT', action: 'ADD', from_pct: 0, to_pct: 4.1, amount_pct: 4.1, priority: 'Should'}]};
d.rate_risk = {has_bonds: true, loss_pct: -0.6, tag: '[CALC]'};
d.meta = Object.assign({}, d.meta, {pricing_assets: ['GLD', 'IBIT'], quota_degraded: true});
d.suitability = Object.assign({}, d.suitability, {within_tolerance: true});
d.downside = Object.assign({}, d.downside, {downside_beta: 9, vol_pct: null, tag: 'error: empty'});
renderRisk(d);
"""
FIXTURE_CORR = r"""
const d = JSON.parse(JSON.stringify(__corr));
if (!d.correlation) throw new Error('correlation payload missing: ' + Object.keys(d) + ' ' + d.status + ' ' + d.message);
d.correlation.meta = Object.assign({}, d.correlation.meta, {cov_mode: '%s', proxy_tickers: ['TMDX', 'HIMS']});
renderCorr(d);
"""
# Source layout is not behaviour, so the markup is normalised before it is compared:
# <style> blocks are dropped (what CSS does is pinned by STYLE_PROBE, which skips the
# non-rendering <script> and <style> elements too), and a whitespace-only text node is
# reduced to one space, which renders the same; the text and the layout digest still
# see any whitespace that shows.
PANEL_HTML = r"""
(id) => {
  const copy = document.getElementById(id).cloneNode(true);
  copy.querySelectorAll('style').forEach(s => s.remove());
  copy.normalize();
  const walk = document.createTreeWalker(copy, NodeFilter.SHOW_TEXT);
  const blank = [];
  while (walk.nextNode()) if (!walk.currentNode.textContent.trim()) blank.push(walk.currentNode);
  blank.forEach(t => { t.textContent = ' '; });
  return copy.innerHTML;
}
"""


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _cut_network():
    import socket

    def _refuse(*_a, **_k):
        raise OSError("ui_snapshot: network disabled")
    socket.socket.connect = _refuse
    socket.create_connection = _refuse
    socket.getaddrinfo = _refuse


def _no_sleep(_seconds):
    """Retry back-off and rate-limit pauses only wait for a network that is cut anyway."""


class _App:
    """Answers the page's requests from the FastAPI app with the server clock frozen."""

    def __init__(self):
        from freezegun import freeze_time
        self._freeze = lambda: freeze_time(FROZEN_AT)
        with self._freeze():
            sys.path.insert(0, os.getcwd())
            from fastapi.testclient import TestClient
            import app as app_mod
            self._client = TestClient(app_mod.app)
        self.pending = 0
        self.failed = []

    def handle(self, route, request):
        if not request.url.startswith(ORIGIN + "/"):
            self.failed.append("refused " + request.method + " " + request.url)
            route.abort()
            return
        path = request.url[len(ORIGIN):]
        real_sleep, time.sleep = time.sleep, _no_sleep
        try:
            with self._freeze():
                r = self._client.request(request.method, path, content=request.post_data_buffer,
                                         headers={"content-type": request.headers.get("content-type", "")})
        finally:
            time.sleep = real_sleep
        if r.status_code >= 400:
            self.failed.append("%s %s -> %s" % (request.method, path, r.status_code))
        headers = {k: v for k, v in r.headers.items() if k.lower() not in HOP_HEADERS}
        route.fulfill(status=r.status_code, headers=headers, body=r.content)


def _settle(page, app, quiet_ms=400, limit_ms=60000):
    """Wait until no request has been in flight for quiet_ms."""
    quiet = waited = 0
    while quiet < quiet_ms and waited < limit_ms:
        page.wait_for_timeout(50)
        waited += 50
        quiet = quiet + 50 if app.pending == 0 else 0


def _views():
    """(name, steps) run in order on one page. Steps click the real controls, so a handler
    that is no longer reachable from its onclick attribute fails the snapshot."""
    def click(sel):
        return ("click", sel)

    def fill(sel, value):
        return ("fill", sel, value)

    def on(handler):
        return '[onclick="%s"]' % handler
    return [
        ("portfolio", []),
        ("portfolio_details_open", [("click_all", "#p1 details > summary")]),
        ("watchlist", [click("#tab2")]),
        ("watchlist_run", [click(on("runWatch()"))]),
        ("allocation", [click("#tab3")]),
        ("allocation_whatif", [fill(".wf-t", "MSFT"), fill(".wf-a", "1000"), click(on("calcWhatif()"))]),
        ("allocation_risk_free", [("select", "#riskEnrich", "free"), click(on("loadRisk(true)"))]),
        ("correlation", [click("#tab6")]),
        ("correlation_crisis", [click("#cmCrisis")]),
        ("correlation_free", [("select", "#corrEnrich", "free"), click(on("loadCorr(true)"))]),
        ("howto", [click("#tab4")]),
        ("ref", [click("#tab5")]),
        ("portfolio_again", [click("#tab1")]),
        ("assumptions_empty", [click(on("saveAssumptions()"))]),
        ("assumptions_bad", [fill("#erpIn", "50"), click(on("saveAssumptions()"))]),
        ("watch_add", [click("#tab2"), fill("#wt", "ZZZZ"), click(on("addWatch()"))]),
        ("watch_remove", [click("#watchNames " + on("removeWatch('ZZZZ')"))]),
        # The stored data never reaches some branches (asset-class cards, crash guard,
        # rebalance trades, proxy correlation). Re-render the real payloads with those
        # fields switched on.
        ("fixture_portfolio", [click("#tab1"), ("js", FIXTURE_PORTFOLIO), ("click_all", "#p1 details > summary")]),
        ("fixture_risk", [click("#tab3"), ("js", FIXTURE_RISK)]),
        ("fixture_corr_mixed", [click("#tab6"), ("js", FIXTURE_CORR % "mixed")]),
        ("fixture_corr_proxy", [("js", FIXTURE_CORR % "proxy")]),
        ("daily", [click("#tab1"), click("#btnDaily")]),
        ("refresh", [click("#btnRefresh")]),
        # last: a rejected add replaces the portfolio view with the error body
        ("holding_bad", [fill("#t", "$$"), click(on("addHolding()"))]),
    ]


def _run_steps(page, app, steps):
    for step in steps:
        kind, sel = step[0], step[1]
        if kind == "click":
            page.click(sel)
        elif kind == "click_all":
            for el in page.query_selector_all(sel):
                el.click()
        elif kind == "fill":
            page.fill(sel, step[2])
        elif kind == "select":
            page.select_option(sel, step[2])
        elif kind == "js":
            page.evaluate("async () => { %s }" % sel)
        _settle(page, app)


def _style_dump(page, view):
    """The computed-style listing; set UI_SNAPSHOT_RAW=<dir> to keep it for diffing a drift."""
    text = page.evaluate(STYLE_PROBE, page.query_selector("body"))
    raw = os.environ.get("UI_SNAPSHOT_RAW")
    if raw:
        os.makedirs(raw, exist_ok=True)
        with open(os.path.join(raw, view + ".style.txt"), "w", encoding="utf-8") as fh:
            fh.write(text)
    return text


def _capture():
    _cut_network()
    from playwright.sync_api import sync_playwright
    app = _App()
    console, page_errors = [], []
    out = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900}, locale="en-US",
                                timezone_id="UTC")
        page.clock.set_fixed_time(BROWSER_NOW)
        page.on("request", lambda _r: setattr(app, "pending", app.pending + 1))
        page.on("requestfinished", lambda _r: setattr(app, "pending", app.pending - 1))
        page.on("requestfailed", lambda _r: setattr(app, "pending", app.pending - 1))
        page.on("console", lambda m: console.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: page_errors.append(str(e)))
        page.on("dialog", lambda d: d.dismiss())
        page.route("**/*", app.handle)
        page.goto(ORIGIN + "/")
        _settle(page, app)
        started = time.monotonic()
        for name, steps in _views():
            del console[:], page_errors[:], app.failed[:]
            _run_steps(page, app, steps)
            sys.stderr.write("view %s settled at %.1fs\n" % (name, time.monotonic() - started))
            panel = page.evaluate(
                "() => [...document.querySelectorAll('.panel > [id^=p]')]"
                ".find(e => e.style.display !== 'none').id")
            out[name] = {
                "panel": panel,
                "text": page.evaluate("() => document.body.innerText"),
                "html": page.evaluate(PANEL_HTML, panel),
                "chrome_html": page.evaluate(
                    "() => ['ver','persist','quota','summary','assumeMsg','momBanner',"
                    "'assumeBanner','cmdStrip','stripSynth','buildStamp']"
                    ".map(i => { const e = document.getElementById(i); return i + '=' + (e ? e.outerHTML : ''); })"
                    ".join('\\n')"),
                "style_sha": _sha(_style_dump(page, name)),
                "console_errors": sorted(set(console)),
                "page_errors": sorted(set(page_errors)),
                "failed_requests": sorted(set(app.failed)),
            }
        browser.close()
    return out


def _run_in_copy():
    tmp = tempfile.mkdtemp(prefix="ui_snapshot_")
    work = os.path.join(tmp, "app")
    shutil.copytree(ROOT, work, ignore=shutil.ignore_patterns(".git", "__pycache__", "golden"))
    age_data_files(os.path.join(work, "data"), FROZEN_AT)
    env = {k: v for k, v in os.environ.items() if k not in SECRET_ENV}
    env["PYTHONHASHSEED"] = "0"
    # Price fetches go through libcurl, below the Python socket patch. A proxy on a
    # closed port makes them fail at once, the same way on every machine.
    for var in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy"):
        env[var] = DEAD_PROXY
    env.pop("NO_PROXY", None)
    env.pop("no_proxy", None)
    try:
        proc = subprocess.run([sys.executable, "-m", "tests.ui_snapshot", "--child"],
                              cwd=work, env=env, capture_output=True, text=True, timeout=900)
        if proc.returncode != 0:
            sys.stderr.write(proc.stdout[-4000:] + proc.stderr[-8000:])
            raise SystemExit("ui_snapshot: capture failed")
        with open(os.path.join(work, "ui_out.json"), encoding="utf-8") as fh:
            return json.load(fh)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _first_difference(a, b):
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        i = next(k for k, (x, y) in enumerate(zip(a, b)) if x != y)
        return "[%d] %s" % (i, _first_difference(a[i], b[i]))
    if isinstance(a, str) and isinstance(b, str):
        i = next((k for k, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
        return "at char %d: %r -> %r" % (i, a[max(0, i - 60):i + 60], b[max(0, i - 60):i + 60])
    return "%s -> %s" % (json.dumps(a)[:300], json.dumps(b)[:300])


def _diff(pinned, current):
    drift = []
    for view in sorted(set(pinned) | set(current)):
        if view not in pinned or view not in current:
            drift.append("%s: view %s" % (view, "added" if view in current else "missing"))
            continue
        for key in sorted(set(pinned[view]) | set(current[view])):
            a, b = pinned[view].get(key), current[view].get(key)
            if a != b:
                drift.append("%s/%s %s" % (view, key, _first_difference(a, b)))
    return drift


def main(argv):
    if "--child" in argv:
        with open("ui_out.json", "w", encoding="utf-8") as fh:
            json.dump(_capture(), fh, ensure_ascii=False, indent=1, sort_keys=True)
        return 0
    if "--update" not in argv and "--twice" not in argv:
        gate = baseline_gate("ui_snapshot", BASELINE, INPUTS, ROOT)
        if gate:
            print(gate[1])
            return gate[0]
    current = _run_in_copy()
    if "--twice" in argv:
        drift = _diff(current, _run_in_copy())
        print("UI CAPTURE NOT DETERMINISTIC:\n  " + "\n  ".join(drift) if drift
              else "ui capture deterministic: %d views" % len(current))
        return 1 if drift else 0
    if "--update" in argv:
        save_pinned_inputs(INPUTS, ROOT)
        with gzip.open(BASELINE, "wt", encoding="utf-8") as fh:
            json.dump(current, fh, ensure_ascii=False, indent=1, sort_keys=True)
        print("ui pinned: %s -> %s" % (", ".join(current), BASELINE))
        return 0
    with gzip.open(BASELINE, "rt", encoding="utf-8") as fh:
        drift = _diff(json.load(fh), current)
    if drift:
        print("UI DRIFT:\n  " + "\n  ".join(drift))
        return 1
    print("ui clean: %s" % ", ".join(current))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
