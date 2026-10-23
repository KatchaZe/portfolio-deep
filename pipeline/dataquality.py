"""
dataquality — is the DATA fit to score, before anything scores it. Pure; no network.

WHY THIS LAYER EXISTS
---------------------
Every other guard in this repo checks OUR CODE: the clock contract, the cross-path
invariants, the replay snapshot. But several defects were never code bugs at all — the
code was right and the DATA was wrong, and the code processed it with full confidence:

    TSM     SEC companyfacts stop at FY2024. On 2026-08-09 that is 20 months old, and
            the row still scored composite 3.95 / HOLD-Accumulate as though current.
            NVO and ASML — foreign filers too — both have FY2025, so this is not a
            structural limit, it is TSM specifically.
    AVGO    no long-term debt tag for FY2022-24, which made invested capital collapse
            and ROIC read 130%.
    ORCL    CostOfRevenue tagged only to 2018, so a "5-year" gross margin was built
            from 2017-2018 data.
    Yahoo   surprise lists arrive newest-first while every other source is oldest-first.

The exposure here is unbounded in a way the code checks are not: every ticker the user
adds brings a filing style nobody has looked at. So the rule is that the data states its
own condition BEFORE the engine trusts it, and anything degraded is visible on the card
and costs confidence — never silently averaged into a score.

SEVERITIES
    block  the figure should not be scored at all
    warn   scoreable, but the reader must be told and confidence drops
    note   worth surfacing, no penalty
"""
import datetime as dt

from domain import contracts        # the unit contract — see FALLBACK_MONEY below

# An annual filer that has not produced a fiscal year end within this window is behind.
# 15 months allows a normal late filing (FY ends December, 20-F lands the following
# April, plus slack); beyond that the figures are describing a different company.
STALE_WARN_MONTHS = 15
STALE_BLOCK_MONTHS = 24
# A base period this old means interim quarters have been reported that the figures
# do not hold. A 10-Q filer is never past ~5 months; a 20-F filer reports quarters by
# 6-K, which carries no XBRL, so its base stays on the fiscal year (ASML and TSM ran
# on FY2025 in October 2026 with three 2026 quarters out: TSM TTM revenue +34%).
STALE_INTERIM_MONTHS = 6
# Confidence points removed per finding. Deliberately smaller than the -18 a missing
# critical field costs: stale data is still data, it is just older than it looks.
PENALTY = {"block": 25, "warn": 12, "note": 0}


def _months_between(a, b):
    return (b.year - a.year) * 12 + (b.month - a.month)


def staleness_months(fiscal_year, today=None):
    """How many months since the fiscal year end the figures describe. None if unknown."""
    if not fiscal_year:
        return None
    try:
        d = dt.date.fromisoformat(str(fiscal_year)[:10])
    except (TypeError, ValueError):
        return None
    return _months_between(d, today or dt.date.today())


def _gaps(dated, years=5):
    """Missing fiscal years inside the most recent `years` of a dated series.

    A gap is not a cosmetic issue: it is what made AVGO's invested capital collapse.
    Reported here so the row can say so, even where the consumer already handles it by
    shortening its window."""
    ends = sorted({d[:4] for d, _ in (dated or []) if isinstance(d, str)}, reverse=True)[:years]
    if len(ends) < 2:
        return []
    try:
        ys = sorted(int(y) for y in ends)
    except ValueError:
        return []
    return [str(y) for y in range(ys[0], ys[-1] + 1) if str(y) not in set(ends)]


def assess(ff, today=None):
    """[{code, severity, message, penalty}] — everything wrong with this row's data.

    Reads only what is on the facts object; safe to call before or after the engine."""
    found = [*_sec_gap(ff), *_staleness(ff, today), *_series_gaps(ff), *_short_history(ff),
             *_forward_eps(ff), *_currency(ff)]
    return [{"code": code, "severity": severity, "message": message,
             "penalty": PENALTY[severity]} for code, severity, message in found]


def _sec_gap(ff):
    """A filing SEC companyfacts has not ingested (sources/sec_gap.py)."""
    g = getattr(ff, "sec_gap", None)
    if not g:
        return
    if g.get("checked") is False:
        yield ("sec_filing_list_unchecked", "note",
               f"ตรวจรายการงบที่ยื่นกับ SEC ไม่สำเร็จ ({g.get('error', '')}) "
               f"จึงไม่รู้ว่ามี 10-Q ใหม่ที่ companyfacts ยังไม่มีหรือไม่")
        return
    if g.get("read"):
        yield ("sec_filing_read_direct", "note",
               f"SEC companyfacts ยังไม่มี {g['form']} งวด {g['period']} (ยื่น {g['filed']}) "
               f"จึงอ่านตัวเลขจาก XBRL ของฉบับนั้นโดยตรง")
    else:
        yield ("sec_filing_missing", "warn",
               f"มี {g['form']} งวด {g['period']} (ยื่น {g['filed']}) แต่ SEC companyfacts ยังไม่มี "
               f"และอ่านเองไม่สำเร็จ ({g.get('error', '')}) ตัวเลขจึงล้าหลังงวดนั้น")


