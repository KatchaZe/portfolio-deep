"""Sector scout data fetch: every member of a few Damodaran industries, read with the app's
own readers, written as one JSON per industry for the Sector scout mockup.

  python -B -m tools.scout_fetch [--out ..\\_scout] [--industry NAME ...] [--workers 4]
  python -B -m tools.scout_fetch --offline --facts-dir DIR --cache DIR   (no network at all)

Nothing here re-implements a reader. Membership and CIKs are pipeline.peers.Universe
(Damodaran indname, SEC ticker map, share-class and exact-name fallbacks, R32/R33); CY
revenue is the frames panel reading (frames_revenue.choose + rebucket, R34-R36, R41);
every company figure is sources.sec_edgar.extract on companyfacts completed by
sources.sec_gap (debt by _DebtBook, R42-R44, E58-E75); TTM EBIT is peers.normalized; the
T-1 close is peers._t1 (Yahoo, then Stooq); market cap and EV are relative.point;
industry beta and WACC are sources.damodaran. FMP is never called (guarded).

SEC's frames API has no ifrs-full frames, so an IFRS member's CY revenue is read from the
ifrs-full:Revenue facts in its own companyfacts by their SEC `frame` tag (the same facts
/api/xbrl/companyconcept serves), through the same choose + rebucket. USD only (R34).

Resumable: the SEC part of each member is cached for SEC_TTL_DAYS, the close per day, and
the app's frames / indname / ticker-map caches keep their own TTLs, all under --cache.
"""
import argparse
import collections
import concurrent.futures as futures
import datetime as dt
import json
import logging
import os
import re
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config                                                     # noqa: E402
from domain import frames_revenue, peer_panel, relative, trend    # noqa: E402
from pipeline import peers                                        # noqa: E402
from pipeline.cik import resolve_cik                              # noqa: E402
from sources import damodaran, fmp, sec_edgar, sec_gap            # noqa: E402
from sources import damodaran_companies as dc                     # noqa: E402
from sources.industries import securities_are_operating           # noqa: E402

log = logging.getLogger("portfolio.scout")

INDUSTRIES = ("Semiconductor", "Healthcare Products", "Software (System & Application)")
SEC_TTL_DAYS = 7
FY_KEEP = 6
STALE_BASE_DAYS = 200
MIN_REVENUE = 200e6
X_KEYS = ("currency", "accounting_standard", "latest_period_end", "revenue", "operating_income",
          "net_income", "shares_diluted", "total_debt", "operating_leases", "cash",
          "minority_interest", "equity", "revenue_annuals_dated")
# requests the app's frames reader makes per calendar year (4 revenue concepts, excise,
# ifrs USD, the other IFRS currencies), the Assets frame per panel year, the ticker map
FRAMES_PER_YEAR = len(dc.FRAMES_CONCEPTS) + 1 + 1 + len(dc.IFRS_OTHER_CURRENCIES)
SEC_PER_MEMBER = 2               # companyfacts + submissions (sec_gap); a missing filing adds 1-2
GUESS_SECS_PER_MEMBER = 2.5      # a guess: companyfacts is 1-7 MB; the log prints the measured rate
_CY = re.compile(r"^CY(\d{4})$")
UNTAGGED_DEBT = "หนี้สินรวม"     # sec_edgar._total_debt's note when the debt date is untagged


class Counter:
    def __init__(self):
        self.n, self.lock = collections.Counter(), threading.Lock()

    def add(self, k, v=1):
        with self.lock:
            self.n[k] += v


CALLS = Counter()


def _count_sec():
    real = sec_edgar._throttle

    def counted(min_interval):
        CALLS.add("sec")
        return real(min_interval)
    sec_edgar._throttle = counted


def _guard_fmp():
    def refused(*a, **k):
        CALLS.add("fmp")
        raise RuntimeError("scout_fetch never calls FMP")
    fmp._send = refused


def fld(value, source, reason=None):
    return {"value": value, "source": source if value is not None else None,
            "reason": None if value is not None else (reason or "not available")}


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
        json.dump(obj, fh, ensure_ascii=False, indent=1, default=str)
    os.replace(tmp, path)


