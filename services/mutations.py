"""Shared write operations for plans, lists and pantry stock.

Pure-ish helpers over an explicit db handle: routes and the MCP server are
thin wrappers that add flashes / structured errors. Failures raise AgentError
(code + user-facing message); pantry validators report every problem at once.
"""

from typing import Dict, List, Optional, Tuple

from models import CATEGORY_LOOKUP, MEAL_TYPES, UNIT_LOOKUP, short_name, validate_date_format


class AgentError(ValueError):
    """Machine-readable failure: code + user-facing message(s).

    Codes: "not_found" (missing row/reference), "validation" (bad input),
    "no_plan_dates" (plan tools without a range). Routes catch ValueError
    and flash the message; the MCP server maps code/message structurally.
    """

    def __init__(self, code: str, message):
        self.code = code
        self.messages = [message] if isinstance(message, str) else list(message)
        super().__init__("; ".join(self.messages))


def _recipe_or_raise(db, recipe_id) -> Dict:
    recipe = db.execute(
        "SELECT id, name, servings FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
    if not recipe:
        raise AgentError("validation", "Choose a recipe to add.")
    return recipe


def place_meal(db, date: str, meal_type: str, recipe_id: Optional[int],
               servings: Optional[int] = None, default_servings: int = 2,
               origin: str = "ui") -> Dict:
    """Manually place a recipe in a slot (replaces whatever is there)."""
    if not validate_date_format(date or "") or (meal_type or "") not in MEAL_TYPES or not recipe_id:
        raise AgentError("validation", "Choose a recipe to add.")
    recipe = _recipe_or_raise(db, recipe_id)
    if servings is None:
        servings = recipe["servings"] or default_servings
    if servings < 1:
        raise AgentError("validation", "Servings must be at least 1.")
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
        "INSERT INTO meal_plan_entries (date, meal_type, recipe_id, servings, is_auto_generated, origin)"
        " VALUES (?, ?, ?, ?, 0, ?)",
        (date, meal_type, recipe_id, servings, origin),
    )
    db.commit()
    return {"entry_id": cur.lastrowid, "recipe": recipe["name"], "servings": servings,
            "replaced_continuation": replaced_continuation}


def swap_meal(db, entry_id: int, new_recipe_id: Optional[int],
              servings: Optional[int] = None, origin: str = "ui") -> Dict:
    """Replace a plan entry's recipe (marks it manual)."""
    entry = db.execute("SELECT * FROM meal_plan_entries WHERE id = ?", (entry_id,)).fetchone()
    if not entry:
        raise AgentError("not_found", "Entry not found.")
    recipe = db.execute(
        "SELECT id, name FROM recipes WHERE id = ?", (new_recipe_id,)).fetchone()
    if not new_recipe_id or not recipe:
        raise AgentError("validation", "No recipe selected.")
    if servings is None:
        servings = entry["servings"]
    if servings < 1:
        raise AgentError("validation", "Servings must be at least 1.")
    if not entry["is_continuation"]:
        db.execute(
            "UPDATE meal_plan_entries SET is_continuation = 0, continuation_of = NULL "
            "WHERE continuation_of = ?",
            (entry_id,),
        )
    if entry["is_auto_generated"] and entry["recipe_id"] != new_recipe_id:
        # A suggested meal swapped for another is a signal to suggest it less.
        db.execute("INSERT INTO suggestion_feedback (kind, item_key, action) VALUES ('meal', ?, 'swapped_out')",
                   (str(entry["recipe_id"]),))
    db.execute(
        "UPDATE meal_plan_entries SET recipe_id = ?, servings = ?, is_auto_generated = 0, "
        "is_continuation = 0, continuation_of = NULL, origin = ? WHERE id = ?",
        (new_recipe_id, servings, origin, entry_id),
    )
    db.commit()
    return {"entry_id": entry_id, "recipe": recipe["name"], "servings": servings}


def lock_meal(db, entry_id: int, locked: bool) -> Dict:
    """Keep (lock) a suggested meal through regeneration, or hand it back to the generator."""
    entry = db.execute("SELECT id FROM meal_plan_entries WHERE id = ?", (entry_id,)).fetchone()
    if not entry:
        raise AgentError("not_found", "Entry not found.")
    db.execute("UPDATE meal_plan_entries SET is_auto_generated = ? WHERE id = ?",
               (0 if locked else 1, entry_id))
    db.commit()
    return {"entry_id": entry_id, "locked": locked}


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


