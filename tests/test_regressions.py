"""Regression tests for bugs found in the September 2026 review."""

import random

from conftest import login
from services.plan_generator import generate_plan_entries, make_recipe, make_rule, select_recipe_for_rule
from services.shopping_list_generator import (
    generate_shopping_list, make_ingredient, make_plan_entry, make_recipe_dict, make_recipe_ingredient, total_cost,
)


def _setup_plan(client, db):
    db.execute("INSERT INTO ingredients (id, name, category, unit, price) VALUES (1, 'Mince', 'meat', 'kg', 12)")
    db.execute("INSERT INTO recipes (id, name, servings, tags) VALUES (1, 'Bolognese', 4, 'dinner, standard')")
    db.execute("INSERT INTO recipe_ingredients (recipe_id, ingredient_id, quantity, unit_override) VALUES (1, 1, 500, 'g')")
    db.execute("INSERT INTO meal_rules (day_of_week, meal_type, tag_filter, is_active, sort_order) VALUES ('all', 'dinner', 'standard', 1, 1)")
    db.commit()
    client.post("/settings/save", data={"action": "save_dates", "plan_start": "2026-10-05", "plan_end": "2026-10-06"})


# --- persistence bugs ---------------------------------------------------------

def test_meal_rule_is_saved(client, db):
    login(client)
    client.post("/meal-rules/add", data={"day_of_week": "weekday", "meal_type": "dinner", "is_active": "on"})
    db.rollback()  # read what's actually committed
    assert db.execute("SELECT COUNT(*) FROM meal_rules").fetchone()[0] == 1


def test_inactive_rules_are_listed(client, db):
    login(client)
    db.execute("INSERT INTO meal_rules (day_of_week, meal_type, tag_filter, is_active) VALUES ('mon', 'lunch', 'paused-tag', 0)")
    db.commit()
    assert b"paused-tag" in client.get("/meal-rules").data


def test_pantry_item_is_saved(client, db):
    login(client)
    db.execute("INSERT INTO ingredients (id, name, category, unit) VALUES (1, 'Rice', 'pantry', 'kg')")
    db.commit()
    client.post("/pantry/add", data={"ingredient_id": "1", "quantity": "2", "location": "pantry"})
    db.rollback()
    row = db.execute("SELECT quantity, unit FROM pantry_items").fetchone()
    assert row["quantity"] == 2 and row["unit"] == "kg"  # unit defaults to the ingredient's


def test_pantry_location_filter(client, db):
    login(client)
    db.execute("INSERT INTO ingredients (id, name, category, unit) VALUES (1, 'Rice', 'pantry', 'kg'), (2, 'Peas', 'frozen', 'kg')")
    db.execute("INSERT INTO pantry_items (ingredient_id, quantity, unit, location) VALUES (1, 1, 'kg', 'pantry'), (2, 1, 'kg', 'freezer')")
    db.commit()
    resp = client.get("/pantry?location=freezer")
    assert b"Peas" in resp.data and b"Rice" not in resp.data


# --- recipe form --------------------------------------------------------------

def test_recipe_bad_quantity_does_not_crash(client, db):
    login(client)
    db.execute("INSERT INTO ingredients (id, name, category, unit) VALUES (1, 'Rice', 'pantry', 'kg')")
    db.commit()
    resp = client.post("/recipes/add", data={"name": "Rice", "servings": "2", "ingredient_ids": "1", "quantities": "abc"})
    assert resp.status_code == 200
    assert b"must be numbers" in resp.data
    assert db.execute("SELECT COUNT(*) FROM recipes").fetchone()[0] == 0


def test_recipe_tags_normalised_and_filterable(client, db):
    login(client)
    client.post("/recipes/add", data={"name": "Tacos", "servings": "4", "tags": "Quick,  Mexican, quick"})
    assert db.execute("SELECT tags FROM recipes").fetchone()[0] == "quick, mexican"
    client.post("/recipes/add", data={"name": "Quiche", "servings": "4", "tags": "french"})
    resp = client.get("/recipes?tag=quick")
    assert b"Tacos" in resp.data and b"Quiche" not in resp.data  # exact tag, not substring


# --- plan generation ----------------------------------------------------------

def test_tag_filter_matches_tags_with_spaces():
    recipes = [make_recipe(id=1, tags="quick, mexican")]
    assert select_recipe_for_rule("mexican", recipes)["id"] == 1
    assert select_recipe_for_rule("mex", recipes) is None  # no substring matches


def test_multi_tag_filter_is_any_of():
    recipes = [make_recipe(id=1, tags="thai"), make_recipe(id=2, tags="indian")]
    assert select_recipe_for_rule("indian, curry", recipes)["id"] == 2


