"""
peers — the network side of the relative-valuation cross-check (domain/relative.py).

Weekly, per ticker:
  1. FMP stock-peers list, disk-cached 30 days (one optional FMP call per ticker a
     month, drawn from the spare budget; an expired list is reused with a flag when
     FMP fails or the budget is spent).
  2. For each peer (up to MAX_PEERS): SEC companyfacts (no disk cache: peers are many
     and the raw JSON is MBs; only the extracted point is kept), T-1 close from the
     Yahoo chart, then Stooq.
  3. domain.relative.fit on the peers that pass every guard. Each excluded peer keeps
     its reason, so the card says why n is what it is.

Between weekly runs the stored model is reused (it is a cross-section of peers on one
date); the target's own side (margin, growth, net debt, shares) is recomputed on every
refresh from the current facts, so a new 10-Q reaches the card the same day.
A model older than lastgood.MAX_AGE_DAYS is dropped, never shown as current.
"""
import json
import logging
import os
import threading

import config
from types import SimpleNamespace

from domain import one_time, relative, pricing
from pipeline import batch, lastgood
from pipeline.cik import resolve_cik
from sources import fmp, sec_edgar, sec_gap, stooq, yahoo

log = logging.getLogger("portfolio.peers")

MAX_PEERS = 12             # FMP lists ~10; the SIC match below may drop some
SIC_MATCH_MIN = 2          # digits of SIC a peer must share with the target
LIST_TTL_DAYS = 30
MODEL_TTL_DAYS = 7
PEER_LIST_CALLS = 1


def _list_path(t, cache_dir):
    return os.path.join(cache_dir, "peers", f"{t}.json")


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _write_json(path, obj):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(obj, fh)
        os.replace(tmp, path)
    except OSError as e:
        log.warning("peer cache write failed (%s): %s", path, e)


def peer_list(t, fmp_key, today, budget=None, cache_dir=None, fetch=None):
    """(symbols, as_of, note). Fresh cache → no call; else one FMP call; on failure the
    expired cache with a note; else ([], None, reason)."""
    cache_dir = cache_dir or config.CACHE_DIR
    fetch = fetch or fmp.fetch_peers
    path = _list_path(t, cache_dir)
    cached = _read_json(path)
    if cached and lastgood.usable(cached.get("as_of"), today, LIST_TTL_DAYS) and cached.get("peers"):
        return cached["peers"], cached["as_of"], None, 0
    calls = 0
    if fmp_key and batch.quota_take(budget, PEER_LIST_CALLS):
        calls = PEER_LIST_CALLS
        try:
            got = [s for s in (fetch(t, fmp_key) or []) if s and s.upper() != t.upper()]
        except Exception as e:
            log.warning("%s FMP peers failed: %s", t, e)
            got = []
        if got:
            _write_json(path, {"as_of": today.isoformat(), "peers": got})
            return got, today.isoformat(), None, calls
    if cached and cached.get("peers"):
        return (cached["peers"], cached.get("as_of"),
                f"peer list from {cached.get('as_of')} (FMP not refreshed)", calls)
    return [], None, ("no FMP key" if not fmp_key else "no peer list from FMP"), calls


def _t1(sym, today_iso):
    for src, f in (("yahoo", lambda: yahoo.fetch_chart(sym, rng="5d", interval="1d")),
                   ("stooq", lambda: stooq.fetch_chart(sym))):
        try:
            ch = f()
            close, day = pricing.t1_close(ch.get("closes"), ch.get("dates"), today_iso)
        except Exception as e:
            log.info("%s %s close failed: %s", sym, src, e)
            continue
        if close:
            return close, day, src
    return None, None, None


class PointMemo:
    """Peer points shared by every target in one run (NVDA, AVGO and TSM share peers)."""

    def __init__(self):
        self._d, self._lock = {}, threading.Lock()

    def get(self, sym, build):
        with self._lock:
            if sym in self._d:
                return self._d[sym]
        v = build(sym)
        with self._lock:
            self._d.setdefault(sym, v)
        return v