def set_tick(db, item_id: int, checked: bool, origin: str = "ui") -> Dict:
    """Set a shopping-list row's ticked state explicitly."""
    if not db.execute("SELECT 1 FROM shopping_list_items WHERE id = ?", (item_id,)).fetchone():
        raise AgentError("not_found", "Item not found.")
    db.execute("UPDATE shopping_list_items SET checked = ?, origin = ? WHERE id = ?",
               (1 if checked else 0, origin, item_id))
    db.commit()
    return {"item_id": item_id, "checked": bool(checked)}


def add_manual_item(db, name: str, quantity: Optional[float],
                    unit: str = "each", category: str = "other", origin: str = "ui") -> Dict:
    """Add a free-text row to the shopping list."""
    name = (name or "").strip()
    if not name:
        raise AgentError("validation", "Item name is required.")
    if quantity is None or quantity <= 0:
        raise AgentError("validation", "Quantity must be a number greater than zero.")
    unit = unit if unit in UNIT_LOOKUP else "each"
    category = category if category in CATEGORY_LOOKUP else "other"
    cur = db.execute(
        """INSERT INTO shopping_list_items (ingredient_name, quantity, unit, category, checked, is_manual, recipe_ref, origin)
           VALUES (?, ?, ?, ?, 0, 1, '', ?)""",
        (name, quantity, unit, category, origin),
    )
    db.commit()
    return {"item_id": cur.lastrowid, "name": name, "quantity": quantity, "unit": unit,
            "category": category}


def consume_stock(db, item_id: int, amount: float, origin: str = "ui") -> Dict:
    """Reduce pantry stock; returns what was used and what remains."""
    entry = db.execute("SELECT * FROM pantry_items WHERE id = ?", (item_id,)).fetchone()
    if not entry:
        raise AgentError("not_found", "Item not found.")
    if amount is None or amount <= 0:
        raise AgentError("validation", "Amount must be positive.")
    new_qty = max(0, entry["quantity"] - amount)
    db.execute(
        "UPDATE pantry_items SET quantity = ?, origin = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (new_qty, origin, item_id),
    )
    db.commit()
    return {"used": amount, "unit": entry["unit"], "remaining": new_qty,
            "depleted": new_qty == 0}


def _validated_stock(db, ingredient_id, quantity, unit, expiry_date, location) -> Dict:
    """Shared validation for pantry add/edit; returns the cleaned row."""
    ingredient = db.execute("SELECT id, unit FROM ingredients WHERE id = ?",
                            (ingredient_id,)).fetchone()
    errors = []
    if not ingredient:
        errors.append("Ingredient is required.")
    if quantity is None:
        errors.append("Quantity must be a number.")
    elif quantity <= 0:
        errors.append("Quantity must be greater than zero.")
    unit = (unit or "").strip() or (ingredient["unit"] if ingredient else "")
    if unit and unit not in UNIT_LOOKUP:
        errors.append("Choose a valid unit.")
    expiry_date = (expiry_date or "").strip() or None
    if expiry_date and not validate_date_format(expiry_date):
        errors.append("Expiry date must be a valid date.")
    location = (location or "").strip()
    if not location:
        errors.append("Location is required.")
    if errors:
        raise AgentError("validation", errors)
    return {"ingredient_id": ingredient["id"], "quantity": quantity, "unit": unit,
            "expiry_date": expiry_date, "location": location}


def add_stock(db, ingredient_id, quantity, unit=None, expiry_date=None,
              location="pantry", origin: str = "ui") -> Dict:
    """Add a pantry stock row."""
    row = _validated_stock(db, ingredient_id, quantity, unit, expiry_date, location)
    cur = db.execute(
        "INSERT INTO pantry_items (ingredient_id, quantity, unit, expiry_date, location, origin)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (row["ingredient_id"], row["quantity"], row["unit"], row["expiry_date"], row["location"], origin),
    )
    db.commit()
    return {"item_id": cur.lastrowid, **row}


