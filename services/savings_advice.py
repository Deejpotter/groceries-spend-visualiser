"""Suggested savings per payday, worked out from bank history.

Pure functions, no Flask, no SQL. Income comes from the detected salary, costs
from the bank rows in the same window. Transfers between your own accounts are
moves, not income or spending. The rules follow common Australian guidance:
keep about three months of essential costs as an emergency fund, and treat 20%
of take-home pay as a savings benchmark.
"""

import math
import re
from datetime import date, timedelta
from typing import Dict, Iterable, List, Optional

from models import parse_ymd
from services.recurring import _INTERNAL_TRANSFER_PATTERNS, normalise_merchant

WINDOW_DAYS = 90
MIN_DAYS = 28
CYCLE_DAYS = {"weekly": 7, "fortnightly": 14, "monthly": 365.25 / 12}
BUFFER_SHARE = 0.2
LONG_TERM_SHARE = 0.5
BENCHMARK_SHARE = 0.2
EMERGENCY_MONTHS = 3
ROUND_TO = 10

_OWN_ACCOUNT_TRANSFER = re.compile(r"\btransfer\s+(to|from)\s+(?:\w+\s+)?xx(\d{3,4})\b",
                                   re.IGNORECASE)


def parse_accounts(text: Optional[str]) -> List[str]:
    """Account endings from a setting like "9910, xx1139" -> ["9910", "1139"]."""
    return re.findall(r"\d{3,4}", text or "")


def transfer_account(description: str) -> Optional[Dict]:
    """{"way": "to"|"from", "account": "9910"} for an own-account transfer, else None."""
    match = _OWN_ACCOUNT_TRANSFER.search(description or "")
    if match:
        return {"way": match.group(1).lower(), "account": match.group(2)}
    return None


def is_move(description: str) -> bool:
    """True when a bank row moves money between your own accounts."""
    if transfer_account(description):
        return True
    lowered = (description or "").lower()
    return any(pattern in lowered for pattern in _INTERNAL_TRANSFER_PATTERNS)


def _day(row: Dict) -> Optional[date]:
    try:
        return parse_ymd(str(row.get("txn_date"))[:10])
    except (TypeError, ValueError):
        return None


def _floor(value: float) -> float:
    return float(max(0, math.floor(value / ROUND_TO) * ROUND_TO))


def cash_flow(transactions: Iterable[Dict], pay_key: Optional[str],
              savings_accounts: Iterable[str] = (), window_days: int = WINDOW_DAYS) -> Optional[Dict]:
    """Totals for the last window_days of bank history, or None without rows.

    Splits debits into groceries, moves and other spending, and credits into
    pay, moves back from savings and other money in. The window ends on the
    latest row, so an old import still gives a full window.
    """
    rows = [(row, _day(row)) for row in transactions]
    rows = [(row, day) for row, day in rows if day]
    if not rows:
        return None
    end = max(day for _, day in rows)
    start = end - timedelta(days=window_days - 1)
    savings_accounts = set(savings_accounts)
    totals = {"pay": 0.0, "other_in": 0.0, "from_savings": 0.0, "groceries": 0.0,
              "spending": 0.0, "moved_out": 0.0}
    merchants: Dict[str, float] = {}
    examples: Dict[str, str] = {}
    first = end
    for row, day in rows:
        if day < start:
            continue
        first = min(first, day)
        amount = abs(float(row.get("amount") or 0))
        description = row.get("description") or ""
        direction = row.get("direction") or "debit"
        is_pay = bool(pay_key) and (row.get("merchant_key") or "") == pay_key
        if direction == "credit" or (direction == "unknown" and is_pay):
            transfer = transfer_account(description)
            if (row.get("merchant_key") or "") == pay_key and pay_key:
                totals["pay"] += amount
            elif transfer and transfer["account"] in savings_accounts:
                totals["from_savings"] += amount
            elif not is_move(description):
                totals["other_in"] += amount
            continue
        if is_move(description):
            totals["moved_out"] += amount
        elif (row.get("status") or "") == "grocery":
            totals["groceries"] += amount
        else:
            totals["spending"] += amount
            key = normalise_merchant(description) or row.get("merchant_key") or description
            merchants[key] = merchants.get(key, 0.0) + amount
            examples[key] = description
    top = sorted(merchants.items(), key=lambda item: -item[1])[:5]
    return {
        "start": first.isoformat(),
        "end": end.isoformat(),
        "days": (end - first).days + 1,
        **{key: round(value, 2) for key, value in totals.items()},
        "top_spending": [{"name": name, "amount": round(total, 2), "example": examples[name]}
                         for name, total in top],
    }


