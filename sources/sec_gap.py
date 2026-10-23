"""
sec_gap — fill a filing that SEC companyfacts has not ingested yet, from the filing's
own XBRL instance.

Found live on 2026-10-23: the 10-Qs HUM, F, CTSH, BIIB and NXPI filed on 2026-07-28/29
are listed in the submissions feed, yet companyfacts carries 0 facts from them (Q1 has
240-320 each). Every number then reads one quarter stale with nothing to say so.

complete() compares the newest periodic filing in the submissions feed with the
accession numbers inside companyfacts. When the newest is missing it reads that
filing's instance document (the same `_htm.xml` revenue_geo reads), keeps the facts
with NO dimensions (the consolidated figures companyfacts publishes), and appends them
in companyfacts' own shape, so sec_edgar.extract runs unchanged. The result carries
`_sec_gap` = {form, filed, period, accn, read, facts | error} for the flags.

Pure parts (latest_filing, gap, parse_facts, merge) are separated from the fetch.
"""
import copy
import json
import logging
import os
import time
import xml.etree.ElementTree as ET

from sources.revenue_geo import _instance_url

log = logging.getLogger("portfolio.sec_gap")

PERIODIC = ("10-Q", "10-K", "20-F", "40-F")
SUBMISSIONS_TTL_HOURS = 12
GAP_WINDOW_DAYS = 400      # every periodic filing of the last ~5 quarters is checked
MAX_GAPS = 6
PARTIAL_FACTS = 50         # fewer facts than this from one 10-Q/10-K = not ingested
NAMESPACES = (("us-gaap", "us-gaap"), ("ifrs-full", "ifrs-full"), ("/dei/", "dei"))


def periodic_filings(sub_json):
    """Periodic filings in an SEC submissions document, newest first."""
    r = ((sub_json or {}).get("filings") or {}).get("recent") or {}
    prim = r.get("primaryDocument") or []
    return [{"form": form, "filed": r["filingDate"][i], "period": r["reportDate"][i],
             "accn": r["accessionNumber"][i], "primary": prim[i] if i < len(prim) else ""}
            for i, form in enumerate(r.get("form") or []) if form in PERIODIC]


def latest_filing(sub_json):
    f = periodic_filings(sub_json)
    return f[0] if f else None


def fact_counts(cf):
    """{accession: number of facts companyfacts carries from it}."""
    out = {}
    for ns in (cf.get("facts") or {}).values():
        for node in ns.values():
            for arr in (node.get("units") or {}).values():
                for x in arr:
                    out[x.get("accn")] = out.get(x.get("accn"), 0) + 1
    return out


def accessions(cf):
    return set(fact_counts(cf))


def gaps(sub_json, cf):
    """Recent periodic filings companyfacts has not (fully) ingested, OLDEST first.

    "Recent" = filed within GAP_WINDOW_DAYS of the newest periodic filing, so a Q2 that
    was skipped stays visible after Q3 lands. "Not fully" = fewer than PARTIAL_FACTS
    facts: a 10-Q carries 240-420 (live, 2026-10-23), so a handful means the filing
    was only partly ingested."""
    fs = periodic_filings(sub_json)
    if not fs:
        return []
    newest = fs[0]["filed"]
    counts = fact_counts(cf)
    recent = [f for f in fs if _days(f["filed"], newest) <= GAP_WINDOW_DAYS][:MAX_GAPS]
    return [f for f in reversed(recent) if counts.get(f["accn"], 0) < PARTIAL_FACTS]


def gap(sub_json, cf):
    """The newest filing among gaps(), or None."""
    g = gaps(sub_json, cf)
    return g[-1] if g else None


def _days(a, b):
    import datetime as dt
    try:
        return abs((dt.date.fromisoformat(a[:10]) - dt.date.fromisoformat(b[:10])).days)
    except (TypeError, ValueError):
        return 10 ** 6


def _ns(uri):
    for needle, name in NAMESPACES:
        if needle in (uri or ""):
            return name
    return None


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _measure(el):
    m = (el.text or "").strip()
    m = m.rsplit(":", 1)[-1]
    return m.upper() if len(m) == 3 and m.isalpha() else m


def _units(root):
    out = {}
    for u in root:
        if _local(u.tag) != "unit":
            continue
        num = [e for e in u.iter() if _local(e.tag) == "unitNumerator"]
        den = [e for e in u.iter() if _local(e.tag) == "unitDenominator"]
        if num and den:
            n = [_measure(e) for e in num[0].iter() if _local(e.tag) == "measure"]
            d = [_measure(e) for e in den[0].iter() if _local(e.tag) == "measure"]
            out[u.get("id")] = f"{n[0]}/{d[0]}" if n and d else None
        else:
            ms = [_measure(e) for e in u.iter() if _local(e.tag) == "measure"]
            out[u.get("id")] = ms[0] if ms else None
    return out


def _contexts(root):
    """{id: (start or None, end)} for contexts with NO dimensions."""
    out = {}
    for c in root:
        if _local(c.tag) != "context":
            continue
        start = end = instant = None
        dims = False
        for el in c.iter():
            name = _local(el.tag)
            if name in ("explicitMember", "typedMember"):
                dims = True
            elif name == "startDate":
                start = (el.text or "").strip()
            elif name == "endDate":
                end = (el.text or "").strip()
            elif name == "instant":
                instant = (el.text or "").strip()
        if not dims and (end or instant):
            out[c.get("id")] = (start, end or instant)
    return out


