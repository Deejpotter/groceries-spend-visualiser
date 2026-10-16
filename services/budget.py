"""Monthly grocery budget pacing and fortnightly summary."""

from calendar import monthrange
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple, Union

from services.subscriptions import (
    fortnight_for_date,
    fortnightly_subscription_total,
    format_frequency,
    subscription_category_label,
    subscriptions_due_in_range,
)


def budget_status(rows: List[Dict], target: Optional[float],
                  today: Optional[date] = None) -> Optional[Dict]:
    """How this month's grocery spend is pacing against a target.

    Returns None when there's no target. rows are purchase dicts
    with order_date and line_total; only this month's count.
    tone follows the dashboard convention: ok / warn / danger.
    """
    if target is None or target <= 0:
        return None
    today = today or date.today()
    month = f"{today.year:04d}-{today.month:02d}"
    spent = sum(
        row["line_total"] for row in rows
        if str(row.get("order_date", ""))[:7] == month
    )
    days_in_month = monthrange(today.year, today.month)[1]
    days_elapsed = today.day
    elapsed_pct = days_elapsed / days_in_month
    spend_pct = spent / target
    projected = spent / elapsed_pct if elapsed_pct > 0 else spent
    variance = target - projected
    if projected <= target:
        tone = "ok"
    elif projected <= target * 1.1:
        tone = "warn"
    else:
        tone = "danger"
    return {
        "month": month,
        "spent": round(spent, 2),
        "target": round(target, 2),
        "days_elapsed": days_elapsed,
        "days_in_month": days_in_month,
        "days_left": days_in_month - today.day,
        "elapsed_pct": round(elapsed_pct, 4),
        "spend_pct": round(spend_pct, 4),
        "projected": round(projected, 2),
        "variance": round(variance, 2),
        "tone": tone,
    }


def _as_date(value) -> Optional[date]:
    """ISO string, datetime or date to a date, else None."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        return None


def prorate_estimate(total: float, range_start, range_end,
                     window_start, window_end) -> Tuple[float, int, int]:
    """Prorate a whole-range estimate down to the overlapping window.

    Returns (prorated, range_days, overlap_days). A 4-week plan overlapping
    a fortnight by 14 of 28 days contributes half its cost. Empty or
    non-overlapping ranges prorate to zero.
    """
    start, end = _as_date(range_start), _as_date(range_end)
    w_start, w_end = _as_date(window_start), _as_date(window_end)
    if start is None or end is None or w_start is None or w_end is None:
        return 0.0, 0, 0
    if end < start or w_end < w_start:
        return 0.0, 0, 0
    range_days = (end - start).days + 1
    overlap = (min(end, w_end) - max(start, w_start)).days + 1
    overlap = max(0, overlap)
    if range_days <= 0 or overlap == 0:
        return 0.0, range_days, 0
    return round(float(total or 0) * overlap / range_days, 2), range_days, overlap


def fortnightly_summary(subscriptions: List[Dict], grocery_estimate: float,
                       income: Optional[float], start: date, end: date) -> Dict:
    """Fortnightly budget breakdown: income - groceries - subscriptions = spare.

    subscriptions: list of subscription dicts (active only).
    grocery_estimate: estimated grocery cost for the period.
    income: fortnightly income (None when not configured).
    start, end: date objects for the fortnight.

    Returns a dict with income, groceries, subscriptions detail, spare, and tone.
    """
    due = subscriptions_due_in_range(subscriptions, start, end)
    sub_total = round(sum(float(s.get("amount") or 0) for s in due), 2)
    groceries = round(float(grocery_estimate or 0), 2)

    if income is not None and income > 0:
        spare = round(income - groceries - sub_total, 2)
        if spare >= 0:
            tone = "ok"
        elif spare >= -income * 0.1:
            tone = "warn"
        else:
            tone = "danger"
    else:
        spare = None
        tone = "neutral"

    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "income": income,
        "groceries": groceries,
        "subscriptions": due,
        "subscription_total": sub_total,
        "spare": spare,
        "tone": tone,
        "has_income": income is not None and income > 0,
    }