def _complete(cik, cf):
    return sec_gap.complete(cik, cf, config.SEC_USER_AGENT, cache_dir=config.CACHE_DIR,
                            throttle=lambda: sec_edgar._throttle(config.SEC_MIN_INTERVAL))


def peer_point(sym, today, fetch_facts=None, fetch_price=None, complete=_complete):
    """(point, reason) for one peer on the common clock. `complete` fills a filing
    companyfacts has not ingested yet (None = skip, for tests)."""
    fetch_facts = fetch_facts or (lambda cik: sec_edgar.fetch_companyfacts(
        cik, config.SEC_USER_AGENT, cache_dir=None, min_interval=config.SEC_MIN_INTERVAL))
    fetch_price = fetch_price or _t1
    cik, _ = resolve_cik(sym)
    if not cik:
        return None, "not an SEC filer"
    try:
        cf = fetch_facts(cik)
        cf = complete(cik, cf) if complete else cf
        x = sec_edgar.extract(cf)
    except Exception as e:
        return None, f"SEC facts unavailable ({str(e)[:40]})"
    if cf.get("_stale_cache"):
        return None, "SEC facts stale"
    g = cf.get("_sec_gap")
    if g and g.get("checked") is False:
        g = None                                # unchecked, not known stale: the 6-month guard decides
    if g and not g.get("read"):
        return None, f"SEC companyfacts lacks the {g['form']} filed {g['filed']} (not read)"
    close, day, src = fetch_price(sym, today.isoformat())
    if not close:
        return None, "no T-1 close (Yahoo and Stooq)"
    x, addback = normalized(x)
    p, why = relative.point(sym, x, close, day, today)
    if p:
        p["sic"] = cf.get("_sic")
        p["addback"] = round(addback / x["revenue"], 4) if addback and x.get("revenue") else 0.0
    return p, why


ONE_TIME_FIELDS = ("operating_income", "revenue", "impairment_ttm", "restructuring_ttm",
                   "restructuring_annuals_dated", "acquired_iprd_ttm",
                   "acquired_iprd_annuals_dated", "revenue_annuals_dated")


def normalized(x):
    """(x with EBIT after the same one-time add-back the target gets, add-back $).
    The target's ff.operating_income is already normalized (domain/one_time.py); a
    peer's raw EBIT next to it would compare two different definitions of margin
    (GILD's acquired IPR&D read -8%, live 2026-10-23)."""
    ns = SimpleNamespace(**{k: x.get(k) for k in ONE_TIME_FIELDS}, operating_income_gaap=None,
                         flags=[], provenance={})
    ns.set = lambda name, value, source: setattr(ns, name, value)
    add = one_time.normalize_operating_income(ns)
    return (dict(x, operating_income=ns.operating_income) if add else x), add


def sic_level(a, b):
    """Leading SIC digits two codes share (0-4); None when either is unknown."""
    a, b = (str(c[0]) if c else None for c in (a, b))
    if not a or not b:
        return None
    n = 0
    while n < 4 and n < len(a) and n < len(b) and a[n] == b[n]:
        n += 1
    return n


def match_industry(pts, target_sic):
    """(kept, dropped[{symbol, reason}], note). Peers outside the target's SIC major
    group are dropped only when at least MIN_PEERS remain; otherwise all stay and the
    note says the set spans industries (the card shows each peer's SIC)."""
    if not target_sic:
        return pts, [], "target SIC unknown: peers not industry-checked"
    lv = [(p, sic_level(p.get("sic"), target_sic)) for p in pts]
    same = [p for p, l in lv if l is None or l >= SIC_MATCH_MIN]
    if len(same) >= relative.MIN_PEERS:
        dropped = [{"symbol": p["symbol"], "reason": f"other industry: SIC {p['sic'][0]} "
                    f"{p['sic'][1] or ''} vs {target_sic[0]}"} for p, l in lv if p not in same]
        return same, dropped, None
    return pts, [], (f"only {len(same)} of {len(pts)} peers share SIC group {target_sic[0][:2]} "
                     f"({target_sic[1] or ''}); all kept")