# --------------------------------------------------------------------------- #
#  Pure: CY revenue from a filer's own facts (the IFRS path)                    #
# --------------------------------------------------------------------------- #
def cy_rows_from_facts(cf, as_of=None):
    """{CY: [revenue, end, concept, note]} for one filer from its companyfacts: per SEC
    `frame` tag (CY2024, not CY2024Q4I) the facts a frames call would return, in USD, read
    by frames_revenue.choose and rebucket exactly as damodaran_companies._frames reads the
    frames. A fact filed after `as_of` is left out."""
    facts = (cf or {}).get("facts") or {}
    by_year = {}
    for qname in frames_revenue.PRIORITY + (frames_revenue.EXCISE,):
        ns, concept = qname.split(":")
        for e in (((facts.get(ns) or {}).get(concept) or {}).get("units") or {}).get("USD") or []:
            m = _CY.match(str(e.get("frame") or ""))
            if not m or (as_of and str(e.get("filed") or "") > as_of):
                continue
            year = by_year.setdefault(int(m.group(1)), {})
            if qname not in year or str(e.get("filed")) > str(year[qname].get("filed")):
                year[qname] = {"val": e.get("val"), "end": e.get("end"), "accn": e.get("accn"),
                               "filed": e.get("filed")}
    rows = {}
    for y, entries in by_year.items():
        c = frames_revenue.choose(entries)
        if c:
            rows[y] = {0: [c["val"], c["end"], c["concept"], c["note"]]}
    return {y: r[0] for y, r in frames_revenue.rebucket(rows).items()}


# --------------------------------------------------------------------------- #
#  SEC part of one member (network: companyfacts, submissions)                  #
# --------------------------------------------------------------------------- #
def sec_part(ticker, cik, cf, as_of):
    """The member's SEC figures from completed companyfacts, JSON-ready."""
    x = sec_edgar.extract(cf, cash_excludes_securities=securities_are_operating(ticker))
    xn, addback = peers.normalized(x)
    tags = peer_panel.frame_tags(cf, as_of)
    rev_d, gp_d, cost_d, oi_d = (x.get(new) or x.get(old) for new, old in peer_panel.FY_INPUTS)
    rev, oi = trend._pairs(rev_d), trend._pairs(oi_d)
    gp, gp_basis = trend.gross_profit(gp_d, rev_d, cost_d)
    ic = x.get("ic_components_dated") or {}
    fy = []
    for end in sorted(rev, reverse=True)[:FY_KEEP]:
        legs = ic.get(end) or {}
        fy.append({"end": end, "cy": peer_panel.frames_cy(end, tags), "revenue": rev.get(end),
                   "gross_profit": gp.get(end), "ebit": oi.get(end), "equity": legs.get("equity"),
                   "debt": legs.get("debt") if legs else None, "debt_rule": legs.get("lt_source"),
                   "cash": legs.get("cash"), "ic_read": bool(legs)})
    g = cf.get("_sec_gap") or {}
    return {"cik": int(cik), "as_of": as_of, "x": {k: xn.get(k) for k in X_KEYS},
            "ebit_ttm_raw": x.get("operating_income"), "addback": addback or 0.0,
            "ebit_derived": bool(x.get("operating_income_derived")), "gp_basis": gp_basis, "fy": fy,
            "ifrs_cy": ({str(k): v for k, v in cy_rows_from_facts(cf, as_of).items()}
                        if x.get("accounting_standard") == "ifrs-full" else {}),
            "debt_notes": [f for f in x.get("xbrl_flags") or [] if UNTAGGED_DEBT in f],
            "fin_lease_in_debt": int(cik) in sec_edgar.FIN_LEASE_IN_DEBT,
            "flags": x.get("xbrl_flags") or [], "sec_gap": g or None, "sec_sic": cf.get("_sic"),
            "stale_cache": bool(cf.get("_stale_cache"))}


# --------------------------------------------------------------------------- #
#  Pure assembly: one member's output record                                    #
# --------------------------------------------------------------------------- #
def _age(end, today):
    try:
        return (today - dt.date.fromisoformat(str(end)[:10])).days
    except (TypeError, ValueError):
        return None


