"""Purchase rhythm analysis over imported grocery history.

Pure functions over plain purchase dicts, no Flask, no SQL. Products linked to
the same ingredient share one rhythm (two brands of milk are one milk habit);
unlinked products are tracked by name. The output answers "what do we usually
buy, how often, and what will we need before a given date?".
"""

from datetime import date, timedelta
from statistics import mean, median
from typing import Dict, Iterable, List, Optional, Tuple

from models import parse_ymd
from services.product_matching import NON_FOOD_ROLES, ROLE_LOOKUP, product_role

HISTORY_DAYS = 180
REGULAR_MIN_BUYS = 3
REGULAR_MAX_SPREAD = 0.4
STOPPED_AFTER_CYCLES = 3


def _as_date(value) -> Optional[date]:
    if isinstance(value, date):
        return value
    try:
        return parse_ymd(str(value)[:10])
    except (TypeError, ValueError):
        return None


def group_key(row: Dict) -> str:
    """'ing:<id>' for products linked to an ingredient, else 'prod:<name>'."""
    if row.get("ingredient_id"):
        return f"ing:{row['ingredient_id']}"
    return f"prod:{row.get('product_name') or ''}"


def _spread(intervals: List[float]) -> float:
    """Relative spread of the gaps between buys (mean absolute deviation / median)."""
    if len(intervals) < 2:
        return 0.0
    mid = median(intervals)
    if not mid:
        return 0.0
    return round(mean(abs(i - mid) for i in intervals) / mid, 2)


def classify_rhythm(buys: int, interval: Optional[float], spread: float,
                    days_since_last: int) -> str:
    """'regular', 'occasional', 'one_off' or 'stopped'."""
    if buys <= 1 or not interval:
        return "one_off"
    if days_since_last > max(interval * STOPPED_AFTER_CYCLES, 21):
        return "stopped"
    if buys >= REGULAR_MIN_BUYS and spread <= REGULAR_MAX_SPREAD:
        return "regular"
    return "occasional"


def build_patterns(purchases: Iterable[Dict], today: date, ingredients: Optional[Dict[int, Dict]] = None,
                   decisions: Optional[Dict[str, Dict]] = None,
                   history_days: int = HISTORY_DAYS) -> List[Dict]:
    """One pattern per ingredient (or unlinked product) bought in the history window.

    Each pattern: key, label, ingredient_id, products, role, buys, interval_days,
    spread, rhythm, last_bought, next_due, days_since_last, typical_quantity,
    typical_spend, last_price, category. Ignored products are left out.
    Sorted with regular buys first, then by how often they are bought.
    """
    ingredients = ingredients or {}
    decisions = decisions or {}
    since = today - timedelta(days=history_days)
    groups: Dict[str, Dict] = {}
    for row in purchases:
        day = _as_date(row.get("order_date"))
        name = row.get("product_name") or ""
        if not day or day < since or day > today or not name:
            continue
        if (decisions.get(name) or {}).get("ignored"):
            continue
        key = group_key(row)
        group = groups.setdefault(key, {"days": {}, "products": {}, "roles": {}})
        visit = group["days"].setdefault(day, {"quantity": 0.0, "spend": 0.0})
        visit["quantity"] += float(row.get("quantity") or 1)
        visit["spend"] += float(row.get("line_total") or 0)
        group["products"][name] = max(group["products"].get(name, date.min), day)
        role = product_role(name, decisions)
        group["roles"][role] = group["roles"].get(role, 0) + 1
        if row.get("unit_price") and day >= group.get("price_day", date.min):
            group["price_day"], group["last_price"] = day, float(row["unit_price"])

    patterns = []
    for key, group in groups.items():
        days = sorted(group["days"])
        intervals = [(b - a).days for a, b in zip(days, days[1:])]
        interval = float(median(intervals)) if intervals else None
        spread = _spread(intervals)
        last = days[-1]
        since_last = (today - last).days
        visits = list(group["days"].values())
        products = sorted(group["products"], key=lambda n: group["products"][n], reverse=True)
        role = max(group["roles"], key=group["roles"].get)
        ingredient = ingredients.get(int(key[4:])) if key.startswith("ing:") else None
        label = ((ingredient or {}).get("display_name") or (ingredient or {}).get("name")
                 or products[0])
        patterns.append({
            "key": key,
            "label": label,
            "ingredient_id": int(key[4:]) if key.startswith("ing:") else None,
            "products": products,
            "role": role,
            "role_label": ROLE_LOOKUP.get(role, role),
            "buys": len(days),
            "interval_days": round(interval, 1) if interval else None,
            "spread": spread,
            "rhythm": classify_rhythm(len(days), interval, spread, since_last),
            "last_bought": last.isoformat(),
            "next_due": (last + timedelta(days=round(interval))).isoformat() if interval else None,
            "days_since_last": since_last,
            "typical_quantity": round(median(v["quantity"] for v in visits), 2),
            "typical_spend": round(median(v["spend"] for v in visits), 2),
            "last_price": group.get("last_price"),
            "category": (ingredient or {}).get("category"),
        })
    order = {"regular": 0, "occasional": 1, "stopped": 2, "one_off": 3}
    return sorted(patterns, key=lambda p: (order[p["rhythm"]], -p["buys"], p["label"].lower()))


