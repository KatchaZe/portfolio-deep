"""
Stooq source adapter — free daily OHLCV (no API key). Used as a price/momentum
FALLBACK when Yahoo is blocked (Yahoo often empties responses from datacenter IPs,
so on a cloud host momentum/price can blank out — Stooq still answers).

Fetch (network) is separated from parse (pure) so the CSV parser is unit-testable
offline. Stooq CSV endpoint:

    https://stooq.com/q/d/l/?s=<symbol>&i=d

US tickers take a '.us' suffix (e.g. nvda.us). Response columns (ascending date):
    Date,Open,High,Low,Close,Volume
An unknown symbol returns the body 'N/D' (no header) -> empty series, never raises.
"""
import io
import csv

_UA = {"User-Agent": "Mozilla/5.0"}


def to_symbol(ticker):
    """US ticker -> Stooq symbol: lower-case + '.us'; a class dot becomes a dash
    (BRK.B -> brk-b.us)."""
    t = (ticker or "").strip().lower()
    if not t:
        return ""
    if t.endswith(".us"):
        return t
    if "." in t:
        t = t.replace(".", "-")
    return f"{t}.us"


def parse_csv(text):
    """Stooq daily CSV -> {closes, volumes, dates} ascending by date. Rows with a
    missing/`N/D` close or volume are skipped. Bad/unknown-symbol bodies (no
    header) yield empty series."""
    closes, vols, dates = [], [], []
    if not text or "Date" not in text.split("\n", 1)[0]:
        return {"closes": closes, "volumes": vols, "dates": dates}
    for row in csv.DictReader(io.StringIO(text)):
        c, v = row.get("Close"), row.get("Volume")
        if c in (None, "", "N/D") or v in (None, "", "N/D"):
            continue
        try:
            close, vol = float(c), float(v)
        except (TypeError, ValueError):
            continue
        closes.append(close)
        vols.append(vol)
        dates.append(row.get("Date"))
    return {"closes": closes, "volumes": vols, "dates": dates}


def fetch_chart(ticker, requests_mod=None, timeout=15):
    """Daily closes+volumes+dates from Stooq (oldest -> newest). Raises on a
    network/HTTP error; returns possibly-empty series for an unknown symbol."""
    import requests as _r
    requests_mod = requests_mod or _r
    sym = to_symbol(ticker)
    r = requests_mod.get(f"https://stooq.com/q/d/l/?s={sym}&i=d",
                         headers=_UA, timeout=timeout)
    r.raise_for_status()
    return parse_csv(r.text)