def _revenue_cy(meta, sec, frames, years):
    cik = meta.get("cik")
    out = {}
    for y in years:
        row = (frames.get(y) or {}).get(cik) if cik else None
        if row and peer_panel._pos(row[0]) is not None:
            src = f"SEC frames CY{y} {row[2] or 'largest concept (old cache)'}" + (f" [{row[3]}]" if row[3] else "")
            out[str(y)] = dict(fld(row[0], src), period_end=row[1])
            continue
        ifrs = (sec or {}).get("ifrs_cy", {}).get(str(y))
        if ifrs:
            out[str(y)] = dict(fld(ifrs[0], f"companyfacts {ifrs[2]} frame CY{y} (USD)"
                                   + (f" [{ifrs[3]}]" if ifrs[3] else "")), period_end=ifrs[1])
            continue
        if not cik:
            why = meta.get("cik_reason") or "no SEC CIK"
        elif sec is None:
            why = meta.get("sec_error") or "SEC facts not read"
        elif (sec["x"].get("currency") or "USD") != "USD":
            why = f"reports in {sec['x']['currency']}; R34 never converts a member's revenue"
        else:
            why = f"no USD revenue fact SEC framed as CY{y} (not filed, or the year sits in another frame)"
        out[str(y)] = dict(fld(None, None, why), period_end=None)
    return out


def assemble(meta, sec, price, frames, years, today):
    """The member record: every figure as {value, source, reason}. `meta` {ticker, name,
    cik, cik_how, cik_reason, domicile, sic}; `sec` a sec_part or None (meta.sec_error);
    `price` {close, day, src, reason}; `frames` {CY: {cik: row}}."""
    x = (sec or {}).get("x") or {}
    no_sec = meta.get("cik_reason") if not meta.get("cik") else (meta.get("sec_error") or "SEC facts not read")
    src_x = "sec_edgar.extract (companyfacts" + (" + sec_gap filing" if ((sec or {}).get("sec_gap") or {}).get("read") else "") + ")"

    def xf(k, source=src_x, reason=None):
        if sec is None:
            return fld(None, None, no_sec)
        return fld(x.get(k), source, reason)

    debt_reason = None
    if sec is not None and x.get("total_debt") is None:
        debt_reason = ("; ".join(sec["debt_notes"]) if sec["debt_notes"] else
                       "no debt concept filed in the 540 days before the latest balance sheet "
                       "(relative.point counts it as 0 in EV)")
    debt = dict(xf("total_debt", reason=debt_reason),
                rule=(("sec_edgar._total_debt: _DebtBook.read on the newest long-term-debt date"
                       + ("; finance leases inside debt per FIN_LEASE_IN_DEBT" if sec["fin_lease_in_debt"] else ""))
                      if sec else None))
    close, day = (price or {}).get("close"), (price or {}).get("day")
    p, why = (None, "no SEC facts") if sec is None else relative.point(
        meta["ticker"], x, close, day, today, check_base=False, need_growth=False, margin_floor=False)
    shares = x.get("shares_diluted")
    mcap = p["market_cap"] if p else (close * shares if close and shares else None)
    mcap_reason = None if mcap else ("no T-1 close: " + str((price or {}).get("reason")) if not close else
                                     "no diluted share count in SEC facts")
    ebit, rev = x.get("operating_income"), x.get("revenue")
    end = x.get("latest_period_end")
    age = _age(end, today)
    rec = {
        "ticker": meta["ticker"], "name": meta.get("name"), "domicile": meta.get("domicile"),
        "cik": fld(meta.get("cik"), f"pipeline.peers.Universe.members_of ({meta.get('cik_how')})",
                   meta.get("cik_reason")),
        "sic": fld(meta.get("sic"), "Damodaran indname SIC Code", "no SIC in Damodaran's row"),
        "sec_sic": (sec or {}).get("sec_sic"),
        "revenue_cy": _revenue_cy(meta, sec, frames, years),
        "ttm": {"period_end": end, "currency": x.get("currency"), "accounting_standard": x.get("accounting_standard"),
                "revenue": xf("revenue"),
                "ebit": xf("operating_income", "peers.normalized(sec_edgar.extract) one-time add-back",
                           "no operating income or pretax+interest filed"),
                "ebit_raw": fld((sec or {}).get("ebit_ttm_raw"), src_x, no_sec or "not filed"),
                "addback": (sec or {}).get("addback"), "ebit_derived": (sec or {}).get("ebit_derived"),
                "net_income": xf("net_income")},
        "balance": {"equity": xf("equity"), "debt": debt, "cash": xf("cash"),
                    "operating_leases": xf("operating_leases", reason="no operating lease filed"),
                    "minority_interest": xf("minority_interest", reason="none filed")},
        "fy": {"rows": (sec or {}).get("fy") or [],
               "source": ("sec_edgar.extract revenue/operating_income *_fy_dated; gross profit "
                          f"trend.gross_profit ({(sec or {}).get('gp_basis')}); equity/debt/cash "
                          "ic_components_dated at each FY end, debt_rule = concepts read; cy = "
                          "peer_panel.frames_cy") if sec else None,
               "reason": None if sec else no_sec},
        "market": {"price": dict(fld(close, (price or {}).get("src") and f"{price['src']} T-1 close (peers._t1)",
                                     (price or {}).get("reason")), date=day),
                   "shares": dict(xf("shares_diluted", reason="no diluted share count filed"),
                                  note="SEC diluted weighted shares, as relative.point uses"),
                   "market_cap": fld(mcap, "relative.point: price x SEC diluted shares", mcap_reason),
                   "ev": fld(p["ev"] if p else None,
                             "relative.point: mcap + debt + operating leases - cash + minority interest",
                             why)},
        "checks": {"latest_period_age_days": age, "stale_base": bool(age is not None and age > STALE_BASE_DAYS),
                   "revenue_ge_200m": bool(rev and rev >= MIN_REVENUE) if rev is not None else None,
                   "ebit_positive": bool(ebit > 0) if ebit is not None else None,
                   "usd": (x.get("currency") or "USD") == "USD" if sec else None,
                   "sec_gap": (sec or {}).get("sec_gap"), "stale_cache": (sec or {}).get("stale_cache"),
                   "flags": (sec or {}).get("flags") or []},
    }
    return rec