def _staleness(ff, today):
    """How old the financials are."""
    age = staleness_months(getattr(ff, "fiscal_year", None), today)
    if age is None:
        yield ("no_fiscal_year", "warn",
               "ไม่รู้ว่างบล่าสุดเป็นงวดไหน — ตรวจความสดของข้อมูลไม่ได้")
    elif age >= STALE_BLOCK_MONTHS:
        yield ("stale_financials", "block",
               f"งบล่าสุดคืองวด {ff.fiscal_year} — เก่า {age} เดือน "
               f"(เกิน {STALE_BLOCK_MONTHS}) ตัวเลขอธิบายบริษัทคนละช่วงเวลากับราคาปัจจุบัน")
    elif age >= STALE_WARN_MONTHS:
        yield ("stale_financials", "warn",
               f"งบล่าสุดคืองวด {ff.fiscal_year} — เก่า {age} เดือน "
               f"(เกิน {STALE_WARN_MONTHS}) น่าจะมีงบใหม่กว่านี้แล้ว")
    elif age >= STALE_INTERIM_MONTHS:
        yield ("interim_not_used", "warn",
               f"ฐานตัวเลขคืองวด {ff.fiscal_year} (เก่า {age} เดือน) — บริษัทน่าจะรายงาน"
               f"ไตรมาสหลังจากนั้นแล้ว (6-K ไม่มี XBRL) มูลค่าจึงอิงงบที่ล้าหลังราคา")


def _series_gaps(ff):
    """Gaps inside the series the trend strip and the ROIC series ride on."""
    for name, label in (("revenue_annuals_dated", "รายได้"),
                        ("operating_income_annuals_dated", "operating income"),
                        ("cfo_annuals_dated", "กระแสเงินสดจากการดำเนินงาน"),
                        ("capex_annuals_dated", "capex")):
        g = _gaps(getattr(ff, name, None))
        if g:
            yield (f"gap_{name}", "note",
                   f"{label}: ปีงบขาด {', '.join(g)} — เทรนด์จะสั้นลงเท่าที่ต่อเนื่องจริง")
    ic = getattr(ff, "ic_components_dated", None) or {}
    if ic:
        g = _gaps([[d, 0] for d in ic])
        if g:
            yield ("gap_invested_capital", "warn",
                   f"ทุนที่ลงไป: ปีงบขาด {', '.join(g)} — ROIC รายปีจะข้ามช่วงนั้น "
                   f"(นี่คือกรณี AVGO ที่เคยทำให้ ROIC อ่านได้ 130%)")


def _short_history(ff):
    n_rev = len(getattr(ff, "revenue_annuals", None) or [])
    if 0 < n_rev < 3:
        yield ("short_history", "note",
               f"มีรายได้ย้อนหลังแค่ {n_rev} ปี — CAGR และเทรนด์จะไม่แสดง")


def _forward_eps(ff):
    """Forward EPS is the input BOTH point-value methods stand on (Fundamental PEG is
    justified_pe x forward_eps; FVP compounds it), so without consensus the fair value
    rests on a fallback the reader is never shown. A warn, not a block: the row is
    still worth showing, just not at face value."""
    if getattr(ff, "profitable", True) and not getattr(ff, "forward_eps", None):
        yield ("no_forward_eps", "warn",
               "ไม่มี forward EPS (consensus) — Fundamental PEG และ FVP ต่างก็ตั้งอยู่บนค่านี้ "
               "ตัวเลข fair value จึงมาจาก fallback ไม่ใช่ consensus จริง อย่าอ่านเป็นค่าแม่น")
    elif (getattr(ff, "forward_eps_n", 0) or 0) == 1:
        yield ("thin_forward_eps", "note",
               "forward EPS มาจากแหล่งเดียว — ไม่มีแหล่งที่สองยืนยัน")


def _currency(ff):
    """A non-USD row with no trace of a conversion."""
    ccy = getattr(ff, "currency", None)
    flags = getattr(ff, "flags", None) or []
    if ccy and ccy != "USD" and not any(str(f).startswith("converted") for f in flags):
        yield ("currency_unconverted", "block",
               f"งบเป็นสกุล {ccy} แต่ไม่พบร่องรอยการแปลงเป็น USD — "
               f"ตัวเลขเงินจะถูกเทียบกับราคาคนละสกุล")


