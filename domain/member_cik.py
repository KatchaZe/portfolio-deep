"""
member_cik: the SEC CIK of one Damodaran industry member when its ticker is not in today's
company_tickers.json as written (SPEC R32, R33). Pure.

Two gaps, both found by the share audit (D1, D2):
  * Damodaran writes share classes with a dot (BRK.A, BF.B, RUSH.A, QVCG.A); SEC writes
    BRK-A, BRK-B, RUSHA, QVCG. Berkshire ($371B) was missing from Diversified.
  * A member delisted, renamed or taken private since (Qorvo, Masimo, Hologic) is absent
    from today's map while SEC frames still hold its revenue for the panel years.
The dotted ticker is tried as SEC spells share classes; every spelling must give ONE CIK.
Otherwise the Damodaran company name must equal exactly one SEC frames entity name after
the same strict normaliser global_sic uses (R29, E48), and that CIK must have revenue in the
panel years. No prefix, substring or fuzzy match, and no hand-written map.
"""
import re

from domain.global_sic import normalise

_STATE = re.compile(r"\s*/[A-Za-z]{2,3}/?\s*$")


def sec_normalise(name):
    """global_sic.normalise after SEC's conformed state suffix ("SEALED AIR CORP/DE")."""
    return normalise(_STATE.sub("", str(name or "").replace("\xa0", " ")))


class NameIndex:
    """{normalised name: {cik}} over SEC frames entity names. `entities` {cik: name or
    [names]}: every name a CIK filed under in the panel years counts (Envirotech Vehicles
    became AZIO AI Holdings in 2025; Damodaran's January list still has the old name)."""

    def __init__(self, entities):
        self._by = {}
        for cik, names in (entities or {}).items():
            for name in ([names] if isinstance(names, str) else names or ()):
                k = sec_normalise(name)
                if k:
                    self._by.setdefault(k, set()).add(int(cik))

    def hits(self, name):
        return sorted(self._by.get(sec_normalise(name), ()))


def _out(cik=None, how=None, reason=None):
    return {"cik": cik, "how": how, "reason": reason}


def _share_class(ticker, name, sec_map, titles):
    """(cik, reason, final) for a dotted ticker: final when two CIKs make it a skip that the
    name fallback must not override; (None, None, False) when no spelling is in the map."""
    base, _, cls = ticker.partition(".")
    if not base or not cls:
        return None, None, False
    found = {}
    for v in (f"{base}-{cls}", f"{base}{cls}"):
        if v in sec_map:
            found.setdefault(int(sec_map[v]), v)
    for k, c in sec_map.items():
        if k.startswith(base + "-"):
            found.setdefault(int(c), k)
    bare = base in sec_map and int(sec_map[base]) not in found
    if base in sec_map:
        found.setdefault(int(sec_map[base]), base)
    if not found:
        return None, None, False
    if len(found) > 1:
        return None, (f"ชั้นหุ้น {ticker} ตรงกับ SEC CIK {len(found)} ราย "
                      f"({', '.join(sorted(found.values()))}) จึงไม่เลือกเอง"), True
    cik = next(iter(found))
    if bare and len(found) == 1:
        key = sec_normalise(name)
        if not key or sec_normalise(titles.get(cik)) != key:
            return None, (f"ticker {base} ของ SEC ชื่อ “{titles.get(cik) or '-'}” "
                          f"ไม่ตรงกับชื่อใน Damodaran “{name or '-'}” จึงไม่ใช้"), False
    return cik, None, False


def resolve(ticker, name, exact_cik, sec_map, titles, index, has_revenue):
    """{cik, how, reason} for one member. `exact_cik` the CIK its ticker resolves to as
    written (pipeline.cik.resolve_cik) or None; `sec_map` {SEC ticker: cik}; `titles`
    {cik: SEC title}; `index` a NameIndex over frames entity names; `has_revenue` the CIKs
    with revenue in the panel years. how: ticker / share_class / name; reason (Thai) only
    when cik is None."""
    if exact_cik:
        return _out(int(exact_cik), "ticker")
    t = str(ticker or "").upper().strip()
    reason = None
    if "." in t:
        cik, reason, final = _share_class(t, name, sec_map or {}, titles or {})
        if cik:
            return _out(cik, "share_class")
        if final:
            return _out(reason=reason)
    if not sec_normalise(name):
        return _out(reason=reason or f"{t} ไม่มีชื่อบริษัทในรายชื่อ Damodaran จึงหา CIK ไม่ได้")
    hits = index.hits(name)
    if not hits:
        return _out(reason=reason or f"ไม่พบ {t} ใน SEC และไม่พบชื่อ “{name}” ในข้อมูลรายได้ SEC")
    if len(hits) > 1:
        return _out(reason=f"ชื่อ “{name}” ตรงกับ SEC {len(hits)} ราย (CIK {', '.join(map(str, hits[:4]))}) "
                           "จึงไม่เลือกเอง")
    if hits[0] not in (has_revenue or ()):
        return _out(reason=f"ชื่อ “{name}” ตรงกับ CIK {hits[0]} แต่ไม่มีรายได้ในปีที่ใช้ จึงไม่นับ")
    return _out(hits[0], "name")