# --------------------------------------------------------------------------- #
#  Fetch steps (network), each cached                                           #
# --------------------------------------------------------------------------- #
class Fetcher:
    def __init__(self, args, today):
        self.args, self.today, self.as_of = args, today, today.isoformat()
        self.cache = args.cache

    def _facts(self, ticker, cik):
        if self.args.facts_dir:
            for name in (f"companyfacts_{ticker}.json", f"companyfacts_{str(cik).zfill(10)}.json"):
                got = _read(os.path.join(self.args.facts_dir, name))
                if got:
                    return got
            raise FileNotFoundError(f"no companyfacts for {ticker} in --facts-dir")
        CALLS.add("companyfacts")
        return sec_edgar.fetch_companyfacts(
            cik, config.SEC_USER_AGENT, min_interval=config.SEC_MIN_INTERVAL,
            cache_dir=os.path.join(self.cache, "companyfacts") if self.args.keep_facts else None,
            ttl_hours=SEC_TTL_DAYS * 24)

    def sec(self, ticker, cik):
        """(sec_part, error, from_cache)."""
        path = os.path.join(self.cache, "members_sec", f"{int(cik)}.json")
        got = _read(path)
        if got and not self.args.refresh and _age(got.get("as_of"), self.today) is not None \
                and _age(got["as_of"], self.today) < SEC_TTL_DAYS:
            return got.get("part"), got.get("error"), True
        try:
            cf = self._facts(ticker, cik)
            if not self.args.offline:
                cf = sec_gap.complete(cik, cf, config.SEC_USER_AGENT, cache_dir=self.cache,
                                      throttle=lambda: sec_edgar._throttle(config.SEC_MIN_INTERVAL))
            part, err = sec_part(ticker, cik, cf, self.as_of), None
        except Exception as e:
            part, err = None, f"SEC facts unavailable ({type(e).__name__}: {str(e)[:80]})"
        if not self.args.offline or part:
            _write(path, {"as_of": self.as_of, "part": part, "error": err})
        return part, err, False

    def price(self, ticker):
        path = os.path.join(self.cache, "prices", self.as_of, f"{ticker}.json")
        got = _read(path)
        if got:
            return got, True
        if self.args.offline:
            return {"close": None, "day": None, "src": None, "reason": "offline run: no price fetched"}, True
        CALLS.add("price")
        close, day, src = peers._t1(ticker, self.as_of)
        out = {"close": close, "day": day, "src": src,
               "reason": None if close else "no T-1 close from Yahoo or Stooq"}
        if close:                            # a miss is retried on the next run
            _write(path, out)
        return out, False


