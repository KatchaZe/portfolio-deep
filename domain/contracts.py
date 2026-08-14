"""
contracts — what CLOCK every FinancialFacts field is measured on, and a machine check
that two different clocks are never divided or subtracted without saying why.

THE DEFECT FAMILY THIS CLOSES
-----------------------------
Across three review rounds the same shape kept coming back: a value whose NAME no
longer describes what is in it. Not a formula error — every line was locally correct —
but two values from different clocks combined as if they shared one:

    P3-1  f.revenue (TTM)  /  revenue_annuals[1] (fiscal year, two periods back)
          -> MSFT's card printed +29.8% growth against a reported +15%
    D2    operating_income (TTM) / revenue (TTM)  vs  oia[1] / ann[1]  (FY-2)
          -> a "1-year margin trend" spanning about two
    D3    R&D-capitalized ROIC  minus  raw ROIC
          -> ASML showed a 25.4pp collapse in returns that never happened
    B6    a list read by POSITION when its order depends on the source
          -> TSLA and REGN read a quarter nine months stale as "latest"

Counting the exposure: 48 of 79 fields sit on a clock, which is 1,128 pairs that could
be mismatched. Nobody reads 1,128 pairs. A machine can.

HOW IT WORKS
------------
1. `CLOCK` declares the clock of every field. A field with no entry FAILS the test —
   adding a field forces the author to answer "measured over what period?" once, at the
   point where the answer is obvious, instead of leaving it to be inferred later by
   whoever divides by it.
2. `scan_source()` walks the AST of the modules that do the arithmetic and reports any
   `f.A / f.B` or `f.A - f.B` where A and B are on different clocks.
3. `ALLOWED` holds the pairs that are deliberately cross-clock, each with a reason.
   That list is the review artifact: it is short, and every line on it is a decision
   someone made on purpose.

The check is intentionally narrow: division and subtraction only, following simple
local aliases (`ann = f.revenue_annuals` then `rev_1y = ann[1]`) but nothing cleverer.
It will not catch a clock crossing that passes through a function call or a container.
It does catch the shape that shipped four times, and it is verified against a
reconstruction of P3-1 in tests/test_contracts.py — a checker nobody has watched fail
is not a checker.
"""
import ast
import os
import re

# --- clocks ----------------------------------------------------------------
# TTM       trailing twelve months, built by summing quarters — moves every quarter
# FY        one fiscal year, or a list of them (newest first)
# FY_DATED  the same, carrying its own fiscal-year-end date: [[end, value], ...]
# QUARTER   per-quarter map or list
# INSTANT   a balance-sheet point in time (a stock, not a flow)
# SPOT      a market observation right now (price, market cap, beta)
# FORWARD   an expectation about the future (consensus)
# STATIC    not a measurement over time (ticker, currency, flags, provenance)
TTM, FY, FY_DATED, QUARTER, INSTANT, SPOT, FORWARD, STATIC = (
    "TTM", "FY", "FY_DATED", "QUARTER", "INSTANT", "SPOT", "FORWARD", "STATIC")

