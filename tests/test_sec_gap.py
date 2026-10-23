"""sources/sec_gap.py: a filing SEC companyfacts has not ingested is read from the
filing's own XBRL instance. Found live 2026-10-23 (HUM, F, CTSH, BIIB, NXPI: the Q2
10-Qs filed 2026-07-28/29 carry 0 facts in companyfacts).

Round-trip backtest on real data: UNH companyfacts (trimmed to 2019+) with its Q2 10-Q
removed, refilled from an instance document built from the removed facts, must give
the same extract() as the untouched companyfacts."""
import copy
import gzip
import json
import os
import tempfile
import unittest
from types import SimpleNamespace

from pipeline import dataquality
from sources import sec_edgar, sec_gap

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "sec_gap", "UNH_companyfacts_trim.json.gz")
ACCN = "0000731766-26-000197"
FILED = "2026-08-10"


def load():
    with gzip.open(FIX, "rt") as fh:
        return json.load(fh)


def split_out(cf, accn):
    """(companyfacts without `accn`, the removed entries as (ns, concept, unit, entry))."""
    out, removed = copy.deepcopy(cf), []
    for ns, concepts in out["facts"].items():
        for concept, node in concepts.items():
            for unit, arr in node["units"].items():
                removed += [(ns, concept, unit, e) for e in arr if e["accn"] == accn]
                node["units"][unit] = [e for e in arr if e["accn"] != accn]
    return out, removed


NS_URI = {"us-gaap": "http://fasb.org/us-gaap/2025", "dei": "http://xbrl.sec.gov/dei/2025"}
UNIT_XML = {
    "USD": '<xbrli:unit id="u_USD"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>',
    "shares": '<xbrli:unit id="u_shares"><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unit>',
    "pure": '<xbrli:unit id="u_pure"><xbrli:measure>xbrli:pure</xbrli:measure></xbrli:unit>',
    "USD/shares": ('<xbrli:unit id="u_USD_shares"><xbrli:divide><xbrli:unitNumerator>'
                   '<xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unitNumerator>'
                   '<xbrli:unitDenominator><xbrli:measure>xbrli:shares</xbrli:measure>'
                   '</xbrli:unitDenominator></xbrli:divide></xbrli:unit>'),
}


def instance(removed, extra=""):
    """An XBRL instance holding `removed`, in the shape SEC's _htm.xml files have."""
    ctx, facts = {}, []
    for ns, concept, unit, e in removed:
        key = (e.get("start"), e["end"])
        if key not in ctx:
            cid = f"c{len(ctx)}"
            period = (f"<xbrli:startDate>{key[0]}</xbrli:startDate><xbrli:endDate>{key[1]}</xbrli:endDate>"
                      if key[0] else f"<xbrli:instant>{key[1]}</xbrli:instant>")
            ctx[key] = (cid, f'<xbrli:context id="{cid}"><xbrli:entity><xbrli:identifier '
                             f'scheme="http://www.sec.gov/CIK">0000731766</xbrli:identifier>'
                             f'</xbrli:entity><xbrli:period>{period}</xbrli:period></xbrli:context>')
        uid = "u_" + unit.replace("/", "_")
        facts.append(f'<{ns}:{concept} contextRef="{ctx[key][0]}" unitRef="{uid}" decimals="-6">'
                     f'{e["val"]}</{ns}:{concept}>')
    return (f'<?xml version="1.0" encoding="utf-8"?><xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" '
            f'xmlns:us-gaap="{NS_URI["us-gaap"]}" xmlns:dei="{NS_URI["dei"]}" '
            f'xmlns:xbrldi="http://xbrl.org/2006/xbrldi" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
            f'xmlns:unh="http://www.unitedhealthgroup.com/20260630">'
            + "".join(c for _, c in ctx.values()) + "".join(UNIT_XML.values())
            + "".join(f'<xbrli:unit id="u_{u}"><xbrli:measure>unh:{u}</xbrli:measure></xbrli:unit>'
                      for u in sorted({r[2] for r in removed} - set(UNIT_XML)))
            + extra + "".join(facts) + "</xbrli:xbrl>").encode()   # decoys FIRST: a dedupe must not save them


SUBMISSIONS = {"filings": {"recent": {
    "form": ["8-K", "10-Q", "10-Q", "10-K"],
    "filingDate": ["2026-10-14", FILED, "2026-05-05", "2026-03-02"],
    "reportDate": ["2026-10-14", "2026-06-30", "2026-03-31", "2025-12-31"],
    "accessionNumber": ["0000731766-26-000300", ACCN, "0000731766-26-000127", "0000731766-26-000062"],
    "primaryDocument": ["x.htm", "unh-20260630.htm", "unh-20260331.htm", "unh-20251231.htm"]}}}


