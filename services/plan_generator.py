"""Plan generation service — pure functions for rule-based meal plan creation.

Extracted from routes/meal_plan.py so generation logic is testable without Flask.
"""

from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple

from models import MEAL_TYPES, parse_date, date_range, get_day_key, is_weekday


# ---------------------------------------------------------------------------
# Rule matching
# ---------------------------------------------------------------------------

def rule_matches_day(rule_day_of_week: str, date: datetime) -> bool:
    """Check if a rule's day_of_week matches the given date."""
    day_key = get_day_key(date)
    if rule_day_of_week == "all":
        return True
    if rule_day_of_week == "weekday":
        return is_weekday(date)
    if rule_day_of_week == "weekend":
        return not is_weekday(date)
    return rule_day_of_week == day_key


def select_recipe_for_rule(
    tag_filter: Optional[str],
    recipes: List[Dict],
) -> Optional[Dict]:
    """Pick a recipe for a rule. Returns None if no matching recipe."""
    if not recipes:
        return None
    if tag_filter:
        filtered = [
            r for r in recipes
            if tag_filter in (r.get("tags") or "").split(",")
        ]
        candidates = filtered
    else:
        candidates = recipes

    if not candidates:
        return None
    return candidates[0]


# ---------------------------------------------------------------------------
# Plan generation (pure function — no DB)
# ---------------------------------------------------------------------------

def generate_plan_entries(
    start_date_str: str,
    end_date_str: str,
    rules: List[Dict],
    recipes: List[Dict],
    existing_entries: List[Dict],
) -> Tuple[List[Dict], Optional[str]]:
    """Generate meal plan entries for the given date range.

    Returns (entries, error_message).
    """
    start = parse_date(start_date_str)
    end = parse_date(end_date_str)
    if not start or not end or end < start:
        return [], "Invalid date range."

    # Build lookup for existing manual entries
    existing_set = {
        (e["date"], e["meal_type"]) for e in existing_entries
    }

    entries = []

    for date in date_range(start_date_str, end_date_str):
        for meal_type in MEAL_TYPES:
            # Find matching ACTIVE rules, sorted by sort_order
            matching_rules = sorted(
                [r for r in rules
                 if r["meal_type"] == meal_type
                 and r.get("is_active", 1)
                 and rule_matches_day(r["day_of_week"], date)],
                key=lambda r: r.get("sort_order", 0),
            )

            matched = False
            for rule in matching_rules:
                entry_key = (date.strftime("%Y-%m-%d"), meal_type)
                if entry_key in existing_set:
                    matched = True
                    break  # Manual override blocks auto-generation

                recipe = select_recipe_for_rule(rule.get("tag_filter"), recipes)
                if not recipe:
                    continue

                recipe_servings = recipe.get("servings") or 1
                servings = (
                    rule["servings_override"]
                    if rule.get("servings_override")
                    else recipe_servings
                )

                entries.append({
                    "date": date.strftime("%Y-%m-%d"),
                    "meal_type": meal_type,
                    "recipe_id": recipe["id"],
                    "servings": servings,
                    "is_auto_generated": 1,
                    "source_rule_id": rule["id"],
                })
                matched = True

                # Handle two-night recipes
                if recipe.get("is_two_night"):
                    next_date = date + timedelta(days=1)
                    if next_date <= end:
                        next_key = (next_date.strftime("%Y-%m-%d"), meal_type)
                        if next_key not in existing_set:
                            if next_key not in {
                                (e["date"], e["meal_type"]) for e in entries
                            }:
                                entries.append({
                                    "date": next_date.strftime("%Y-%m-%d"),
                                    "meal_type": meal_type,
                                    "recipe_id": recipe["id"],
                                    "servings": servings,
                                    "is_auto_generated": 1,
                                    "source_rule_id": rule["id"],
                                })
                                # Also block this cascade target from being filled
                                # by another rule on its own day
                                existing_set.add(next_key)
                break  # First matching rule wins

    return entries, None


# ---------------------------------------------------------------------------
# Helpers for building test data
# ---------------------------------------------------------------------------

def make_rule(
    id: int = 1,
    day_of_week: str = "mon",
    meal_type: str = "dinner",
    tag_filter: Optional[str] = None,
    servings_override: Optional[int] = None,
    is_active: int = 1,
    sort_order: int = 0,
) -> Dict:
    """Create a rule dict for testing."""
    return {
        "id": id,
        "day_of_week": day_of_week,
        "meal_type": meal_type,
        "tag_filter": tag_filter,
        "servings_override": servings_override,
        "is_active": is_active,
        "sort_order": sort_order,
    }


def make_recipe(
    id: int = 1,
    name: str = "Test Recipe",
    servings: int = 4,
    is_two_night: bool = False,
    tags: Optional[str] = None,
) -> Dict:
    """Create a recipe dict for testing."""
    return {
        "id": id,
        "name": name,
        "servings": servings,
        "is_two_night": is_two_night,
        "tags": tags or "",
    }