# Flows can only be compared with flows over the SAME window; stocks only with stocks
# at the same instant. These two are the pairs that produced every defect above.
CLOCK = {
    # --- identity / metadata -------------------------------------------------
    "ticker": STATIC, "company": STATIC, "sector": STATIC, "currency": STATIC,
    "fiscal_year": STATIC, "as_of": STATIC, "provenance": STATIC, "confidence": STATIC,
    "confidence_tier": STATIC, "flags": STATIC, "peers": STATIC, "n_analysts": STATIC,
    "per_share_unit_ok": STATIC,

    # --- market, observed now ------------------------------------------------
    "price": SPOT, "market_cap": SPOT, "beta": SPOT, "own_pe_pctile": SPOT,

    # --- income statement, TTM (sec_edgar.ttm sums the last four quarters) ----
    "revenue": TTM, "operating_income": TTM, "net_income": TTM, "eps_gaap": TTM,
    "income_before_tax": TTM, "tax_expense": TTM, "interest_expense": TTM,
    "rnd_expense": TTM, "capex": TTM, "dep_amort": TTM, "sbc": TTM, "cfo": TTM,
    "acquisitions_net": TTM, "dividend_ps": TTM,

    # --- balance sheet, a point in time --------------------------------------
    "total_debt": INSTANT, "cash": INSTANT, "equity": INSTANT, "total_assets": INSTANT,
    "receivables": INSTANT, "inventory": INSTANT, "accounts_payable": INSTANT,
    "operating_leases": INSTANT, "deferred_revenue": INSTANT, "shares_diluted": INSTANT,
    "equity_prior": INSTANT, "cash_prior": INSTANT, "total_debt_prior": INSTANT,
    "receivables_prior": INSTANT, "inventory_prior": INSTANT,
    "accounts_payable_prior": INSTANT, "operating_leases_prior": INSTANT,
    "deferred_revenue_prior": INSTANT,

    # --- fiscal-year series (newest first, NO date attached) ------------------
    "revenue_annuals": FY, "operating_income_annuals": FY, "rnd_annuals": FY,
    "shares_diluted_annuals": FY,

    # --- fiscal-year series WITH the year-end date ---------------------------
    "revenue_annuals_dated": FY_DATED, "operating_income_annuals_dated": FY_DATED,
    "cfo_annuals_dated": FY_DATED, "capex_annuals_dated": FY_DATED,
    "gross_profit_annuals_dated": FY_DATED, "cost_of_revenue_annuals_dated": FY_DATED,
    "eps_annuals_dated": FY_DATED, "ic_components_dated": FY_DATED,

    # --- quarterly -----------------------------------------------------------
    "revenue_quarters": QUARTER, "operating_income_quarters": QUARTER,
    "eps_quarters": QUARTER, "earnings_surprises": QUARTER,
    "rev_surprises_fmp": QUARTER, "eps_surprises_backfill": QUARTER,
    "rev_estimate_curq": QUARTER,

    # --- expectations about the future ---------------------------------------
    "forward_eps": FORWARD, "forward_eps_raw": FORWARD, "forward_eps_low": FORWARD,
    "forward_eps_high": FORWARD, "forward_eps_spread_pct": FORWARD,
    "forward_eps_n": FORWARD, "forward_eps_sources": FORWARD,
    "growth_lt": FORWARD, "fwd_growth_near": FORWARD, "fwd_growth_far": FORWARD,
    "peer_median_growth": FORWARD, "terminal_roic_sector": FORWARD,
}

# --- units -----------------------------------------------------------------
# MONEY      a currency amount. Reported by the filer in ITS OWN currency, so it must be
#            FX-converted in pipeline/normalize before anything compares it to a USD
#            price. This is the REV-1 defect: `sbc` was money, was NOT in the conversion
#            list, and an IFRS filer's DKK compensation was divided by a USD market cap.
# SHARES     a share count. Never FX-converted — doing so silently rescales EPS.
# PER_SHARE  money DIVIDED by shares (EPS, dividend/share). Converting one side of that
#            ratio without the other breaks it, so these are excluded on purpose.
# RATIO      a pure number (growth rates, margins, percentiles, beta) — dimensionless.
# COUNT      an integer tally (analyst count, source count).
# TEXT       identity/metadata.
MONEY, SHARES, PER_SHARE, RATIO, COUNT, TEXT = (
    "MONEY", "SHARES", "PER_SHARE", "RATIO", "COUNT", "TEXT")

