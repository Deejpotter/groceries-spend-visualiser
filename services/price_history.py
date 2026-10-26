"""Price history for a merchant, built from stored bank transactions.

A subscription's amount is only its current price. These helpers turn the
transaction rows into a dated series so the UI can show how a bill has moved
over time, and whether the latest payment is higher or lower than the first.
"""

from datetime import datetime
from typing import Dict, List, Optional
from models import parse_ymd


def _as_date(value):
    if isinstance(value, datetime):
        return value.date()
    try:
        return parse_ymd(value)
    except (ValueError, TypeError):
        return None


def group_price_history(transactions: List[Dict]) -> Dict[str, List[Dict]]:
    """Dated price points per merchant key, oldest first.

    Only spend rows are used. A credit or refund would otherwise look like a
    price crash. Rows without a merchant key or a readable date are skipped.
    """
    groups: Dict[str, List[Dict]] = {}
    for row in transactions:
        if (row.get("direction") or "debit") == "credit":
            continue
        try:
            amount = float(row.get("amount") or 0)
        except (TypeError, ValueError):
            continue
        seen = _as_date(row.get("txn_date") or row.get("order_date"))
        key = row.get("merchant_key")
        if not key or amount <= 0 or seen is None:
            continue
        groups.setdefault(key, []).append({
            "date": seen.isoformat(),
            "amount": round(amount, 2),
        })
    for points in groups.values():
        points.sort(key=lambda point: point["date"])
    return groups


def payment_rows(points: List[Dict]) -> List[Dict]:
    """Each payment with its change from the previous one, oldest first."""
    rows = []
    previous = None
    for point in points:
        amount = float(point["amount"])
        change = round(amount - previous, 2) if previous is not None else None
        if change is None or abs(change) < 0.005:
            direction = "flat"
        elif change > 0:
            direction = "up"
        else:
            direction = "down"
        rows.append({**point, "change": change, "direction": direction})
        previous = amount
    return rows


def summarise(points: List[Dict]) -> Optional[Dict]:
    """First vs latest price for a series, or None when there is no history."""
    if not points:
        return None
    first = points[0]
    latest = points[-1]
    change = round(latest["amount"] - first["amount"], 2)
    if abs(change) < 0.005:
        direction = "flat"
    elif change > 0:
        direction = "up"
    else:
        direction = "down"
    pct = round(abs(change) / first["amount"] * 100, 1) if first["amount"] else 0
    return {
        "first": first["amount"],
        "latest": latest["amount"],
        "change": change,
        "pct": pct,
        "direction": direction,
        "started": first["date"],
        "last_seen": latest["date"],
        "points": len(points),
    }