class FakeRequests:
    def __init__(self, xml, fail=False):
        self.xml, self.fail, self.urls = xml, fail, []

    def get(self, url, headers=None, timeout=None):
        self.urls.append(url)
        if "submissions" in url:
            return SimpleNamespace(json=lambda: SUBMISSIONS, raise_for_status=lambda: None)
        if self.fail:
            def boom():
                raise OSError("503 Service Unavailable")
            return SimpleNamespace(raise_for_status=boom)
        assert url.endswith("/000073176626000197/unh-20260630_htm.xml"), url
        return SimpleNamespace(content=self.xml, raise_for_status=lambda: None)


class RoundTrip(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.full = load()
        cls.gapped, cls.removed = split_out(cls.full, ACCN)

    def test_the_gap_reproduces_the_live_defect(self):
        self.assertEqual(len(self.removed), 409)
        self.assertEqual(sec_edgar.extract(self.full)["latest_period_end"], "2026-06-30")
        self.assertEqual(sec_edgar.extract(self.gapped)["latest_period_end"], "2026-03-31")
        self.assertEqual(sec_gap.gap(SUBMISSIONS, self.gapped)["accn"], ACCN)
        self.assertIsNone(sec_gap.gap(SUBMISSIONS, self.full))

    def test_refilled_extract_equals_the_original(self):
        rq = FakeRequests(instance(self.removed))
        out = sec_gap.complete("731766", self.gapped, "ua", requests_mod=rq)
        self.assertEqual(out["_sec_gap"]["read"], True)
        self.assertEqual(out["_sec_gap"]["facts"], 409)
        want, got = sec_edgar.extract(self.full), sec_edgar.extract(out)
        diff = [k for k in want if want[k] != got[k]]
        self.assertEqual(diff, [])

    def test_dimensional_nil_and_custom_facts_are_ignored(self):
        bogus = ('<xbrli:context id="dim"><xbrli:entity><xbrli:identifier scheme="s">1</xbrli:identifier>'
                 '<xbrli:segment><xbrldi:explicitMember dimension="us-gaap:StatementBusinessSegmentsAxis">'
                 'unh:OptumMember</xbrldi:explicitMember></xbrli:segment></xbrli:entity><xbrli:period>'
                 '<xbrli:startDate>2026-04-01</xbrli:startDate><xbrli:endDate>2026-06-30</xbrli:endDate>'
                 '</xbrli:period></xbrli:context>'
                 '<us-gaap:Revenues contextRef="dim" unitRef="u_USD" decimals="-6">1</us-gaap:Revenues>'
                 '<us-gaap:Revenues contextRef="c0" unitRef="u_USD" xsi:nil="true"/>'
                 '<unh:MedicalCareRatio contextRef="c0" unitRef="u_pure">0.89</unh:MedicalCareRatio>')
        patch = sec_gap.parse_facts(instance(self.removed, bogus), ACCN, "10-Q", FILED)
        n = sum(len(a) for c in patch.values() for node in c.values() for a in node["units"].values())
        self.assertEqual(n, 409)
        self.assertNotIn("MedicalCareRatio", patch.get("us-gaap", {}))
        self.assertIn("USD/shares", patch["us-gaap"]["EarningsPerShareDiluted"]["units"])
        q2 = [e for e in patch["us-gaap"]["Revenues"]["units"]["USD"]
              if e.get("start") == "2026-04-01" and e["end"] == "2026-06-30"]
        want = [e for ns, c, u, e in self.removed if c == "Revenues" and e.get("start") == "2026-04-01"
                and e["end"] == "2026-06-30"]
        self.assertEqual([e["val"] for e in q2], [want[0]["val"]])

    def test_no_gap_returns_the_same_object_and_reads_nothing(self):
        rq = FakeRequests(b"")
        out = sec_gap.complete("731766", self.full, "ua", requests_mod=rq)
        self.assertIs(out, self.full)
        self.assertEqual(len(rq.urls), 1)          # the submissions feed only

    def test_failed_read_is_flagged_never_silent(self):
        out = sec_gap.complete("731766", self.gapped, "ua", requests_mod=FakeRequests(b"", fail=True))
        g = out["_sec_gap"]
        self.assertEqual((g["read"], g["form"], g["filed"]), (False, "10-Q", FILED))
        self.assertIn("503", g["error"])
        found = list(dataquality._sec_gap(SimpleNamespace(sec_gap=g)))
        self.assertEqual(found[0][:2], ("sec_filing_missing", "warn"))
        read = dict(g, read=True)
        self.assertEqual(list(dataquality._sec_gap(SimpleNamespace(sec_gap=read)))[0][1], "note")

    def test_parsed_filing_is_cached_forever_submissions_12h(self):
        tmp = tempfile.mkdtemp()
        rq = FakeRequests(instance(self.removed))
        sec_gap.complete("731766", self.gapped, "ua", cache_dir=tmp, requests_mod=rq)
        rq2 = FakeRequests(b"<never read/>")
        out = sec_gap.complete("731766", self.gapped, "ua", cache_dir=tmp, requests_mod=rq2)
        self.assertEqual(rq2.urls, [])
        self.assertTrue(out["_sec_gap"]["read"])

    def test_peer_with_unread_gap_is_excluded(self):
        from pipeline import peers
        g = {"form": "10-Q", "filed": FILED, "period": "2026-06-30", "accn": ACCN, "read": False}
        orig = peers.resolve_cik
        peers.resolve_cik = lambda s: ("0000731766", s)
        try:
            p, why = peers.peer_point("UNH", __import__("datetime").date(2026, 10, 21),
                                      fetch_facts=lambda c: dict(self.gapped, _sec_gap=g),
                                      fetch_price=lambda s, d: (400, "2026-10-19", "yahoo"), complete=None)
        finally:
            peers.resolve_cik = orig
        self.assertIsNone(p)
        self.assertIn("lacks the 10-Q filed 2026-08-10", why)


def subs(*rows):
    """A submissions document from (form, filed, period, accn) rows, newest first."""
    return {"filings": {"recent": {
        "form": [r[0] for r in rows], "filingDate": [r[1] for r in rows],
        "reportDate": [r[2] for r in rows], "accessionNumber": [r[3] for r in rows],
        "primaryDocument": [f"{r[3]}.htm" for r in rows]}}}


def cf_with(counts):
    return {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [
        {"end": "2026-01-01", "val": 1, "accn": a} for a, n in counts.items() for _ in range(n)]}}}}}


