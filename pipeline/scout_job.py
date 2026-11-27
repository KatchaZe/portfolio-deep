"""Sector scout job: tools/scout_fetch run inside the app, one industry at a time.

Display only (PP1): the job writes data/cache/scout/<industry>.json and status.json and
nothing a holding's score reads. It never calls FMP: scout_fetch reads SEC (companyfacts +
submissions), the T-1 close and a 2y daily history (Yahoo, then Stooq), and the run records
the FMP ledger before and after as proof.

Render's free tier (512 MB, a shared CPU, the instance sleeps when idle) shapes it:
  - one background thread, one industry at a time, SCOUT_WORKERS member fetches at once;
  - incremental: each member's SEC part is cached for scout_fetch.SEC_TTL_DAYS, so a rerun,
    or a run cut off by a restart, refetches only what is missing or expired;
  - resumable: the queue lives in status.json; a run whose thread is gone reads
    "interrupted" and the tab offers to continue with the industries left;
  - every finished industry file and status.json are mirrored to Drive like the other caches,
    and pulled back when the local disk was wiped by a redeploy.
"""
import collections
import datetime as dt
import json
import logging
import os
import threading
import time
from types import SimpleNamespace

import config

log = logging.getLogger("portfolio.scout_job")

SCOUT_WORKERS = 2
REMOTE_PREFIX = "scout_"
_lock = threading.Lock()
_thread = None


JOB_THREAD = "scout-job"


def scout_dir():
    return os.path.join(config.CACHE_DIR, "scout")


def _status_path():
    return os.path.join(scout_dir(), "status.json")


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False)
    os.replace(tmp, path)


def _mirror(path):
    try:
        import store_sync
        store_sync.schedule_push_named(path, REMOTE_PREFIX + os.path.basename(path))
    except Exception as e:                       # rebuildable cache: never fail the run on it
        log.warning("scout mirror %s: %s", path, e)


def slug(industry):
    from domain.scout import slug as _slug
    return _slug(industry)


def holding_industries(store):
    """The Damodaran industries of the current holdings, {industry: [tickers]}."""
    out = collections.defaultdict(list)
    for t in sorted(store.get("holdings") or {}):
        ind = ((store.get("facts") or {}).get(t) or {}).get("damodaran_industry")
        if ind:
            out[ind].append(t)
    return dict(out)


def all_industries():
    """{industry: US member count} from the cached Damodaran company list, or {}."""
    from sources import damodaran_companies as dc
    rows = (_read(os.path.join(config.CACHE_DIR, dc.INDNAME_CACHE)) or {}).get("rows") or {}
    return dict(collections.Counter(ind for ind, _sic, ctry in rows.values() if ctry == "United States"))


def load(industry):
    """The job's JSON for one industry (local, else Drive), or None."""
    path = os.path.join(scout_dir(), slug(industry) + ".json")
    got = _read(path)
    if got is None:
        try:
            from sources import gdrive_store
            got = gdrive_store.drive_pull_json(REMOTE_PREFIX + os.path.basename(path))
            if got:
                _write(path, got)
        except Exception as e:
            log.warning("scout pull %s: %s", path, e)
    return got


def status():
    """status.json with the thread's liveness checked: a run whose thread is gone (a restart)
    reads "interrupted" and keeps its queue, so the tab can continue it."""
    s = _read(_status_path()) or {"state": "idle", "queue": [], "runs": {}}
    alive = _thread is not None and _thread.is_alive()
    if s.get("state") == "running" and not alive:
        s["state"] = "interrupted"
    s["alive"] = alive
    return s


def _save(s):
    s["updated"] = _now()
    _write(_status_path(), s)


def enqueue(industries):
    """Add industries to the queue and start the worker when idle. Returns the status."""
    global _thread
    with _lock:
        s = status()
        q = [i for i in s.get("queue") or [] if i != s.get("current")] if s["state"] == "interrupted" else list(s.get("queue") or [])
        if s["state"] == "interrupted" and s.get("current"):
            q.insert(0, s["current"])
        for i in industries:
            if i not in q and i != (s.get("current") if s.get("alive") else None):
                q.append(i)
        s.update(queue=q, error=None)
        if not s.get("alive") and q:
            s.update(state="running", started=_now(), current=None)
            _save(s)
            _thread = threading.Thread(target=_worker, name=JOB_THREAD, daemon=True)
            _thread.start()
        else:
            _save(s)
        return status()


def _worker():
    while True:
        with _lock:
            s = status()
            if not s.get("queue"):
                s.update(state="idle", current=None, finished=_now())
                _save(s)
                return
            industry = s["queue"][0]
            s.update(state="running", current=industry, done_n=0, total_n=None)
            _save(s)
        try:
            run = run_one(industry)
            err = None
        except Exception as e:                   # one industry failing never stops the queue
            log.exception("scout job %s", industry)
            run, err = None, f"{type(e).__name__}: {str(e)[:200]}"
        with _lock:
            s = status()
            s["queue"] = [i for i in s.get("queue") or [] if i != industry]
            if run:
                s.setdefault("runs", {})[industry] = run
            if err:
                s.setdefault("errors", {})[industry] = err
                s["error"] = f"{industry}: {err}"
            else:
                (s.get("errors") or {}).pop(industry, None)
            s["state"] = "running"
            _save(s)
            _mirror(_status_path())


def _progress(industry, done, total, ticker):
    with _lock:
        s = _read(_status_path()) or {}
        if s.get("current") == industry and (done % 5 == 0 or done == total):
            s.update(done_n=done, total_n=total, last_ticker=ticker)
            _save(s)


def run_one(industry, today=None, offline=False, facts_dir=None):
    """Run scout_fetch for one industry into scout_dir(). Returns the run record:
    finish time, seconds, members and the requests it made (FMP from the ledger)."""
    import store
    from sources import fmp
    from tools import scout_fetch as sf
    sf._count_sec()
    # E90: the scout never calls FMP. Refused by thread name (this job thread and
    # scout_fetch's workers), so the app's own FMP use in other threads is untouched.
    fmp.refuse_threads(JOB_THREAD, "the sector scout never calls FMP")
    fmp.refuse_threads(sf.WORKER_PREFIX, "the sector scout never calls FMP")
    before = dict(sf.CALLS.n)
    fmp0 = fmp.LEDGER.used()
    t0 = time.time()
    args = SimpleNamespace(out=scout_dir(), cache=config.CACHE_DIR, industry=[industry],
                           workers=SCOUT_WORKERS, limit=None, today=today, refresh=False, keep_facts=False,
                           offline=offline, facts_dir=facts_dir, portfolio=store.PATH, group2=True,
                           progress=_progress)
    os.makedirs(scout_dir(), exist_ok=True)
    code = sf.run(args)
    if code:
        raise RuntimeError(f"scout_fetch exited {code} (Damodaran company list unavailable?)")
    path = os.path.join(scout_dir(), slug(industry) + ".json")
    out = _read(path) or {}
    _mirror(path)
    calls = {k: sf.CALLS.n.get(k, 0) - before.get(k, 0) for k in ("sec", "companyfacts", "price", "history")}
    calls["fmp"] = fmp.LEDGER.used() - fmp0
    return {"finished": _now(), "as_of": out.get("as_of"), "members": len(out.get("members") or []),
            "secs": round(time.time() - t0, 1), "requests": calls}
