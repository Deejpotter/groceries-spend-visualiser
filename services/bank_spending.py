"""All spending from bank history, not just groceries.

Pure functions, no Flask, no SQL. Money out is grouped into groceries and the
same categories the Bills page uses. Transfers between your own accounts are
moves, so they are left out, as are credits and rows whose direction is
unknown.
"""

import re
from collections import defaultdict
from datetime import timedelta
from typing import Dict, Iterable, List, Optional

from functools import lru_cache

from services.recurring import (
    _display_name, guess_category, known_merchant, normalise_merchant, transfer_payee,
)
from services.savings_advice import _day, is_move
from services.subscriptions import SUBSCRIPTION_CATEGORIES, subscription_category_label

DAYS_PER_MONTH = 365.25 / 12
TOP_MERCHANTS = 8

# Everyday spending has no place in the Bills categories, so the Spend page
# adds its own. They are only guessed when nothing more specific matched.
SPEND_CATEGORIES = [
    ("eating_out", "Eating out"),
    ("fuel", "Fuel"),
    ("shopping", "Shopping"),
]
_SPEND_KEYWORDS = [
    ("eating_out", ("mcdonalds", "mcdonald's", "kfc", "hungry jack", "guzman", "dominos", "domino's",
                    "pizza", "uber eats", "doordash", "menulog", "subway", "oporto", "red rooster",
                    "nandos", "grill'd", "cafe", "coffee", "restaurant", "bakery", "sushi", "kebab")),
    ("fuel", ("bp", "shell", "ampol", "caltex", "7-eleven", "united petroleum", "metro petroleum",
              "liberty", "puma energy", "fuel", "petrol")),
    ("shopping", ("kmart", "target", "big w", "bunnings", "jb hi-fi", "officeworks", "amazon",
                  "ebay", "temu", "myer", "david jones", "harvey norman", "rebel", "spotlight")),
]
_SPEND_LOOKUP = dict(SPEND_CATEGORIES)


def category_choices() -> List[tuple]:
    """Every category a merchant can be put in, as (code, label)."""
    bills = [c for c in SUBSCRIPTION_CATEGORIES if c[0] != "other"]
    return [("groceries", "Groceries")] + SPEND_CATEGORIES + bills + [("other", "Other")]


def guess_spend_category(description: str) -> Optional[str]:
    """An everyday spending category from the description, or None."""
    text = (description or "").lower()
    for code, keywords in _SPEND_KEYWORDS:
        if any(re.search(rf"(?<![\w-]){re.escape(k)}(?![\w-])", text) for k in keywords):
            return code
    return None


@lru_cache(maxsize=4096)
def merchant_label(description: str) -> str:
    """A readable merchant name from a bank description: "U Go Carrum Downs", not "u"."""
    payee = transfer_payee(description)
    if payee:
        return f"Transfer to {payee}"
    words, seen = [], set()
    for word in _display_name([description]).split():
        if word.lower() not in seen:
            seen.add(word.lower())
            words.append(word)
    return " ".join(words[:4]) or "Unknown merchant"


def merchant_key_for(row: Dict) -> str:
    """The key spending is grouped by, and that a category choice is saved against."""
    description = row.get("description") or ""
    return normalise_merchant(description) or row.get("merchant_key") or description


def category_for(row: Dict, overrides: Optional[Dict[str, str]] = None) -> str:
    """Category code for one bank debit.

    A category the user chose for the merchant wins. Otherwise grocery rows are
    groceries, then known merchants, the Bills guesses and the everyday guesses.
    """
    if overrides:
        chosen = overrides.get(merchant_key_for(row))
        if chosen:
            return chosen
    if (row.get("status") or "") == "grocery":
        return "groceries"
    description = row.get("description") or ""
    known = known_merchant([description])
    code = (known or {}).get("category") or guess_category(description)
    if code == "other":
        return guess_spend_category(description) or "other"
    return code


def category_label(code: str) -> str:
    if code == "groceries":
        return "Groceries"
    return _SPEND_LOOKUP.get(code) or subscription_category_label(code)


def spend_items(transactions: Iterable[Dict], days: Optional[int] = None,
                overrides: Optional[Dict[str, str]] = None) -> Optional[Dict]:
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
        items.append({"day": day, "amount": amount, "description": description,
                      "category": category_for(row, overrides), "key": merchant_key_for(row),
                      "name": merchant_label(description)})
    return {"end": end, "items": items, "moved": round(moved, 2), "unsure": round(unsure, 2)}


def category_detail(transactions: Iterable[Dict], code: str, days: Optional[int] = None,
                    overrides: Optional[Dict[str, str]] = None) -> Optional[Dict]:
    """One category's transactions, grouped by merchant, or None when it has none.

    Merchants are the sub-categories: each has a total, a count and its rows,
    newest first.
    """
    window = spend_items(transactions, days, overrides)
    items = [i for i in (window or {}).get("items", []) if i["category"] == code]
    overrides = overrides or {}
    if not items:
        return None
    groups: Dict[str, Dict] = {}
    for item in items:
        group = groups.setdefault(item["key"], {"key": item["key"], "name": item["name"], "amount": 0.0,
                                                "count": 0, "rows": [], "chosen": item["key"] in overrides})
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


def bank_spending(transactions: Iterable[Dict], days: Optional[int] = None,
                  overrides: Optional[Dict[str, str]] = None) -> Optional[Dict]:
    """Money out over the last `days` days of bank history (all of it when None).

    Returns None when there are no bank debits in the window. The window ends
    on the latest bank row, so an old import still shows a full range.
    """
    window = spend_items(transactions, days, overrides)
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


