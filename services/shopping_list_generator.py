"""Shopping list generation service — pure functions for aggregating ingredients.

Extracted from routes/shopping_list.py so aggregation logic is testable without Flask.
"""

from typing import Dict, List, Optional

from models import convert_unit


# ---------------------------------------------------------------------------
# Unit conversion for display
# ---------------------------------------------------------------------------

def convert_to_display_unit(
    quantity: float,
    unit: str,
    preference: str,  # "metric" or "imperial"
) -> Dict[str, float]:
    """Convert a quantity+unit to the preferred display unit.

    Returns dict with 'quantity' and 'unit' keys.
    Count units (each, pack, etc.) are not converted.
    """
    COUNT_UNITS = {
        "each", "pack", "bunch", "roll", "can", "jar", "box",
        "bag", "tube", "carton", "slice", "cup", "tbsp", "tsp",
    }

    if unit in COUNT_UNITS:
        return {"quantity": quantity, "unit": unit}

    if preference == "imperial":
        # Convert metric -> imperial
        conversions = {
            "kg": ("lb", 2.20462),
            "g": ("oz", 0.035274),
            "L": ("gal", 0.264172),
            "mL": ("fl_oz", 0.033814),
        }
        if unit in conversions:
            new_unit, factor = conversions[unit]
            return {
                "quantity": round(quantity * factor, 2),
                "unit": new_unit,
            }

    # Metric preference or no conversion needed
    return {"quantity": quantity, "unit": unit}


# ---------------------------------------------------------------------------
# Shopping list aggregation (pure function — no DB)
# ---------------------------------------------------------------------------

def generate_shopping_list(
    plan_entries: List[Dict],           # [{date, meal_type, recipe_id, servings}]
    recipes: Dict[int, Dict],           # {id: {name, servings, ...}}
    recipe_ingredients: Dict[int, List[Dict]],  # {recipe_id: [{quantity, unit_override, ingredient_id}]}
    ingredients: Dict[int, Dict],       # {id: {name, unit, category, price}}
    unit_preference: str = "metric",
    subtract_pantry: bool = False,
    pantry_items: Optional[Dict[str, float]] = None,  # {ingredient_name: quantity}
) -> Dict[str, List[Dict]]:
    """Generate a grouped shopping list from meal plan entries.

    Returns {category: [{ingredient_name, quantity, unit, category, recipe_refs, meal_date}]}
    """
    if not plan_entries:
        return {}

    # Aggregate ingredients across all entries
    aggregated: Dict[tuple, dict] = {}

    for entry in plan_entries:
        recipe_id = entry["recipe_id"]
        recipe = recipes.get(recipe_id)
        if not recipe:
            continue

        recipe_servings = recipe.get("servings") or 1
        scale_factor = entry["servings"] / recipe_servings

        ri_list = recipe_ingredients.get(recipe_id, [])
        for ri in ri_list:
            ingredient_id = ri["ingredient_id"]
            ingredient = ingredients.get(ingredient_id)
            if not ingredient:
                continue

            qty = ri["quantity"] * scale_factor
            name = ingredient["name"]
            unit = ri.get("unit_override") or ingredient["unit"]
            category = ingredient.get("category") or "other"

            key = (name, unit)

            if key in aggregated:
                aggregated[key]["quantity"] += qty
                if recipe["name"] not in aggregated[key]["recipe_refs"]:
                    aggregated[key]["recipe_refs"].append(recipe["name"])
            else:
                aggregated[key] = {
                    "ingredient_name": name,
                    "quantity": qty,
                    "unit": unit,
                    "category": category,
                    "recipe_refs": [recipe["name"]],
                    "meal_date": entry.get("date", ""),
                }

    # Apply unit conversion
    for key, item in aggregated.items():
        converted = convert_to_display_unit(
            item["quantity"], item["unit"], unit_preference
        )
        item["quantity"] = converted["quantity"]
        item["unit"] = converted["unit"]

    # Round quantities
    for key, item in aggregated.items():
        item["quantity"] = round(item["quantity"], 2)
        if item["quantity"] == int(item["quantity"]):
            item["quantity"] = int(item["quantity"])

    # Subtract pantry if enabled
    if subtract_pantry and pantry_items:
        keys_to_remove = []
        for key, item in aggregated.items():
            name = item["ingredient_name"]
            if name in pantry_items:
                remaining = item["quantity"] - pantry_items[name]
                if remaining <= 0:
                    keys_to_remove.append(key)
                else:
                    item["quantity"] = round(remaining, 2)
                    if item["quantity"] == int(item["quantity"]):
                        item["quantity"] = int(item["quantity"])
        for key in keys_to_remove:
            del aggregated[key]

    # Group by category, sort
    grouped: Dict[str, list] = {}
    for item in aggregated.values():
        cat = item["category"] or "other"
        if cat not in grouped:
            grouped[cat] = []
        grouped[cat].append(item)

    sorted_categories = sorted(grouped.keys())
    for cat in sorted_categories:
        grouped[cat] = sorted(grouped[cat], key=lambda x: x["ingredient_name"])

    return grouped


# ---------------------------------------------------------------------------
# Helpers for building test data
# ---------------------------------------------------------------------------

def make_plan_entry(
    date: str = "2026-10-01",
    meal_type: str = "dinner",
    recipe_id: int = 1,
    servings: int = 4,
) -> Dict:
    """Create a plan entry dict for testing."""
    return {
        "date": date,
        "meal_type": meal_type,
        "recipe_id": recipe_id,
        "servings": servings,
    }


def make_recipe_dict(
    id: int = 1,
    name: str = "Test Recipe",
    servings: int = 4,
) -> Dict:
    """Create a recipe dict for testing."""
    return {"id": id, "name": name, "servings": servings}


def make_recipe_ingredient(
    ingredient_id: int = 1,
    quantity: float = 1.0,
    unit_override: Optional[str] = None,
) -> Dict:
    """Create a recipe ingredient link dict for testing."""
    return {
        "ingredient_id": ingredient_id,
        "quantity": quantity,
        "unit_override": unit_override,
    }


def make_ingredient(
    id: int = 1,
    name: str = "Test Ingredient",
    unit: str = "each",
    category: str = "pantry",
    price: Optional[float] = None,
) -> Dict:
    """Create an ingredient dict for testing."""
    return {
        "id": id,
        "name": name,
        "unit": unit,
        "category": category,
        "price": price,
    }
