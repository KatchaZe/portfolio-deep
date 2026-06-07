"""
Momentum indicators — RSI, MACD, dynamic-Bollinger (DBBMV), combined signal.
Pure functions over daily closes + volumes (Yahoo chart). Ported & verified.
"""
import math


def ema(a, p):
    k = 2 / (p + 1)
    e = a[0]
    out = [e]
    for i in range(1, len(a)):
        e = a[i] * k + e * (1 - k)
        out.append(e)
    return out


def rsi(c, p=14):
    if len(c) < p + 1:
        return None
    g = l = 0.0
    for i in range(1, p + 1):
        d = c[i] - c[i - 1]
        g += d if d > 0 else 0
        l += -d if d < 0 else 0
    ag, al = g / p, l / p
    for i in range(p + 1, len(c)):
        d = c[i] - c[i - 1]
        ag = (ag * (p - 1) + max(d, 0)) / p
        al = (al * (p - 1) + max(-d, 0)) / p
    return 100.0 if al == 0 else 100 - 100 / (1 + ag / al)


def macd(c, f=12, s=26, sg=9):
    if len(c) < s + sg:
        return None
    ef, es = ema(c, f), ema(c, s)
    line = [ef[i] - es[i] for i in range(len(ef))]
    sig = ema(line, sg)
    n = len(line)
    return {"line": line[n - 1], "pd": line[n - 2] - sig[n - 2], "cd": line[n - 1] - sig[n - 1]}


def _sma(a, p):
    return sum(a[-p:]) / p if len(a) >= p else None


def _std(a, p, m):
    return math.sqrt(sum((a[i] - m) ** 2 for i in range(len(a) - p, len(a))) / p)


def dbbmv(c, v, L=20, mult=2, ml=14, vw=0.5):
    if len(c) < L + ml:
        return None
    b = _sma(c, L)
    d = mult * _std(c, L, b)
    r = (c[-1] / c[-1 - ml] - 1) * 100
    vs = _sma(v, L)
    a = d * (1 + r / 100) * (1 + (v[-1] / vs - 1) * vw)
    return {"u": b + a, "l": b - a}


def compute(ticker, closes, vols, dates):
    if len(closes) < 30:
        return {"ticker": ticker, "error": "insufficient_data"}
    rv = rsi(closes)
    rs = "Bullish" if rv < 30 else "Bearish" if rv > 70 else "Neutral"
    m = macd(closes)
    if m["pd"] <= 0 and m["cd"] > 0:
        ms = "Bullish"
    elif m["pd"] >= 0 and m["cd"] < 0:
        ms = "Bearish"
    else:
        ms = "Bullish" if m["cd"] > 0 else "Bearish"
    bb = dbbmv(closes, vols)
    last, prev = closes[-1], closes[-2]
    ds = "Neutral"
    if bb:
        if closes[-2] <= bb["l"] and last > bb["l"]:
            ds = "Bullish"
        elif closes[-2] >= bb["u"] and last < bb["u"]:
            ds = "Bearish"
        elif last < bb["l"]:
            ds = "Bullish"
        elif last > bb["u"]:
            ds = "Bearish"
    sm = {"Bullish": 1, "Neutral": 0, "Bearish": -1}
    tot = sm[rs] + sm[ms] + sm[ds]
    mo = "Bullish" if tot >= 2 else "Bearish" if tot <= -2 else "Neutral"
    return {"ticker": ticker, "price": round(last, 2), "change": round((last - prev) / prev * 100, 2),
            "rsi": round(rv, 1), "rsi_signal": rs, "macd_signal": ms, "dbbmv_signal": ds,
            "momentum_score": tot, "momentum_signal": mo, "as_of": dates[-1] if dates else None}


# combined Action (DEEP signal x momentum)  — used by the daily refresh
ACTION_MAP = {
    ("BUY", "Bullish"): "STRONG BUY", ("BUY", "Neutral"): "BUY", ("BUY", "Bearish"): "WAIT",
    ("HOLD", "Bullish"): "ACCUMULATE", ("HOLD", "Neutral"): "HOLD", ("HOLD", "Bearish"): "WATCH",
    ("SELL", "Bullish"): "TRIM", ("SELL", "Neutral"): "SELL", ("SELL", "Bearish"): "STRONG SELL",
}


def action(signal, momentum_signal):
    if signal is None:
        return None
    if momentum_signal is None:
        return signal
    return ACTION_MAP.get((signal, momentum_signal), signal)
