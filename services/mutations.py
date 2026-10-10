"""Shared write operations for plans, lists and pantry stock.

Pure-ish helpers over an explicit db handle: routes and the MCP server are
thin wrappers that add flashes / structured errors. Validation failures
raise ValueError with the user-facing message.
"""

from typing import Dict, Optional

from models import CATEGORY_LOOKUP, MEAL_TYPES, UNIT_LOOKUP, validate_date_format


def _recipe_or_raise(db, recipe_id) -> Dict:
    recipe = db.execute(
        "SELECT id, name, servings FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
    if not recipe:
        raise ValueError("Choose a recipe to add.")
    return recipe


def place_meal(db, date: str, meal_type: str, recipe_id: Optional[int],
               servings: Optional[int] = None, default_servings: int = 2) -> Dict:
    """Manually place a recipe in a slot (replaces whatever is there)."""
    if not validate_date_format(date or "") or (meal_type or "") not in MEAL_TYPES or not recipe_id:
        raise ValueError("Choose a recipe to add.")
    recipe = _recipe_or_raise(db, recipe_id)
    if servings is None:
        servings = recipe["servings"] or default_servings
    if servings < 1:
        raise ValueError("Servings must be at least 1.")
    existing = db.execute(
        "SELECT id, is_continuation FROM meal_plan_entries WHERE date = ? AND meal_type = ?",
        (date, meal_type),
    ).fetchone()
    replaced_continuation = False
    if existing and not existing["is_continuation"]:
        db.execute(
            "UPDATE meal_plan_entries SET is_continuation = 0, continuation_of = NULL "
            "WHERE continuation_of = ?",
            (existing["id"],),
        )
    elif existing:
        replaced_continuation = True
    db.execute("DELETE FROM meal_plan_entries WHERE date = ? AND meal_type = ?", (date, meal_type))
    cur = db.execute(
        "INSERT INTO meal_plan_entries (date, meal_type, recipe_id, servings, is_auto_generated)"
        " VALUES (?, ?, ?, ?, 0)",
        (date, meal_type, recipe_id, servings),
    )
    db.commit()
    return {"entry_id": cur.lastrowid, "recipe": recipe["name"], "servings": servings,
            "replaced_continuation": replaced_continuation}


def swap_meal(db, entry_id: int, new_recipe_id: Optional[int],
              servings: Optional[int] = None) -> Dict:
    """Replace a plan entry's recipe (marks it manual)."""
    entry = db.execute("SELECT * FROM meal_plan_entries WHERE id = ?", (entry_id,)).fetchone()
    if not entry:
        raise ValueError("Entry not found.")
    recipe = db.execute(
        "SELECT id, name FROM recipes WHERE id = ?", (new_recipe_id,)).fetchone()
    if not new_recipe_id or not recipe:
        raise ValueError("No recipe selected.")
    if servings is None:
        servings = entry["servings"]
    if servings < 1:
        raise ValueError("Servings must be at least 1.")
    if not entry["is_continuation"]:
        db.execute(
            "UPDATE meal_plan_entries SET is_continuation = 0, continuation_of = NULL "
            "WHERE continuation_of = ?",
            (entry_id,),
        )
    db.execute(
        "UPDATE meal_plan_entries SET recipe_id = ?, servings = ?, is_auto_generated = 0, "
        "is_continuation = 0, continuation_of = NULL WHERE id = ?",
        (new_recipe_id, servings, entry_id),
    )
    db.commit()
    return {"entry_id": entry_id, "recipe": recipe["name"], "servings": servings}


def remove_meal(db, entry_id: int) -> Dict:
    """Delete a plan entry, promoting its continuations to standalone meals."""
    entry = db.execute("SELECT * FROM meal_plan_entries WHERE id = ?", (entry_id,)).fetchone()
    if entry and not entry["is_continuation"]:
        db.execute(
            "UPDATE meal_plan_entries SET is_continuation = 0, continuation_of = NULL "
            "WHERE continuation_of = ?",
            (entry_id,),
        )
    db.execute("DELETE FROM meal_plan_entries WHERE id = ?", (entry_id,))
    db.commit()
    return {"removed": entry_id}


def set_tick(db, item_id: int, checked: bool) -> Dict:
    """Set a shopping-list row's ticked state explicitly."""
    if not db.execute("SELECT 1 FROM shopping_list_items WHERE id = ?", (item_id,)).fetchone():
        raise ValueError("Item not found.")
    db.execute("UPDATE shopping_list_items SET checked = ? WHERE id = ?",
               (1 if checked else 0, item_id))
    db.commit()
    return {"item_id": item_id, "checked": bool(checked)}


def add_manual_item(db, name: str, quantity: Optional[float],
                    unit: str = "each", category: str = "other") -> Dict:
    """Add a free-text row to the shopping list."""
    name = (name or "").strip()
    if not name:
        raise ValueError("Item name is required.")
    if quantity is None or quantity <= 0:
        raise ValueError("Quantity must be a number greater than zero.")
    unit = unit if unit in UNIT_LOOKUP else "each"
    category = category if category in CATEGORY_LOOKUP else "other"
    cur = db.execute(
        """INSERT INTO shopping_list_items (ingredient_name, quantity, unit, category, checked, is_manual, recipe_ref)
           VALUES (?, ?, ?, ?, 0, 1, '')""",
        (name, quantity, unit, category),
    )
    db.commit()
    return {"item_id": cur.lastrowid, "name": name, "quantity": quantity, "unit": unit,
            "category": category}


def consume_stock(db, item_id: int, amount: float) -> Dict:
    """Reduce pantry stock; returns what was used and what remains."""
    entry = db.execute("SELECT * FROM pantry_items WHERE id = ?", (item_id,)).fetchone()
    if not entry:
        raise ValueError("Item not found.")
    if amount is None or amount <= 0:
        raise ValueError("Amount must be positive.")
    new_qty = max(0, entry["quantity"] - amount)
    db.execute(
        "UPDATE pantry_items SET quantity = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (new_qty, item_id),
    )
    db.commit()
    return {"used": amount, "unit": entry["unit"], "remaining": new_qty,
            "depleted": new_qty == 0}
