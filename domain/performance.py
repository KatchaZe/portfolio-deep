"""
Realized performance of the book against SPY: time-weighted return (TWR), tracking
error and information ratio.

The input is the store's NAV log, one entry per day the daily refresh ran:
    {"date": "YYYY-MM-DD", "nav": total value (holdings at close + cash),
     "flow": money put in (+) or taken out (-) since the previous entry,
     "spy": SPY close}
A sub-period return nets the flow out: r_i = (NAV_i - flow_i) / NAV_{i-1} - 1, with
the flow taken at the end of the period (it bought at that day's close). Chaining the
r_i gives the TWR, the return of the decisions rather than of the deposits.

Tracking error = stdev of the active sub-period returns, annualized by the average
number of periods per year in the log. It and the information ratio need
MIN_PERIODS periods; below that only the cumulative returns are reported.

Pure: no I/O.
"""
import datetime as dt
import math

MIN_PERIODS = 20


def _date(s):
    return dt.date.fromisoformat(s)


def periods(log):
    """[(date, portfolio r, SPY r)] between consecutive usable entries."""
    out = []
    rows = [e for e in (log or []) if e.get("nav") and e["nav"] > 0]
    for prev, cur in zip(rows, rows[1:]):
        rp = (cur["nav"] - (cur.get("flow") or 0.0)) / prev["nav"] - 1
        rb = (cur["spy"] / prev["spy"] - 1) if (cur.get("spy") and prev.get("spy")) else None
        out.append((cur["date"], rp, rb))
    return out


def _chain(rs):
    g = 1.0
    for r in rs:
        g *= 1 + r
    return g - 1


def summary(log):
    """Cumulative TWR vs SPY over the log, annualized once it spans a year, and
    tracking error / information ratio once there are MIN_PERIODS periods."""
    ps = periods(log)
    if not ps:
        return {"status": "insufficient", "n_periods": 0,
                "note": "ต้องมี NAV อย่างน้อย 2 วัน (กด Run Daily วันละครั้ง)"}
    start = next(e["date"] for e in log if e.get("nav") and e["nav"] > 0)
    end = ps[-1][0]
    days = max(1, (_date(end) - _date(start)).days)
    both = [(rp, rb) for _, rp, rb in ps if rb is not None]
    twr = _chain(rp for _, rp, _ in ps)
    spy = _chain(rb for _, rb in both) if len(both) == len(ps) else None
    out = {"status": "ok", "start": start, "end": end, "days": days, "n_periods": len(ps),
           "twr_pct": round(twr * 100, 2),
           "spy_pct": round(spy * 100, 2) if spy is not None else None,
           "active_pp": round((twr - spy) * 100, 2) if spy is not None else None,
           "annualized": days >= 365}
    if days >= 365:
        yrs = days / 365.25
        out["twr_ann_pct"] = round(((1 + twr) ** (1 / yrs) - 1) * 100, 2)
        if spy is not None:
            out["spy_ann_pct"] = round(((1 + spy) ** (1 / yrs) - 1) * 100, 2)
    if len(both) >= MIN_PERIODS:
        act = [rp - rb for rp, rb in both]
        m = sum(act) / len(act)
        sd = math.sqrt(sum((a - m) ** 2 for a in act) / (len(act) - 1))
        per_year = len(ps) / (days / 365.25)
        te = sd * math.sqrt(per_year)
        out["tracking_error_pct"] = round(te * 100, 2)
        out["information_ratio"] = round(m * per_year / te, 2) if te > 0 else None
    else:
        out["note"] = f"tracking error / IR ต้องมี {MIN_PERIODS} ช่วงขึ้นไป (มี {len(both)})"
    return out