def worst(findings):
    """block > warn > note > None."""
    for s in ("block", "warn", "note"):
        if any(f["severity"] == s for f in findings or []):
            return s
    return None


def total_penalty(findings):
    """Confidence points to remove. One penalty per SEVERITY, not per finding — five
    notes about missing years are one data-quality problem, not five."""
    seen, total = set(), 0
    for f in findings or []:
        if f["severity"] not in seen:
            seen.add(f["severity"])
            total += f["penalty"]
    return total


# Fields FMP's annual statements can refresh. Everything NOT on this list stays as SEC
# gave it — see refresh_from_fallback for why that is the dangerous part.
FALLBACK_SCALARS = ("revenue", "operating_income", "net_income", "eps_gaap",
                    "shares_diluted", "income_before_tax", "tax_expense",
                    "total_debt", "cash", "equity", "capex", "dep_amort", "sbc",
                    "cfo", "total_assets", "interest_expense", "rnd_expense")
# SEC scalars the fallback has no figure for. Left in place they pair an old fiscal
# year with the new one (TSM: FY2025 revenue beside FY2024 CFO and FY2023 balances),
# so they are cleared and the engine reads them as missing, which it handles.
FALLBACK_CLEARS = ("cfo", "total_assets", "interest_expense", "rnd_expense",
                   "receivables", "receivables_prior", "inventory", "inventory_prior",
                   "accounts_payable", "accounts_payable_prior", "deferred_revenue",
                   "deferred_revenue_prior", "equity_prior", "cash_prior", "total_debt_prior",
                   "operating_leases", "operating_leases_prior", "acquisitions_net",
                   "impairment_ttm", "restructuring_ttm", "acquired_iprd_ttm")
# The fields above that carry a CURRENCY, derived from the unit contract so the two
# lists cannot drift (a hand-typed list once omitted eps_gaap and left TSM's EPS in
# TWD beside a USD ADR price). `shares_diluted` is excluded by construction: a COUNT.
FALLBACK_MONEY = tuple(f for f in FALLBACK_SCALARS
                       if contracts.UNIT.get(f) in (contracts.MONEY, contracts.PER_SHARE))

# SEC-derived series that describe a DIFFERENT set of years once the scalars come from a
# newer source. Cleared, not kept: a fresh FY2025 revenue divided by a stale FY2023
# series entry is the TTM-vs-FY mismatch again.
FALLBACK_INVALIDATES = ("revenue_annuals", "revenue_annuals_dated",
                        "operating_income_annuals", "operating_income_annuals_dated",
                        "cfo_annuals_dated", "capex_annuals_dated", "sbc_annuals_dated",
                        "gross_profit_annuals_dated", "cost_of_revenue_annuals_dated",
                        "rnd_annuals", "shares_diluted_annuals", "acquisitions_annuals_dated",
                        "restructuring_annuals_dated", "acquired_iprd_annuals_dated",
                        "impairment_annuals_dated")


def fallback_rate(alt_currency, sec_currency, sec_fx, fetch_rate):
    """The FX rate to convert a FALLBACK source's figures with, or None when it cannot
    be established — in which case the fallback MUST NOT be used.

    `sec_fx` describes the currency SEC reported; never reuse it for another source's
    numbers. TSM's 20-F carries a USD convenience translation (SEC reads USD, no rate)
    while FMP reports the same company in TWD.

    None means REFUSE THE FALLBACK. Stale-but-coherent beats fresh-but-mixed: a row
    silently denominated in two currencies is not recoverable."""
    alt = (alt_currency or "USD").upper()
    if alt == "USD":
        return 1.0
    if alt == (sec_currency or "USD").upper() and sec_fx:
        return sec_fx                       # same currency, rate already fetched
    try:
        return fetch_rate(alt) or None
    except Exception:
        return None