UNIT = {
    # money — every one of these MUST be FX-converted (test_contracts enforces it)
    "revenue": MONEY, "operating_income": MONEY, "net_income": MONEY,
    "income_before_tax": MONEY, "tax_expense": MONEY, "interest_expense": MONEY,
    "rnd_expense": MONEY, "capex": MONEY, "dep_amort": MONEY, "sbc": MONEY, "cfo": MONEY,
    "acquisitions_net": MONEY, "total_debt": MONEY, "cash": MONEY, "equity": MONEY,
    "total_assets": MONEY, "receivables": MONEY, "inventory": MONEY,
    "accounts_payable": MONEY, "operating_leases": MONEY, "deferred_revenue": MONEY,
    "equity_prior": MONEY, "cash_prior": MONEY, "total_debt_prior": MONEY,
    "receivables_prior": MONEY, "inventory_prior": MONEY, "accounts_payable_prior": MONEY,
    "operating_leases_prior": MONEY, "deferred_revenue_prior": MONEY,
    "revenue_annuals": MONEY, "operating_income_annuals": MONEY, "rnd_annuals": MONEY,
    "revenue_annuals_dated": MONEY, "operating_income_annuals_dated": MONEY,
    "cfo_annuals_dated": MONEY, "capex_annuals_dated": MONEY,
    "gross_profit_annuals_dated": MONEY, "cost_of_revenue_annuals_dated": MONEY,
    "ic_components_dated": MONEY, "revenue_quarters": MONEY,
    "operating_income_quarters": MONEY, "market_cap": MONEY,
    # share counts — deliberately NOT converted
    "shares_diluted": SHARES, "shares_diluted_annuals": SHARES,
    # per-share — money over shares; converting one leg would break the ratio
    "price": PER_SHARE, "eps_gaap": PER_SHARE, "dividend_ps": PER_SHARE,
    "forward_eps": PER_SHARE, "forward_eps_raw": PER_SHARE, "forward_eps_low": PER_SHARE,
    "forward_eps_high": PER_SHARE, "eps_annuals_dated": PER_SHARE,
    "eps_quarters": PER_SHARE, "forward_eps_sources": PER_SHARE,
    # dimensionless
    "beta": RATIO, "growth_lt": RATIO, "fwd_growth_near": RATIO, "fwd_growth_far": RATIO,
    "peer_median_growth": RATIO, "terminal_roic_sector": RATIO, "own_pe_pctile": RATIO,
    "forward_eps_spread_pct": RATIO, "confidence": RATIO,
    # counts
    "n_analysts": COUNT, "forward_eps_n": COUNT,
    # text / structures
    "ticker": TEXT, "company": TEXT, "sector": TEXT, "currency": TEXT,
    "fiscal_year": TEXT, "as_of": TEXT, "provenance": TEXT, "confidence_tier": TEXT,
    "per_share_unit_ok": TEXT,          # a verdict, not a quantity — never arithmetic
    "flags": TEXT, "peers": TEXT, "earnings_surprises": TEXT, "rev_surprises_fmp": TEXT,
    "eps_surprises_backfill": TEXT, "rev_estimate_curq": TEXT,
}

# Money fields that are deliberately NOT FX-converted, with the reason.
FX_EXEMPT = {
    "market_cap":
        "quoted for the US-LISTED security, so it is already in USD (and for an ADR it "
        "embeds the depositary ratio — see the note in facts.py). Converting it would "
        "double-apply the rate.",
}

# Units that may be combined. Money over money is a ratio; money over shares is a
# per-share figure; anything over a ratio stays in its own unit.
UNIT_COMPATIBLE = {frozenset({MONEY, MONEY}), frozenset({MONEY, SHARES}),
                   frozenset({MONEY, RATIO}), frozenset({PER_SHARE, PER_SHARE}),
                   frozenset({PER_SHARE, RATIO}), frozenset({RATIO, RATIO}),
                   frozenset({SHARES, SHARES}), frozenset({SHARES, RATIO}),
                   frozenset({COUNT, COUNT}), frozenset({COUNT, RATIO}),
                   frozenset({MONEY, COUNT})}

# Clocks that may be mixed freely: a ratio of a flow to the stock that produced it is
# standard finance (ROIC = TTM NOPAT / INSTANT invested capital), and anything against
# a market observation is a valuation multiple by construction.
COMPATIBLE = {frozenset({TTM, INSTANT}), frozenset({TTM, SPOT}), frozenset({INSTANT, SPOT}),
              frozenset({FORWARD, SPOT}), frozenset({FORWARD, TTM})}

