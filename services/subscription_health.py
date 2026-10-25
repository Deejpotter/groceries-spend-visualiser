"""Detect bills that have stopped paying.

A subscription is "possibly ended" when its linked payment stream has missed
three or more expected cycles. This is a suggestion, not an automatic action:
the user can pause it, remove it, or say they are still paying it.
"""

from datetime import date, datetime
from typing import Dict, List, Optional

from services.subscriptions import FREQUENCIES, advance_occurrence

MISSED_CYCLES_THRESHOLD = 3
_MAX_CYCLES = 200


def _as_date(value) -> Optional[date]:
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def missed_cycles(last_seen, frequency: str, today: Optional[date] = None) -> int:
    """Expected payments that have not happened since the last sighting.

    Month-end bills use calendar arithmetic, so a payment on the 14th stays on
    the 14th. Returns 0 for an unknown frequency or date.
    """
    start = _as_date(last_seen)
    freq = (frequency or "").lower()
    if start is None or freq not in FREQUENCIES:
        return 0
    today = today or date.today()
    current = advance_occurrence(start, freq)
    count = 0
    while current <= today and count < _MAX_CYCLES:
        count += 1
        current = advance_occurrence(current, freq)
    return count


def ended_subscriptions(subscriptions: List[Dict],
                        today: Optional[date] = None,
                        threshold: int = MISSED_CYCLES_THRESHOLD) -> List[Dict]:
    """Active subscriptions whose linked stream has missed enough cycles.

    ``subscriptions`` need to carry ``price_points`` (already filtered to their
    stream by the Bills route) and ``ended_dismissed_at``. A dismissal is
    ignored once a newer payment appears.
    """
    today = today or date.today()
    results = []
    for sub in subscriptions:
        if not sub.get("active", 1):
            continue
        points = sub.get("price_points") or []
        if not points:
            continue
        last_seen = points[-1].get("date")
        missed = missed_cycles(last_seen, sub.get("frequency"), today)
        if missed < threshold:
            continue
        dismissed = _as_date(sub.get("ended_dismissed_at"))
        if dismissed and last_seen is not None:
            last_day = _as_date(last_seen)
            if last_day is not None and last_day <= dismissed:
                continue
        row = dict(sub)
        row["last_seen"] = last_seen
        row["missed_cycles"] = missed
        results.append(row)
    return sorted(results, key=lambda row: -row["missed_cycles"])
