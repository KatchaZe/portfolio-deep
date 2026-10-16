"""The growth a fair value is built on must be a forward estimate when one exists.

Before 2026-10-17 every holding valued on its historical revenue CAGR (the provenance
said "sec-cagr" for 21/21), so NVO was a BUY at $119 on 19.5% growth while it guided
2026 sales -5% to -13% and consensus EPS sat below trailing."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from domain.facts import FinancialFacts
from pipeline import normalize
from tests._harness import check, run_suite


def _ff(**kw):
    ff = FinancialFacts(ticker="ZZZ")
    ff.set("growth_lt", kw.pop("growth_lt", 0.195), kw.pop("src", normalize.HISTORICAL_GROWTH))
    for k, v in kw.items():
        ff.set(k, v, "test")
    return ff


def test_consensus_path_replaces_history():
    ff = _ff(fwd_growth_near=0.20, fwd_growth_far=0.10)
    normalize.resolve_growth(ff)
    check(abs(ff.growth_lt - (1.2 * 1.1) ** 0.5 + 1) < 1e-9, "growth = 2-year consensus CAGR",
          str(ff.growth_lt))
    check("consensus" in ff.provenance["growth_lt"], "provenance names the consensus path")


def test_history_is_capped_by_consensus_eps_and_flagged():
    ff = _ff(eps_gaap=3.57, forward_eps=3.41)
    normalize.resolve_growth(ff)
    check(ff.growth_lt == 0.0, "NVO shape: falling consensus EPS caps 19.5% history to 0%",
          str(ff.growth_lt))
    check(any("HISTORICAL revenue CAGR" in f for f in ff.flags), "the card says the growth is historical")
    check(any("growth capped" in f for f in ff.flags), "and that it was capped")


def test_history_kept_when_consensus_agrees():
    ff = _ff(eps_gaap=10.0, forward_eps=13.0)
    normalize.resolve_growth(ff)
    check(abs(ff.growth_lt - 0.195) < 1e-12, "history below the EPS-implied 30% is kept", str(ff.growth_lt))


def test_yahoo_long_term_growth_is_left_alone():
    ff = _ff(growth_lt=0.12, src="yahoo", fwd_growth_near=0.30, fwd_growth_far=0.30)
    normalize.resolve_growth(ff)
    check(ff.growth_lt == 0.12, "an analyst long-term EPS growth estimate wins", str(ff.growth_lt))


if __name__ == "__main__":
    run_suite(globals(), "ALL test_growth_source PASSED")