# Deliberate exceptions, each with the reason it is safe. Keep this list short — a long
# one means the clocks are not really being respected.
# Each entry is (field_a, field_b, function_or_None) -> reason. SCOPED to a function
# wherever possible: the first version of this list whitelisted ("revenue",
# "revenue_annuals") globally, which is the exact P3-1 pair, and the checker then walked
# past a deliberate reproduction of the defect it was written to catch. A blanket
# exception for the pair you are trying to police is not an exception, it is a hole.
ALLOWED = {
    ("revenue", "revenue_annuals", "reverse_dcf"):
        "the fallback for callers that supply no actual_growth (skill-parity). The "
        "engine always passes rev_growth_yoy, so this branch is dead in production — "
        "see the P3-1 note in reverse_dcf.",
}


# --- G3: what CLOCK a function parameter expects -----------------------------
# Closes the cross-function hole. The alias tracker stops at the function boundary, but
# the P3-1 shape at the CALL SITE is visible without any interprocedural analysis:
# somebody passes a TTM into a parameter that means "the prior fiscal year". Declaring
# the expectation once turns that into a mechanical check at every call.
PARAM_CLOCK = {
    "reverse_dcf": {"revenue": TTM, "rev_1y": FY, "actual_growth": RATIO,
                    "margin": RATIO, "margin_now": RATIO, "market_cap": SPOT},
    "earnings_quality": {"revenue": TTM, "revenue_prior": FY, "revenue_growth": RATIO,
                         "net_income": TTM, "cfo": TTM, "total_assets": INSTANT,
                         "sbc": TTM, "receivables": INSTANT, "receivables_prior": INSTANT},
    "working_capital_change": {},
    "fundamental_growth": {"capex": TTM, "dep_amort": TTM, "acquisitions": TTM},
    "terminal_margin": {"operating_income": TTM, "revenue": TTM},
    # take a fact SERIES directly, so a scalar passed in by mistake is visible here
    "rd_capitalize": {"rnd_annuals": FY},
    "share_count_growth": {"shares_annuals": FY},
}


def clock_of(field):
    return CLOCK.get(field)


def unit_of(field):
    return UNIT.get(field)


def undeclared_units(facts_cls):
    """Fields with no unit declared. Must be empty."""
    return sorted(set(facts_cls.__dataclass_fields__) - set(UNIT))


FX_BLOCK_START = 'if ff.currency and ff.currency != "USD":'


# --------------------------------------------------------------------------- #
#  Every place currency is converted — registered, not assumed                  #
# --------------------------------------------------------------------------- #
# L2 (2026-08-10). `unconverted_money` below was written for REV-1 and only ever
# opened pipeline/normalize.py. DQ2 later added a SECOND conversion block, in
# refresh.py, for the stale-financials fallback — and `eps_gaap` was moved by that
# fallback but left out of its conversion list. TSM's EPS stayed in TWD next to a USD
# ADR price, the forward-EPS gate then handed it on (L1), and the row printed a
# $8,612 fair value with a BUY. The checker could not see any of it, because a new
# conversion site is invisible to a checker that knows one filename.
#
# So the site list is now the thing being policed: an UNREGISTERED conversion site is
# a failure. Adding one makes the build red until its required field set is declared.
#
#   path -> (block start marker, block end marker, required-set name)
#     "ALL_MONEY"  every MONEY field on the schema, minus FX_EXEMPT
#     "FALLBACK"   the money/per-share subset of dataquality.FALLBACK_SCALARS
FX_SITES = {
    "pipeline/normalize.py": (FX_BLOCK_START, "\n    # 3)", "ALL_MONEY"),
    "pipeline/dataquality.py": ("if fx_rate != 1.0:", "\n    return True, notes", "FALLBACK"),
}
# A block that only LOOKS UP a rate (`fx = yahoo.fetch_fx_to_usd(ccy)`) or merely checks
# for one is not a conversion. MULTIPLYING by it is — so that is the signal, on its own.
#
# 2026-08-11: this used to require a `!= "USD"` test near the multiplication, which was
# a second condition that could drift away from the first. Moving the fallback's
# conversion from refresh.py into dataquality.py separated them and the scanner went
# blind to BOTH sites at once — the exact hole the registry exists to close, reopened
# by a refactor. One signal now: if a module multiplies by an fx rate, it is registered
# here or the build fails.
_FX_APPLY = re.compile(r"\*\s*\w*fx\w*")     # * fx, * fx_rate, * alt_fx


