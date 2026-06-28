"""test_philosophy — Damodaran S1/S42 philosophy-fit. Pure/offline."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from domain import philosophy


def test_assess():
    prof = {"role": "value-first", "tax_status": "taxable", "horizon": "long"}
    mom_rows = [{"composite": 2.0, "momentum_v2": {"mom_label": "Strong"}, "garp_candidate": False}
                for _ in range(5)]
    a = philosophy.assess(mom_rows, prof)
    assert a["running"] == "momentum / trend-led", a
    assert a["fit_ok"] is False                       # value role but momentum-led
    assert any("turnover" in x for x in a["notes"])
    val_rows = [{"composite": 4.2, "momentum_v2": {"mom_label": "Neutral"}, "garp_candidate": True}
                for _ in range(4)]
    b = philosophy.assess(val_rows, prof)
    assert b["running"] == "value / quality (DEEP-led)", b
    assert b["fit_ok"] is True and b["garp_candidates"] == 4
    assert philosophy.load("/no/such/file.json")["role"] == "value-first"   # default
    print("philosophy assess OK")


if __name__ == "__main__":
    test_assess()
    print("\nALL test_philosophy PASSED")