def build_model(t, symbols, today, memo=None, price_date=None, target_sic=None, **kw):
    """(model, excluded[{symbol, reason}]) from up to MAX_PEERS symbols."""
    memo = memo or PointMemo()
    pts, excluded = [], []
    for sym in symbols[:MAX_PEERS]:
        p, why = memo.get(sym, lambda s: peer_point(s, today, **kw))
        if p and price_date and relative._days(p["price_date"], price_date) > relative.PRICE_LAG_DAYS:
            p, why = None, f"close {p['price_date']} not within {relative.PRICE_LAG_DAYS} days of {price_date}"
        (pts.append(p) if p else excluded.append({"symbol": sym, "reason": why}))
    pts, dropped, note = match_industry(pts, target_sic)
    excluded += dropped
    model, why = relative.fit(pts)
    if model is None:
        return None, excluded, "; ".join(w for w in (why, note) if w)
    excluded += [{"symbol": s, "reason": "EV/Sales outlier (IQR fence)"} for s in model["outliers"]]
    model["as_of"] = today.isoformat()
    model["industry_note"] = note
    model["target_sic"] = target_sic
    return model, excluded, None


def _price_date(f):
    """The T-1 session the target's price closed on ("yahoo close YYYY-MM-DD (T-1)")."""
    prov = str((getattr(f, "provenance", None) or {}).get("price", ""))
    return prov.split(" ")[2] if prov.startswith("yahoo close ") else None


def _target_side(f, model, today):
    """The target's implied values from its CURRENT facts (`f` = FinancialFacts)."""
    x = {k: getattr(f, k, None) for k in ("currency", "revenue", "operating_income",
                                          "total_debt", "operating_leases", "cash",
                                          "revenue_annuals_dated", "shares_diluted")}
    # normalize._resolve_shares already put the count in the price's unit (ADRs), so
    # the target is valued exactly like a peer: price x diluted shares
    shares = f.shares_diluted
    tp, why = relative.point(f.ticker, x, f.price, today.isoformat(), today,
                             shares=shares, check_base=False)
    if tp is None:
        return {"target_excluded": why}
    net_debt = (f.total_debt or 0) + (f.operating_leases or 0) - (f.cash or 0)
    return relative.implied(model, tp, net_debt, shares)


def relative_value(f, fmp_key, today, prev=None, budget=None, memo=None, **kw):
    """(block, fmp_calls). `prev` = last stored block for this ticker (read-only)."""
    t = f.ticker
    calls, notes = 0, []
    model = None
    if prev and prev.get("model") and lastgood.usable(prev["model"].get("as_of"), today, MODEL_TTL_DAYS):
        model, excluded = prev["model"], prev.get("excluded", [])
        list_as_of, notes = prev.get("peer_list_as_of"), list(prev.get("notes") or [])
    else:
        symbols, list_as_of, note, calls = peer_list(t, fmp_key, today, budget, kw.pop("cache_dir", None),
                                                     kw.pop("fetch_list", None))
        notes += [note] if note else []
        model, excluded, why = build_model(t, symbols, today, memo, _price_date(f),
                                                     getattr(f, "sic", None), **kw) if symbols \
            else (None, [], None if notes else "no peers")
        if model is None:
            notes += [why] if why else []
            if prev and prev.get("model") and lastgood.usable(prev["model"].get("as_of"), today):
                model, excluded = prev["model"], prev.get("excluded", [])
                notes.append(f"peer fit from {model['as_of']} reused (this week's failed)")
    if model is None:
        return {"model": None, "excluded": excluded, "notes": notes,
                "peer_list_as_of": list_as_of}, calls
    return {"model": model, "excluded": excluded, "notes": notes,
            "peer_list_as_of": list_as_of, **_target_side(f, model, today)}, calls
