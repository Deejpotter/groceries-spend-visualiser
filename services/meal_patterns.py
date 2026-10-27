"""What meals do we actually eat? Pure analysis of meal history and grocery baskets.

No Flask, no SQL. Past meal plan entries are the record of meals eaten; grocery
baskets that contain most of a recipe's ingredients add "probably cooked"
evidence for meals that were never planned. The stats feed meal suggestion
scoring in ``plan_generator``.
"""

from collections import Counter
from datetime import date, timedelta
from statistics import median
from typing import Dict, Iterable, List, Optional

from models import convert_unit, parse_ymd, units_compatible

HISTORY_DAYS = 180
BASKET_MATCH_SHARE = 0.6
DAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _as_date(value) -> Optional[date]:
    if isinstance(value, date):
        return value
    try:
        return parse_ymd(str(value)[:10])
    except (TypeError, ValueError):
        return None


def recipe_cost_per_serving(recipe: Dict, links: List[Dict], ingredients: Dict[int, Dict]) -> Optional[float]:
    """Estimated cost of one serving from ingredient prices; None if nothing is priced."""
    total, priced = 0.0, False
    for link in links:
        ingredient = ingredients.get(link["ingredient_id"]) or {}
        price, base = ingredient.get("price"), ingredient.get("unit")
        unit = link.get("unit_override") or base
        if not price or not base or not unit or not units_compatible(unit, base):
            continue
        total += convert_unit(link["quantity"], unit, base) * price
        priced = True
    if not priced:
        return None
    return round(total / max(1, recipe.get("servings") or 1), 2)


def baskets_cooked(recipe_links: Dict[int, List[Dict]], purchases: Iterable[Dict],
                   share: float = BASKET_MATCH_SHARE) -> Dict[int, int]:
    """Baskets per recipe that held at least ``share`` (and at least two) of its ingredients."""
    baskets: Dict[tuple, set] = {}
    for row in purchases:
        if row.get("ingredient_id"):
            baskets.setdefault((row.get("store"), row.get("basket_id")), set()).add(row["ingredient_id"])
    counts: Dict[int, int] = {}
    for recipe_id, links in recipe_links.items():
        needed = {l["ingredient_id"] for l in links}
        if len(needed) < 2:
            continue
        hit = sum(1 for held in baskets.values()
                  if len(needed & held) >= max(2, share * len(needed)))
        if hit:
            counts[recipe_id] = hit
    return counts


def meal_stats(history: Iterable[Dict], today: date, recipes: Dict[int, Dict],
               recipe_links: Optional[Dict[int, List[Dict]]] = None,
               ingredients: Optional[Dict[int, Dict]] = None,
               purchases: Optional[Iterable[Dict]] = None,
               history_days: int = HISTORY_DAYS) -> Dict[int, Dict]:
    """Per-recipe history: {recipe_id: {eaten, last_eaten, days_since, meal_types,
    weekdays, typical_meal_type, cooked_baskets, cost_per_serving}}.

    History rows are past meal plan entries ({date, meal_type, recipe_id,
    is_continuation}); continuations are leftovers, not separate meals, so
    they are not counted. Every recipe gets an entry, eaten or not.
    """
    recipe_links = recipe_links or {}
    ingredients = ingredients or {}
    since = today - timedelta(days=history_days)
    stats = {rid: {"eaten": 0, "last_eaten": None, "days_since": None, "meal_types": Counter(),
                   "weekdays": Counter()} for rid in recipes}
    for row in history:
        day = _as_date(row.get("date"))
        rid = row.get("recipe_id")
        if not day or rid not in stats or day >= today or day < since or row.get("is_continuation"):
            continue
        s = stats[rid]
        s["eaten"] += 1
        s["meal_types"][row.get("meal_type")] += 1
        s["weekdays"][DAY_KEYS[day.weekday()]] += 1
        if not s["last_eaten"] or day > s["last_eaten"]:
            s["last_eaten"] = day
    recent_purchases = [p for p in (purchases or []) if (_as_date(p.get("order_date")) or date.min) >= since]
    cooked = baskets_cooked(recipe_links, recent_purchases) if recent_purchases else {}
    for rid, s in stats.items():
        if s["last_eaten"]:
            s["days_since"] = (today - s["last_eaten"]).days
            s["last_eaten"] = s["last_eaten"].isoformat()
        s["typical_meal_type"] = s["meal_types"].most_common(1)[0][0] if s["meal_types"] else None
        s["meal_types"] = dict(s["meal_types"])
        s["weekdays"] = dict(s["weekdays"])
        s["cooked_baskets"] = cooked.get(rid, 0)
        s["cost_per_serving"] = recipe_cost_per_serving(recipes[rid], recipe_links.get(rid, []), ingredients)
    return stats


def history_slots(history: Iterable[Dict], today: date, weeks: int = 8, min_count: int = 2) -> Dict[tuple, int]:
    """{(weekday, meal_type): count} for slots filled at least ``min_count`` times recently.

    Used to plan meals the household normally has even when no rule covers them.
    """
    since = today - timedelta(weeks=weeks)
    counts: Counter = Counter()
    for row in history:
        day = _as_date(row.get("date"))
        if day and since <= day < today:
            counts[(DAY_KEYS[day.weekday()], row.get("meal_type"))] += 1
    return {slot: n for slot, n in counts.items() if n >= min_count}


def median_cost(stats: Dict[int, Dict]) -> Optional[float]:
    costs = [s["cost_per_serving"] for s in stats.values() if s.get("cost_per_serving")]
    return round(median(costs), 2) if costs else None


def make_history_entry(date_str: str = "2026-10-01", recipe_id: int = 1, meal_type: str = "dinner",
                       is_continuation: int = 0) -> Dict:
    """Create a past meal plan entry dict for testing."""
    return {"date": date_str, "recipe_id": recipe_id, "meal_type": meal_type,
            "is_continuation": is_continuation}
