"""test_damodaran_round4 — decisions taken after the round-3 report (2026-10-18).

  * health insurers: short-term investments are claim reserves, not excess cash
  * splits: per-share history restated from the market's split dates (TSLA 2020 5:1,
    2022 3:1, which the share-ratio test could not see)
  * revenue mix: read off the latest 10-K's geographic revenue table on Refresh, or
    typed on the dashboard, for the country risk premium

The 10-K rows below are the real facts of each company's latest 10-K, read with a
browser from sec.gov on 2026-10-18 ($m). Run: python -m tests.test_damodaran_round4
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config                                   # noqa: E402
from tests._harness import check, run_suite     # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
G, S = "StatementGeographicalAxis", "StatementBusinessSegmentsAxis"
RFCC = "RevenueFromContractWithCustomerExcludingAssessedTax"


def _rows(concept, start, end, total, members, axis=G, total_concept=None):
    out = [(total_concept or concept, start, end, (), total * 1e6)]
    out += [(concept, start, end, ((axis, m),), v * 1e6) for m, v in members.items()]
    return out


TEN_K = {
    "NVDA": _rows("Revenues", "2025-01-27", "2026-01-25", 215938, {
        "country:US": 149617, "country:TW": 42345,
        "nvda:ChinaIncludingHongKongMember": 19677, "nvda:OtherCountriesMember": 4299}),
    # geography on RFCC, the total on Revenues
    "GOOGL": _rows(RFCC, "2025-01-01", "2025-12-31", 402836, {
        "country:US": 194229, "us-gaap:EMEAMember": 117152, "srt:AsiaPacificMember": 67680,
        "goog:AmericasExcludingUnitedStatesMember": 23902}, total_concept="Revenues"),
    # regions AND countries in one table
    "AVGO": _rows("Revenues", "2024-11-04", "2025-11-02", 63887, {
        "srt:AmericasMember": 18939, "srt:AsiaPacificMember": 35896, "us-gaap:EMEAMember": 9052,
        "country:US": 16506, "avgo:ChinaincludingHongKongMember": 11155, "country:SG": 10796,
        "country:TW": 6451, "avgo:AllOtherGeographiesMemberMember": 18979}),
    "ORCL": _rows("Revenues", "2025-06-01", "2026-05-31", 67357, {
        "srt:AmericasMember": 44478, "us-gaap:EMEAMember": 15297, "srt:AsiaPacificMember": 7582,
        "country:US": 39835, "country:GB": 2816, "country:DE": 1993, "country:JP": 1870,
        "orcl:OtherCountriesMember": 20843}),
    # non-US AND its parts
    "LLY": _rows("Revenues", "2025-01-01", "2025-12-31", 65179, {
        "country:US": 43481, "us-gaap:NonUsMember": 21698, "srt:EuropeMember": 11558,
        "country:JP": 2132, "country:CN": 1951, "lly:RestOfWorldMember": 6057}),
    "MSFT": _rows("Revenues", "2025-07-01", "2026-06-30", 331839, {
        "country:US": 170794, "us-gaap:NonUsMember": 161045}),
    "AAPL": _rows("Revenues", "2024-09-29", "2025-09-27", 416161, {
        "country:US": 151790, "country:CN": 64377, "aapl:OtherCountriesMember": 199994}),
    # only a US line: no split that adds up
    "MELI": _rows("Revenues", "2025-01-01", "2025-12-31", 20335, {"country:US": 51}),
    # product segments are not regions
    "AXON_SEG": _rows("Revenues", "2025-01-01", "2025-12-31", 2780, {
        "axon:ConnectedDevicesMember": 1577, "axon:SoftwareAndServicesSegmentMember": 1203}, axis=S),
}


def test_market_of_members():
    print("10-K members map onto countries and regions")
    from sources.revenue_geo import market_of
    cases = {"country:TW": "TW", "nvda:ChinaIncludingHongKongMember": "China",
             "nvda:OtherCountriesMember": "rest of world", "us-gaap:NonUsMember": "rest of world",
             "avgo:AllOtherGeographiesMemberMember": "rest of world",
             "lly:RestOfWorldMember": "rest of world", "us-gaap:EMEAMember": "emea",
             "srt:AsiaPacificMember": "asia pacific", "srt:EuropeMember": "western europe",
             "goog:AmericasExcludingUnitedStatesMember": "americas ex us",
             "srt:AmericasMember": "US", "meli:BrazilMember": "BR",
             "axon:ConnectedDevicesMember": None}
    for m, want in cases.items():
        check(market_of(m) == want, f"{m} -> {want}", str(market_of(m)))


def test_mix_from_real_10k_tables():
    print("the most detailed split that adds up to total revenue")
    from sources.revenue_geo import mix_from_facts
    mix, note = mix_from_facts(TEN_K["NVDA"])
    check(mix == {"US": 149617e6, "TW": 42345e6, "China": 19677e6, "rest of world": 4299e6},
          "NVDA: US / Taiwan / China / other", str(mix))
    check(note == "10-K FY2026", "labelled with the fiscal year", note)
    mix, _ = mix_from_facts(TEN_K["GOOGL"])
    check(set(mix) == {"US", "emea", "asia pacific", "americas ex us"},
          "GOOGL: members on one concept, total on another", str(mix))
    mix, _ = mix_from_facts(TEN_K["AVGO"])
    check(set(mix) == {"US", "China", "SG", "TW", "rest of world"},
          "AVGO: the country table wins over the regions", str(mix))
    mix, _ = mix_from_facts(TEN_K["ORCL"])
    check(set(mix) == {"US", "GB", "DE", "JP", "rest of world"}, "ORCL: countries", str(mix))
    mix, _ = mix_from_facts(TEN_K["LLY"])
    check(set(mix) == {"US", "western europe", "JP", "CN", "rest of world"}
          and abs(sum(mix.values()) - 65179e6) < 1e6,
          "LLY: non-US is replaced by its parts, never added to them", str(mix))
    mix, _ = mix_from_facts(TEN_K["MSFT"])
    check(mix == {"US": 170794e6, "rest of world": 161045e6}, "MSFT: US / non-US", str(mix))
    mix, why = mix_from_facts(TEN_K["MELI"])
    check(mix is None and "adds up" in why, "MELI: a lone US line is no split", why)
    mix, _ = mix_from_facts(TEN_K["AXON_SEG"])
    check(mix is None, "product segments are never read as regions", str(mix))
    seg = _rows("Revenues", "2025-01-01", "2025-12-31", 100, {
        "meli:BrazilMember": 55, "meli:MexicoMember": 25, "meli:ArgentinaMember": 20}, axis=S)
    mix, note = mix_from_facts(seg)
    check(set(mix) == {"BR", "MX", "AR"} and "segments" in note,
          "regional segments are used when there is no geographic table", str((mix, note)))


def test_crp_from_10k_mix():
    print("the 10-K mix prices country risk")
    from sources import country_risk as C
    from sources.revenue_geo import mix_from_facts
    mix, note = mix_from_facts(TEN_K["NVDA"])
    crp, basis = C.crp_for("NVDA", "US", C.CRP_FALLBACK, mix=mix, source=note)
    check(crp is not None and 0.001 < crp < 0.02, "NVDA carries Taiwan/China risk (0.19% over the US)", str(crp))
    check(basis.startswith("revenue mix (10-K FY2026): US 69%"), "basis names the 10-K", basis)
    for region in ("emea", "americas ex us", "rest of world"):
        check(C.market_crp(region, C.CRP_FALLBACK) is not None, f"region '{region}' has a CRP")
    us_only, _ = C.crp_for("X", "US", C.CRP_FALLBACK)
    check(us_only == 0.0, "a US company with no mix stays at 0")


def test_mix_priority():
    print("config beats the dashboard beats the 10-K")
    from sources import country_risk as C
    auto = {"mix": {"US": 50, "China": 50}, "note": "10-K FY2025"}
    saved_cfg, saved_man = dict(config.REVENUE_MIX), dict(config.REVENUE_MIX_MANUAL)
    try:
        config.REVENUE_MIX_MANUAL = {"ZZTEST": {"Japan": 1}}
        check(C.revenue_mix("ZZTEST", auto) == ({"Japan": 1}, "dashboard"), "dashboard over 10-K")
        config.REVENUE_MIX["ZZTEST"] = {"Taiwan": 1}
        check(C.revenue_mix("ZZTEST", auto) == ({"Taiwan": 1}, "config"), "config over dashboard")
        config.REVENUE_MIX.pop("ZZTEST")
        config.REVENUE_MIX_MANUAL = {}
        check(C.revenue_mix("ZZTEST", auto) == (auto["mix"], "10-K FY2025"), "10-K when nothing typed")
        check(C.revenue_mix("ZZTEST", None) == (None, None), "nothing at all -> domicile")
    finally:
        config.REVENUE_MIX.clear()
        config.REVENUE_MIX.update(saved_cfg)
        config.REVENUE_MIX_MANUAL = saved_man


XML = b"""<?xml version="1.0" encoding="utf-8"?>
<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance"
  xmlns:xbrldi="http://xbrl.org/2006/xbrldi" xmlns:us-gaap="http://fasb.org/us-gaap/2025"
  xmlns:srt="http://fasb.org/srt/2025" xmlns:country="http://xbrl.sec.gov/country/2025"
  xmlns:nvda="http://www.nvidia.com/20260125">
 <xbrli:context id="c-1"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">0001045810</xbrli:identifier></xbrli:entity>
  <xbrli:period><xbrli:startDate>2025-01-27</xbrli:startDate><xbrli:endDate>2026-01-25</xbrli:endDate></xbrli:period></xbrli:context>
 <xbrli:context id="c-2"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">0001045810</xbrli:identifier>
  <xbrli:segment><xbrldi:explicitMember dimension="srt:StatementGeographicalAxis">country:US</xbrldi:explicitMember></xbrli:segment></xbrli:entity>
  <xbrli:period><xbrli:startDate>2025-01-27</xbrli:startDate><xbrli:endDate>2026-01-25</xbrli:endDate></xbrli:period></xbrli:context>
 <xbrli:context id="c-3"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">0001045810</xbrli:identifier>
  <xbrli:segment><xbrldi:explicitMember dimension="srt:StatementGeographicalAxis">nvda:OtherCountriesMember</xbrldi:explicitMember></xbrli:segment></xbrli:entity>
  <xbrli:period><xbrli:startDate>2025-01-27</xbrli:startDate><xbrli:endDate>2026-01-25</xbrli:endDate></xbrli:period></xbrli:context>
 <xbrli:context id="c-4"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">0001045810</xbrli:identifier></xbrli:entity>
  <xbrli:period><xbrli:instant>2026-01-25</xbrli:instant></xbrli:period></xbrli:context>
 <us-gaap:Revenues contextRef="c-1" unitRef="usd" decimals="-6">215938000000</us-gaap:Revenues>
 <us-gaap:Revenues contextRef="c-2" unitRef="usd" decimals="-6">149617000000</us-gaap:Revenues>
 <us-gaap:Revenues contextRef="c-3" unitRef="usd" decimals="-6">66321000000</us-gaap:Revenues>
 <us-gaap:Assets contextRef="c-4" unitRef="usd" decimals="-6">200000000000</us-gaap:Assets>