def update_stock(db, item_id: int, ingredient_id, quantity, unit=None,
                 expiry_date=None, location="pantry", origin: str = "ui") -> Dict:
    """Replace a pantry stock row."""
    if not db.execute("SELECT 1 FROM pantry_items WHERE id = ?", (item_id,)).fetchone():
        raise AgentError("not_found", "Item not found.")
    row = _validated_stock(db, ingredient_id, quantity, unit, expiry_date, location)
    db.execute(
        """UPDATE pantry_items SET ingredient_id=?, quantity=?, unit=?,
           expiry_date=?, location=?, origin=?, updated_at=CURRENT_TIMESTAMP WHERE id=?""",
        (row["ingredient_id"], row["quantity"], row["unit"], row["expiry_date"],
         row["location"], origin, item_id),
    )
    db.commit()
    return {"item_id": item_id, **row}


def set_ticks(db, item_ids: List[int], checked: bool, origin: str = "ui") -> Dict:
    """Set the ticked state on many shopping-list rows at once."""
    ids = [int(i) for i in (item_ids or [])]
    if not ids:
        return {"updated": 0, "missing": []}
    found = {r[0] for r in db.execute(
        f"SELECT id FROM shopping_list_items WHERE id IN ({','.join('?' * len(ids))})", ids)}
    missing = [i for i in ids if i not in found]
    if found:
        db.execute(
            f"UPDATE shopping_list_items SET checked = ?, origin = ? WHERE id IN ({','.join('?' * len(found))})",
            [1 if checked else 0, origin, *sorted(found)],
        )
        db.commit()
    return {"updated": len(found), "missing": missing}


def _latest_purchase(db, product_name):
    return db.execute(
        "SELECT unit_price, cup_price, store, stockcode FROM purchases"
        " WHERE product_name = ? ORDER BY order_date DESC LIMIT 1",
        (product_name,),
    ).fetchone()


def link_product(db, product_name: str, choice: str, update_price: bool = True,
                 new_category: str = "other", origin: str = "ui") -> Tuple[str, str, bool]:
    """Link one product to an ingredient. Returns (level, message, price_updated)."""
    from services.spend_import import parse_cup_price, price_per_unit, product_url
    latest = _latest_purchase(db, product_name)
    if latest is None:
        return "error", "No purchases found for that product.", False
    if choice == "new":
        # Create an ingredient named after the product, priced from the latest purchase.
        cup = parse_cup_price(latest["cup_price"]) if latest else None
        unit = {"g": "kg", "mL": "L"}.get(cup[2], cup[2]) if cup else "each"
        price = price_per_unit(latest["cup_price"], latest["unit_price"], unit) if latest else None
        category = new_category if new_category in CATEGORY_LOOKUP else "other"
        cur = db.execute(
            "INSERT INTO ingredients (name, display_name, category, unit, price, store, url, origin) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (product_name, short_name(product_name), category, unit, price, latest["store"] if latest else None,
             product_url(latest["store"], latest["stockcode"]) if latest else None, origin),
        )
        db.execute("UPDATE purchases SET ingredient_id = ?, origin = ? WHERE product_name = ?", (cur.lastrowid, origin, product_name))
        db.commit()
        return "success", f"Created ingredient “{product_name}” in {CATEGORY_LOOKUP.get(category, category)}.", False
    if choice:
        ingredient = db.execute("SELECT id, unit, price FROM ingredients WHERE id = ?", (choice,)).fetchone()
        if not ingredient:
            return "error", "Ingredient not found.", False
        ingredient_id = ingredient["id"]
        price_updated = False
        if update_price and latest:
            price = price_per_unit(latest["cup_price"], latest["unit_price"], ingredient["unit"])
            if price is not None:
                old_price = ingredient["price"]
                db.execute("UPDATE ingredients SET price = ? WHERE id = ?", (price, ingredient_id))
                price_updated = True
                if old_price is None:
                    note = f"Linked and set price to ${price:.2f} per {ingredient['unit']}."
                else:
                    note = (f"Linked and updated price ${old_price:.2f} → ${price:.2f}"
                            f" per {ingredient['unit']}.")
                level = "success"
            else:
                note = "Linked. The purchase price couldn't be converted to the ingredient's unit, so the price wasn't changed."
                level = "warning"
        else:
            note, level = "Product linked.", "success"
        db.execute("UPDATE purchases SET ingredient_id = ?, origin = ? WHERE product_name = ?", (ingredient_id, origin, product_name))
        db.commit()
        return level, note, price_updated
    db.execute("UPDATE purchases SET ingredient_id = NULL, origin = ? WHERE product_name = ?", (origin, product_name,))
    db.commit()
    return "info", "Product unlinked.", False