def suggest_savings(flow: Optional[Dict], pay: Optional[Dict], monthly_bills: float = 0.0,
                    income_override: Optional[float] = None) -> Optional[Dict]:
    """Suggested savings and long term amounts for each payday, or None.

    flow is a cash_flow() result and pay a detect_payday() result. Costs are
    scaled to one pay cycle. What is left after costs is the surplus; a fifth of
    it stays unallocated as a buffer for irregular costs. Half of the rest goes
    to long term savings until the emergency fund target is met.
    """
    if not flow or not pay:
        return None
    cycle = CYCLE_DAYS.get(pay.get("frequency") or "", 14)
    days = max(flow["days"], 1)
    per_cycle = cycle / days
    income = round(income_override if income_override else pay["amount"], 2)
    groceries = round(flow["groceries"] * per_cycle, 2)
    bills = round(monthly_bills * 12 / 365.25 * cycle, 2)
    spending = round(flow["spending"] * per_cycle, 2)
    everyday = round(max(spending - bills, 0.0), 2)
    costs = round(groceries + max(spending, bills), 2)
    surplus = round(income - costs, 2)
    to_save = _floor(surplus * (1 - BUFFER_SHARE))
    long_term = _floor(to_save * LONG_TERM_SHARE)
    savings = round(to_save - long_term, 2)
    monthly_essentials = round((groceries + bills) / cycle * 365.25 / 12, 2)
    emergency_target = _floor(monthly_essentials * EMERGENCY_MONTHS)
    benchmark = round(income * BENCHMARK_SHARE, 2)
    from_savings = round(flow["from_savings"] * per_cycle, 2)
    return {
        "frequency": pay.get("frequency") or "fortnightly",
        "income": income,
        "groceries": groceries,
        "bills": bills,
        "everyday": everyday,
        "costs": costs,
        "surplus": surplus,
        "buffer": round(max(surplus, 0) - to_save, 2),
        "savings": savings,
        "long_term": long_term,
        "save_share": round(to_save / income * 100) if income else 0,
        "benchmark": benchmark,
        "benchmark_gap": round(max(benchmark - to_save, 0), 2),
        "emergency_target": emergency_target,
        "pays_to_target": math.ceil(emergency_target / long_term) if long_term and emergency_target else None,
        "from_savings": from_savings,
        "top_spending": [dict(item, amount=round(item["amount"] * per_cycle, 2))
                         for item in flow["top_spending"]],
        "days": flow["days"],
        "start": flow["start"],
        "end": flow["end"],
        "enough_history": flow["days"] >= MIN_DAYS,
        "overspending": surplus <= 0,
    }


SAVINGS_WORDS = ("saver", "savings", "goal", "save", "bonus")


def savings_accounts(accounts: Iterable[Dict], endings: Iterable[str] = ()) -> List[Dict]:
    """The accounts that hold savings.

    Uses the endings from Settings when there are any; otherwise any account
    whose name says saver, savings, goal or bonus, or whose type is exactly
    savings. CDR types like TRANS_AND_SAVINGS_ACCOUNTS cover everyday accounts
    too, so a type that merely contains "savings" doesn't count.
    """
    accounts = list(accounts or [])
    endings = [e for e in endings if e]
    if endings:
        return [a for a in accounts if a.get("ending") and a["ending"] in endings]
    return [a for a in accounts
            if any(w in (a.get("name") or "").lower() for w in SAVINGS_WORDS)
            or (a.get("type") or "").strip().lower() in ("savings", "saver")]


def savings_progress(accounts: Iterable[Dict], target: float,
                     long_term_per_pay: float = 0.0) -> Optional[Dict]:
    """Progress of savings balances towards the emergency fund target, or None.

    None when no savings account has a known balance.
    """
    known = [a for a in accounts if a.get("balance") is not None]
    if not known:
        return None
    saved = round(sum(float(a["balance"]) for a in known), 2)
    remaining = round(max(target - saved, 0.0), 2)
    return {
        "saved": saved,
        "target": target,
        "remaining": remaining,
        "percent": min(100, round(saved / target * 100)) if target else 100,
        "reached": bool(target) and saved >= target,
        "pays_to_go": math.ceil(remaining / long_term_per_pay) if remaining and long_term_per_pay else None,
        "accounts": [{"name": a.get("name") or "Account", "ending": a.get("ending") or "",
                      "balance": round(float(a["balance"]), 2)} for a in known],
    }


def make_txn(day: str, amount: float, description: str, direction: str = "debit",
             status: str = "other", merchant_key: Optional[str] = None) -> Dict:
    """A bank_transactions row for tests."""
    return {"txn_date": day, "amount": amount, "description": description,
            "direction": direction, "status": status,
            "merchant_key": merchant_key or normalise_merchant(description)}