</xbrli:xbrl>"""


class _Resp:
    def __init__(self, body):
        self.content = body if isinstance(body, bytes) else json.dumps(body).encode()

    def json(self):
        return json.loads(self.content)

    def raise_for_status(self):
        pass


class _Req:
    def __init__(self, forms):
        self.forms, self.urls = forms, []

    def get(self, url, headers=None, timeout=None):
        self.urls.append(url)
        if "submissions" in url:
            n = len(self.forms)
            return _Resp({"filings": {"recent": {
                "form": self.forms, "accessionNumber": [f"0001-26-00000{i}" for i in range(n)],
                "primaryDocument": [f"nvda-2026012{i}.htm" for i in range(n)]}}})
        return _Resp(XML)


def test_fetch_mix_reads_the_instance_and_caches():
    print("fetch_mix: latest 10-K instance, cached, 20-F filers skipped")
    import tempfile
    from sources import revenue_geo as R
    rows = R.parse_instance(XML)
    check(len(rows) == 3 and rows[1][3] == ((G, "country:US"),),
          "instance -> revenue facts with their dimensions", str(rows))
    with tempfile.TemporaryDirectory() as d:
        req = _Req(["8-K", "10-K", "10-Q"])
        res = R.fetch_mix("1045810", "ua", cache_dir=d, requests_mod=req)
        check(res["mix"] == {"US": 149617e6, "rest of world": 66321e6}, "mix from the 10-K", str(res))
        check(req.urls[1].endswith("/000126000001/nvda-20260121_htm.xml"),
              "the 10-K's own XBRL instance", req.urls[1])
        req2 = _Req(["10-K"])
        check(R.fetch_mix("1045810", "ua", cache_dir=d, requests_mod=req2) == res and not req2.urls,
              "inside 30 days nothing is fetched")
        check(R.cached_mix("1045810", d) == res, "cached_mix reads it offline")
        res20 = R.fetch_mix("1046179", "ua", cache_dir=d, requests_mod=_Req(["20-F", "6-K"]))
        check(res20 is None, "a 20-F filer has no 10-K table (config.REVENUE_MIX covers it)")


def test_dashboard_revenue_mix():
    print("dashboard mix: parsed, validated, stored, overlaid, cleared")
    import store
    mix, err = store.parse_revenue_mix("US 56, China 20%, western europe: 24")
    check(err is None and mix == {"US": 56.0, "China": 20.0, "western europe": 24.0}, "parsed", str(mix))
    check(store.parse_revenue_mix("Narnia 5")[1] == "unknown market: Narnia", "unknown market refused")
    check(store.parse_revenue_mix("US")[1] is not None, "a market without a weight refused")
    saved = dict(config.REVENUE_MIX_MANUAL)
    orig_mark = store.store_sync.mark_local_edit
    store.store_sync.mark_local_edit = lambda: None
    try:
        s = {"assumptions": {}}
        store.set_revenue_mix(s, "ZZTEST", {"Japan": 1.0})
        check(config.REVENUE_MIX_MANUAL.get("ZZTEST") == {"Japan": 1.0}, "overlaid at once")
        store.set_revenue_mix(s, "ZZTEST", None)
        check("ZZTEST" not in config.REVENUE_MIX_MANUAL and not s["assumptions"]["revenue_mix"],
              "cleared from the store and the overlay")
    finally:
        store.store_sync.mark_local_edit = orig_mark
        config.REVENUE_MIX_MANUAL = saved


# --------------------------------------------------------------------------- #
#  splits                                                                      #
# --------------------------------------------------------------------------- #
TSLA_CHART = {"meta": {"symbol": "TSLA"}, "events": {"splits": {
    "1596254400": {"date": 1598880600, "numerator": 5, "denominator": 1, "splitRatio": "5:1"},
    "1659326400": {"date": 1661434200, "numerator": 3, "denominator": 1, "splitRatio": "3:1"}}}}


def test_yahoo_split_events():
    print("Yahoo split events (real TSLA chart response)")
    from sources.yahoo import parse_splits
    check(parse_splits(TSLA_CHART) == [("2020-08-31", 5.0), ("2022-08-25", 3.0)],
          "5:1 on 2020-08-31, 3:1 on 2022-08-25", str(parse_splits(TSLA_CHART)))
    check(parse_splits({"events": {"splits": {"1": {"date": 1, "numerator": 1, "denominator": 10}}}})
          == [("1970-01-01", 0.1)], "a reverse split is a ratio below 1")
    check(parse_splits({}) == [], "no events, no splits")


def _ann(end, val, filed):
    start = f"{int(end[:4])}-01-01"
    return {"start": start, "end": end, "val": val, "filed": filed, "form": "10-K"}


def _tsla_like():
    """Per-share history as companyfacts keeps it: each period's newest filing wins.
    FY2019 was last presented in the 10-K filed 2022-02 (after the 5:1, before the 3:1)."""
    eps = [_ann("2025-12-31", 1.08, "2026-01-29"), _ann("2022-12-31", 3.62, "2025-01-29"),
           _ann("2021-12-31", 1.63, "2024-01-29"), _ann("2020-12-31", 0.21, "2023-01-31"),
           _ann("2019-12-31", -0.98, "2022-02-07"), _ann("2018-12-31", -1.14, "2021-02-08"),
           _ann("2017-12-31", -11.83, "2019-02-19")]
    sh = [_ann("2025-12-31", 3528e6, "2026-01-29"), _ann("2020-12-31", 3249e6, "2023-01-31"),
          _ann("2019-12-31", 887e6, "2022-02-07"), _ann("2017-12-31", 169e6, "2019-02-19")]
    rev = [_ann("2025-12-31", 94.8e9, "2026-01-29")]
    return {"facts": {"us-gaap": {
        "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": rev}},
        "EarningsPerShareDiluted": {"units": {"USD/shares": eps}},
        "WeightedAverageNumberOfDilutedSharesOutstanding": {"units": {"shares": sh}}}}}


def test_splits_restated_by_filing_date():
    print("split dates: each value restated by the filing it came from")
    from sources import sec_edgar as S
    d = S.extract(_tsla_like(), splits=[("2020-08-31", 5.0), ("2022-08-25", 3.0)])
    eps = dict(d["eps_annuals_dated"])
    check(eps["2025-12-31"] == 1.08 and eps["2020-12-31"] == 0.21,
          "values filed after both splits are untouched")
    check(abs(eps["2019-12-31"] - (-0.3267)) < 1e-3, "FY2019 (filed 2022-02) / 3", str(eps["2019-12-31"]))
    check(abs(eps["2017-12-31"] - (-0.7887)) < 1e-3, "FY2017 (filed 2019) / 15", str(eps["2017-12-31"]))
    sh = d["shares_diluted_annuals"]
    check([round(v / 1e6) for v in sh] == [3528, 3249, 2661, 2535], "share counts x3, x15", str(sh))
    check(d["split_boundaries"] == [("2020-08-31", 5.0), ("2022-08-25", 3.0)], "both splits reported")
    d0 = S.extract(_tsla_like())
    check(dict(d0["eps_annuals_dated"])["2019-12-31"] == -0.98,
          "without the dates the 2022 split stays invisible (the old limitation)")
    d1 = S.extract(_tsla_like(), splits=[])
    check(dict(d1["eps_annuals_dated"])["2019-12-31"] == -0.98, "an empty history changes nothing")


def test_real_tsla_companyfacts_if_cached():
    print("real TSLA companyfacts (when the local SEC cache has it)")
    path = os.path.join(config.CACHE_DIR, "companyfacts_0001318605.json")
    if not os.path.exists(path):
        print("  (skipped: no cached TSLA companyfacts)")
        return
    from sources import sec_edgar as S
    with open(path, encoding="utf-8") as fh:
        cf = json.load(fh)
    d = S.extract(cf, splits=[("2020-08-31", 5.0), ("2022-08-25", 3.0)])
    eps = dict(d["eps_annuals_dated"])
    check(abs(eps["2019-12-31"] - (-4.92 / 15)) < 0.01, "FY2019 -4.92 pre-splits = -0.33 today",
          str(eps.get("2019-12-31")))
    check(abs(dict(d["eps_quarters"]).get("2021-09-30", 0) - 0.48) < 0.01,
          "Q3 2021 (restated by a 2022 10-Q) unchanged at 0.48")


def test_split_flow_in_pipeline():
    print("the pipeline asks Yahoo for splits and hands them to the SEC reader")
    src = open(os.path.join(ROOT, "pipeline", "fundamentals.py"), encoding="utf-8").read()
    check("splits=splits" in src and "_split_history(t)" in src, "wired into analyze()")
    from sources import sec_edgar as S
    ifrs = {"facts": {"ifrs-full": {"Revenue": {"units": {"DKK": [_ann("2025-12-31", 3e11, "2026-02-01")]}}}}}
    d = S.extract(ifrs, splits=[("2023-09-20", 2.0)])
    check(d["split_boundaries"] == [], "IFRS (ADR) filers keep the share-ratio test: an ADR ratio "
          "change is not a split of the ordinary shares")


# --------------------------------------------------------------------------- #
#  health insurers                                                             #
# --------------------------------------------------------------------------- #
def test_health_insurer_cash():
    print("health insurers: securities backing claims are not cash")
    from sources import sec_edgar as S
    from sources.industries import securities_are_operating
    from domain.facts import FinancialFacts
    from sources import fmp
    inst = lambda end, v: {"end": end, "val": v, "filed": end, "form": "10-K"}  # noqa: E731
    cf = {"facts": {"us-gaap": {
        "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [
            _ann("2025-12-31", 199e9, "2026-02-01")]}},
        "CashAndCashEquivalentsAtCarryingValue": {"units": {"USD": [inst("2025-12-31", 10.2e9)]}},
        "ShortTermInvestments": {"units": {"USD": [inst("2025-12-31", 25.7e9)]}}}}}
    check(S.extract(cf)["cash"] == 35.9e9, "other firms: cash + short-term investments")
    check(S.extract(cf, cash_excludes_securities=True)["cash"] == 10.2e9, "ELV: cash only")
    check(securities_are_operating("ZZ", "Medical - Healthcare Plans"), "by FMP industry")
    check(securities_are_operating("ELV") and securities_are_operating("cvs"), "by ticker without FMP")
    check(not securities_are_operating("MSFT", "Software - Infrastructure"), "not for anyone else")
    bal = [{"cashAndShortTermInvestments": 35.9e9, "cashAndCashEquivalents": 10.2e9}]
    f = FinancialFacts("ZZHMO")
    fmp.parse({"profile": [{"industry": "Medical - Healthcare Plans"}], "balance_annual": bal}, f)
    check(f.cash == 10.2e9, "the FMP fallback reads cash the same way", str(f.cash))
    f = FinancialFacts("ZZTECH")
    fmp.parse({"profile": [{"industry": "Software"}], "balance_annual": bal}, f)
    check(f.cash == 35.9e9, "and keeps securities for everyone else")


def test_dashboard_wiring():
    print("dashboard: revenue-mix form and endpoints")
    html = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
    js = open(os.path.join(ROOT, "static", "js", "assumptions.js"), encoding="utf-8").read()
    app = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
    check('id="mixForm"' in html and "saveRevenueMix()" in html, "form present")
    check("async function saveRevenueMix" in js and "/api/revenue_mix" in js, "JS posts the mix")
    check('@app.get("/api/revenue_mix")' in app and '@app.post("/api/revenue_mix")' in app, "endpoints")


if __name__ == "__main__":
    run_suite(globals(), "test_damodaran_round4 OK - insurer cash, dated splits, 10-K revenue mix")
