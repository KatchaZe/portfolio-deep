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
import math
import os
import threading

import config
from types import SimpleNamespace

from domain import moat_trend, one_time, peer_panel, relative, pricing
from pipeline import batch, lastgood
from pipeline.cik import resolve_cik
from sources import fmp, sec_edgar, sec_gap, stooq, yahoo
from sources.industries import securities_are_operating

log = logging.getLogger("portfolio.peers")

MAX_PEERS = 12             # FMP lists ~10; the SIC match below may drop some
SIC_MATCH_MIN = 2          # digits of SIC a peer must share with the target
LIST_TTL_DAYS = 30
MODEL_TTL_DAYS = 7
# bump when the way peers are chosen changes: a stored fit from the old method is not
# reused for its remaining week (live 2026-10-24: MSFT/NVDA kept their FMP-peer fits
# for days after the Damodaran list went in)
# 3 (2026-11-16): stored points carry the peer panel's fields (model["panel_points"])
# 4 (2026-11-17): points' fiscal years by year-end date across revenue concepts (GOOGL,
#   JNJ, EXEL had no FY3); health-insurer peers count cash only
MODEL_VERSION = 4
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
        # a health insurer's short-term investments back claim reserves: not cash
        # (user rule 2026-10-18), on a peer exactly as on the target
        x = sec_edgar.extract(cf, cash_excludes_securities=securities_are_operating(sym))
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
        p.update({k: x.get(k) for k in peer_panel.EV_LEGS})
        p.update(peer_panel.point_fields(x, today.isoformat(),
                                         peer_panel.frame_tags(cf, today.isoformat())))
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


def collect_points(symbols, today, memo=None, price_date=None, source="fmp", **kw):
    """(points, excluded) for up to MAX_PEERS symbols, each point tagged with its source."""
    memo = memo or PointMemo()
    pts, excluded = [], []
    for sym in symbols[:MAX_PEERS]:
        p, why = memo.get(sym, lambda s: peer_point(s, today, **kw))
        if p and price_date and relative._days(p["price_date"], price_date) > relative.PRICE_LAG_DAYS:
            p, why = None, f"close {p['price_date']} not within {relative.PRICE_LAG_DAYS} days of {price_date}"
        if p:
            pts.append(dict(p, source=source))
        else:
            excluded.append({"symbol": sym, "reason": why, "source": source})
    return pts, excluded


def fit_points(pts, excluded, today, target_sic=None):
    """(model, excluded, why). Damodaran-list peers are his industry by construction;
    the SIC match applies to the FMP fill-ins only."""
    ours = [p for p in pts if p.get("source") == "damodaran"]
    fmp_pts = [p for p in pts if p.get("source") != "damodaran"]
    fmp_pts, dropped, note = match_industry(fmp_pts, target_sic) if fmp_pts else (fmp_pts, [], None)
    pts = ours + fmp_pts
    excluded = excluded + dropped
    model, why = relative.fit(pts)
    if model is None:
        return None, excluded, "; ".join(w for w in (why, note) if w)
    excluded += [{"symbol": s, "reason": "EV/Sales outlier (IQR fence)"} for s in model["outliers"]]
    model["as_of"] = today.isoformat()
    model["version"] = MODEL_VERSION
    # every usable point, the EV/Sales outliers included: the panel flags its own outliers
    model["panel_points"] = [peer_panel.compact(p) for p in pts]
    model["industry_note"] = note
    same = len(ours) + sum(1 for p in fmp_pts if (sic_level(p.get("sic"), target_sic) or 0) >= SIC_MATCH_MIN)
    if note and "all kept" in note and same < len(pts) / 2:
        model["weak"] = True                    # mostly other industries: not a peer multiple
    model["target_sic"] = target_sic
    return model, excluded, None


def build_model(t, symbols, today, memo=None, price_date=None, target_sic=None, **kw):
    """(model, excluded, why) from FMP-listed symbols alone (the fallback path)."""
    pts, excluded = collect_points(symbols, today, memo, price_date, "fmp", **kw)
    return fit_points(pts, excluded, today, target_sic)


