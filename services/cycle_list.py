"""Grocery lists that cover a whole pay cycle, not just the meal plan.

Pure functions, no Flask, no SQL. One generator combines the meal plan's
ingredient lines with the regular buys predicted from purchase history, and
tags every line with a ``source`` so the full, meals-only and non-meal lists
are just filters over the same lines and always agree with each other.
"""

from datetime import date
from typing import Dict, Iterable, List, Optional, Set

from services.product_matching import NON_FOOD_ROLES
from services.purchase_patterns import feedback_suppressed

SOURCES = [
    ("meal", "Meals"),
    ("staple", "Staples"),
    ("treat", "Snacks & treats"),
    ("household", "Household"),
    ("manual", "Added by hand"),
]
SOURCE_LOOKUP = dict(SOURCES)

LIST_MODES = [
    ("full", "Everything", "Meals plus staples, treats and household items."),
    ("meals", "Meals only", "Just the ingredients for the planned meals."),
    ("nonmeal", "Everything except meals", "Staples, treats and household items you usually buy."),
    ("staples", "Staples restock", "Regular food buys only, e.g. a restock after payday."),
    ("household", "Household only", "Cleaning, toiletries, pet and other non-food items."),
    ("topup", "Top-up shop", "Only regular buys running out before the next shop. No meals."),
    ("tight", "Tight budget", "Meals plus regular food staples. No treats or occasional buys."),
]
MODE_LOOKUP = {code: label for code, label, _ in LIST_MODES}
MODE_SOURCES = {
    "full": {"meal", "staple", "treat", "household"},
    "meals": {"meal"},
    "nonmeal": {"staple", "treat", "household"},
    "staples": {"staple"},
    "household": {"household"},
    "topup": {"staple", "treat", "household"},
    "tight": {"meal", "staple"},
}
_ROLE_CATEGORY = {"treat": "snacks", "household": "household", "personal_care": "household",
                  "pet": "household", "baby": "household", "fee": "other"}


def source_for_role(role: Optional[str]) -> str:
    if role in NON_FOOD_ROLES:
        return "household"
    if role == "treat":
        return "treat"
    return "staple"


def _cadence(pattern: Dict) -> str:
    days = pattern.get("interval_days")
    if not days:
        return "bought before"
    if days <= 8:
        return "about weekly"
    if days <= 16:
        return "about fortnightly"
    if days <= 35:
        return "about monthly"
    return f"every {round(days)} days"


def _staple_line(need: Dict) -> Dict:
    times = need.get("times", 1)
    product = (need.get("products") or [need["label"]])[0]
    reason = f"Usually bought {_cadence(need)}, last on {need['last_bought']}"
    if times > 1:
        reason += f"; due {times} times this cycle"
    if product != need["label"]:
        reason += f" ({product})"
    return {
        "ingredient_id": need.get("ingredient_id"),
        "ingredient_name": need["label"],
        "quantity": need.get("quantity") or 1,
        "unit": "pack",
        "category": need.get("category") or _ROLE_CATEGORY.get(need.get("role"), "other"),
        "estimated_cost": need.get("estimated_cost"),
        "source": source_for_role(need.get("role")),
        "reason": reason,
        "meal_date": need.get("first_due") or "",
        "recipe_refs": [],
        "rhythm": need.get("rhythm"),
        "key": need["key"],
    }


