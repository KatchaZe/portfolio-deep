"""
golden_snapshot — pin the app's whole observable output, then prove a change kept it.

replay_snapshot pins 19 headline values per ticker. A behaviour-preserving refactor
needs more than that: every field the engine returns, every endpoint the dashboard
reads, and the offline sources -> normalize -> validate -> engine path. This harness
captures all of it in one offline, clock-frozen run and diffs it against
tests/golden/*.json.

    python -m tests.golden_snapshot            # diff against the pinned output
    python -m tests.golden_snapshot --update   # re-pin (only before a refactor starts)

The run happens in a throwaway copy of the repo, so the endpoints that write the store
never touch data/. Network is cut at the socket, API keys and Drive credentials are
removed from the environment, and the clock is frozen, so two runs on the same code
and data produce identical bytes.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

from tests._harness import age_data_files, baseline_gate, save_pinned_inputs

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
GOLDEN_DIR = os.path.join(HERE, "golden")
INPUTS = os.path.join(GOLDEN_DIR, "_inputs.json")
FROZEN_AT = "2026-10-15 05:00:00"
SECRET_ENV = ("FMP_API_KEY", "FINNHUB_API_KEY", "ALPHAVANTAGE_API_KEY", "APP_TOKEN",
              "GDRIVE_TOKEN_JSON", "GDRIVE_FOLDER_ID", "GOOGLE_APPLICATION_CREDENTIALS",
              "RENDER", "RENDER_EXTERNAL_URL", "PUBLIC_DEPLOY", "WEB_CONCURRENCY")
WHATIF = [{"ticker": "MSFT", "amount": 1000}, {"ticker": "NVDA", "amount": 500}]


def _canonical(obj):
    if isinstance(obj, float):
        return round(obj, 9)
    if isinstance(obj, dict):
        return {str(k): _canonical(v) for k, v in sorted(obj.items(), key=lambda kv: str(kv[0]))}
    if isinstance(obj, (list, tuple)):
        return [_canonical(v) for v in obj]
    return obj


def _capture():
    """Runs inside the throwaway copy (cwd), with the network cut and the clock frozen."""
    import socket

    def _refuse(*_a, **_k):
        raise OSError("golden_snapshot: network disabled")
    socket.socket.connect = _refuse
    socket.create_connection = _refuse
    socket.getaddrinfo = _refuse

    from freezegun import freeze_time
    with freeze_time(FROZEN_AT):
        sys.path.insert(0, os.getcwd())
        from fastapi.testclient import TestClient
        import app as app_mod
        import store as st
        from domain.engine import get_engine
        from domain.facts import FinancialFacts
        from pipeline import fundamentals

        client = TestClient(app_mod.app)
        out = {}

        def get(path):
            r = client.get(path)
            return {"status": r.status_code, "body": r.json()}

        def post(path, **kw):
            r = client.post(path, **kw)
            return {"status": r.status_code, "body": r.json()}

        store = st.load()
        engine = get_engine()
        fields = set(FinancialFacts.__dataclass_fields__)

        def stored_facts(d):
            kw = {k: v for k, v in d.items() if k in fields}
            kw["flags"] = []
            return FinancialFacts(**kw)
        out["engine"] = {t: engine.evaluate(stored_facts(d), rf=0.045).to_dict()
                         for t, d in sorted(store.get("facts", {}).items())}

        out["api_read"] = {p: get(p) for p in (
            "/healthz", "/api/portfolio", "/api/quota", "/api/persist", "/api/assumptions",
            "/api/watchlist", "/api/screen", "/api/allocation", "/api/risk?enrich=free")}
        out["api_read"]["whatif"] = post("/api/allocation/whatif", json=WHATIF)
        out["api_read"]["whatif_bad"] = post("/api/allocation/whatif",
                                             json=[{"ticker": "MSFT", "amount": "abc"}])

        holding = next(iter(sorted(store.get("holdings", {}))), None)
        out["api_write"] = {
            "assumptions_bad": post("/api/assumptions?erp_pct=50"),
            "assumptions_none": post("/api/assumptions"),
            "holding_bad": post("/api/holding?ticker=%24%24"),
            "watch_add": post("/api/watchlist/add?ticker=ZZZZ"),
            "watch_remove": post("/api/watchlist/remove?ticker=ZZZZ"),
        }
        if holding:
            h = store["holdings"][holding]
            out["api_write"]["holding_update"] = post(
                f"/api/holding?ticker={holding}&shares={h.get('shares')}&avg_cost={h.get('avg_cost')}")
        out["api_write"]["assumptions_set"] = post("/api/assumptions?erp_pct=4.5&market_pe=24")
        out["api_write"]["assumptions_after"] = get("/api/assumptions")

        tickers = sorted(store.get("holdings", {}))[:6]
        fetched, errors, calls, rf_pct, rf_live = fundamentals.fetch_fundamentals(tickers, "", 0, 250)
        out["offline_pipeline"] = {
            "errors": errors, "calls": calls, "rf_pct": rf_pct, "rf_live": rf_live,
            "fetched": {t: {"facts": ff.to_dict(), "valuation": v.to_dict()}
                        for t, (ff, v) in sorted(fetched.items())}}
        return _canonical(json.loads(json.dumps(out, default=str)))


def _run_in_copy():
    tmp = tempfile.mkdtemp(prefix="golden_")
    work = os.path.join(tmp, "app")
    shutil.copytree(ROOT, work, ignore=shutil.ignore_patterns(".git", "__pycache__", "golden"))
    age_data_files(os.path.join(work, "data"), FROZEN_AT)
    env = {k: v for k, v in os.environ.items() if k not in SECRET_ENV}
    env["PYTHONHASHSEED"] = "0"
    try:
        proc = subprocess.run([sys.executable, "-m", "tests.golden_snapshot", "--child"],
                              cwd=work, env=env, capture_output=True, text=True, timeout=900)
        if proc.returncode != 0:
            sys.stderr.write(proc.stdout[-4000:] + proc.stderr[-8000:])
            raise SystemExit("golden_snapshot: capture failed")
        with open(os.path.join(work, "golden_out.json"), encoding="utf-8") as fh:
            return json.load(fh)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _diff(a, b, path="", out=None, limit=60):
    out = [] if out is None else out
    if len(out) >= limit:
        return out
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a:
                out.append(f"+ {path}/{k}")
            elif k not in b:
                out.append(f"- {path}/{k}")
            else:
                _diff(a[k], b[k], f"{path}/{k}", out, limit)
    elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        for i, (x, y) in enumerate(zip(a, b)):
            _diff(x, y, f"{path}[{i}]", out, limit)
    elif a != b:
        out.append(f"~ {path}: {json.dumps(a)[:160]} -> {json.dumps(b)[:160]}")
    return out


def main(argv):
    if "--child" in argv:
        with open("golden_out.json", "w", encoding="utf-8") as fh:
            json.dump(_capture(), fh, ensure_ascii=False, indent=1, sort_keys=True)
        return 0
    if "--update" not in argv:
        gate = baseline_gate("golden_snapshot", os.path.join(GOLDEN_DIR, "engine.json"), INPUTS, ROOT)
        if gate:
            print(gate[1])
            return gate[0]
    current = _run_in_copy()
    if "--update" in argv:
        os.makedirs(GOLDEN_DIR, exist_ok=True)
        save_pinned_inputs(INPUTS, ROOT)
        for section, payload in current.items():
            with open(os.path.join(GOLDEN_DIR, f"{section}.json"), "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=1, sort_keys=True)
                fh.write("\n")
        print(f"golden pinned: {sorted(current)} -> {GOLDEN_DIR}")
        return 0
    drift = []
    for section, payload in current.items():
        try:
            with open(os.path.join(GOLDEN_DIR, f"{section}.json"), encoding="utf-8") as fh:
                pinned = json.load(fh)
        except OSError:
            drift.append(f"missing golden section {section}")
            continue
        drift += _diff(pinned, payload, section)
    if drift:
        print("GOLDEN DRIFT:\n  " + "\n  ".join(drift))
        return 1
    print(f"golden clean: {', '.join(sorted(current))}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