def damodaran_block(industry, cache):
    def labelled(fetch, cache_file, snapshot):
        before = _read(os.path.join(cache, cache_file))
        table = fetch(cache_dir=cache)
        after = _read(os.path.join(cache, cache_file))
        if after and (not before or after.get("fetched") != before.get("fetched")):
            how = "live " + after.get("url", "")
        elif after:
            how = "cache from " + dt.datetime.fromtimestamp(after.get("fetched", 0)).date().isoformat()
        else:
            how = snapshot
        return table.get(damodaran._norm(industry)), how
    w, w_how = labelled(damodaran.fetch_wacc_table, damodaran.WACC_CACHE_FILE, None)
    b, b_how = labelled(damodaran.fetch_beta_table, damodaran.BETA_CACHE_FILE,
                        f"bundled snapshot {damodaran._snap.AS_OF}")
    return {"wacc": fld(w, f"Damodaran wacc.html Cost of Capital ({w_how})",
                        "wacc.html not reachable and no damodaran_wacc.json cache"),
            "unlevered_beta_cash_corrected": fld(b, f"Damodaran Betas.html ({b_how})",
                                                 "industry not in the beta table")}


# --------------------------------------------------------------------------- #
#  Run                                                                          #
# --------------------------------------------------------------------------- #
def estimate(n_uncached, frames_cached):
    sec = n_uncached * SEC_PER_MEMBER
    frames = 0 if frames_cached else (dc.PANEL_YEARS + 1) * FRAMES_PER_YEAR + dc.PANEL_YEARS + 1
    return {"sec_requests": sec + frames, "yahoo_requests": n_uncached,
            "minutes_floor_by_sec_throttle": round((sec + frames) * config.SEC_MIN_INTERVAL / 60, 1),
            "minutes_guess": round((n_uncached * GUESS_SECS_PER_MEMBER / 2 + frames * 1.0) / 60, 1)}


def _frames_cached(cache, years):
    return all(os.path.exists(os.path.join(cache, f"sec_frames_revenue_CY{y}.json")) for y in years)


def run(args):
    today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()
    os.makedirs(args.cache, exist_ok=True)
    config.CACHE_DIR = args.cache            # every app reader caches under --cache
    years = dc.panel_years(today)
    say = lambda s: print(s, flush=True)     # noqa: E731
    lines = [f"scout_fetch {dt.datetime.now().isoformat(timespec='seconds')} as_of {today} "
             f"offline={args.offline} panel CY{years[0]}-CY{years[-1]}"]
    frames_were_cached = _frames_cached(args.cache, years)
    uni = peers.Universe.lazy(today, args.cache, config.SEC_USER_AGENT)
    t0 = time.time()
    if not uni.rows:
        say("Damodaran company list unavailable: " + "; ".join(uni.notes))
        return 2
    n_all = sum(len(dc.members(uni.rows, i)) + len(dc.foreign_members(uni.rows, i)) for i in args.industry)
    lines.append(f"whole run, upper bound (every member uncached): {n_all} members; "
                 f"estimate {estimate(0 if args.offline else n_all, frames_were_cached or args.offline)}")
    say(lines[-1])
    frames = uni.frames
    say(f"universe loaded in {time.time() - t0:.0f}s (frames years {sorted(frames)})")
    names = uni.us_names
    fetch = Fetcher(args, today)
    for industry in args.industry:
        members, foreign, unresolved = uni.members_of(industry)
        metas = []
        for dom, group in (("us", members), ("foreign", foreign)):
            for t, cik in sorted(group.items()):
                row = uni.rows.get(t) or [None, None, None]
                exact = resolve_cik(t)[0]
                metas.append({"ticker": t, "name": names.get(t), "cik": int(cik) if cik else None,
                              "domicile": dom, "sic": row[1] or None, "country": row[2],
                              "cik_how": None if not cik else "ticker" if exact and int(exact) == int(cik)
                              else "domain/member_cik share-class or exact-name fallback",
                              "cik_reason": None if cik else (unresolved.get(t) or (
                                  "not in SEC's ticker map, and no share-class spelling or exact "
                                  "SEC frames name matched (members_of keeps a reason for US members only)"))})
        if args.limit:
            metas = metas[:args.limit]
        uncached = sum(1 for m in metas if m["cik"] and not _read(
            os.path.join(args.cache, "members_sec", f"{int(m['cik'])}.json")))
        est = estimate(0 if args.offline else uncached, frames_were_cached)
        lines.append(f"[{industry}] members {len(metas)} (us {len(members)}, foreign {len(foreign)}); "
                     f"SEC part to fetch {uncached}; estimate {est}")
        say(lines[-1])
        frames_were_cached = True
        recs, t1, done = [None] * len(metas), time.time(), [0]

        def one(i):
            m = metas[i]
            sec, err, hit = (None, None, True) if not m["cik"] else fetch.sec(m["ticker"], m["cik"])
            m["sec_error"] = err
            price, _ = fetch.price(m["ticker"])
            recs[i] = assemble(m, sec, price, frames, years, today)
            done[0] += 1
            el = time.time() - t1
            eta = el / done[0] * (len(metas) - done[0])
            say(f"  [{done[0]}/{len(metas)}] {m['ticker']:<8} {'cache' if hit else 'fetch'} "
                f"{'ok' if sec else 'no-sec'}  {el:.0f}s elapsed, ETA {eta / 60:.1f} min  "
                f"(SEC calls {CALLS.n['sec']})")

        with futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
            for f in [ex.submit(one, i) for i in range(len(metas))]:
                f.result()
        out = {"industry": industry, "as_of": today.isoformat(), "panel_years": years,
               "damodaran": damodaran_block(industry, args.cache),
               "sic_counts": dict(collections.Counter(m["sic"] for m in metas).most_common()),
               "notes": list(uni.frame_notes) + list(uni.name_notes) + list(uni.notes),
               "members": recs}
        out["coverage"] = coverage(recs, years)
        path = os.path.join(args.out, _slug(industry) + ".json")
        _write(path, out)
        lines.append(f"[{industry}] wrote {path} in {(time.time() - t1) / 60:.1f} min; coverage "
                     + json.dumps(out["coverage"], ensure_ascii=False))
        say(lines[-1])
    lines.append(f"requests: SEC {CALLS.n['sec']} (companyfacts {CALLS.n['companyfacts']}), "
                 f"price {CALLS.n['price']}, FMP {CALLS.n['fmp']}; total {(time.time() - t0) / 60:.1f} min")
    say(lines[-1])
    with open(os.path.join(args.out, "_scout_summary.log"), "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n\n")
    return 0