class Universe:
    """Damodaran's company list, SEC revenue frames and psdata, loaded once per run.
    Every part is optional: a missing part only removes what it feeds, with a note."""

    def __init__(self, rows=None, revenue_by_cik=None, psdata=None, notes=(), as_of=None, loader=None,
                 frames=None, frames_loader=None, assets=None, global_rows=None, global_loader=None):
        self._data = None if loader else (rows or {}, revenue_by_cik or {}, psdata or {},
                                          [n for n in notes if n], as_of)
        self._loader, self._lock = loader, threading.Lock()
        # the peer panel's five calendar years of frames (revenue, and the Assets filers
        # that count toward coverage, R20), loaded only when a panel asks
        self._frames = None if frames_loader else (frames or {}, [], assets or {})
        self._frames_loader = frames_loader
        # Damodaran's non-US rows: a home-listed target's own SIC only (R29)
        self._global = None if global_loader else (global_rows or [], [])
        self._global_loader = global_loader

    def _get(self):
        with self._lock:
            if self._data is None:
                try:
                    self._data = self._loader()
                except Exception as e:          # never fails the refresh
                    log.warning("peer universe failed to load: %s", e)
                    self._data = ({}, {}, {}, [f"peer universe unavailable ({type(e).__name__})"], None)
            return self._data

    def _get_frames(self):
        with self._lock:
            if self._frames is None:
                try:
                    self._frames = self._frames_loader()
                except Exception as e:          # never fails the refresh
                    log.warning("peer panel frames failed to load: %s", e)
                    self._frames = ({}, [f"SEC revenue frames unavailable ({type(e).__name__})"], {})
            return self._frames

    def _get_global(self):
        with self._lock:
            if self._global is None:
                try:
                    rows, note = self._global_loader()
                    self._global = (rows or [], [note] if note else [])
                except Exception as e:          # never fails the refresh
                    log.warning("Damodaran global list failed to load: %s", e)
                    self._global = ([], [f"Damodaran global list unavailable ({type(e).__name__})"])
            return self._global

    global_rows = property(lambda self: self._get_global()[0])
    global_notes = property(lambda self: self._get_global()[1])
    frames = property(lambda self: self._get_frames()[0])
    frame_notes = property(lambda self: self._get_frames()[1])
    assets = property(lambda self: (self._get_frames()[2:] or ({},))[0])
    rows = property(lambda self: self._get()[0])
    rev = property(lambda self: self._get()[1])
    psdata = property(lambda self: self._get()[2])
    notes = property(lambda self: self._get()[3])
    as_of = property(lambda self: self._get()[4])

    @classmethod
    def lazy(cls, today, cache_dir=None, user_agent=None):
        """Loaded on first use (one download shared by every ticker in the run)."""
        def load():
            from sources import damodaran, damodaran_companies as dc
            cd = cache_dir or config.CACHE_DIR
            ua = user_agent or config.SEC_USER_AGENT
            rows, n1 = dc.load_indname(cd, ua)
            rev, n2 = dc.load_revenue_frames(cd, dc.frames_year(today), ua)
            # Damodaran republishes every January: the vintage is this year's January
            # once February starts (his update lands in the first week)
            ps = damodaran.fetch_psdata(cd)
            vintage = (f"{damodaran._snap.AS_OF} (bundled)" if ps.get("snapshot") else
                       f"{today.year if today.month >= 2 else today.year - 1}-01")
            return rows or {}, rev or {}, ps, [n for n in (n1, n2) if n], vintage
        def load_frames():
            from sources import damodaran_companies as dc
            cd, ua = cache_dir or config.CACHE_DIR, user_agent or config.SEC_USER_AGENT
            rev, n1 = dc.load_revenue_frames_years(cd, today, ua)
            assets, n2 = dc.load_assets_frames_years(cd, today, ua)
            return rev, n1 + n2, assets
        def load_global():
            from sources import damodaran_companies as dc
            return dc.load_global_sic(cache_dir or config.CACHE_DIR, user_agent or config.SEC_USER_AGENT)
        return cls(loader=load, frames_loader=load_frames, global_loader=load_global)

    def target_sic(self, f):
        """{code, source, reason} for the target (R25, R29): the US rows of Damodaran's list,
        else a unique name-and-country match in its non-US rows, else none with a reason."""
        from domain import global_sic
        from sources import country_risk
        row = (self.rows or {}).get(f.ticker.upper())
        if row:
            return {"code": row[1], "source": peer_panel.SIC_SOURCE, "ticker": f.ticker.upper(), "reason": None}
        iso = str(getattr(f, "country", None) or "")
        r = global_sic.resolve(self.global_rows, getattr(f, "company", None),
                               country_risk.ISO2.get(iso.upper(), iso))
        return {"code": r["sic"], "source": r["source"], "ticker": r["ticker"], "reason": r["reason"]}

    def industry_of(self, f):
        """(Damodaran industry, basis): his own classification when he lists the
        ticker in the US, else the app's mapping (ADRs list on their home exchange)."""
        row = self.rows.get(f.ticker.upper())
        if row:
            return row[0], "Damodaran company list"
        ind = getattr(f, "damodaran_industry", None)
        return (ind, "app industry mapping (not US-listed in Damodaran's list)") if ind else (None, None)

    def candidates(self, f, industry, k=MAX_PEERS):
        """(tickers nearest the target's revenue, band note). 0.1-10x first; a firm
        too large or small for that band widens to 0.02-50x, and the card says so."""
        if not (industry and self.rows and self.rev and f.revenue):
            return [], None
        from sources import damodaran_companies as dc
        sized = []
        for tk in dc.members(self.rows, industry, exclude=(f.ticker,)):
            cik, _ = resolve_cik(tk)
            r = self.rev.get(int(cik)) if cik else None
            if r and r > 0:
                sized.append((abs(math.log(r / f.revenue)), tk, r))
        sized.sort()
        for lo, hi, note in ((0.1, 10, None), (0.02, 50, "size band widened to 0.02-50x revenue")):
            pick = [tk for d, tk, r in sized if lo <= r / f.revenue <= hi][:k]
            if len(pick) >= relative.MIN_PEERS:
                return pick, note
        return [tk for d, tk, r in sized if 0.02 <= r / f.revenue <= 50][:k], \
            "fewer than 5 Damodaran peers within 0.02-50x revenue"

    def industry_block(self, f, industry, basis, target, net_debt):
        """The target valued at its industry's EV/Sales (psdata), or None."""
        from sources import damodaran
        m = damodaran.industry_multiple(self.psdata, industry)
        if not m or not target:
            return None
        out = relative.industry_implied(target, m["ev_sales"], m["margin"], net_debt,
                                        f.shares_diluted)
        return dict(out, industry=industry, basis=basis, n=m["n"], as_of=self.as_of)


