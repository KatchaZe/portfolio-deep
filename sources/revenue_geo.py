"""Where a US filer earns its revenue, read from its latest 10-K (XBRL), for the
country risk premium (sources/country_risk.py).

companyfacts carries no dimensions, so the 10-K's own XBRL instance is read: the
revenue facts broken down on the geographical axis (srt:StatementGeographicalAxis),
or on the segment axis when the segments are regions (MELI: Brazil, Mexico, ...).

Filers mix levels in one table. ORCL tags Americas / EMEA / APAC AND US / UK /
Germany / Japan / other; LLY tags US and non-US AND Europe / Japan / China / rest of
world inside non-US. The mix is the most detailed set of members that adds up to
total revenue (within 1%), so no amount is counted twice.

Members are named by the filer (nvda:ChinaIncludingHongKongMember); market_of() maps
them onto a country (ISO-2) or a region country_risk knows. A catch-all ("other
countries", "non-US") becomes "rest of world".
"""
import datetime as dt
import itertools
import json
import os
import re
import time
import xml.etree.ElementTree as ET

REVENUE_CONCEPTS = ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                    "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet")
GEO_AXIS = "StatementGeographicalAxis"
SEGMENT_AXIS = "StatementBusinessSegmentsAxis"
TOLERANCE = 0.01             # members must add up to total revenue within 1%
MAX_MEMBERS = 16             # subset search bound (2^16 sets)
CACHE_TTL_DAYS = 30          # how often the filing list is re-read for a new 10-K
REST_OF_WORLD = "rest of world"

# Order matters: "non-US" before "US", "Americas excluding US" before "Americas".
_RULES = (
    (r"^(nonus|nonunitedstates|outside(the)?(us|unitedstates)|international|foreign"
     r"|restofworld|rowregion|allother.*|other(countries|country|foreign|international"
     r"|geograph.*|regions?|markets?|nonus.*)?|othercountriesandregions)$", REST_OF_WORLD),
    (r"americas?(excluding|exclusive|ex|other|outside)(the)?(us|unitedstates)"
     r"|otheramericas|latinamerica|southamerica|centralandsouthamerica", "americas ex us"),
    (r"^(unitedstates|us|domestic|usandcanada|northamerica|americas?|americassegment)$", "US"),
    (r"emea|europemiddleeast(and)?africa", "emea"),
    (r"europe", "western europe"),
    (r"greaterchina|china|hongkong", "China"),
    (r"asiapacific|apac|^asia$|asiaandpacific", "asia pacific"),
    (r"middleeast", "middle east"),
    (r"africa", "africa"),
)


def _local(qname):
    return (qname or "").rsplit(":", 1)[-1]


def market_of(member):
    """'country:TW' -> 'TW'; 'us-gaap:EMEAMember' -> 'emea'; None when unknown."""
    from sources import country_risk
    prefix, _, local = (member or "").rpartition(":")
    if prefix == "country":
        return local.upper()
    key = re.sub(r"(segment)?(member)+$", "", local.lower())
    key = re.sub(r"[^a-z]", "", key)
    for pattern, market in _RULES:
        if re.search(pattern, key):
            return market
    for code, name in country_risk.ISO2.items():
        if re.sub(r"[^a-z]", "", name.lower()) == key:
            return code
    return None


# --------------------------------------------------------------------------- #
#  XBRL instance -> revenue facts                                              #
# --------------------------------------------------------------------------- #
def parse_instance(xml_bytes):
    """[(concept, start, end, ((axis, member), ...), value)] for revenue facts."""
    root = ET.fromstring(xml_bytes)
    contexts = {}
    for c in root:
        if not c.tag.endswith("}context"):
            continue
        start = end = None
        dims = []
        for el in c.iter():
            name = el.tag.rsplit("}", 1)[-1]
            if name == "startDate":
                start = (el.text or "").strip()
            elif name == "endDate":
                end = (el.text or "").strip()
            elif name == "explicitMember":
                dims.append((_local(el.get("dimension")), (el.text or "").strip()))
        if start and end:
            contexts[c.get("id")] = (start, end, tuple(sorted(dims)))
    out = []
    for el in root:
        concept = el.tag.rsplit("}", 1)[-1]
        if concept not in REVENUE_CONCEPTS:
            continue
        ctx = contexts.get(el.get("contextRef"))
        try:
            val = float((el.text or "").strip())
        except ValueError:
            continue
        if ctx:
            out.append((concept, ctx[0], ctx[1], ctx[2], val))
    return out


