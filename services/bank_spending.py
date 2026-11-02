"""All spending from bank history, not just groceries.

Pure functions, no Flask, no SQL. Money out is grouped into groceries and the
same categories the Bills page uses. Transfers between your own accounts are
moves, so they are left out, as are credits and rows whose direction is
unknown.
"""

from collections import defaultdict
from datetime import timedelta
from typing import Dict, Iterable, List, Optional

from services.recurring import guess_category, known_merchant, normalise_merchant, transfer_payee
from services.savings_advice import _day, is_move
from services.subscriptions import subscription_category_label

DAYS_PER_MONTH = 365.25 / 12
TOP_MERCHANTS = 8


def category_for(row: Dict) -> str:
    """Category code for one bank debit: 'groceries' or a Bills category."""
    if (row.get("status") or "") == "grocery":
        return "groceries"
    description = row.get("description") or ""
    known = known_merchant([description])
    return (known or {}).get("category") or guess_category(description)


def category_label(code: str) -> str:
    return "Groceries" if code == "groceries" else subscription_category_label(code)


def spend_items(transactions: Iterable[Dict], days: Optional[int] = None) -> Optional[Dict]:
    """Money-out rows in the window as {end, items, moved, unsure}, or None.

    Each item is {day, amount, description, category, key, name}. Credits are
    skipped; moves between own accounts and rows of unknown direction are only
    totalled. Both the Spend page and its category drill-down use this.
    """
    rows = [(row, _day(row)) for row in transactions]
    rows = [(row, day) for row, day in rows if day]
    if not rows:
        return None
    end = max(day for _, day in rows)
    start = end - timedelta(days=days - 1) if days else None
    items, moved, unsure = [], 0.0, 0.0
    for row, day in rows:
        if start and day < start:
            continue
        direction = row.get("direction") or "debit"
        if direction == "credit":
            continue
        amount = abs(float(row.get("amount") or 0))
        description = row.get("description") or ""
        if direction == "unknown":
            unsure += amount
            continue
        if is_move(description):
            moved += amount
            continue
        key = normalise_merchant(description) or row.get("merchant_key") or description
        payee = transfer_payee(description)
        items.append({"day": day, "amount": amount, "description": description,
                      "category": category_for(row), "key": key,
                      "name": f"Transfer to {payee}" if payee else key})
    return {"end": end, "items": items, "moved": round(moved, 2), "unsure": round(unsure, 2)}


def category_detail(transactions: Iterable[Dict], code: str, days: Optional[int] = None) -> Optional[Dict]:
    """One category's transactions, grouped by merchant, or None when it has none.

    Merchants are the sub-categories: each has a total, a count and its rows,
    newest first.
    """
    window = spend_items(transactions, days)
    items = [i for i in (window or {}).get("items", []) if i["category"] == code]
    if not items:
        return None
    groups: Dict[str, Dict] = {}
    for item in items:
        group = groups.setdefault(item["key"], {"name": item["name"], "amount": 0.0, "count": 0, "rows": []})
        group["amount"] += item["amount"]
        group["count"] += 1
        group["rows"].append({"date": item["day"].isoformat(), "description": item["description"],
                              "amount": round(item["amount"], 2)})
    total = sum(g["amount"] for g in groups.values())
    merchants = sorted(groups.values(), key=lambda g: -g["amount"])
    for group in merchants:
        group["amount"] = round(group["amount"], 2)
        group["share"] = round(group["amount"] / total * 100)
        group["rows"].sort(key=lambda r: r["date"], reverse=True)
    return {
        "code": code,
        "label": category_label(code),
        "total": round(total, 2),
        "count": len(items),
        "start": min(i["day"] for i in items).isoformat(),
        "end": max(i["day"] for i in items).isoformat(),
        "merchants": merchants,
    }


def bank_spending(transactions: Iterable[Dict], days: Optional[int] = None) -> Optional[Dict]:
    """Money out over the last `days` days of bank history (all of it when None).

    Returns None when there are no bank debits in the window. The window ends
    on the latest bank row, so an old import still shows a full range.
    """
    window = spend_items(transactions, days)
    if not window:
        return None
    end = window["end"]
    categories: Dict[str, float] = defaultdict(float)
    months: Dict[str, Dict[str, float]] = defaultdict(lambda: {"groceries": 0.0, "other": 0.0})
    merchants: Dict[str, Dict] = {}
    moved, unsure = window["moved"], window["unsure"]
    first = end
    for item in window["items"]:
        day, amount, code, key = item["day"], item["amount"], item["category"], item["key"]
        first = min(first, day)
        categories[code] += amount
        months[day.strftime("%Y-%m")]["groceries" if code == "groceries" else "other"] += amount
        entry = merchants.setdefault(key, {"name": item["name"], "amount": 0.0, "count": 0, "category": code})
        entry["amount"] += amount
        entry["count"] += 1
    total = sum(categories.values())
    if not total:
        return None
    span = (end - first).days + 1
    return {
        "start": first.isoformat(),
        "end": end.isoformat(),
        "days": span,
        "total": round(total, 2),
        "per_month": round(total / max(span / DAYS_PER_MONTH, 1), 2),
        "groceries": round(categories.get("groceries", 0.0), 2),
        "groceries_share": round(categories.get("groceries", 0.0) / total * 100),
        "categories": [
            {"code": code, "label": category_label(code), "amount": round(amount, 2),
             "share": round(amount / total * 100)}
            for code, amount in sorted(categories.items(), key=lambda item: -item[1])
        ],
        "months": [
            {"month": month, "groceries": round(v["groceries"], 2), "other": round(v["other"], 2),
             "total": round(v["groceries"] + v["other"], 2)}
            for month, v in sorted(months.items())
        ],
        "top_merchants": [
            {**m, "amount": round(m["amount"], 2), "label": category_label(m["category"])}
            for m in sorted(merchants.values(), key=lambda m: -m["amount"])[:TOP_MERCHANTS]
        ],
        "moved": round(moved, 2),
        "unsure": round(unsure, 2),
    }