def _price_date(f):
    """The T-1 session the target's price closed on ("yahoo close YYYY-MM-DD (T-1)")."""
    prov = str((getattr(f, "provenance", None) or {}).get("price", ""))
    return prov.split(" ")[2] if prov.startswith("yahoo close ") else None


def _target(f, today):
    """(point, net_debt, why) for the target from its CURRENT facts."""
    x = {k: getattr(f, k, None) for k in ("currency", "revenue", "operating_income",
                                          "total_debt", "operating_leases", "cash",
                                          "minority_interest",
                                          "revenue_annuals_dated", "shares_diluted")}
    # normalize._resolve_shares already put the count in the price's unit (ADRs), so
    # the target is valued exactly like a peer: price x diluted shares
    tp, why = relative.point(f.ticker, x, f.price, today.isoformat(), today,
                             shares=f.shares_diluted, check_base=False, need_growth=False,
                             margin_floor=False)
    net_debt = ((f.total_debt or 0) + (f.operating_leases or 0) - (f.cash or 0)
                + (getattr(f, "minority_interest", None) or 0))
    return tp, net_debt, why


def relative_value(f, fmp_key, today, prev=None, budget=None, memo=None, universe=None, **kw):
    """(block, fmp_calls). `prev` = last stored block for this ticker (read-only).
    `universe` = Universe.load(...) in production; None = FMP peers only (tests)."""
    t = f.ticker
    calls, notes = 0, []
    universe = universe or Universe()
    industry, basis = universe.industry_of(f)
    from sources.industries import FINANCIAL_INDUSTRIES
    if industry in FINANCIAL_INDUSTRIES:
        # a lender's debt is its raw material, so EV and EV/Sales mean nothing (SOFI read
        # 18.9x on the watchlist, live 2026-10-24); the excess-return model values it
        return {"model": None, "excluded": [], "industry": None, "peer_list_as_of": None,
                "notes": [f"{industry}: EV/Sales does not apply to a financial firm"]}, 0
    tp, net_debt, t_why = _target(f, today)
    ind = universe.industry_block(f, industry, basis, tp, net_debt) if tp else None
    cache_dir, fetch_list = kw.pop("cache_dir", None), kw.pop("fetch_list", None)
    model, excluded, list_as_of = None, [], None
    same_method = bool(prev and prev.get("model") and prev["model"].get("version") == MODEL_VERSION)
    if same_method and lastgood.usable(prev["model"].get("as_of"), today, MODEL_TTL_DAYS):
        model, excluded = prev["model"], prev.get("excluded", [])
        list_as_of, notes = prev.get("peer_list_as_of"), list(prev.get("notes") or [])
    else:
        notes += universe.notes
        cands, band = universe.candidates(f, industry)
        notes += [band] if band else []
        pts, excluded = collect_points(cands, today, memo, _price_date(f), "damodaran", **kw)
        usable = len(relative.drop_outliers(pts)[0])
        if usable < relative.MIN_PEERS:
            symbols, list_as_of, note, calls = peer_list(t, fmp_key, today, budget, cache_dir, fetch_list)
            notes += [note] if note else []
            have = {p["symbol"] for p in pts} | {e["symbol"] for e in excluded}
            more, ex2 = collect_points([s for s in symbols if s not in have], today, memo,
                                       _price_date(f), "fmp", **kw)
            if cands:
                notes.append(f"{usable} usable Damodaran peers: filled from the FMP list")
            pts, excluded = pts + more, excluded + ex2
        if pts or excluded:
            model, excluded, why = fit_points(pts, excluded, today, getattr(f, "sic", None))
        else:
            why = None if notes else "no peers"
        if model is None:
            notes += [why] if why else []
            if same_method and lastgood.usable(prev["model"].get("as_of"), today):
                model, excluded = prev["model"], prev.get("excluded", [])
                notes.append(f"peer fit from {model['as_of']} reused (this week's failed)")
    block = {"model": model, "excluded": excluded, "notes": notes, "peer_list_as_of": list_as_of,
             "industry": ind}
    if tp is None:
        block["target_excluded"] = t_why
    elif model is not None:
        block.update(relative.implied(model, tp, net_debt, f.shares_diluted))
    return block, calls


