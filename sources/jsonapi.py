"""The one HTTP call shape the keyed JSON sources share (FMP, Finnhub, Alpha Vantage)."""


def get_json(url, requests_mod=None, **kwargs):
    """Parsed JSON of a 200 response; None on a non-200, a network error or bad JSON.

    `kwargs` go to `requests_mod.get` unchanged, so injected test fakes see exactly
    the call they were written for."""
    import requests as _r
    requests_mod = requests_mod or _r
    try:
        r = requests_mod.get(url, **kwargs)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None