class SubmissionsFailure(unittest.TestCase):
    def test_unreadable_filing_list_is_flagged(self):
        class Down:
            def get(self, url, headers=None, timeout=None):
                raise ConnectionError("403 Forbidden")
        cf = cf_with({"Q1": 300})
        out = sec_gap.complete("1", cf, "ua", requests_mod=Down())
        self.assertIs(out["_sec_gap"]["checked"], False)
        self.assertIn("403", out["_sec_gap"]["error"])
        code, sev, _ = list(dataquality._sec_gap(SimpleNamespace(sec_gap=out["_sec_gap"])))[0]
        self.assertEqual((code, sev), ("sec_filing_list_unchecked", "note"))


class Truncation(unittest.TestCase):
    def test_insider_filings_do_not_push_the_10q_out(self):
        rows = [("4", f"2026-10-{d:02d}", "", f"F4-{d}") for d in range(1, 23)] * 3
        rows += [("10-Q", "2026-07-29", "2026-06-30", "Q2"), ("10-Q", "2026-04-29", "2026-03-31", "Q1")]
        full = subs(*rows)

        class R:
            def get(self, url, headers=None, timeout=None):
                if "submissions" in url:
                    return SimpleNamespace(json=lambda: full, raise_for_status=lambda: None)
                raise ConnectionError("instance down")
        out = sec_gap.complete("1", cf_with({"Q1": 300}), "ua", requests_mod=R())
        self.assertEqual(out["_sec_gap"]["accn"], "Q2")         # found (read fails, flagged)
        self.assertIs(out["_sec_gap"]["read"], False)


class Gaps(unittest.TestCase):
    ROWS = [("10-Q", "2026-10-22", "2026-09-30", "Q3"), ("8-K", "2026-08-01", "", "X"),
            ("10-Q", "2026-07-29", "2026-06-30", "Q2"), ("10-Q", "2026-04-29", "2026-03-31", "Q1"),
            ("10-K", "2026-02-12", "2025-12-31", "K"), ("10-Q", "2025-07-30", "2025-06-30", "OLD")]

    def test_skipped_quarter_stays_visible_after_the_next_lands(self):
        g = sec_gap.gaps(subs(*self.ROWS), cf_with({"Q3": 300, "Q1": 280, "K": 600}))
        self.assertEqual([f["accn"] for f in g], ["Q2"])      # OLD is outside the window

    def test_partly_ingested_filing_counts_as_missing(self):
        g = sec_gap.gaps(subs(*self.ROWS), cf_with({"Q3": 3, "Q2": 300, "Q1": 280, "K": 600}))
        self.assertEqual([f["accn"] for f in g], ["Q3"])

    def test_oldest_first_and_failure_is_not_overwritten(self):
        g = sec_gap.gaps(subs(*self.ROWS), cf_with({"Q1": 280, "K": 600}))
        self.assertEqual([f["accn"] for f in g], ["Q2", "Q3"])
        failed = dict(cf_with({}), _sec_gap={"form": "10-Q", "filed": "2026-07-29", "period": "2026-06-30",
                                             "accn": "Q2", "read": False, "error": "503"})
        out = sec_gap.merge(failed, {}, {"form": "10-Q", "filed": "2026-10-22", "period": "2026-09-30",
                                         "accn": "Q3"})
        self.assertIs(out["_sec_gap"]["read"], False)
        self.assertEqual(out["_sec_gap"]["accn"], "Q2")


if __name__ == "__main__":
    unittest.main()
