"""Normalize TTM operating income for one-time charges before anything values it.

Damodaran values the operating income a business will keep earning, so charges that
describe the past, not the run-rate, come out of EBIT first:

  impairment        a write-down of capital already spent; no cash leaves this year and
                    the investment was counted when it was made. Always added back.
  restructuring     added back only when it does not recur. A charge filed in 3 of the
  acquired IPR&D    last 4 fiscal years at > 1% of revenue is how the firm operates
                    (Damodaran: a "one-time" charge every year is an operating expense).

Measured 2026-10-19 on PFE: impairments 4.94B and restructuring 1.09B sat inside a
10.8% margin, and its FCFF DCF read $0.76 a share. The reported figure is kept in
`operating_income_gaap`. Pure apart from the FinancialFacts it edits.
"""
RECURRING_YEARS, RECURRING_OF, RECURRING_MIN_SHARE = 3, 4, 0.01
# a guard on what a mis-tagged concept could do: the add-back never exceeds this share
# of revenue
MAX_ADDBACK_SHARE = 0.25
# the charges were deducted for tax, so the add-back comes back taxed at the marginal
# rate they saved (Damodaran's US marginal rate, sources.damodaran.MARGINAL_TAX_US)
ADDBACK_TAX_RATE = 0.25


def _recurs(dated, revenue_dated):
    rev = {d: v for d, v in (revenue_dated or [])}
    years = sorted(((d, v) for d, v in (dated or []) if d in rev and rev[d]),
                   reverse=True)[:RECURRING_OF]
    hits = sum(1 for d, v in years if v and v / rev[d] > RECURRING_MIN_SHARE)
    return hits >= RECURRING_YEARS


def normalize_operating_income(ff):
    """Add the non-recurring charges back to ff.operating_income. Returns the
    add-back (0.0 when nothing applies)."""
    oi, rev = ff.operating_income, ff.revenue
    if oi is None or not rev or rev <= 0:
        return 0.0
    parts = []
    if ff.impairment_ttm and ff.impairment_ttm > 0:
        parts.append(("impairment", ff.impairment_ttm))
    for name, ttm, dated in (("restructuring", ff.restructuring_ttm, ff.restructuring_annuals_dated),
                             ("acquired IPR&D", ff.acquired_iprd_ttm, ff.acquired_iprd_annuals_dated)):
        if not ttm or ttm <= 0:
            continue
        if _recurs(dated, ff.revenue_annuals_dated):
            ff.flags.append(f"{name} ${ttm/1e9:.2f}B kept in EBIT: it recurs (filed in "
                            f"{RECURRING_YEARS}+ of the last {RECURRING_OF} years), so it is "
                            f"an operating cost")
            continue
        parts.append((name, ttm))
    total = sum(v for _, v in parts)
    if total <= 0:
        return 0.0
    capped = min(total, MAX_ADDBACK_SHARE * rev)
    ff.operating_income_gaap = oi
    ff.set("operating_income", oi + capped,
           (ff.provenance.get("operating_income") or "sec") + " + one-time add-back")
    ff.flags.append("EBIT normalized: +" + " +".join(f"${v/1e9:.2f}B {n}" for n, v in parts)
                    + (f" (capped at {MAX_ADDBACK_SHARE:.0%} of revenue)" if capped < total else "")
                    + f" -> margin {oi/rev:.1%} to {(oi+capped)/rev:.1%}")
    return capped


def annual_addbacks(f):
    """{fiscal-year end: add-back} under the TTM rules, per year: that year's
    impairment always, restructuring and acquired IPR&D only when the series does not
    recur, the sum capped at MAX_ADDBACK_SHARE of that year's revenue."""
    rev = {d: v for d, v in (f.revenue_annuals_dated or [])}
    parts = [dict(f.impairment_annuals_dated or [])]
    for dated in (f.restructuring_annuals_dated, f.acquired_iprd_annuals_dated):
        if dated and not _recurs(dated, f.revenue_annuals_dated):
            parts.append(dict(dated))
    out = {}
    for d, r in rev.items():
        total = sum(max(0.0, p.get(d) or 0.0) for p in parts)
        if total > 0 and r and r > 0:
            out[d] = min(total, MAX_ADDBACK_SHARE * r)
    return out