RECENT_DAYS = 30
BASELINE_DAYS = 90
MIN_CHANGE = 50.0
MIN_SHARE = 0.25


def spending_changes(transactions: Iterable[Dict], recent_days: int = RECENT_DAYS,
                     baseline_days: int = BASELINE_DAYS, min_change: float = MIN_CHANGE,
                     min_share: float = MIN_SHARE,
                     overrides: Optional[Dict[str, str]] = None) -> Optional[Dict]:
    """Categories that moved in the last `recent_days` against your usual, or None.

    The usual is the `baseline_days` before that, scaled to the same length.
    A category counts as changed when it moved by at least `min_change`
    dollars and by `min_share` of its usual amount. Each change names the
    merchant that moved most in the same direction. None when there is less
    than one full recent period of earlier history, or nothing changed.
    """
    window = spend_items(transactions, overrides=overrides)
    if not window or not window["items"]:
        return None
    end = window["end"]
    recent_start = end - timedelta(days=recent_days - 1)
    baseline_start = recent_start - timedelta(days=baseline_days)
    first = min(item["day"] for item in window["items"])
    span = (recent_start - max(first, baseline_start)).days
    if span < recent_days:
        return None
    scale = recent_days / span
    recent: Dict[str, float] = defaultdict(float)
    usual: Dict[str, float] = defaultdict(float)
    by_merchant: Dict[str, Dict[str, List[float]]] = defaultdict(dict)
    names: Dict[str, str] = {}
    for item in window["items"]:
        day, code, key = item["day"], item["category"], item["key"]
        if day >= recent_start:
            slot = 0
        elif day >= baseline_start:
            slot = 1
        else:
            continue
        (recent if slot == 0 else usual)[code] += item["amount"]
        by_merchant[code].setdefault(key, [0.0, 0.0])[slot] += item["amount"]
        names[key] = item["name"]
    up, down = [], []
    for code in set(recent) | set(usual):
        now, before = recent.get(code, 0.0), usual.get(code, 0.0) * scale
        delta = now - before
        if abs(delta) < min_change or (before and abs(delta) / before < min_share):
            continue
        moves = [(key, amounts[0] - amounts[1] * scale) for key, amounts in by_merchant[code].items()]
        key, move = max(moves, key=lambda m: m[1] if delta > 0 else -m[1])
        entry = {
            "code": code, "label": category_label(code),
            "recent": round(now, 2), "usual": round(before, 2), "delta": round(delta, 2),
            "percent": round(delta / before * 100) if before else None,
            "driver": names[key] if move * delta > 0 else None,
            "driver_delta": round(move, 2),
        }
        (up if delta > 0 else down).append(entry)
    if not up and not down:
        return None
    return {
        "start": recent_start.isoformat(),
        "end": end.isoformat(),
        "days": recent_days,
        "baseline_start": max(first, baseline_start).isoformat(),
        "up": sorted(up, key=lambda e: -e["delta"])[:5],
        "down": sorted(down, key=lambda e: e["delta"])[:5],
    }


FLEXIBLE = ("eating_out", "shopping", "entertainment", "streaming", "memberships", "software", "other")
LEVER_SHARE = 0.2
MIN_LEVER = 20.0


def savings_levers(transactions: Iterable[Dict], cycle_days: float, overrides: Optional[Dict[str, str]] = None,
                   days: int = 90, share: float = LEVER_SHARE, gap: float = 0.0, limit: int = 3) -> Optional[Dict]:
    """The flexible spending categories that could free up money each pay, or None.

    Bills, groceries, transfers to people and transport are left out: they are
    harder to change from one pay to the next. Each lever is a category's
    usual spend per pay, its biggest merchants and what cutting it by `share`
    would free, rounded down to $5. `gap` is how far costs are over pay, so
    the result can say whether the cuts would cover it.
    """
    window = spend_items(transactions, days, overrides)
    if not window or not window["items"]:
        return None
    first = min(i["day"] for i in window["items"])
    span = max((window["end"] - first).days + 1, 1)
    scale = cycle_days / span
    totals: Dict[str, float] = defaultdict(float)
    merchants: Dict[str, Dict[str, Dict]] = defaultdict(dict)
    for item in window["items"]:
        if item["category"] not in FLEXIBLE:
            continue
        totals[item["category"]] += item["amount"]
        entry = merchants[item["category"]].setdefault(item["key"], {"name": item["name"], "amount": 0.0})
        entry["amount"] += item["amount"]
    levers = []
    for code, total in sorted(totals.items(), key=lambda kv: -kv[1]):
        per_pay = round(total * scale, 2)
        if per_pay < MIN_LEVER:
            continue
        top = sorted(merchants[code].values(), key=lambda m: -m["amount"])[:2]
        levers.append({
            "code": code, "label": category_label(code), "per_pay": per_pay,
            "cut": float(int(per_pay * share / 5) * 5),
            "merchants": [{"name": m["name"], "per_pay": round(m["amount"] * scale, 2)} for m in top],
        })
        if len(levers) == limit:
            break
    if not levers:
        return None
    freed = round(sum(l["cut"] for l in levers), 2)
    return {"levers": levers, "freed": freed, "share": round(share * 100), "gap": round(max(gap, 0.0), 2),
            "covers_gap": freed >= gap if gap > 0 else None}