def coverage(recs, years):
    c = collections.Counter()
    for r in recs:
        c["members"] += 1
        c["cik"] += r["cik"]["value"] is not None
        c["sec"] += r["fy"]["reason"] is None
        for y in years:
            c[f"revenue_CY{y}"] += r["revenue_cy"][str(y)]["value"] is not None
        c["ebit_ttm"] += r["ttm"]["ebit"]["value"] is not None
        c["equity"] += r["balance"]["equity"]["value"] is not None
        c["debt_known"] += r["balance"]["debt"]["value"] is not None
        c["cash"] += r["balance"]["cash"]["value"] is not None
        c["price"] += r["market"]["price"]["value"] is not None
        c["market_cap"] += r["market"]["market_cap"]["value"] is not None
        c["ev"] += r["market"]["ev"]["value"] is not None
        c["revenue_ge_200m"] += bool(r["checks"]["revenue_ge_200m"])
        c["value_tier_eligible"] += bool(r["checks"]["revenue_ge_200m"] and r["checks"]["ebit_positive"])
        c["stale_base"] += r["checks"]["stale_base"]
    return dict(c)


def _slug(s):
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=os.path.join("..", "_scout"))
    ap.add_argument("--cache", help="default: <out>/_cache")
    ap.add_argument("--industry", action="append", help=f"repeatable; default {', '.join(INDUSTRIES)}")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, help="first N members per industry (a trial run)")
    ap.add_argument("--today", help="as-of date YYYY-MM-DD (default today)")
    ap.add_argument("--refresh", action="store_true", help="refetch SEC parts younger than the TTL")
    ap.add_argument("--keep-facts", action="store_true", help="also cache raw companyfacts (~3 MB each)")
    ap.add_argument("--offline", action="store_true", help="cut the network; caches and --facts-dir only")
    ap.add_argument("--facts-dir", help="read companyfacts_<TICKER|CIK10>.json from here")
    args = ap.parse_args(argv)
    args.industry = args.industry or list(INDUSTRIES)
    args.cache = args.cache or os.path.join(args.out, "_cache")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    os.makedirs(args.out, exist_ok=True)
    logging.basicConfig(filename=os.path.join(args.out, "_scout_fetch.log"), level=logging.WARNING,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s", encoding="utf-8")
    if args.offline:
        from tests import _harness
        _harness.cut_network()
    elif "your-email@example.com" in config.SEC_USER_AGENT:
        print("SEC_CONTACT_EMAIL is not set; SEC rejects the default User-Agent. Set it and rerun.")
        return 2
    _count_sec()
    _guard_fmp()
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
