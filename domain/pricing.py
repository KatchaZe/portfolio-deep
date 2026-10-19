"""Which close the valuation runs on: the last COMPLETED session before today in New
York (the project's T-1 rule). A live quote or today's partial daily bar moves through
the session; a fair value tested against it changes by the hour. Monday -> Friday's
close, Tuesday-Friday -> yesterday's.

Yahoo stamps a daily bar at the session open (13:30/14:30 UTC), so the UTC date of the
timestamp is the New York trading date. Pure: `today` is injectable."""
import datetime as dt

try:
    from zoneinfo import ZoneInfo
    _NY = ZoneInfo("America/New_York")
except Exception:                     # tzdata missing (bare Windows Python)
    _NY = dt.timezone(dt.timedelta(hours=-5))


def ny_today(now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    return now.astimezone(_NY).strftime("%Y-%m-%d")


def completed_bars(closes, dates, today=None, *others):
    """Drop the bars dated today (NY) or later. Returns (closes, dates, *others) cut to
    the same length; series without dates are returned unchanged."""
    if not closes or not dates or len(dates) != len(closes):
        return (closes, dates, *others)
    today = today or ny_today()
    n = len(closes)
    while n and str(dates[n - 1])[:10] >= today:
        n -= 1
    return (closes[:n], dates[:n], *[o[:n] if o is not None else o for o in others])


def t1_close(closes, dates, today=None):
    """(close, date) of the last session before today, else (None, None)."""
    c, d = completed_bars(closes, dates, today)[:2]
    return (float(c[-1]), str(d[-1])[:10]) if c else (None, None)
