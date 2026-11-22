"""
global_sic: the SIC of a target Damodaran lists only on its home exchange (an ADR such
as ASML, NVO, TSM), from the non-US rows of his company list (SPEC R29). Pure.

The match is automatic and must be unique: the profile's company name, normalised, equals
exactly one global row's normalised name, and that row's country is the profile's
country. Anything else is a skip with a Thai reason. There is no hand-written ADR map.
The SIC found here is the target's only: the same-business sub-group stays the US-filer
members of the target's Damodaran industry (R28).
"""
import re

SOURCE_PREFIX = "Damodaran global"
# legal-form and listing words that differ between an ADR's SEC name and the home listing
SUFFIXES = {"nv", "as", "asa", "ab", "ag", "sa", "se", "spa", "plc", "ltd", "limited", "inc",
            "incorporated", "corp", "corporation", "co", "company", "holding", "holdings",
            "group", "adr", "ads", "sponsored", "the", "oyj", "kk", "bhd", "tbk", "pte", "llc", "lp"}
LISTING_WORDS = {"adr", "ads", "sponsored", "unsponsored"}


def normalise(name):
    """'ASML Holding N.V.' -> 'asml'; 'Novo Nordisk A/S' -> 'novo nordisk'; 'Taiwan
    Semiconductor Manufacturing Company Limited (TWSE:2330)' -> 'taiwan semiconductor
    manufacturing'. Lower case, '&' as 'and', parentheses dropped, '.' and '/' joined
    (N.V. -> nv, A/S -> as), other punctuation a space, ADR words dropped anywhere and
    legal-form words dropped from the end. '' when nothing is left."""
    s = re.sub(r"\([^)]*\)", " ", str(name or "").lower()).replace("&", " and ")
    s = re.sub(r"[./]", "", s)
    words = [w for w in re.sub(r"[^0-9a-zÀ-ɏ]+", " ", s).split() if w not in LISTING_WORDS]
    while words and words[-1] in SUFFIXES:
        words.pop()
    return " ".join(words)


def _country(c):
    return re.sub(r"\s+", " ", str(c or "").strip().lower())


def resolve(rows, company, country):
    """{sic, source, ticker, reason}. `rows` [[name, exchange:ticker, country, sic,
    industry]] (damodaran_global_sic.json); `company` the profile name; `country` the
    profile's country as a name (Damodaran writes names, "Netherlands"). `sic` None with
    a Thai `reason` unless exactly one row matches the name and its country agrees."""
    out = {"sic": None, "source": None, "ticker": None, "reason": None}
    if not rows:
        out["reason"] = "ไม่มีรายชื่อบริษัทนอกสหรัฐของ Damodaran ในเครื่อง จึงหารหัส SIC ของหุ้นนี้ไม่ได้"
        return out
    key = normalise(company)
    if not key:
        out["reason"] = "ไม่มีชื่อบริษัทในโปรไฟล์ จึงจับคู่กับรายชื่อ Damodaran ทั่วโลกไม่ได้"
        return out
    hits = [r for r in rows if normalise(r[0]) == key]
    if not hits:
        out["reason"] = f"ไม่พบชื่อ “{company}” ในรายชื่อ Damodaran ทั่วโลก จึงไม่มีรหัส SIC"
        return out
    if len(hits) > 1:
        out["reason"] = (f"ชื่อ “{company}” ตรงกับ {len(hits)} แถวในรายชื่อ Damodaran ทั่วโลก "
                         f"({', '.join(h[1] for h in hits[:4])}) จึงไม่เลือกเอง")
        return out
    h = hits[0]
    if _country(h[2]) != _country(country):
        out["reason"] = (f"ชื่อตรงกับ {h[1]} แต่ประเทศไม่ตรง (Damodaran: {h[2]}, โปรไฟล์: {country or 'ไม่ทราบ'}) "
                         "จึงไม่ใช้รหัส SIC นี้")
        return out
    if not str(h[3] or "").strip():
        out["reason"] = f"แถว {h[1]} ในรายชื่อ Damodaran ทั่วโลกไม่มีรหัส SIC"
        return out
    out.update(sic=h[3], ticker=h[1], source=f"{SOURCE_PREFIX}: {h[1]}")
    return out
