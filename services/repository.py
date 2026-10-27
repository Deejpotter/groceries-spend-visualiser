"""DB loaders that turn rows into the plain dicts the pure services expect."""

from typing import Dict, List, Tuple

from database import get_db, get_setting
from models import convert_unit, units_compatible
from services.spend_import import product_url


def load_rules() -> List[Dict]:
    return [dict(r) for r in get_db().execute("SELECT * FROM meal_rules ORDER BY sort_order, id")]


def load_recipes() -> List[Dict]:
    return [dict(r) for r in get_db().execute(
        "SELECT id, name, servings, covers_days, tags, is_favourite, prep_time, cook_time FROM recipes ORDER BY name"
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


def load_pantry_stock(today=None) -> Dict[int, Tuple[float, str]]:
    """Pantry totals per ingredient id as (quantity, unit).

    Rows are converted to the ingredient's unit and summed; rows in a unit that
    can't be converted (e.g. 'each' for a kg ingredient) are left out.
    Expired rows are left out so old stock doesn't shrink the shopping list.
    """
    from datetime import datetime
    from models import today as local_today
    today = today or local_today()
    stock: Dict[int, Tuple[float, str]] = {}
    for r in get_db().execute(
        "SELECT p.ingredient_id, p.quantity, COALESCE(NULLIF(p.unit, ''), i.unit) AS unit, i.unit AS base_unit, "
        "p.expiry_date FROM pantry_items p JOIN ingredients i ON p.ingredient_id = i.id"
    ):
        expiry = r["expiry_date"]
        if expiry:
            try:
                exp_day = datetime.strptime(str(expiry)[:10], "%Y-%m-%d").date()
            except (ValueError, TypeError):
                exp_day = None
            if exp_day is not None and exp_day < today:
                continue
        if not units_compatible(r["unit"], r["base_unit"]):
            continue
        qty = convert_unit(r["quantity"], r["unit"], r["base_unit"])
        previous = stock.get(r["ingredient_id"], (0.0, r["base_unit"]))[0]
        stock[r["ingredient_id"]] = (previous + qty, r["base_unit"])
    return stock


def shopping_preferences() -> Tuple[str, bool]:
    return get_setting("unit_preference", "metric"), get_setting("subtract_pantry_from_list", "0") == "1"


def load_list_rows() -> List[Dict]:
    """Shopping-list rows with the fields of their ingredient (ing_*), ticked items last."""
    return [dict(r) for r in get_db().execute(
        """SELECT s.*, i.url AS product_url, i.name AS ing_name, i.display_name,
                  i.unit AS ing_unit, i.pack_size AS ing_pack_size, i.price AS ing_price
           FROM shopping_list_items s
           LEFT JOIN ingredients i ON i.id = s.ingredient_id
           ORDER BY s.checked, s.category, s.ingredient_name"""
    )]


def load_linked_products() -> Dict[int, List[Dict]]:
    """Purchased products linked to each ingredient, with the shelf price last paid and product link."""
    products: Dict[int, List[Dict]] = {}
    for r in get_db().execute(
        """SELECT p.ingredient_id, p.product_name, p.unit_price, p.store, p.stockcode
           FROM purchases p
           WHERE p.ingredient_id IS NOT NULL AND p.unit_price IS NOT NULL
             AND p.order_date = (SELECT MAX(x.order_date) FROM purchases x
                                 WHERE x.product_name = p.product_name AND x.unit_price IS NOT NULL)
           GROUP BY p.ingredient_id, p.product_name"""
    ):
        products.setdefault(r["ingredient_id"], []).append({
            "product_name": r["product_name"], "unit_price": r["unit_price"],
            "url": product_url(r["store"], r["stockcode"]),
        })
    return products


def load_purchase_rows(since: str = "0000-00-00") -> List[Dict]:
    """Purchase lines on or after ``since`` for pattern analysis."""
    return [dict(r) for r in get_db().execute(
        "SELECT order_date, basket_id, store, product_name, quantity, unit_price, line_total, ingredient_id"
        " FROM purchases WHERE order_date >= ? ORDER BY order_date",
        (since,),
    )]


def load_product_decisions() -> Dict[str, Dict]:
    return {r["product_name"]: dict(r) for r in get_db().execute("SELECT * FROM product_decisions")}


def load_ingredient_map() -> Dict[int, Dict]:
    return {r["id"]: dict(r) for r in get_db().execute(
        "SELECT id, name, display_name, unit, category, price FROM ingredients"
    )}


def load_feedback(kind: str) -> List[Dict]:
    """Suggestion feedback rows of one kind, oldest first."""
    return [dict(r) for r in get_db().execute(
        "SELECT item_key, action, created_at FROM suggestion_feedback WHERE kind = ? ORDER BY id", (kind,)
    )]


def record_feedback(kind: str, item_key: str, action: str) -> None:
    get_db().execute("INSERT INTO suggestion_feedback (kind, item_key, action) VALUES (?, ?, ?)",
                     (kind, str(item_key), action))


def load_meal_history(before: str) -> List[Dict]:
    """Meal plan entries dated before ``before`` (the record of meals eaten)."""
    return [dict(r) for r in get_db().execute(
        "SELECT date, meal_type, recipe_id, is_continuation FROM meal_plan_entries WHERE date < ?",
        (before,),
    )]


def load_recipe_links() -> Dict[int, List[Dict]]:
    links: Dict[int, List[Dict]] = {}
    for r in get_db().execute("SELECT recipe_id, ingredient_id, quantity, unit_override FROM recipe_ingredients"):
        links.setdefault(r["recipe_id"], []).append(dict(r))
    return links
