"""test_damodaran_round2 — country risk, industry mapping, FCFF DCF and the model router
(2026-10-17, round 2). Offline: every Damodaran table comes from the bundled snapshot.

The point of this round is that a ticker added TOMORROW is valued on the same
principles as the ones in the portfolio today, so most checks run on synthetic
companies the app has never seen (a bank, an oil major, an ADR, a firm with no FMP
profile) rather than on the current holdings.

Run: python -m tests.test_damodaran_round2
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config                                           # noqa: E402
from domain import company_type as CT                   # noqa: E402
from domain.engine import deep_v82 as E                 # noqa: E402
from domain.engine import fcff_dcf, financial           # noqa: E402
from domain.engine.contract import Valuation            # noqa: E402
from domain.facts import FinancialFacts                 # noqa: E402
from sources import country_risk as C                   # noqa: E402
from sources import damodaran as D                      # noqa: E402
from sources import industries as I                     # noqa: E402
from tests._harness import check, run_suite             # noqa: E402

RF = 0.045


def _facts(ticker="NEWCO", **kw):
    f = FinancialFacts(ticker)
    for k, v in kw.items():
        setattr(f, k, v)
    return f


def _operating(**kw):
    """A plain profitable operating company nobody has mapped."""
    base = dict(price=100.0, shares_diluted=1e9, market_cap=100e9, revenue=50e9,
                operating_income=10e9, net_income=7.5e9, total_debt=10e9, cash=5e9,
                equity=40e9, beta=1.1, growth_lt=0.10, eps_gaap=7.5, forward_eps=8.2,
                revenue_annuals=[50e9, 45e9, 41e9, 37e9],
                operating_income_annuals=[10e9, 9e9, 8e9, 7e9],
                income_before_tax=9.5e9, tax_expense=2.0e9, capex=3e9, dep_amort=2e9,
                interest_expense=0.5e9)
    base.update(kw)
    return _facts(**base)


# --------------------------------------------------------------------------
# 1. every industry the mapping can produce exists in every Damodaran table
# --------------------------------------------------------------------------
def test_mapping_targets_exist_in_tables():
    print("1 every mapped industry exists in the beta, margin and sales-to-capital tables")
    targets = (set(I.INDUSTRY_BY_TICKER.values()) | set(I.FMP_INDUSTRY.values())
               | {v for _, v in I.KEYWORD_RULES} | set(I.SECTOR_TO_INDUSTRY.values())
               | set(I.FINANCIAL_INDUSTRIES) | set(CT.CYCLICAL_INDUSTRIES))
    for mix in getattr(config, "INDUSTRY_MIX", {}).values():
        targets |= set(mix)
    for name, table in (("beta", D.BETA_FALLBACK), ("margin", D.MARGIN_FALLBACK),
                        ("sales-to-capital", D.S2C_FALLBACK)):
        missing = sorted(t for t in targets if D._norm(t) not in table)
        check(not missing, f"{name}: all {len(targets)} targets present", str(missing))
    check(len(D.BETA_FALLBACK) == len(D.MARGIN_FALLBACK) == len(D.S2C_FALLBACK) == 96,
          "full January 2026 tables bundled (94 industries + 2 totals)",
          f"{len(D.BETA_FALLBACK)}/{len(D.MARGIN_FALLBACK)}/{len(D.S2C_FALLBACK)}")
    for t, mix in getattr(config, "REVENUE_MIX", {}).items():
        unknown = [m for m in mix if C.market_crp(m, C.CRP_FALLBACK) is None]
        check(not unknown, f"REVENUE_MIX[{t}] markets all priced", str(unknown))


def test_industry_resolution_for_unseen_tickers():
    print("2 industry resolution: override, FMP industry, keyword, sector, business mix")
    check(I.industry_for("AXON") == ("Software (System & Application)", "override"),
          "AXON is software, not aerospace", str(I.industry_for("AXON")))
    check(I.industry_for("MELI")[0] == "Retail (General)" and I.industry_mix("MELI"),
          "MELI: largest business + a commerce/fintech mix", str(I.industry_for("MELI")))
    b, lbl = D.industry_beta_for("MELI", None)
    check(b == D.BETA_FALLBACK[D._norm("Retail (General)")] and "excluded" in lbl,
          "a lender's beta (un-levered over funding debt) is not averaged in", f"{b} {lbl}")
    s2c = D.industry_value(D.S2C_FALLBACK, None, I.industry_mix("MELI"), skip_financial=True)
    check(s2c == D.S2C_FALLBACK[D._norm("Retail (General)")],
          "nor its 0.07 sales-to-capital", str(s2c))
    check(I.industry_for("V", "Financial - Credit Services")[0] == "Information Services",
          "a payment network is not valued as a lender", str(I.industry_for("V")))
    check(I.industry_for("ZZLEND", "Financial - Credit Services")[0] in I.FINANCIAL_INDUSTRIES,
          "an unseen consumer lender is routed to the equity model")
    check(I.industry_for("ZZNEW", "Banks - Regional")[0] == "Banks (Regional)",
          "an unseen regional bank maps from its FMP industry", str(I.industry_for("ZZNEW", "Banks - Regional")))
    check(I.industry_for("ZZNEW", "Oil & Gas Integrated")[0] == "Oil/Gas (Integrated)",
          "an unseen oil major maps from its FMP industry", str(I.industry_for("ZZNEW", "Oil & Gas Integrated")))
    ind, basis = I.industry_for("ZZNEW", "Quantum Widgets & Something Reinsurance")
    check(basis == "keyword" and ind == "Reinsurance", "an unknown string falls to keywords",
          f"{ind} {basis}")
    check(I.industry_for("ZZNEW", None, None) == (None, None),
          "no profile at all: no invented industry", str(I.industry_for("ZZNEW", None, None)))


# --------------------------------------------------------------------------
# 3. country risk
# --------------------------------------------------------------------------
def test_country_risk():
    print("3 country risk premium over the US, by revenue mix or domicile")
    t = C.CRP_FALLBACK
    check(len(t) == 178 and abs(t["united states"] - 0.0023) < 1e-9, "178 countries, US 0.23%",
          f"{len(t)} {t.get('united states')}")
    meli, basis = C.crp_for("MELI", "US", t)
    expect = (15201 * .0324 + 6475 * .0246 + 5962 * .0971
              + 1255 * C.market_crp("Latin America", t)) / 28893 - .0023
    check(abs(meli - expect) < 1e-6 and "Brazil" in basis, "MELI weighted by its revenue",
          f"{meli} vs {expect} ({basis})")
    nvo, _ = C.crp_for("NVO", "DK", t)
    check(0 < nvo < 0.01, "NVO: mostly US/Europe revenue, small premium", str(nvo))
    tsm, b = C.crp_for("TSM", "TW", t)
    check(abs(tsm - (0.0078 - 0.0023)) < 1e-9 and "Taiwan" in b, "TSM: Taiwan operations", str(tsm))
    check(C.crp_for("ZZNEW", "US", t) == (0.0, "United States"), "a US domicile adds nothing")
    br, b = C.crp_for("ZZNEW", "BR", t)
    check(abs(br - (0.0324 - 0.0023)) < 1e-9 and "domicile Brazil" in b,
          "an unseen Brazilian firm is priced on its domicile", f"{br} {b}")
    check(C.crp_for("ZZNEW", "DK", t)[0] == 0.0, "an Aaa domicile is floored at 0, never negative")
    check("holding jurisdiction" in C.crp_for("ZZNEW", "KY", t)[1],
          "a Cayman holding company is told to add its revenue mix")
    check(C.crp_for("ZZNEW", "XX", t)[0] is None and C.crp_for("ZZNEW", None, t)[0] is None,
          "unknown or missing country: no premium invented")
    html = ("<table><tr><td>Country</td><td>Moody's rating</td><td>Country Risk Premium</td></tr>"
            "<tr><td>United States</td><td>Aa1</td><td>0.23%</td></tr>"
            "<tr><td>Turkey (updated February 2026)</td><td>B1</td><td>4.66%</td></tr></table>")
    p = C.parse_ctryprem_html(html)
    check(p.get("turkey") == 0.0466 and p.get("united states") == 0.0023,
          "page parsed by header text, footnoted names cleaned", str(p))
    check(C.parse_ctryprem_html(html.replace("United States", "Canada")) == {},
          "a page without the US row is rejected (snapshot kept)")


def test_cost_of_equity_carries_crp():
    print("4 Ke = Rf + beta x (ERP + CRP), in today's and the terminal WACC")
    check(abs(E.cost_of_equity(0.04, 1.2, 0.03) - (0.04 + 1.2 * (config.ERP + 0.03))) < 1e-12,
          "beta-scaled country premium")
    check(E.cost_of_equity(0.04, 1.2) == E.cost_of_equity(0.04, 1.2, 0.0), "no CRP = unchanged")
    base = E.DeepV82Engine().evaluate(_operating(), rf=RF)
    risky = E.DeepV82Engine().evaluate(_operating(crp=0.03, crp_basis="domicile Brazil"), rf=RF)
    kb, kr = base.key_metrics, risky.key_metrics
    check(kr["ke_pct"] > kb["ke_pct"] and kr["terminal_wacc_pct"] > kb["terminal_wacc_pct"],
          "country risk raises Ke and the stable WACC", f"{kb['ke_pct']}->{kr['ke_pct']}")
    check(risky.fv_dcf < base.fv_dcf, "and lowers the DCF value", f"{base.fv_dcf} -> {risky.fv_dcf}")
    check(kr["crp_pct"] == 3.0 and any("country risk premium" in x for x in risky.flags),
          "shown on the card and flagged", str(kr.get("crp_pct")))


# --------------------------------------------------------------------------
# 5. the models themselves
# --------------------------------------------------------------------------
def test_fcff_dcf_reduces_to_gordon():
    print("5 FCFF DCF reduces to the perpetuity when nothing changes")
    g, r, roic = 0.03, 0.08, 0.15
    v = fcff_dcf.value(revenue=100.0, margin_now=0.2, margin_target=0.2, g_high=g, g_stable=g,
                       sales_to_capital=roic / (0.2 * 0.75), tax_now=0.25, tax_marginal=0.25,
                       wacc=r, wacc_stable=r, roic_stable=roic, cash=0, debt=0, shares=1.0)
    nopat1 = 100 * 1.03 * 0.2 * 0.75
    gordon = nopat1 * (1 - g / roic) / (r - g)
    check(abs(v["firm_value"] - gordon) / gordon < 0.01, "matches NOPAT(1-g/ROIC)/(r-g)",
          f"{v['firm_value']} vs {gordon:.1f}")
    check(fcff_dcf.value(revenue=100.0, margin_now=0.2, margin_target=0.2, g_high=g, g_stable=g,
                         sales_to_capital=1, tax_now=.25, tax_marginal=.25, wacc=r,
                         wacc_stable=g, roic_stable=roic, cash=0, debt=0, shares=1) is None,
          "WACC <= g refuses rather than dividing by ~0")


def test_excess_return_model():
    print("6 excess-return model: ROE = Ke is worth book")
    v = financial.value(book_equity=100.0, roe=0.10, ke=0.10, g_high=0.05, g_stable=0.03, shares=1)
    check(abs(v["per_share"] - 100.0) < 1e-6, "book value when no excess return", str(v["per_share"]))
    hi = financial.value(book_equity=100.0, roe=0.15, ke=0.10, g_high=0.05, g_stable=0.03, shares=1)
    lo = financial.value(book_equity=100.0, roe=0.06, ke=0.10, g_high=0.05, g_stable=0.03, shares=1)
    check(hi["per_share"] > 100 > lo["per_share"], "above book when ROE > Ke, below when ROE < Ke",
          f"{hi['per_share']} / {lo['per_share']}")


def test_classifier():
    print("7 company type follows the business")
    check(CT.classify("Banks (Regional)", 5e9, 20e9)[0] is CT.CompanyType.FINANCIAL, "bank")
    check(CT.classify("Software (System & Application)", -1e9, 5e9)[0] is CT.CompanyType.YOUNG,
          "pre-profit")
    hist = [0.12, 0.05, 0.20, 0.08, 0.10]
    check(CT.classify("Oil/Gas (Integrated)", 5e9, 50e9, hist)[0] is CT.CompanyType.CYCLICAL,
          "oil major with a cycle of history")
    check(CT.classify("Oil/Gas (Integrated)", 5e9, 50e9, hist[:3])[0] is CT.CompanyType.STANDARD,
          "too short a history to normalize: standard")
    check(CT.classify("Semiconductor", 5e9, 50e9, hist)[0] is CT.CompanyType.STANDARD,
          "semiconductor designers are not normalized")
    check(CT.classify(None, 5e9, 50e9)[0] is CT.CompanyType.STANDARD, "unknown industry: standard")
    check(abs(CT.normalized_margin(hist) - 0.10) < 1e-12, "median margin")


# --------------------------------------------------------------------------
# 8. the engine on companies the app has never seen
# --------------------------------------------------------------------------
def test_engine_bank():
    print("8 a new bank is valued on equity, not FCFF")
    f = _facts("ZZBANK", price=300.0, shares_diluted=2.8e9, market_cap=840e9, revenue=170e9,
               operating_income=65e9, net_income=50e9, equity=330e9, equity_prior=320e9,
               total_debt=450e9, cash=500e9, beta=1.1, growth_lt=0.06, eps_gaap=17.8,
               forward_eps=19.0, damodaran_industry="Bank (Money Center)",
               revenue_annuals=[170e9, 160e9, 150e9, 130e9])
    v = E.DeepV82Engine().evaluate(f, rf=RF)
    km = v.key_metrics
    check(v.company_type == "financial" and v.anchor_method == "Excess Return Model",
          "routed to the excess-return model", f"{v.company_type} {v.anchor_method}")
    check(abs(km["wacc_pct"] - km["ke_pct"]) < 1e-9 and km["roic_basis"] == "roe_book_equity",
          "WACC = Ke, ROE is the return measured", f"{km['wacc_pct']} {km['ke_pct']}")
    check(v.reverse_dcf == {} and v.fv_dcf is None, "no FCFF reverse DCF or DCF for a bank")
    check(v.anchor_value and v.anchor_value > 0 and v.verdict and "None" not in v.verdict,
          "a usable value and verdict", f"{v.anchor_value} / {v.verdict}")


def test_engine_cyclical():
    print("9 a new oil major reverts to its normalized margin")
    rev = [340e9, 400e9, 280e9, 180e9, 250e9, 290e9, 260e9]
    oi = [40e9, 70e9, 25e9, -5e9, 20e9, 30e9, 22e9]
    f = _operating(ticker="ZZOIL", price=110.0, shares_diluted=4.3e9, market_cap=470e9,
                   revenue=340e9, operating_income=40e9, net_income=33e9, equity=260e9,
                   total_debt=40e9, cash=20e9, beta=0.9, growth_lt=0.03,
                   revenue_annuals=rev, operating_income_annuals=oi,
                   damodaran_industry="Oil/Gas (Integrated)", industry_sales_to_capital=1.1)
    f.ticker = "ZZOIL"
    v = E.DeepV82Engine().evaluate(f, rf=RF)
    med = CT.normalized_margin([o / r for o, r in zip(oi, rev)])
    check(v.company_type == "cyclical" and v.anchor_method == "FCFF DCF",
          "cyclical, valued by FCFF DCF", f"{v.company_type} {v.anchor_method}")
    check(abs(v.dcf["inputs"]["margin_target"] - round(med, 4)) < 1e-4,
          "target margin = the firm's own median", f"{v.dcf['inputs']['margin_target']} vs {med:.4f}")


def test_engine_unmapped_and_adr():
    print("10 no FMP profile, and a depositary listing")
    v = E.DeepV82Engine().evaluate(_operating(), rf=RF)
    check(v.company_type == "standard" and v.anchor_method == "FCFF DCF" and v.fv_dcf,
          "unknown industry still gets a DCF", f"{v.anchor_method} {v.fv_dcf}")
    check(v.fv_peg and v.fv_fvp, "PEG and FVP stay as cross-checks")
    s2c = v.dcf["inputs"]["sales_to_capital"]
    check(E.S2C_BAND[0] <= s2c <= E.S2C_BAND[1], "own sales-to-capital inside the plausible band",
          str(s2c))
    adr = E.DeepV82Engine().evaluate(_operating(shares_diluted=5e9), rf=RF)   # 5 ordinary / ADR
    check(abs(adr.fv_dcf - v.fv_dcf) / v.fv_dcf < 0.02,
          "per-ADR value uses market cap / price, not the ordinary-share count",
          f"{adr.fv_dcf} vs {v.fv_dcf}")
    check(any("market cap / price" in x for x in adr.flags), "and says so")


def test_engine_pre_profit_unchanged_route():
    print("11 a pre-profit firm still goes to the young-company model")
    f = _operating(operating_income=-2e9, net_income=-2.5e9, eps_gaap=-2.5, forward_eps=-1.0,
                   operating_income_annuals=[-2e9, -3e9, -4e9, -5e9],
                   industry_margin=0.18, damodaran_industry="Software (System & Application)")
    v = E.DeepV82Engine().evaluate(f, rf=RF)
    check(v.company_type == "young" and v.fv_dcf is None, "no FCFF DCF on a loss", str(v.company_type))
    check(v.anchor_method in ("Young-Company DCF (failure-adjusted)", "Terminal-Anchored Reverse DCF"),
          "young model or reverse DCF", v.anchor_method)
    check(v.key_metrics["terminal_margin_pct"] == 18.0, "target margin is the industry's",
          str(v.key_metrics["terminal_margin_pct"]))


def test_broken_dcf_is_not_the_anchor():
    print("12 a DCF far below the price stays the anchor, with the gap named (2026-10-19)")
    v = E.DeepV82Engine().evaluate(_operating(total_debt=140e9), rf=RF)   # equity ~5% of price
    check(v.fv_dcf and v.fv_dcf < 10, "fixture: DCF below 10% of the $100 price", str(v.fv_dcf))
    # the old guard swapped to the earnings paths, which handed TSLA an $18 PEG in place
    # of its $30 DCF; charge-laden EBIT is now normalized upstream instead
    check(v.anchor_method == "FCFF DCF" and v.anchor_value == v.fv_dcf
          and any("under 10% of the price" in x for x in v.flags),
          "the DCF is the anchor and the card says the price holds a story", v.anchor_method)
    neg = E.DeepV82Engine().evaluate(_operating(total_debt=150e9), rf=RF)  # equity < 0
    check(neg.fv_dcf is None and any("equity value <= 0" in x for x in neg.flags),
          "a negative equity value is named, not hidden", str(neg.fv_dcf))
    check(Valuation("x").fv_dcf is None and Valuation("x").dcf == {},
          "contract: new fields default empty (older engines unaffected)")


# --------------------------------------------------------------------------
# 13. independent review findings (2026-10-17), each pinned
# --------------------------------------------------------------------------
def test_review_findings():
    print("13 review: leases once, no 40% cap / 5% floor in the DCF, cyclical loss year, banks")
    base = E.DeepV82Engine().evaluate(_operating(accounting_standard="us-gaap"), rf=RF)
    lease = E.DeepV82Engine().evaluate(_operating(accounting_standard="us-gaap",
                                                  operating_leases=20e9), rf=RF)
    drop = base.fv_dcf - lease.fv_dcf
    check(0 < drop < 20.0, "a $20B lease costs less than $20/share (counted once, EBIT adjusted)",
          f"{base.fv_dcf} -> {lease.fv_dcf}")
    check(lease.dcf["sources"]["lease_interest_added"], "lease interest added back to EBIT")
    ifrs = E.DeepV82Engine().evaluate(_operating(accounting_standard="ifrs",
                                                 operating_leases=20e9), rf=RF)
    check(not ifrs.dcf["sources"]["lease_interest_added"], "IFRS 16: interest already below EBIT")

    hi = E.DeepV82Engine().evaluate(_operating(operating_income=32e9, industry_margin=0.35), rf=RF)
    check(abs(hi.dcf["inputs"]["margin_target"] - 0.64) < 1e-3,
          "a 64% margin is kept (2x industry 70% > 64%)", str(hi.dcf["inputs"]["margin_target"]))
    # its history is thin too (round 8 reverts a trough margin to the 5y median)
    thin = E.DeepV82Engine().evaluate(_operating(operating_income=1.35e9, industry_margin=0.03,
                                                 operating_income_annuals=[0.027 * r for r in (50e9, 45e9, 41e9, 37e9)]), rf=RF)
    check(abs(thin.dcf["inputs"]["margin_target"] - 0.03) < 1e-3,
          "a 2.7% firm in a 3% industry targets 3%, not a 5% floor",
          str(thin.dcf["inputs"]["margin_target"]))

    rev = [40e9, 50e9, 45e9, 38e9, 42e9, 47e9]
    oi = [-1e9, 6e9, 5e9, 3e9, 4e9, 5e9]
    steel = E.DeepV82Engine().evaluate(_operating(
        revenue=40e9, operating_income=-1e9, net_income=-1.5e9, eps_gaap=-1.5,
        revenue_annuals=rev, operating_income_annuals=oi, damodaran_industry="Steel"), rf=RF)
    check(steel.company_type == "cyclical" and steel.anchor_method == "FCFF DCF" and steel.fv_dcf,
          "a steel maker in a loss year is a cyclical, valued on its normal margin",
          f"{steel.company_type} {steel.anchor_method}")

    bank = _facts("ZZBANK2", price=50.0, shares_diluted=1e9, market_cap=50e9, revenue=20e9,
                  operating_income=-6e9, net_income=-5e9, equity=100e9, equity_prior=100e9,
                  beta=1.2, eps_gaap=-5.0, growth_lt=0.04, damodaran_industry="Banks (Regional)")
    v = E.DeepV82Engine().evaluate(bank, rf=RF)
    check("Reverse DCF" not in v.anchor_method and "Young" not in v.anchor_method,
          "a loss-making bank never falls into the operating-company models", v.anchor_method)
    er = financial.value(book_equity=100e9, roe=-0.05, ke=0.10, g_high=0.0, g_stable=0.045, shares=1e9)
    check(er and er["per_share"] and 0 < er["per_share"] < 100,
          "its excess-return value sits below book, not below zero", str(er and er["per_share"]))
    check(er["g_stable"] <= er["roe_stable"] + 1e-12, "stable growth held to the stable ROE")

    pfe_like = E.DeepV82Engine().evaluate(_operating(total_debt=140e9), rf=RF)
    check(not pfe_like.dcf.get("discarded") and pfe_like.range_low == pfe_like.fv_dcf,
          "the anchored DCF is the low end of the range", f"{pfe_like.range_low}")

    good_bank = _facts("ZZBANK3", price=300.0, shares_diluted=2.8e9, market_cap=840e9, revenue=170e9,
                       operating_income=65e9, net_income=50e9, equity=330e9, equity_prior=320e9,
                       beta=1.1, growth_lt=0.06, eps_gaap=17.8, forward_eps=19.0,
                       damodaran_industry="Bank (Money Center)")
    gv = E.DeepV82Engine().evaluate(good_bank, rf=RF)
    check("ROE" in gv.verdict and "WACC" not in gv.verdict, "the bank's verdict speaks ROE vs Ke",
          gv.verdict)


if __name__ == "__main__":
    run_suite(globals(), "test_damodaran_round2 OK - country risk, industries, DCF and router hold")
