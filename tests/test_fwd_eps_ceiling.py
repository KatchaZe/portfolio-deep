"""The forward-EPS ceiling reads consensus revenue growth as reported (round 8 took
the 30% cap off reported consensus years), and two independent sources that agree
are a flag, not a rejection. Live 2026-10-26: NVDA 14.16 (Yahoo 14.16, FMP 14.17),
AVGO 19.33 and TSM 22.32 were thrown away for the SEC trailing EPS. Also: an unread
filing that the quarterly base already covers is a note, not a warning."""
import unittest

from domain.facts import FinancialFacts
from pipeline import dataquality, validate

LIVE = {   # stored facts, refresh 2026-10-26 06:56 Bangkok
    "NVDA": dict(revenue=302_970_000_000, shares_diluted=24_338_000_000, price=210.96,
                 growth_lt=0.7972, fwd_growth_y1=0.8984, net_income=192_880_000_000,
                 forward_eps=14.1634, forward_eps_sources={"yahoo": 14.16, "fmp": 14.1667},
                 forward_eps_spread_pct=0.0),
    "AVGO": dict(revenue=89_104_000_000, shares_diluted=4_884_000_000, price=321.68,
                 growth_lt=0.651, fwd_growth_y1=0.6589, net_income=38_265_000_000,
                 forward_eps=19.325, forward_eps_sources={"yahoo": 19.325},
                 forward_eps_spread_pct=0.0),
    "TSM": dict(revenue=152_099_671_213.6, shares_diluted=5_186_111_400, price=418.51,
                growth_lt=0.3727, fwd_growth_y1=None, net_income=78_360_830_900.7,
                rev_growth_path=[[0.4364, 23], [0.3727, 23]],
                forward_eps=22.3219, forward_eps_sources={"yahoo": 22.3219},
                forward_eps_spread_pct=0.0),
}


def facts(t, **over):
    ff = FinancialFacts(t)
    for k, v in {**LIVE[t], **over}.items():
        setattr(ff, k, v)
    return ff


class Ceiling(unittest.TestCase):
    def test_live_consensus_is_kept(self):
        for t in LIVE:
            ff = facts(t)
            validate._resolve_forward_eps(ff)
            self.assertAlmostEqual(ff.forward_eps, LIVE[t]["forward_eps"], places=3, msg=t)
            self.assertFalse(any("rejected" in x for x in ff.flags), (t, ff.flags))

    def test_old_30pct_cap_would_reject(self):
        r = validate.forward_eps_rejection(14.1634, 302.97e9, 24.338e9, 210.96, 0.7972)
        self.assertIn("revenue-capacity ceiling", r)           # the bug, kept as evidence

    def test_agreement_turns_a_breach_into_a_flag(self):
        ff = facts("NVDA", fwd_growth_y1=0.0, growth_lt=0.0)   # ceiling 8.09 < 14.16
        validate._resolve_forward_eps(ff)
        self.assertAlmostEqual(ff.forward_eps, 14.1634, places=3)
        self.assertTrue(any("kept:" in x and "agree within" in x for x in ff.flags), ff.flags)

    def test_single_source_breach_still_rejected(self):
        ff = facts("AVGO", fwd_growth_y1=0.0, growth_lt=0.0)
        validate._resolve_forward_eps(ff)
        self.assertEqual(ff.provenance.get("forward_eps"), "sec-derived (consensus rejected)")

    def test_av_with_yahoo_is_not_agreement(self):
        ff = facts("AVGO", fwd_growth_y1=0.0, growth_lt=0.0,
                   forward_eps_sources={"yahoo": 19.325, "alphavantage": 19.3})
        self.assertFalse(validate._sources_agree(ff))

    def test_unit_band_still_rejects_when_sources_agree(self):
        r = validate.forward_eps_rejection(692.42, None, None, 418.51, 0.37, sources_agree=True)
        self.assertIn("below", r)


class SupersededGap(unittest.TestCase):
    GAP = {"form": "20-F", "filed": "2026-04-16", "period": "2025-12-31", "read": False,
           "error": "read, but the base period stayed at 2024-12-31"}

    def codes(self, fy):
        ff = FinancialFacts("TSM")
        ff.sec_gap, ff.fiscal_year = dict(self.GAP), fy
        return {(c, sev) for c, sev, _ in dataquality._sec_gap(ff)}

    def test_quarterly_base_covers_the_gap(self):
        self.assertEqual(self.codes("2026-09-30"), {("sec_filing_superseded", "note")})

    def test_still_warns_when_behind(self):
        self.assertEqual(self.codes("2024-12-31"), {("sec_filing_missing", "warn")})


if __name__ == "__main__":
    unittest.main()
