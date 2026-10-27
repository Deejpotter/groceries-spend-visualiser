"""Pay cycle helpers: when is the next payday, and what range does a shop need to cover?

Pure functions, no Flask, no SQL. Payday is stored as one known payday date plus
a frequency; every other payday is worked out from that anchor. Payday can also
be detected from regular salary credits in imported bank transactions.
"""

from calendar import monthrange
from datetime import date, timedelta
from statistics import mean, median
from typing import Dict, List, Optional, Tuple

from models import parse_ymd

PAY_FREQUENCIES = [("weekly", "Weekly"), ("fortnightly", "Fortnightly"), ("monthly", "Monthly")]
_STEP_DAYS = {"weekly": 7, "fortnightly": 14}
_PAY_WORDS = ("salary", "wage", "pay ", "payroll", "pay-", "pay/")
MIN_PAY_AMOUNT = 300


def _add_months(day: date, months: int, anchor_day: int) -> date:
    month = day.month - 1 + months
    year = day.year + month // 12
    month = month % 12 + 1
    return date(year, month, min(anchor_day, monthrange(year, month)[1]))


def paydays_around(anchor: date, frequency: str, today: date) -> Tuple[date, date]:
    """(last payday on or before today, next payday after today)."""
    if frequency in _STEP_DAYS:
        step = _STEP_DAYS[frequency]
        offset = (today - anchor).days // step
        last = anchor + timedelta(days=offset * step)
        return last, last + timedelta(days=step)
    months = (today.year - anchor.year) * 12 + (today.month - anchor.month)
    last = _add_months(anchor, months, anchor.day)
    if last > today:
        last = _add_months(anchor, months - 1, anchor.day)
    return last, _add_months(last, 1, anchor.day)


def pay_cycle(anchor_text: Optional[str], frequency: Optional[str], today: date) -> Optional[Dict]:
    """The pay cycle containing today, or None when payday isn't set.

    Returns {last_payday, next_payday, cycle_start, cycle_end, until_payday_start,
    days_left, frequency}. ``cycle_end`` is the day before the next payday, and
    ``until_payday_start`` is today: a shop now only needs to last until then.
    """
    try:
        anchor = parse_ymd(anchor_text) if anchor_text else None
    except ValueError:
        anchor = None
    if not anchor or frequency not in dict(PAY_FREQUENCIES):
        return None
    last, upcoming = paydays_around(anchor, frequency, today)
    end = upcoming - timedelta(days=1)
    return {
        "frequency": frequency,
        "last_payday": last.isoformat(),
        "next_payday": upcoming.isoformat(),
        "cycle_start": last.isoformat(),
        "cycle_end": end.isoformat(),
        "until_payday_start": today.isoformat(),
        "days_left": (end - today).days + 1,
    }


def _frequency_for(interval: float) -> Optional[str]:
    if 6 <= interval <= 8:
        return "weekly"
    if 12 <= interval <= 16:
        return "fortnightly"
    if 27 <= interval <= 33:
        return "monthly"
    return None


def detect_payday(transactions: List[Dict]) -> Optional[Dict]:
    """Best guess at a regular salary from bank credits, or None.

    Looks for a merchant paying in at least three times on a weekly, fortnightly
    or monthly rhythm with amounts of at least MIN_PAY_AMOUNT. Descriptions that
    mention salary or wages win; otherwise the largest total paid in wins.
    Returns {merchant_key, description, frequency, last_payday, amount, occurrences}.
    """
    groups: Dict[str, List[Dict]] = {}
    for row in transactions:
        if (row.get("direction") or "debit") != "credit":
            continue
        amount = abs(float(row.get("amount") or 0))
        if amount < MIN_PAY_AMOUNT:
            continue
        try:
            day = parse_ymd(str(row.get("txn_date"))[:10])
        except (TypeError, ValueError):
            continue
        groups.setdefault(row.get("merchant_key") or "", []).append(
            {"day": day, "amount": amount, "description": row.get("description") or ""})

    best, best_rank = None, None
    for key, rows in groups.items():
        days = sorted({r["day"] for r in rows})
        if not key or len(days) < 3:
            continue
        intervals = [(b - a).days for a, b in zip(days, days[1:])]
        frequency = _frequency_for(median(intervals))
        if not frequency:
            continue
        description = rows[-1]["description"]
        says_pay = any(w in (" " + description.lower() + " ") for w in _PAY_WORDS)
        total = sum(r["amount"] for r in rows)
        rank = (says_pay, total)
        if best_rank is None or rank > best_rank:
            best_rank = rank
            best = {
                "merchant_key": key,
                "description": description,
                "frequency": frequency,
                "last_payday": days[-1].isoformat(),
                "amount": round(mean(r["amount"] for r in rows), 2),
                "occurrences": len(days),
            }
    return best
