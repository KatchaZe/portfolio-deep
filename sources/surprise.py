"""Beat / meet / miss: one rule for every EPS and revenue surprise, whatever the source."""

THRESHOLD = 2.0    # percent; |surprise| <= 2% == "meet"


def grade(surprise_pct):
    if surprise_pct is None:
        return None
    if surprise_pct > THRESHOLD:
        return "beat"
    if surprise_pct < -THRESHOLD:
        return "miss"
    return "meet"
