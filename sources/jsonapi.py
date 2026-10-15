"""The one HTTP call shape the keyed JSON sources share (FMP, Finnhub, Alpha Vantage)."""


def get_json(url, client, **kwargs):
    """Parsed JSON of a 200 response; None on a non-200, a network error or bad JSON.

    `client` is `requests` or an injected fake; `kwargs` go to `client.get` unchanged,
    so a fake sees exactly the call it was written for."""
    try:
        r = client.get(url, **kwargs)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None
