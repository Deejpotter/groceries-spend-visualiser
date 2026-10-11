"""Shared write operations (used by routes and the MCP server)."""

import pytest

from services.mutations import (
    add_manual_item, add_stock, consume_stock, place_meal, remove_meal, set_tick, set_ticks,
    swap_meal, update_stock,
)


def _seed(db):
    db.execute("INSERT INTO ingredients (id, name, category, unit) VALUES (1, 'Mince', 'meat', 'kg')")
    db.execute("INSERT INTO recipes (id, name, servings) VALUES (1, 'Bolognese', 4)")
    db.execute("INSERT INTO recipes (id, name, servings) VALUES (2, 'Soup', 2)")
    db.commit()


def test_place_meal_validates(db):
    _seed(db)
    with pytest.raises(ValueError, match="Choose a recipe"):
        place_meal(db, "not-a-date", "dinner", 1)
    with pytest.raises(ValueError, match="Choose a recipe"):
        place_meal(db, "2026-10-05", "brunch", 1)
    with pytest.raises(ValueError, match="Choose a recipe"):
        place_meal(db, "2026-10-05", "dinner", 99)
    with pytest.raises(ValueError, match="at least 1"):
        place_meal(db, "2026-10-05", "dinner", 1, servings=0)


def test_place_meal_replaces_and_promotes(db):
    _seed(db)
    first = place_meal(db, "2026-10-05", "dinner", 1)
    assert first["servings"] == 4  # recipe default
    second = place_meal(db, "2026-10-05", "dinner", 2, servings=3)
    assert second["servings"] == 3
    assert db.execute("SELECT COUNT(*) FROM meal_plan_entries").fetchone()[0] == 1


def test_swap_and_remove_meal(db):
    _seed(db)
    entry = place_meal(db, "2026-10-05", "dinner", 1)
    swapped = swap_meal(db, entry["entry_id"], 2)
    assert swapped["recipe"] == "Soup" and swapped["servings"] == 4
    with pytest.raises(ValueError, match="Entry not found"):
        swap_meal(db, 999, 2)
    remove_meal(db, entry["entry_id"])
    assert db.execute("SELECT COUNT(*) FROM meal_plan_entries").fetchone()[0] == 0


def test_set_tick(db):
    db.execute("INSERT INTO shopping_list_items (ingredient_name, quantity, unit, checked, is_manual)"
               " VALUES ('Milk', 2, 'L', 0, 0)")
    db.commit()
    assert set_tick(db, 1, True)["checked"] is True
    with pytest.raises(ValueError, match="Item not found"):
        set_tick(db, 999, True)


def test_add_manual_item(db):
    with pytest.raises(ValueError, match="Item name"):
        add_manual_item(db, "  ", 1)
    with pytest.raises(ValueError, match="greater than zero"):
        add_manual_item(db, "Milk", 0)
    item = add_manual_item(db, "Milk", 2, unit="bogus", category="bogus")
    assert (item["unit"], item["category"]) == ("each", "other")


def test_consume_stock(db):
    _seed(db)
    db.execute("INSERT INTO pantry_items (ingredient_id, quantity, unit) VALUES (1, 5, 'kg')")
    db.commit()
    result = consume_stock(db, 1, 2)
    assert (result["remaining"], result["depleted"]) == (3, False)
    result = consume_stock(db, 1, 9)
    assert (result["remaining"], result["depleted"]) == (0, True)
    with pytest.raises(ValueError, match="positive"):
        consume_stock(db, 1, 0)
    with pytest.raises(ValueError, match="Item not found"):
        consume_stock(db, 999, 1)


def test_add_and_update_stock(db):
    _seed(db)
    with pytest.raises(ValueError, match="Ingredient is required"):
        add_stock(db, 99, 1)
    with pytest.raises(ValueError, match="greater than zero"):
        add_stock(db, 1, 0)
    row = add_stock(db, 1, 2, expiry_date="2026-12-01", location="fridge")
    assert (row["unit"], row["location"]) == ("kg", "fridge")  # unit defaults to ingredient's
    updated = update_stock(db, row["item_id"], 1, 3, unit="g")
    assert (updated["quantity"], updated["unit"]) == (3, "g")
    with pytest.raises(ValueError, match="Item not found"):
        update_stock(db, 999, 1, 1)


def test_set_ticks_bulk(db):
    for i in (1, 2, 3):
        db.execute("INSERT INTO shopping_list_items (ingredient_name, quantity, unit, checked, is_manual)"
                   f" VALUES ('Item{i}', 1, 'each', 0, 0)")
    db.commit()
    result = set_ticks(db, [1, 2, 99], True)
    assert result == {"updated": 2, "missing": [99]}
    assert set_ticks(db, [], True) == {"updated": 0, "missing": []}
