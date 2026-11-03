"""How much is left to spend before the next payday.

Pure functions, no Flask, no SQL. Takes this pay, takes off the suggested
savings, what has gone out since payday, and the bills still due before the
next payday. Moves between your own accounts are not spending, and rows of
unknown direction are left out.
"""

from datetime import date, timedelta
from typing import Dict, Iterable, List, Optional

from models import parse_ymd
from services.savings_advice import CYCLE_DAYS, _day, is_move
from services.subscriptions import next_occurrence


def next_payday(last_payday: date, frequency: str) -> date:
    """The payday after last_payday."""
    if frequency == "monthly":
        month = last_payday.month % 12 + 1
        year = last_payday.year + (1 if month == 1 else 0)
        for day in (last_payday.day, 30, 29, 28):
            try:
                return date(year, month, day)
            except ValueError:
                continue
    return last_payday + timedelta(days=int(CYCLE_DAYS.get(frequency, 14)))


def spent_since(transactions: Iterable[Dict], start: date, pay_key: Optional[str] = None) -> float:
    """Money out from start onwards, leaving out moves and unknown-direction rows."""
    total = 0.0
    for row in transactions:
        day = _day(row)
        if not day or day < start:
            continue
        if (row.get("direction") or "debit") != "debit":
            continue
        if pay_key and (row.get("merchant_key") or "") == pay_key:
            continue
        if is_move(row.get("description") or ""):
            continue
        total += abs(float(row.get("amount") or 0))
    return round(total, 2)


def bills_due(bills: Iterable[Dict], start: date, end: date) -> List[Dict]:
    """Active bills with a payment due from start up to (not including) end."""
    due = []
    for bill in bills:
        if not bill.get("active", 1):
            continue
        when = next_occurrence(bill, start)
        if when and when < end:
            due.append({"name": bill.get("name") or "Bill", "amount": round(float(bill.get("amount") or 0), 2),
                        "date": when.isoformat()})
    return sorted(due, key=lambda b: b["date"])


def left_to_spend(pay: Optional[Dict], advice: Optional[Dict], transactions: Iterable[Dict],
                  bills: Iterable[Dict], today: date) -> Optional[Dict]:
    """What is left of this pay after savings, spending so far and bills still due.

    None without a detected payday and a savings suggestion, or when the bank
    history stops before this pay cycle (an old import would give nonsense).
    """
    if not pay or not advice:
        return None
    try:
        last = parse_ymd(pay["last_payday"])
    except (KeyError, TypeError, ValueError):
        return None
    following = next_payday(last, pay.get("frequency") or "fortnightly")
    if today < last or today >= following:
        return None
    transactions = list(transactions)
    spent = spent_since(transactions, last, pay.get("merchant_key"))
    latest = max((d for d in (_day(r) for r in transactions) if d), default=today)
    due = bills_due(bills, max(today, latest + timedelta(days=1)), following)
    saving = round(advice["savings"] + advice["long_term"], 2)
    bills_total = round(sum(b["amount"] for b in due), 2)
    income = advice["income"]
    left = round(income - saving - spent - bills_total, 2)
    days_left = (following - today).days
    return {
        "income": income,
        "saving": saving,
        "spent": spent,
        "bills_due": due,
        "bills_total": bills_total,
        "left": left,
        "per_day": round(max(left, 0) / days_left, 2) if days_left else 0.0,
        "days_left": days_left,
        "since": last.isoformat(),
        "next_payday": following.isoformat(),
        "over": left < 0,
    }
