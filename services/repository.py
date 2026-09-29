"""DB loaders that turn rows into the plain dicts the pure services expect."""

from typing import Dict, List, Tuple

from database import get_db, get_setting
from models import convert_unit, units_compatible


def load_rules() -> List[Dict]:
    return [dict(r) for r in get_db().execute("SELECT * FROM meal_rules ORDER BY sort_order, id")]


def load_recipes() -> List[Dict]:
    return [dict(r) for r in get_db().execute(
        "SELECT id, name, servings, is_two_night, tags FROM recipes ORDER BY name"
    )]


def load_manual_entries(start: str, end: str) -> List[Dict]:
    return [dict(r) for r in get_db().execute(
        "SELECT date, meal_type, recipe_id FROM meal_plan_entries "
        "WHERE date BETWEEN ? AND ? AND is_auto_generated = 0",
        (start, end),
    )]


def load_shopping_inputs(start: str, end: str) -> Tuple[List[Dict], Dict, Dict, Dict]:
    """Return (plan_entries, recipes, recipe_ingredients, ingredients) for a date range."""
    db = get_db()
    entries = [dict(r) for r in db.execute(
        "SELECT date, meal_type, recipe_id, servings, is_continuation FROM meal_plan_entries WHERE date BETWEEN ? AND ?",
        (start, end),
    )]
    recipes = {r["id"]: dict(r) for r in db.execute("SELECT id, name, servings FROM recipes")}
    recipe_ingredients: Dict[int, List[Dict]] = {}
    for r in db.execute("SELECT recipe_id, ingredient_id, quantity, unit_override FROM recipe_ingredients"):
        recipe_ingredients.setdefault(r["recipe_id"], []).append(dict(r))
    ingredients = {r["id"]: dict(r) for r in db.execute(
        "SELECT id, name, unit, category, price FROM ingredients"
    )}
    return entries, recipes, recipe_ingredients, ingredients


def load_pantry_stock() -> Dict[int, Tuple[float, str]]:
    """Pantry totals per ingredient id as (quantity, unit).

    Rows are converted to the ingredient's unit and summed; rows in a unit that
    can't be converted (e.g. 'each' for a kg ingredient) are left out.
    """
    stock: Dict[int, Tuple[float, str]] = {}
    for r in get_db().execute(
        "SELECT p.ingredient_id, p.quantity, COALESCE(NULLIF(p.unit, ''), i.unit) AS unit, i.unit AS base_unit "
        "FROM pantry_items p JOIN ingredients i ON p.ingredient_id = i.id"
    ):
        if not units_compatible(r["unit"], r["base_unit"]):
            continue
        qty = convert_unit(r["quantity"], r["unit"], r["base_unit"])
        previous = stock.get(r["ingredient_id"], (0.0, r["base_unit"]))[0]
        stock[r["ingredient_id"]] = (previous + qty, r["base_unit"])
    return stock


def shopping_preferences() -> Tuple[str, bool]:
    return get_setting("unit_preference", "metric"), get_setting("subtract_pantry_from_list", "0") == "1"