def apply_stale_fallback(ff, alt, sec_currency, sec_fx, fetch_rate):
    """The whole stale-financials decision in one testable place: get the right rate,
    apply the fallback only if it is genuinely newer, convert what it moved, and say
    what happened. Returns (applied, [notes]) — the caller attaches the notes."""
    notes = []
    # named fx_rate, not rate: contracts.fx_conversion_sites finds every place money is
    # multiplied by an exchange rate by looking for `* ...fx...`.
    fx_rate = fallback_rate(getattr(alt, "currency", None), sec_currency, sec_fx, fetch_rate)
    ccy = (getattr(alt, "currency", None) or "USD").upper()
    if not fx_rate:
        notes.append(f"งบสำรองรายงานเป็น {ccy} แต่หาอัตราแลกเปลี่ยนไม่ได้ — ไม่ใช้แหล่งสำรอง "
                     "คงงบเดิมไว้และหัก confidence (ตัวเลขคนละสกุลปนกันอันตรายกว่างบเก่า)")
        return False, notes
    if not fallback_is_fresher(getattr(ff, "fiscal_year", None), getattr(alt, "fiscal_year", None)):
        notes.append("แหล่งสำรองไม่ได้ใหม่กว่า SEC — คงตัวเลขเดิมไว้และหัก confidence")
        return False, notes
    # Convert the FALLBACK's own figures before they move. Converting ff afterwards
    # also multiplied the SEC values the fallback did not replace (already USD), and
    # TSM's capex came out 32x too small.
    if fx_rate != 1.0:
        for k in FALLBACK_MONEY:
            v = getattr(alt, k, None)
            if isinstance(v, (int, float)):
                setattr(alt, k, v * fx_rate)
    applied, note = refresh_from_fallback(ff, alt)
    notes.append(note)
    if fx_rate != 1.0:
        notes.append(f"converted {ccy}->USD @ {round(fx_rate, 4)} "
                     "(แหล่งสำรอง — คนละสกุลกับที่ SEC รายงาน)")
    return True, notes


def fallback_is_fresher(sec_fiscal_year, alt_fiscal_year):
    """True when the alternative source is reporting a strictly later fiscal year end."""
    try:
        a = dt.date.fromisoformat(str(sec_fiscal_year)[:10]) if sec_fiscal_year else None
        b = dt.date.fromisoformat(str(alt_fiscal_year)[:10]) if alt_fiscal_year else None
    except (TypeError, ValueError):
        return False
    return bool(b and (a is None or b > a))


def refresh_from_fallback(ff, alt_facts):
    """Replace stale SEC scalars with a fresher source's, and DROP the SEC series that
    those scalars no longer belong with. Returns (applied, note).

    Keeping the SEC series would put a value from one clock beside a series from
    another. A blank strip that says why is correct; a strip drawn from two different
    years of the same company is not.

    `alt_facts` is a FinancialFacts already populated by the fallback source."""
    if not fallback_is_fresher(getattr(ff, "fiscal_year", None),
                               getattr(alt_facts, "fiscal_year", None)):
        return False, None
    old_fy = getattr(ff, "fiscal_year", None)
    moved = []
    for name in FALLBACK_SCALARS:
        v = getattr(alt_facts, name, None)
        if v is not None:
            ff.set(name, v, (alt_facts.provenance or {}).get(name, "fallback"))
            moved.append(name)
    ff.set("fiscal_year", alt_facts.fiscal_year, "fallback")
    cleared = []
    # every old-period figure the fallback did not replace (a share COUNT excepted: it
    # barely moves in a year and every per-share figure divides by it)
    stale = [n for n in (*FALLBACK_SCALARS, *FALLBACK_CLEARS) if n != "shares_diluted"]
    for name in dict.fromkeys(stale):
        if name not in moved and getattr(ff, name, None) is not None:
            setattr(ff, name, None)
            (ff.provenance or {}).pop(name, None)
            cleared.append(name)
    dropped = []
    for name in FALLBACK_INVALIDATES:
        if getattr(ff, name, None):
            setattr(ff, name, [] if not isinstance(getattr(ff, name), dict) else {})
            dropped.append(name)
    if getattr(ff, "ic_components_dated", None):
        ff.ic_components_dated = {}
        dropped.append("ic_components_dated")
    note = (f"งบจาก SEC เก่า ({old_fy}) — ใช้ตัวเลขจากแหล่งสำรองงวด {alt_facts.fiscal_year} แทน "
            f"{len(moved)} รายการ · ซีรีส์ย้อนหลังของ SEC ถูกล้าง {len(dropped)} ชุด "
            f"และค่างวดเก่าที่ไม่มีตัวแทน {len(cleared)} รายการ "
            f"เพราะคนละงวดกับตัวเลขใหม่ (เทรนด์/ROIC รายปีจะว่างจนกว่า SEC จะอัปเดต)")
    return True, note


def apply(ff, today=None):
    """Assess, attach the messages to ff.flags, and return the findings.

    Flags are prefixed so the card can style them and so a later reader can tell a
    data-condition warning from a valuation caveat."""
    findings = assess(ff, today)
    for f in findings:
        note = f"DATA[{f['severity']}] {f['message']}"
        if note not in ff.flags:
            ff.flags.append(note)
    return findings