def _read(root, rel, sources=None):
    """Source of `rel`, or the caller's substitute for it.

    `sources` exists so a mutation test can ask "what would this checker say about THIS
    text" without writing mutated code into the working tree — a crash mid-test would
    otherwise leave a production file in the broken state on purpose."""
    if sources and rel in sources:
        return sources[rel]
    try:
        with open(os.path.join(root, *rel.split("/")), encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def fx_conversion_sites(root, scan=("pipeline", "sources", "domain")):
    """Files that actually multiply a value by an FX rate, as (rel_path, registered)."""
    out = []
    for pkg in scan:
        d = os.path.join(root, pkg)
        for fn in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            if not fn.endswith(".py") or fn == "contracts.py":
                continue
            rel = f"{pkg}/{fn}"
            if _FX_APPLY.search(_read(root, rel)):
                out.append((rel, rel in FX_SITES))
    return out


def unregistered_fx_sites(root):
    """Conversion sites the contract has never been told about — the L2 hole."""
    return [rel for rel, known in fx_conversion_sites(root) if not known]


def _fallback_money(root, sources=None):
    """The money/per-share subset of dataquality.FALLBACK_SCALARS, read from source so
    `domain` never has to import `pipeline`. Also refuses a HAND-TYPED FALLBACK_MONEY:
    the whole point is that the conversion list is derived from the unit contract, so it
    cannot drift away from the list of fields the fallback actually moves."""
    src = _read(root, "pipeline/dataquality.py", sources)
    m = re.search(r"FALLBACK_SCALARS\s*=\s*\((.*?)\)\n", src, re.S)
    scalars = set(re.findall(r'"([a-z_0-9]+)"', m.group(1))) if m else set()
    required = {f for f in scalars if UNIT.get(f) in (MONEY, PER_SHARE)}
    d = re.search(r"FALLBACK_MONEY\s*=\s*(.*?)\n\n", src, re.S)
    derived = bool(d and "UNIT" in d.group(1) and "FALLBACK_SCALARS" in d.group(1))
    return required, derived


def unconverted_money_at(root, rel, sources=None):
    """MONEY/PER_SHARE fields a registered conversion site fails to convert."""
    start, end_marker, required_set = FX_SITES[rel]
    src = _read(root, rel, sources)
    i = src.find(start)
    if i < 0:
        return [f"<conversion block not found in {rel} — it was restructured>"]
    block = src[i:]
    end = block.find(end_marker)
    block = block[:end] if end > 0 else block[:4000]
    if required_set == "ALL_MONEY":
        return unconverted_money(src)
    required, derived = _fallback_money(root, sources)
    if not derived:
        return ["<FALLBACK_MONEY is not derived from UNIT — it can drift again>"]
    seen = set(re.findall(r'"([a-z_0-9]+)"', block)) | set(re.findall(r"\.([a-z_0-9]+)", block))
    if "FALLBACK_MONEY" in block:
        seen |= required             # derived above, so referencing it covers the set
    return sorted(required - seen)


def unconverted_money(normalize_src):
    """MONEY fields that pipeline/normalize never FX-converts.

    This is REV-1 turned into a check. `sbc` was declared on FinancialFacts, consumed by
    two scoring rules, and absent from `_MONEY` — so for an IFRS filer the engine
    compared DKK compensation against a USD market cap and nothing said a word. Reading
    the list will not catch the next one; comparing the list to the schema will.

    Only the CURRENCY-CONVERSION BLOCK is searched, not the whole module: a field named
    somewhere else in normalize (in a validation rule, say) is not thereby converted, and
    counting that as coverage would make the check pass while the bug is live — the
    failure mode this whole file exists to remove. Both spellings are accepted, because
    the block converts scalars from a quoted list and series by direct attribute
    assignment (`ff.revenue_annuals = [v * fx_rate ...]`)."""
    i = normalize_src.find(FX_BLOCK_START)
    if i < 0:
        return ["<FX conversion block not found — normalize.build was restructured>"]
    # to the end of the branch: the next line at the same indent that is not part of it
    block = normalize_src[i:]
    end = block.find("\n    # 3)")
    block = block[:end] if end > 0 else block[:4000]
    declared = {k for k, v in UNIT.items() if v == MONEY} - set(FX_EXEMPT)
    seen = set(re.findall(r'"([a-z_0-9]+)"', block)) | set(re.findall(r"ff\.([a-z_0-9]+)", block))
    # a field listed in a module-level tuple that the block iterates counts as covered
    for name in ("_MONEY", "_MONEY_SERIES_DATED"):
        if name in block:
            m = re.search(name + r"\s*=\s*\((.*?)\)\n\n", normalize_src, re.S)
            if m:
                seen |= set(re.findall(r'"([a-z_0-9]+)"', m.group(1)))
    return sorted(declared - seen)


def undeclared_fields(facts_cls):
    """Fields on FinancialFacts with no clock declared. Must be empty."""
    return sorted(set(facts_cls.__dataclass_fields__) - set(CLOCK))


def stale_declarations(facts_cls):
    """Declared clocks for fields that no longer exist. Must be empty."""
    return sorted(set(CLOCK) - set(facts_cls.__dataclass_fields__))


def _attr_field(node):
    """`f.revenue` / `ff.revenue` -> 'revenue', else None."""
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        if node.value.id in ("f", "ff", "facts") and node.attr in CLOCK:
            return node.attr
    return None


def _aliases(fn):
    """Local names that stand for a fact field, within one function.

    Needed because the defect this file exists to catch did NOT read like
    `f.revenue / f.revenue_annuals`. It read:

        ann    = f.revenue_annuals or []
        rev_1y = ann[1]
        ...
        a1 = revenue / rev_1y - 1          # 167 lines later

    Two hops and a function boundary, and each line is unremarkable on its own. A
    checker that only looks at attribute-on-attribute arithmetic would have declared
    P3-1 clean, so it follows simple assignment chains: `x = f.field`,
    `x = f.field or []`, and `y = x[i]` where x is already an alias. Anything more
    involved than that is out of scope on purpose — a checker nobody trusts because it
    guesses is worse than a narrow one that never cries wolf."""
    alias = {}
    for node in ast.walk(fn):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        tgt = node.targets[0]
        if not isinstance(tgt, ast.Name):
            continue
        val = _unwrap(node.value)
        fld = _attr_field(val)
        if fld:
            alias[tgt.id] = fld
            continue
        # `rev_1y = ann[1]` — inherits the clock of the series it indexes
        if isinstance(val, ast.Subscript):
            base = _unwrap(val.value)
            src = _attr_field(base) or (alias.get(base.id) if isinstance(base, ast.Name) else None)
            if src:
                alias[tgt.id] = src
    return alias


def _unwrap(val):
    """Strip the wrappers a fact field is normally read through, so the underlying
    attribute is still visible: `f.x or []`, `ann[1] if len(ann) > 1 else None`,
    `(f.x)`. These are how the codebase actually writes it — `rev_1y = ann[1] if
    len(ann) > 1 else None` is the exact line P3-1 lived behind, and a checker that
    only understood a bare subscript walked straight past it."""
    for _ in range(4):                                  # bounded: no pathological nesting
        if isinstance(val, ast.BoolOp) and val.values:  # `f.x or []`
            val = val.values[0]
        elif isinstance(val, ast.IfExp):                # `a[1] if cond else None`
            val = val.body
        else:
            return val
    return val


def _field_of(node, alias):
    """Resolve an expression to the fact field it ultimately reads, or None.

    Handles the three shapes a field appears in: the attribute itself (`f.revenue`), a
    local alias (`rev_1y`), and an index into an aliased series (`ann[1]`) — the last
    one written inline rather than via a variable, which is how `oia[1] / ann[1]` is
    written throughout the engine."""
    node = _unwrap(node)
    fld = _attr_field(node)
    if fld:
        return fld
    if isinstance(node, ast.Name):
        return alias.get(node.id)
    if isinstance(node, ast.Subscript):
        base = _unwrap(node.value)
        return _attr_field(base) or (alias.get(base.id) if isinstance(base, ast.Name) else None)
    return None


def _pair_violation(a, b, fname, kind):
    """(a, b) combined — return a reason string when they must not be, else None."""
    if not a or not b or a == b:
        return None
    if any(k in ALLOWED for k in ((a, b, fname), (b, a, fname), (a, b, None), (b, a, None))):
        return None
    ca, cb = CLOCK.get(a), CLOCK.get(b)
    if ca and cb and ca != cb and frozenset({ca, cb}) not in COMPATIBLE:
        return f"clock {ca} vs {cb}"
    ua, ub = UNIT.get(a), UNIT.get(b)
    if ua and ub and ua != ub and frozenset({ua, ub}) not in UNIT_COMPATIBLE:
        return f"unit {ua} vs {ub}"
    return None


def scan_source(path):
    """[(lineno, left, right, reason, kind)] — clock or unit crossings.

    Three kinds are checked, and the second and third exist because the first was not
    enough:
      binop    `a / b`, `a - b`
      compare  `a > b` — comparing a TTM against a fiscal year is exactly as wrong as
               dividing them, and reads even more innocently
      call     `foo(rev_1y=f.revenue)` — the cross-FUNCTION hole. The alias tracker stops
               at the function boundary, but a parameter that declares what it expects
               (PARAM_CLOCK) makes the mismatch visible at the call site without any
               interprocedural analysis. This is the P3-1 shape one level up.
    """
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)
    funcs = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    # Nodes inside a function must be attributed to THAT function, not to the module.
    # Walking the module also descends into every function body, so without this the
    # same expression is judged twice — once with the enclosing function's name (where a
    # scoped ALLOWED entry applies) and once with no name at all (where it does not).
    inside = {id(n) for fn in funcs for n in ast.walk(fn)}
    hits = []
    for fn in funcs + [tree]:
        alias = _aliases(fn)
        fname = getattr(fn, "name", None)
        at_module = fn is tree
        for node in ast.walk(fn):
            if at_module and id(node) in inside:
                continue

            if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Div, ast.Sub)):
                a, b = _field_of(node.left, alias), _field_of(node.right, alias)
                why = _pair_violation(a, b, fname, "binop")
                if why:
                    hits.append((node.lineno, a, b, why, "binop"))

            elif isinstance(node, ast.Compare) and len(node.ops) == 1:
                if isinstance(node.ops[0], (ast.Lt, ast.LtE, ast.Gt, ast.GtE)):
                    a = _field_of(node.left, alias)
                    b = _field_of(node.comparators[0], alias)
                    why = _pair_violation(a, b, fname, "compare")
                    if why:
                        hits.append((node.lineno, a, b, why, "compare"))

            elif isinstance(node, ast.Call):
                callee = node.func.attr if isinstance(node.func, ast.Attribute) else (
                    node.func.id if isinstance(node.func, ast.Name) else None)
                expected = PARAM_CLOCK.get(callee)
                if not expected:
                    continue
                for kw in node.keywords:
                    if kw.arg is None or kw.arg not in expected:
                        continue
                    fld = _field_of(kw.value, alias)
                    if not fld:
                        continue
                    want, got = expected[kw.arg], CLOCK.get(fld)
                    if got and want and want != got and frozenset({want, got}) not in COMPATIBLE:
                        hits.append((node.lineno, f"{callee}({kw.arg}=)", fld,
                                     f"parameter expects {want}, got {got}", "call"))

    out = []
    for h in hits:
        if h not in out:
            out.append(h)
    return sorted(out)


DEFAULT_TARGETS = ("domain/engine/deep_v82.py", "domain/engine/deep_v73.py",
                   "domain/engine/young_dcf.py", "domain/trend.py",
                   "pipeline/refresh.py", "pipeline/normalize.py", "pipeline/validate.py")


def scan_all(root, targets=DEFAULT_TARGETS):
    out = {}
    for rel in targets:
        p = os.path.join(root, rel)
        if os.path.exists(p):
            hits = scan_source(p)
            if hits:
                out[rel] = hits
    return out
