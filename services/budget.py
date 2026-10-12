"""Monthly grocery budget pacing and fortnightly summary."""

from calendar import monthrange
from datetime import date
from typing import Dict, List, Optional

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