def parse_facts(xml_bytes, accn, form, filed):
    """{ns: {concept: {"units": {unit: [entry]}}}} in companyfacts' shape."""
    root = ET.fromstring(xml_bytes)
    units, ctx = _units(root), _contexts(root)
    fy = fp = None
    for el in root:
        if _local(el.tag) == "DocumentFiscalYearFocus":
            fy = (el.text or "").strip()
        elif _local(el.tag) == "DocumentFiscalPeriodFocus":
            fp = (el.text or "").strip()
    out, seen = {}, set()
    for el in root:
        ns = _ns(el.tag.split("}")[0])
        c = ctx.get(el.get("contextRef"))
        unit = units.get(el.get("unitRef"))
        if not ns or not c or not unit:
            continue
        if el.get("{http://www.w3.org/2001/XMLSchema-instance}nil") == "true":
            continue
        try:
            val = float((el.text or "").strip())
        except ValueError:
            continue
        concept = _local(el.tag)
        key = (ns, concept, unit, c)
        if key in seen:
            continue
        seen.add(key)
        e = {"end": c[1], "val": int(val) if val.is_integer() else val, "accn": accn,
             "fy": int(fy) if fy and fy.isdigit() else fy, "fp": fp, "form": form, "filed": filed}
        if c[0]:
            e["start"] = c[0]
        out.setdefault(ns, {}).setdefault(concept, {"units": {}})["units"].setdefault(unit, []).append(e)
    return out


def merge(cf, patch, info):
    """A copy of companyfacts with the patch's entries appended, marked `_sec_gap`
    (the newest filling; `filled` lists every accession read so far)."""
    out = copy.deepcopy(cf)
    facts = out.setdefault("facts", {})
    n = 0
    for ns, concepts in patch.items():
        for concept, node in concepts.items():
            dst = facts.setdefault(ns, {}).setdefault(concept, {"units": {}})
            for unit, arr in node["units"].items():
                dst.setdefault("units", {}).setdefault(unit, []).extend(arr)
                n += len(arr)
    prev = cf.get("_sec_gap") or {}
    if prev.get("read") is False:               # an earlier filing stays unread: say so
        out["_sec_gap"] = dict(prev, filled=list(prev.get("filled") or []) + [info["accn"]])
        return out
    out["_sec_gap"] = dict(info, read=True, facts=n,
                           filled=list(prev.get("filled") or []) + [info["accn"]])
    return out


# --------------------------------------------------------------------------- #
#  fetch                                                                       #
# --------------------------------------------------------------------------- #
def _cached_json(path, ttl_hours):
    try:
        if path and os.path.exists(path) and (ttl_hours is None or
                                              time.time() - os.path.getmtime(path) < ttl_hours * 3600):
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
    except (OSError, ValueError):
        return None
    return None


def _write(path, obj):
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(obj, fh)
        os.replace(tmp, path)
    except OSError as e:
        log.warning("sec_gap cache write failed (%s): %s", path, e)


def complete(cik, cf, user_agent, cache_dir=None, requests_mod=None, timeout=30, throttle=None):
    """companyfacts with its newest missing filing filled in, or `cf` unchanged.

    Never raises: a failed read returns `cf` with `_sec_gap.read = False` and the
    error, so the card can say the figures lag a filing it knows exists."""
    if not cf or not cik:
        return cf
    import requests as _r
    requests_mod = requests_mod or _r
    cik10 = str(cik).zfill(10)
    headers = {"User-Agent": user_agent}
    sub_path = os.path.join(cache_dir, "sec_gap", f"submissions_{cik10}.json") if cache_dir else None
    try:
        sub = _cached_json(sub_path, SUBMISSIONS_TTL_HOURS)
        if sub is None:
            if throttle:
                throttle()
            r = requests_mod.get(f"https://data.sec.gov/submissions/CIK{cik10}.json",
                                 headers=headers, timeout=timeout)
            r.raise_for_status()
            full = r.json()
            sub = {"filings": {"recent": {k: (full.get("filings") or {}).get("recent", {}).get(k, [])[:40]
                                          for k in ("form", "filingDate", "reportDate",
                                                    "accessionNumber", "primaryDocument")}}}
            _write(sub_path, sub)
    except Exception as e:
        # not silent: the card says the filing list could not be checked
        log.warning("%s submissions failed: %s", cik10, e)
        out = dict(cf)
        out["_sec_gap"] = {"checked": False, "error": f"{type(e).__name__}: {str(e)[:70]}"}
        return out
    out = cf
    for g in gaps(sub, cf):                     # oldest first: the newest wins `_sec_gap`
        out = _fill(cik10, out, g, headers, cache_dir, requests_mod, timeout, throttle)
    return out


def _fill(cik10, cf, g, headers, cache_dir, requests_mod, timeout, throttle):
    info = {k: g[k] for k in ("form", "filed", "period", "accn")}
    patch_path = os.path.join(cache_dir, "sec_gap", f"facts_{g['accn']}.json") if cache_dir else None
    patch = _cached_json(patch_path, None)      # a filing never changes
    if patch is None:
        try:
            if throttle:
                throttle()
            url = _instance_url(int(cik10), g["accn"], g["primary"], requests_mod, headers, timeout)
            if not url:
                raise ValueError("no XBRL instance in the filing")
            if throttle:
                throttle()
            r = requests_mod.get(url, headers=headers, timeout=timeout)
            r.raise_for_status()
            patch = parse_facts(r.content, g["accn"], g["form"], g["filed"])
            if not patch:
                raise ValueError("instance has no us-gaap/ifrs facts")
            _write(patch_path, patch)
        except Exception as e:
            log.warning("%s %s %s not read: %s", cik10, g["form"], g["accn"], e)
            out = dict(cf)
            out["_sec_gap"] = dict(info, read=False, error=str(e)[:80])
            return out
    return merge(cf, patch, info)
