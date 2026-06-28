"""
Damodaran S6 (Trading Costs & Taxes): a gross upside/return is eaten by trading
costs (bid-ask + price impact, round trip) and capital-gains tax on the gain.
Pure helpers so every 'after cost & tax' number is transparent and testable.
"""


def net_upside(gross_upside_pct, tax_rate, roundtrip_cost_bps):
    """Net upside % an investor keeps if they buy now and sell at fair value:
        after_cost = gross - roundtrip_cost(%)
        net        = after_cost - tax * max(after_cost, 0)
    Gains are taxed only when positive after costs. Returns None if no gross."""
    if gross_upside_pct is None:
        return None
    cost = (roundtrip_cost_bps or 0) / 100.0          # bps -> %
    after_cost = gross_upside_pct - cost
    tax = tax_rate * after_cost if after_cost > 0 else 0.0
    return round(after_cost - tax, 1)