def _days(a, b):
    return (dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days


def _best_subset(members, total):
    """The largest set of members adding up to `total` within TOLERANCE, or None."""
    items = sorted(members.items(), key=lambda kv: -kv[1])[:MAX_MEMBERS]
    for size in range(len(items), 0, -1):
        hits = [combo for combo in itertools.combinations(items, size)
                if abs(sum(v for _, v in combo) - total) <= TOLERANCE * abs(total)]
        if hits:
            # among equally detailed sets, the one with the most named countries
            return dict(max(hits, key=lambda c: sum(1 for m, _ in c if m.startswith("country:"))))
    return None


def mix_from_facts(facts):
    """({market: revenue}, note) from parse_instance() rows, or (None, why)."""
    annual = [f for f in facts if 350 <= _days(f[1], f[2]) <= 380]
    if not annual:
        return None, "no annual revenue facts"
    end = max(f[2] for f in annual)
    year = [f for f in annual if f[2] == end]
    totals = {f[0]: f[4] for f in year if not f[3]}
    if not totals:
        return None, "no total revenue in the 10-K"
    for axis in (GEO_AXIS, SEGMENT_AXIS):
        for concept in REVENUE_CONCEPTS:
            members = {f[3][0][1]: f[4] for f in year
                       if f[0] == concept and len(f[3]) == 1 and f[3][0][0] == axis and f[4] > 0}
            if len(members) < 2:
                continue
            total = totals.get(concept) or max(totals.values())
            chosen = _best_subset(members, total)
            if not chosen:
                continue
            mapped = {m: market_of(m) for m in chosen}
            if axis == SEGMENT_AXIS and any(v is None for v in mapped.values()):
                continue        # product segments, not regions
            mix, unknown = {}, [_local(m) for m, v in mapped.items() if v is None]
            for m, v in chosen.items():
                market = mapped[m] or REST_OF_WORLD
                mix[market] = mix.get(market, 0.0) + v
            note = f"10-K FY{end[:4]}" + (" segments" if axis == SEGMENT_AXIS else "")
            if unknown:
                note += f" ({', '.join(unknown)} read as rest of world)"
            return mix, note
    return None, "no geographic revenue breakdown that adds up to total revenue"


# --------------------------------------------------------------------------- #
#  fetch (runs where SEC is reachable), cached per CIK                         #
# --------------------------------------------------------------------------- #
def _instance_url(cik_int, acc, primary, requests_mod, headers, timeout):
    base = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc.replace('-', '')}/"
    if primary and primary.endswith(".htm"):
        return base + primary[:-4] + "_htm.xml"
    idx = requests_mod.get(base + "index.json", headers=headers, timeout=timeout).json()
    names = [i.get("name", "") for i in (idx.get("directory") or {}).get("item", [])]
    xml = [n for n in names if n.endswith(".xml") and not re.search(r"_(cal|def|lab|pre)\.xml$", n)
           and n != "FilingSummary.xml"]
    return base + xml[0] if xml else None


def fetch_mix(cik, user_agent, cache_dir=None, requests_mod=None, timeout=30,
              ttl_days=CACHE_TTL_DAYS, throttle=None):
    """{"mix", "note", "accession", "fy_end"?} for the latest 10-K, or None when the
    filer has no 10-K (20-F filers) or no usable breakdown. Cached: the filing list is
    re-read every `ttl_days`, the instance only when a new 10-K appears."""
    import requests as _r
    requests_mod = requests_mod or _r
    cik10 = str(cik).zfill(10)
    path = os.path.join(cache_dir, f"revenue_geo_{cik10}.json") if cache_dir else None
    cached = None
    if path and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                cached = json.load(fh)
            if time.time() - cached.get("checked", 0) < ttl_days * 86400:
                return cached.get("result")
        except Exception:
            cached = None
    headers = {"User-Agent": user_agent}
    if throttle:
        throttle()
    sub = requests_mod.get(f"https://data.sec.gov/submissions/CIK{cik10}.json",
                           headers=headers, timeout=timeout)
    sub.raise_for_status()
    recent = (sub.json().get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    if "10-K" not in forms:
        result = None
    else:
        i = forms.index("10-K")
        acc = recent["accessionNumber"][i]
        if cached and (cached.get("result") or {}).get("accession") == acc:
            result = cached["result"]
        else:
            if throttle:
                throttle()
            url = _instance_url(int(cik10), acc, (recent.get("primaryDocument") or [""])[i],
                                requests_mod, headers, timeout)
            result = None
            if url:
                if throttle:
                    throttle()
                r = requests_mod.get(url, headers=headers, timeout=timeout)
                r.raise_for_status()
                mix, note = mix_from_facts(parse_instance(r.content))
                result = {"accession": acc, "note": note,
                          "mix": {k: round(v, 2) for k, v in mix.items()} if mix else None}
    if path:
        try:
            os.makedirs(cache_dir, exist_ok=True)
            tmp = f"{path}.tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"checked": time.time(), "result": result}, fh)
            os.replace(tmp, path)
        except Exception:
            pass
    return result


def cached_mix(cik, cache_dir):
    """The last fetched result for a CIK without touching the network."""
    path = os.path.join(cache_dir, f"revenue_geo_{str(cik).zfill(10)}.json")
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh).get("result")
    except Exception:
        return None
