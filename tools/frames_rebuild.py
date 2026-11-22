"""Upgrade the frames caches to the panel reading (SPEC R33-R36) offline, from raw SEC frames
files already on disk, without a fetch. Production gets the same fields from _frames on its
next refresh; this exists for machines that cannot reach SEC.

  python -m tools.frames_rebuild <raw_dir> [indname.xls]

<raw_dir> holds frames_<Concept>_CY<year>.json as the SEC frames API returns them (us-gaap,
USD) and, when present, frames_ifrs_Revenue_<UNIT>_CY<year>.json. Each data/cache
sec_frames_revenue_CY<year>.json keeps its rev / end / at byte for byte (the relative model
sizes peers on rev, PP1) and gains panel, name, ifrs_other, panel_schema. A filer in rev with
no raw frame of the three cached concepts tagged only SalesRevenueNet; it keeps its rev value
under that concept. With indname.xls, damodaran_us_names.json is written from it."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config                                          # noqa: E402
from domain import frames_revenue                      # noqa: E402
from sources import damodaran_companies as dc         # noqa: E402


def _load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def rebuild(raw_dir, cache_dir=None, xls=None):
    cache_dir = cache_dir or config.CACHE_DIR
    report = {}
    for name in sorted(os.listdir(cache_dir)):
        if not (name.startswith("sec_frames_revenue_CY") and name.endswith(".json")):
            continue
        year = int(name[21:25])
        path = os.path.join(cache_dir, name)
        c = _load(path)
        raw, names, other, seen = {}, {}, {}, set()
        for concept in dc.FRAMES_CONCEPTS:
            f = os.path.join(raw_dir, f"frames_{concept}_CY{year}.json")
            if not os.path.exists(f):
                continue
            seen.add(concept)
            for d in _load(f).get("data") or []:
                raw.setdefault(int(d["cik"]), {})["us-gaap:" + concept] = d
                names.setdefault(int(d["cik"]), d.get("entityName"))
        ifrs = os.path.join(raw_dir, f"frames_ifrs_Revenue_USD_CY{year}.json")
        if os.path.exists(ifrs):
            for d in _load(ifrs).get("data") or []:
                raw.setdefault(int(d["cik"]), {})["ifrs-full:Revenue"] = d
                names.setdefault(int(d["cik"]), d.get("entityName"))
        for cur in dc.IFRS_OTHER_CURRENCIES:
            f = os.path.join(raw_dir, f"frames_ifrs_Revenue_{cur}_CY{year}.json")
            for d in (_load(f).get("data") or []) if os.path.exists(f) else []:
                if int(d["cik"]) not in raw:
                    other.setdefault(int(d["cik"]), cur)
        panel, legacy_only = {}, 0
        for cik, by in raw.items():
            ch = frames_revenue.choose({k: {"val": d.get("val"), "end": d.get("end"), "accn": d.get("accn")}
                                        for k, d in by.items()})
            if ch:
                panel[cik] = [ch["val"], ch["end"], ch["concept"], ch["note"]]
        missing = sorted(set(dc.FRAMES_CONCEPTS) - seen)
        for k, v in (c.get("rev") or {}).items():
            if int(k) not in panel and v:
                panel[int(k)] = [v, (c.get("end") or {}).get(k), "us-gaap:" + (missing[0] if missing else "?"), None]
                legacy_only += 1
        c.update(panel_schema=dc.PANEL_SCHEMA, panel=panel, name={k: v for k, v in names.items() if v},
                 ifrs_other=other, ifrs_note=None if os.path.exists(ifrs) else
                 f"CY{year}: ไม่มีข้อมูล IFRS ของ SEC ในเครื่องนี้ (สร้างแคชออฟไลน์) จึงไม่นับบริษัทที่รายงาน IFRS")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(c, fh)
        os.replace(tmp, path)
        changed = sum(1 for k, v in (c.get("rev") or {}).items() if int(k) in panel and panel[int(k)][0] != v)
        report[year] = {"filers": len(panel), "legacy_only": legacy_only, "value_changed_vs_rev": changed,
                        "notes": sum(1 for r in panel.values() if r[3]), "missing_concepts": missing}
    if xls:
        with open(xls, "rb") as fh:
            dc.write_names(cache_dir, dc.parse_indname_names(fh.read()))
        report["names"] = len(_load(os.path.join(cache_dir, dc.NAMES_CACHE))["names"])
    return report


if __name__ == "__main__":
    print(json.dumps(rebuild(sys.argv[1], xls=sys.argv[2] if len(sys.argv) > 2 else None), indent=1))