TARGET_PANEL_FIELDS = ("currency", "accounting_standard", "revenue", "operating_income", "net_income",
                       "revenue_annuals_dated", "gross_profit_annuals_dated",
                       "cost_of_revenue_annuals_dated", "operating_income_annuals_dated",
                       "revenue_fy_dated", "gross_profit_fy_dated", "cost_of_revenue_fy_dated",
                       "operating_income_fy_dated")


def _target_panel_point(f, today):
    """(point, why): the target on the panel's clocks from its CURRENT facts (EBIT
    already normalized), or (None, the reason it cannot be put on the common clock)."""
    tp, _nd, why = _target(f, today)
    if tp is None:
        return None, why
    x = {k: getattr(f, k, None) for k in TARGET_PANEL_FIELDS}
    # the newest quarter the TTM sums; a 20-F filer has no SEC quarters and its TTM end
    # (the quarterly fallback's, after FX) is the facts' fiscal_year
    q = getattr(f, "revenue_quarters", None)
    end = max(q) if isinstance(q, dict) and q else getattr(f, "fiscal_year", None)
    gaap = getattr(f, "operating_income_gaap", None)
    addback = (round((f.operating_income - gaap) / f.revenue, 4)
               if gaap is not None and f.operating_income is not None and f.revenue else 0.0)
    # _target stamps the as-of date; the price is the T-1 close and the row says which day
    return dict(tp, **{k: getattr(f, k, None) for k in peer_panel.EV_LEGS},
                symbol=f.ticker, source="target", period_end=str(end)[:10] if end else None,
                addback=addback, price_date=_price_date(f) or tp.get("price_date"),
                **peer_panel.point_fields(x, today.isoformat())), None


def panel_and_moat(f, val, block, universe, today):
    """(peer_panel, moat_trend) for one ticker. Display only: reads the relative block
    and the facts, writes nothing back into either, and no score reads its output
    (invariant PP1). The peers are the stored panel points of this week's fit; the
    industry share uses Damodaran's industry members only."""
    from sources import damodaran_companies as dc
    as_of = today.isoformat()
    block = block or {}
    industry, basis = universe.industry_of(f)
    target, target_why = _target_panel_point(f, today)
    pts = (block.get("model") or {}).get("panel_points")
    share = None
    # one SIC source for the target, every peer and the sub-group (SPEC R25)
    sics = {t: row[1] for t, row in (universe.rows or {}).items()}
    tsic = universe.target_sic(f)
    if tsic["source"] != peer_panel.SIC_SOURCE and tsic["code"]:
        sics[f.ticker.upper()] = tsic["code"]      # the target only; members stay US rows (R29)
    if industry:
        members = {t: resolve_cik(t)[0] for t in dc.members(universe.rows, industry)}
        foreign = {t: resolve_cik(t)[0] for t in dc.foreign_members(universe.rows, industry)}
        tcik, _ = resolve_cik(f.ticker)
        years = dc.panel_years(today)
        share = peer_panel.industry_share(members, universe.frames, tcik, years, as_of,
                                          foreign, universe.assets)
        share["sub_group"] = peer_panel.sub_group(members, universe.frames, tcik, years, as_of, foreign,
                                                  universe.assets, sics, f.ticker)
    notes = list(universe.frame_notes) + list(block.get("notes") or [])
    if tsic["source"] != peer_panel.SIC_SOURCE:
        notes += list(universe.global_notes)
    panel = peer_panel.build(target, pts, as_of, industry, basis, share,
                             block.get("excluded") or [], notes, target_why, sics, tsic)
    kept = peer_panel.peer_set(target, pts, as_of)[0] if pts else []
    km = getattr(val, "key_metrics", None) or {}
    return panel, moat_trend.build(f, target, kept, share, km.get("wacc_pct"), km.get("spread_pct"), sics)
