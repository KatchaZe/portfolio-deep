"""
frames_revenue: which SEC frames value is a filer's revenue for one calendar year, and
which calendar year a fiscal year counts in, for the industry share (SPEC R35, R36). Pure.

The frames API answers one concept per call, each with the latest-filed value per filer
and the accession it came from. A filer can tag several revenue concepts in one year:
  * TransMedics' FY2021 10-K tagged Revenues $30.262M and RevenueFromContract $31.385M;
    every later 10-K reports that year under Revenues only. The largest concept counted
    a line the company itself dropped (audit D3).
  * Mastercard tagged a gross RevenueFromContract once and reports net Revenues since.
  * BlackRock, General Mills and BNSF tag Revenues on a component line ($0.1B of BNSF's
    $23B) in the same filing as the total under RevenueFromContract.
  * Tigo Energy tagged one value twice, once 1000x too large.
  * Brewers and distillers report sales, excise taxes, and net sales. Molson Coors tags
    gross sales as ...ExcludingAssessedTax and net sales as ...IncludingAssessedTax (the
    reverse of the names), so the priority read its gross $13.7B for FY2024.
So the rule reads the filings, not the sizes: the newest filing first, Revenues (the
us-gaap total) first within it, a contract-revenue concept only when Revenues is
materially below it in that same filing, and a 1000x twin dropped. Then net of excise
(R41): when the filer's ExciseAndSalesTaxes for that period is the gap between two of its
revenue values, the smaller one is the revenue.
"""
import datetime as dt

# priority within one filing; ifrs-full is read only when no us-gaap value exists
PRIORITY = ("us-gaap:Revenues", "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
            "us-gaap:RevenueFromContractWithCustomerIncludingAssessedTax", "us-gaap:SalesRevenueNet",
            "ifrs-full:Revenue")
CONTRACT = PRIORITY[1:3]
# Revenues below a contract-revenue concept of the same filing by more than this is a
# component line, not the total. Utilities and producers file Revenues 1-3% under contract
# revenue (alternative-revenue programs, hedges): those stay on Revenues.
COMPONENT_GAP = 0.10
UNIT_SLIPS = (1e3, 1e6)
UNIT_TOL = 0.01
JUNE_WINDOW_DAYS = 7
# R41: the excise the filer tagged for the period, read only as evidence that two of its
# revenue values are gross and net; within this share of the gross value
EXCISE = "us-gaap:ExciseAndSalesTaxes"
EXCISE_TOL = 0.01


def _accn_year(accn):
    """The year an accession number was assigned (0001564590-22-008045 -> 22), 0 if unknown."""
    try:
        return int(str(accn).split("-")[1])
    except (IndexError, ValueError):
        return 0


def choose(entries):
    """{val, end, concept, note} from {concept: {val, end, accn}} for one filer-year, or None.
    note None, "revenues_component" (a contract concept used because Revenues is a component
    line in the same filing), "revenues_older_filing" (Revenues came only from an older
    filing than the concept used) or "unit_1000x" (a twin 1000x or 1e6x too large dropped).
    Any value above the filer's Revenues therefore carries a note (audit2 A13c)."""
    ok = {c: e for c, e in (entries or {}).items()
          if c in PRIORITY and isinstance(e.get("val"), (int, float)) and not isinstance(e.get("val"), bool)}
    us = {c: e for c, e in ok.items() if c.startswith("us-gaap:")}
    ok = us or ok
    if not ok:
        return None
    newest = max(_accn_year(e.get("accn")) for e in ok.values())
    keep = {c: e for c, e in ok.items() if _accn_year(e.get("accn")) == newest}
    note = None
    for c in list(keep):
        v = keep[c]["val"]
        for d, e in keep.items():
            w = e["val"]
            if d != c and w > 0 and v > w and any(abs(v / w / k - 1) <= UNIT_TOL for k in UNIT_SLIPS):
                keep.pop(c)
                note = "unit_1000x"
                break
    base = next(c for c in PRIORITY if c in keep)
    pick = base
    if base == "us-gaap:Revenues":
        bv = keep[base]["val"]
        over = next((c for c in CONTRACT if c in keep and keep[c]["val"] > bv * (1 + COMPONENT_GAP)), None)
        if over:
            pick, note = over, note or "revenues_component"
    if pick != "us-gaap:Revenues" and "us-gaap:Revenues" in ok and "us-gaap:Revenues" not in keep and not note:
        note = "revenues_older_filing"
    net = _net_of_excise(keep, pick, (entries or {}).get(EXCISE))
    if net:
        pick, note = net
    e = keep[pick]
    return {"val": e["val"], "end": e.get("end"), "concept": pick, "note": note}


def _net_of_excise(keep, pick, tax):
    """(concept, note) when `pick` is the gross figure of a gross/net pair the filer's own
    excise tag explains (gross - net = excise, same period end), else None. Note
    "excise_tags_swapped" when the gross value sits under ...ExcludingAssessedTax and the
    net under ...IncludingAssessedTax, else "excise_net". No excise tag: no change, since a
    second revenue value alone does not say which is net (Xylem's two differ by 4%)."""
    t = (tax or {}).get("val")
    if not isinstance(t, (int, float)) or isinstance(t, bool) or t <= 0:
        return None
    g = keep[pick]
    if str(tax.get("end")) != str(g.get("end")):
        return None
    for c, e in keep.items():
        if c != pick and 0 < e["val"] < g["val"] and abs(g["val"] - e["val"] - t) <= EXCISE_TOL * g["val"]:
            swapped = pick == CONTRACT[0] and c == CONTRACT[1]
            return c, "excise_tags_swapped" if swapped else "excise_net"
    return None


def _d(s):
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def june_cy(end):
    """The calendar year of a fiscal year ending within JUNE_WINDOW_DAYS of June 30: the year
    it ends in, the year SEC gives a June-30 year (MSFT, RMD). SEC frames put a 52/53-week
    year ending 2025-06-28 (SYNA) in CY2024 instead, so one calendar year held SYNA's FY
    ending 2026-06-27 beside RMD's ending 2025-06-30. None outside the window."""
    d = _d(end)
    if d is None:
        return None
    return d.year if abs((d - dt.date(d.year, 6, 30)).days) <= JUNE_WINDOW_DAYS else None


def rebucket(by_sec_year):
    """{CY: {cik: row}} from frames keyed by SEC's own calendar year, row[1] the period end:
    a June-window year moves to june_cy, every other row keeps SEC's year. Two rows of one
    filer landing in one year keep the one SEC filed under that year."""
    out, own = {}, {}
    for y, rows in sorted((by_sec_year or {}).items(), key=lambda kv: int(kv[0])):
        for cik, row in (rows or {}).items():
            cy = june_cy(row[1] if len(row) > 1 else None) or int(y)
            if cik in out.get(cy, {}) and own.get((cy, cik)):
                continue
            out.setdefault(cy, {})[cik] = row
            own[(cy, cik)] = cy == int(y)
    return out
