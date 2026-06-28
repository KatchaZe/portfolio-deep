"""
Damodaran S1 + S42 (the Grand Finale): an investment philosophy must fit YOU, and
mixing conflicting philosophies (value vs momentum/trading) needs a governing layer.
This reads a user profile and reports what philosophy the portfolio is ACTUALLY
running, plus a fit check. Pure logic over the portfolio rows + a profile dict.
"""
import json

DEFAULT_PROFILE = {"horizon": "long", "role": "value-first",
                   "tax_status": "taxable", "risk_aversion": "medium"}


def load(path):
    """Load the user's philosophy profile (JSON), merged onto defaults. Never raises."""
    try:
        with open(path, encoding="utf-8") as fh:
            return {**DEFAULT_PROFILE, **(json.load(fh) or {})}
    except Exception:
        return dict(DEFAULT_PROFILE)


def assess(rows, profile):
    """What philosophy is the portfolio running, and does it fit `profile`?
    Pure. Returns {running, avg_composite, strong_momentum_pct, garp_candidates,
    fit_ok, profile, notes}."""
    n = len(rows) or 1
    comps = [r.get("composite") for r in rows if isinstance(r.get("composite"), (int, float))]
    avg_comp = round(sum(comps) / len(comps), 2) if comps else None
    strong_mom = sum(1 for r in rows
                     if (r.get("momentum_v2") or {}).get("mom_label") in ("Strong", "Positive"))
    garp = sum(1 for r in rows if r.get("garp_candidate"))
    mom_pct = round(strong_mom / n * 100)

    value_lean = avg_comp is not None and avg_comp >= 3.5
    momentum_lean = mom_pct >= 60
    if value_lean and momentum_lean:
        running = "value + momentum (blended)"
    elif value_lean:
        running = "value / quality (DEEP-led)"
    elif momentum_lean:
        running = "momentum / trend-led"
    else:
        running = "mixed / unclear"

    role = (profile.get("role") or "")
    mismatch = (("value" in role and momentum_lean and not value_lean) or
                ("momentum" in role and value_lean and not momentum_lean))
    notes = []
    if profile.get("tax_status") == "taxable" and momentum_lean:
        notes.append("momentum-led + taxable → ระวัง turnover/ภาษี (S6)")
    if mismatch:
        notes.append(f"พอร์ตจริงเอียงไป '{running}' แต่ profile ตั้ง '{role}' — ทบทวนความสอดคล้อง (S42)")
    return {"running": running, "avg_composite": avg_comp, "strong_momentum_pct": mom_pct,
            "garp_candidates": garp, "fit_ok": not mismatch, "profile": profile, "notes": notes}