def due_dates(pattern: Dict, start: date, end: date) -> List[date]:
    """Dates in [start, end] when the pattern says the item will be bought again.

    An overdue item counts once on ``start``. Stopped and one-off items are never due.
    """
    if pattern["rhythm"] in ("stopped", "one_off") or not pattern.get("interval_days"):
        return []
    step = max(1, round(pattern["interval_days"]))
    due = _as_date(pattern["next_due"])
    found = []
    if due < start:
        found.append(start)
        due = start + timedelta(days=step)
    while due <= end:
        found.append(due)
        due += timedelta(days=step)
    return found


def predict_needs(patterns: List[Dict], start: date, end: date,
                  include_occasional: bool = True) -> List[Dict]:
    """Items expected to be bought between ``start`` and ``end`` (inclusive).

    Each result is the pattern plus times (buys expected), quantity (packs),
    estimated_cost and first_due. Occasional items are included only when they
    fall due, and only if ``include_occasional``.
    """
    needs = []
    for pattern in patterns:
        if pattern["rhythm"] == "occasional" and not include_occasional:
            continue
        dates = due_dates(pattern, start, end)
        if not dates:
            continue
        times = len(dates)
        needs.append({
            **pattern,
            "times": times,
            "quantity": round(times * pattern["typical_quantity"], 2),
            "estimated_cost": round(times * pattern["typical_spend"], 2),
            "first_due": dates[0].isoformat(),
        })
    return needs


def is_household(pattern: Dict) -> bool:
    return pattern.get("role") in NON_FOOD_ROLES


def prediction_accuracy(purchases: List[Dict], as_of: date, until: date,
                        ingredients: Optional[Dict[int, Dict]] = None,
                        decisions: Optional[Dict[str, Dict]] = None) -> Dict:
    """How well history before ``as_of`` predicted what was bought from ``as_of`` to ``until``.

    Returns {predicted, bought, hits, precision, recall, missed, extra}, where
    missed are regular buys that happened but weren't predicted and extra were
    predicted but not bought. Only regular buys are judged.
    """
    before = [p for p in purchases if (_as_date(p.get("order_date")) or date.max) < as_of]
    after = [p for p in purchases if as_of <= (_as_date(p.get("order_date")) or date.min) <= until]
    patterns = build_patterns(before, as_of - timedelta(days=1), ingredients, decisions)
    regular = {p["key"]: p for p in patterns if p["rhythm"] == "regular"}
    predicted = {n["key"] for n in predict_needs(list(regular.values()), as_of, until)}
    bought = {group_key(p) for p in after} & set(regular)
    hits = predicted & bought
    labels = {k: regular[k]["label"] for k in regular}
    return {
        "predicted": len(predicted),
        "bought": len(bought),
        "hits": len(hits),
        "precision": round(len(hits) / len(predicted), 2) if predicted else None,
        "recall": round(len(hits) / len(bought), 2) if bought else None,
        "missed": sorted(labels[k] for k in bought - predicted),
        "extra": sorted(labels[k] for k in predicted - bought),
    }


def feedback_suppressed(feedback: List[Dict], key: str, removals: int = 2) -> Tuple[bool, bool]:
    """(suppressed, forced) for an item from its feedback rows, oldest first.

    'never' suppresses and 'always' forces until the other is chosen; ``removals``
    or more 'removed' rows since the last 'always' or 'kept' suppress it too.
    """
    suppressed, forced, removed = False, False, 0
    for row in feedback:
        if row.get("item_key") != key:
            continue
        action = row.get("action")
        if action == "never":
            suppressed, forced = True, False
        elif action == "always":
            suppressed, forced, removed = False, True, 0
        elif action == "kept":
            removed = 0
        elif action == "removed":
            removed += 1
    return suppressed or (removed >= removals and not forced), forced


# ---------------------------------------------------------------------------
# Helpers for building test data
# ---------------------------------------------------------------------------

def make_purchase(order_date: str = "2026-10-01", product_name: str = "Full Cream Milk 2L",
                  quantity: float = 1, line_total: float = 3.1, unit_price: Optional[float] = 3.1,
                  ingredient_id: Optional[int] = None, basket_id: str = "b1") -> Dict:
    """Create a purchase row dict for testing."""
    return {"order_date": order_date, "product_name": product_name, "quantity": quantity,
            "line_total": line_total, "unit_price": unit_price, "ingredient_id": ingredient_id,
            "basket_id": basket_id}


def make_history(product_name: str, start: str, every_days: int, times: int, **kwargs) -> List[Dict]:
    """A product bought ``times`` times every ``every_days`` days from ``start``."""
    first = parse_ymd(start)
    return [make_purchase((first + timedelta(days=every_days * i)).isoformat(), product_name,
                          basket_id=f"b{i}", **kwargs) for i in range(times)]