def test_generation_avoids_repeats_when_possible():
    rules = [make_rule(id=1, day_of_week="all", meal_type="dinner")]
    recipes = [make_recipe(id=i, name=f"R{i}") for i in range(1, 4)]
    entries, _ = generate_plan_entries("2026-10-05", "2026-10-07", rules, recipes, [], chooser=random.choice)
    assert sorted(e["recipe_id"] for e in entries) == [1, 2, 3]


def test_generate_route_one_entry_per_slot(client, db):
    """The old route joined every rule with every recipe; each slot must get exactly one entry."""
    login(client)
    _setup_plan(client, db)
    db.execute("INSERT INTO recipes (name, servings, tags) VALUES ('Other', 4, 'standard'), ('Third', 4, 'standard')")
    db.commit()
    client.post("/meal-plan/generate", data={})
    rows = db.execute("SELECT date, meal_type, COUNT(*) AS n FROM meal_plan_entries GROUP BY date, meal_type").fetchall()
    assert len(rows) == 2 and all(r["n"] == 1 for r in rows)


def test_manual_add_survives_regenerate(client, db):
    login(client)
    _setup_plan(client, db)
    db.execute("INSERT INTO recipes (id, name, servings) VALUES (2, 'Takeaway', 2)")
    db.commit()
    client.post("/meal-plan/add", data={"date": "2026-10-05", "meal_type": "dinner", "recipe_id": "2"})
    client.post("/meal-plan/generate", data={})
    row = db.execute("SELECT recipe_id, is_auto_generated FROM meal_plan_entries WHERE date = '2026-10-05'").fetchone()
    assert row["recipe_id"] == 2 and row["is_auto_generated"] == 0


def test_meal_plan_page_renders_grid(client, db):
    login(client)
    _setup_plan(client, db)
    client.post("/meal-plan/generate", data={})
    resp = client.get("/meal-plan")
    assert resp.status_code == 200
    assert b"Bolognese" in resp.data and b"Monday 05 Oct" in resp.data


# --- shopping list ------------------------------------------------------------

def test_shopping_list_uses_pantry_setting_and_costs(client, db):
    login(client)
    _setup_plan(client, db)
    client.post("/meal-plan/generate", data={})
    # 2 dinners x 500 g mince; 0.4 kg in the pantry (different unit) -> buy 600 g
    db.execute("INSERT INTO pantry_items (ingredient_id, quantity, unit) VALUES (1, 0.4, 'kg')")
    db.commit()
    client.post("/settings/save", data={"action": "save_prefs", "unit_preference": "metric",
                                        "default_servings": "2", "subtract_pantry": "on"})
    client.post("/shopping-list/generate", data={})
    item = db.execute("SELECT quantity, unit, estimated_cost FROM shopping_list_items").fetchone()
    assert (item["quantity"], item["unit"]) == (600, "g")
    assert item["estimated_cost"] == 7.2  # 0.6 kg x $12/kg


def test_shopping_list_regenerate_keeps_manual_and_ticks(client, db):
    login(client)
    _setup_plan(client, db)
    client.post("/meal-plan/generate", data={})
    client.post("/shopping-list/generate", data={})
    client.post("/shopping-list/add-manual", data={"name": "Paper towel", "quantity": "1", "unit": "pack"})
    generated_id = db.execute("SELECT id FROM shopping_list_items WHERE is_manual = 0").fetchone()[0]
    client.post(f"/shopping-list/toggle/{generated_id}")
    client.post("/shopping-list/generate", data={})
    rows = {r["ingredient_name"]: r for r in db.execute("SELECT * FROM shopping_list_items")}
    assert "Paper towel" in rows
    assert rows["Mince"]["checked"] == 1


def test_shopping_list_page_shows_saved_items(client, db):
    login(client)
    _setup_plan(client, db)
    client.post("/meal-plan/generate", data={})
    client.post("/shopping-list/generate", data={})
    resp = client.get("/shopping-list")
    assert b"1 kg Mince" in resp.data  # 2 x 500 g, displayed tidily
    assert b"$12.00" in resp.data


def test_service_cost_and_imperial_display():
    result = generate_shopping_list(
        [make_plan_entry(recipe_id=1, servings=4)],
        {1: make_recipe_dict(id=1, servings=4)},
        {1: [make_recipe_ingredient(ingredient_id=1, quantity=500, unit_override="g")]},
        {1: make_ingredient(id=1, name="Mince", unit="kg", price=10.0)},
        unit_preference="imperial",
    )
    item = next(iter(result.values()))[0]
    assert item["unit"] == "oz"
    assert item["estimated_cost"] == 5.0
    assert total_cost(result) == 5.0