def generate_cycle_list(meal_grouped: Dict[str, List[Dict]], needs: Iterable[Dict],
                        mode: str = "full", in_stock: Optional[Set[int]] = None,
                        feedback: Optional[List[Dict]] = None, patterns: Optional[Iterable[Dict]] = None,
                        due_by: Optional[date] = None) -> List[Dict]:
    """Lines for a list mode (see LIST_MODES), each carrying a source and a reason.

    ``meal_grouped`` is ``generate_shopping_list`` output; ``needs`` comes from
    ``purchase_patterns.predict_needs``. An ingredient needed both for meals and
    as a regular buy appears once, as a meal line that notes the habit. Staples
    in ``in_stock`` (ingredient ids with pantry stock) are left out, as are items
    the user has removed often or marked 'never'; items marked 'always' are added
    even when not due. ``due_by`` limits non-meal lines to items due by that date.
    """
    if mode not in MODE_SOURCES:
        mode = "full"
    wanted = MODE_SOURCES[mode]
    in_stock = in_stock or set()
    feedback = feedback or []

    lines: List[Dict] = []
    meal_ids = set()
    for category, items in (meal_grouped or {}).items():
        for item in items:
            meal_ids.add(item.get("ingredient_id"))
            refs = item.get("recipe_refs") or []
            lines.append({**item, "category": item.get("category") or category, "source": "meal",
                          "reason": "For " + ", ".join(refs) if refs else "For the meal plan"})

    needs = list(needs)
    seen = {n["key"] for n in needs}
    for pattern in patterns or []:
        if pattern["key"] not in seen and feedback_suppressed(feedback, pattern["key"])[1]:
            needs.append({**pattern, "times": 1, "quantity": pattern.get("typical_quantity") or 1,
                          "estimated_cost": pattern.get("typical_spend"),
                          "first_due": (due_by or date.today()).isoformat()})

    by_meal_id = {l["ingredient_id"]: l for l in lines if l.get("ingredient_id")}
    for need in needs:
        suppressed, forced = feedback_suppressed(feedback, need["key"])
        if suppressed:
            continue
        if need.get("ingredient_id") in by_meal_id:
            meal_line = by_meal_id[need["ingredient_id"]]
            meal_line["reason"] += f"; also a regular buy ({_cadence(need)})"
            continue
        if need.get("ingredient_id") in in_stock and not forced:
            continue
        if mode == "tight" and need.get("rhythm") != "regular" and not forced:
            continue
        if due_by and need.get("first_due") and need["first_due"] > due_by.isoformat() and not forced:
            continue
        lines.append(_staple_line(need))

    result = [l for l in lines if l["source"] in wanted]
    return sorted(result, key=lambda l: (l["category"] or "other", l["ingredient_name"].lower()))


def totals_by_source(lines: Iterable[Dict], budget: Optional[float] = None) -> Dict:
    """{'sources': {source: total}, 'total', 'budget', 'remaining'} over line costs."""
    sources: Dict[str, float] = {}
    for line in lines:
        cost = line.get("line_cost") if line.get("line_cost") is not None else line.get("estimated_cost")
        source = line.get("source") or ("manual" if line.get("is_manual") else "meal")
        sources[source] = round(sources.get(source, 0) + (cost or 0), 2)
    total = round(sum(sources.values()), 2)
    return {
        "sources": sources,
        "total": total,
        "budget": budget,
        "remaining": round(budget - total, 2) if budget else None,
    }


def trim_to_budget(lines: List[Dict], budget: float) -> Dict:
    """Which lines to drop first to get under budget: treats, then occasional staples, then household.

    Meals and regular staples are never suggested for dropping. Returns
    {over_by, drop: [names], still_over}.
    """
    total = sum((l.get("estimated_cost") or 0) for l in lines)
    over = round(total - budget, 2)
    if over <= 0:
        return {"over_by": 0, "drop": [], "still_over": False}
    order = ([l for l in lines if l["source"] == "treat"]
             + [l for l in lines if l["source"] == "staple" and l.get("rhythm") != "regular"]
             + [l for l in lines if l["source"] == "household" and l.get("rhythm") != "regular"])
    drop, saved = [], 0.0
    for line in sorted(order, key=lambda l: -(l.get("estimated_cost") or 0)):
        if saved >= over:
            break
        if line.get("estimated_cost"):
            drop.append(line["ingredient_name"])
            saved += line["estimated_cost"]
    return {"over_by": over, "drop": drop, "still_over": saved < over}
