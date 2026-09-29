"""Regression tests for the Copilot review findings on PR #1."""

import io
import sqlite3

import pytest

from conftest import create_user, login
from services.plan_generator import generate_plan_entries, make_recipe, make_rule
from services.shopping_list_generator import (
    generate_shopping_list, make_ingredient, make_plan_entry, make_recipe_dict, make_recipe_ingredient,
)
from services.spend_analysis import analyze_purchases
from services.spend_import import parse_cup_price, parse_purchase_csv


# --- auth ---------------------------------------------------------------------

def test_setup_closed_when_env_admin_configured(client, db, monkeypatch):
    """A direct first request to /setup must not beat the env-configured admin."""
    monkeypatch.setenv("ADMIN_USERNAME", "envadmin")
    monkeypatch.setenv("ADMIN_PASSWORD", "envpass123")
    client.post("/setup", data={"username": "attacker", "password": "12345678", "confirm_password": "12345678"})
    users = [r["username"] for r in db.execute("SELECT username FROM users")]
    assert users == ["envadmin"]


def test_login_returns_to_requested_page(client):
    create_user(client, "admin", "password123")
    first = client.get("/recipes?tag=quick")
    assert first.status_code == 302
    login_url = first.headers["Location"]
    resp = client.post(login_url, data={"username": "admin", "password": "password123"})
    assert resp.headers["Location"] == "/recipes?tag=quick"


# --- plan / shopping list -----------------------------------------------------

def test_two_night_leftovers_not_bought_twice():
    rules = [make_rule(id=1, day_of_week="all", meal_type="dinner")]
    recipes = [make_recipe(id=1, name="Roast", servings=4, is_two_night=True)]
    entries, _ = generate_plan_entries("2026-10-05", "2026-10-06", rules, recipes, [])
    assert [e["is_continuation"] for e in entries] == [0, 1]
    result = generate_shopping_list(
        entries, {1: make_recipe_dict(id=1, name="Roast", servings=4)},
        {1: [make_recipe_ingredient(ingredient_id=1, quantity=2, unit_override=None)]},
        {1: make_ingredient(id=1, name="Chicken", unit="kg", category="meat")},
    )
    assert result["meat"][0]["quantity"] == 2  # not 4


def test_removing_first_night_makes_leftovers_count(client, db):
    login(client)
    db.execute("INSERT INTO recipes (id, name, servings) VALUES (1, 'Roast', 4)")
    db.execute("INSERT INTO meal_plan_entries (id, date, meal_type, recipe_id, servings, is_continuation) "
               "VALUES (1, '2026-10-05', 'dinner', 1, 4, 0), (2, '2026-10-06', 'dinner', 1, 4, 1)")
    db.commit()
    client.post("/meal-plan/remove/1")
    assert db.execute("SELECT is_continuation FROM meal_plan_entries WHERE id = 2").fetchone()[0] == 0


def test_compatible_units_merged_before_pantry_subtraction():
    entries = [make_plan_entry(recipe_id=1, servings=1), make_plan_entry(recipe_id=2, servings=1)]
    recipes = {1: make_recipe_dict(id=1, name="A", servings=1), 2: make_recipe_dict(id=2, name="B", servings=1)}
    ri = {1: [make_recipe_ingredient(1, 500, "g")], 2: [make_recipe_ingredient(1, 1, "kg")]}
    ingredients = {1: make_ingredient(id=1, name="Flour", unit="kg")}
    result = generate_shopping_list(entries, recipes, ri, ingredients,
                                    subtract_pantry=True, pantry_items={1: (0.4, "kg")})
    items = result["pantry"]
    assert len(items) == 1
    assert (items[0]["quantity"], items[0]["unit"]) == (1.1, "kg")


def test_pantry_keyed_by_ingredient_id_not_name():
    entries = [make_plan_entry(recipe_id=1, servings=1)]
    ri = {1: [make_recipe_ingredient(1, 1), make_recipe_ingredient(2, 1)]}
    ingredients = {1: make_ingredient(id=1, name="Salt", unit="kg"), 2: make_ingredient(id=2, name="Salt", unit="kg")}
    result = generate_shopping_list(entries, {1: make_recipe_dict(id=1, servings=1)}, ri, ingredients,
                                    subtract_pantry=True, pantry_items={1: (5, "kg")})
    assert [i["ingredient_id"] for i in result["pantry"]] == [2]


def test_pantry_rows_in_different_units_are_summed(client, db):
    from services.repository import load_pantry_stock
    db.execute("INSERT INTO ingredients (id, name, category, unit) VALUES (1, 'Rice', 'pantry', 'kg')")
    db.execute("INSERT INTO pantry_items (ingredient_id, quantity, unit) VALUES (1, 500, 'g'), (1, 1, 'kg')")
    db.commit()
    assert load_pantry_stock() == {1: (1.5, "kg")}


def test_ticks_survive_unit_preference_change(client, db):
    login(client)
    db.execute("INSERT INTO ingredients (id, name, category, unit) VALUES (1, 'Mince', 'meat', 'kg')")
    db.execute("INSERT INTO recipes (id, name, servings) VALUES (1, 'Bolognese', 4)")
    db.execute("INSERT INTO recipe_ingredients (recipe_id, ingredient_id, quantity) VALUES (1, 1, 1)")
    db.execute("INSERT INTO meal_plan_entries (date, meal_type, recipe_id, servings, is_auto_generated) "
               "VALUES ('2026-10-05', 'dinner', 1, 4, 0)")
    db.commit()
    client.post("/settings/save", data={"action": "save_dates", "plan_start": "2026-10-05", "plan_end": "2026-10-05"})
    client.post("/shopping-list/generate", data={})
    item_id = db.execute("SELECT id FROM shopping_list_items").fetchone()[0]
    client.post(f"/shopping-list/toggle/{item_id}")
    client.post("/settings/save", data={"action": "save_prefs", "unit_preference": "imperial", "default_servings": "2"})
    client.post("/shopping-list/generate", data={})
    row = db.execute("SELECT unit, checked FROM shopping_list_items").fetchone()
    assert (row["unit"], row["checked"]) == ("lb", 1)


@pytest.mark.parametrize("path", ["/meal-plan/add", "/meal-plan/swap/1"])
def test_meal_servings_must_be_positive(client, db, path):
    login(client)
    db.execute("INSERT INTO recipes (id, name, servings) VALUES (1, 'Soup', 2)")
    db.execute("INSERT INTO meal_plan_entries (id, date, meal_type, recipe_id, servings) VALUES (1, '2026-10-05', 'lunch', 1, 2)")
    db.commit()
    client.post(path, data={"date": "2026-10-06", "meal_type": "dinner", "recipe_id": "1", "servings": "-3"})
    assert db.execute("SELECT MIN(servings) FROM meal_plan_entries").fetchone()[0] == 2


# --- recipe form / settings ---------------------------------------------------

def test_recipe_validation_error_keeps_submitted_rows(client, db):
    login(client)
    db.execute("INSERT INTO ingredients (id, name, category, unit) VALUES (1, 'Basil', 'produce', 'bunch')")
    db.commit()
    resp = client.post("/recipes/add", data={"name": "", "servings": "2", "ingredient_ids": "1", "quantities": "3"})
    assert b"Recipe name is required" in resp.data
    assert b"Basil" in resp.data and b'value="3"' in resp.data


def test_default_servings_prefills_new_recipe(client, db):
    login(client)
    client.post("/settings/save", data={"action": "save_prefs", "unit_preference": "metric", "default_servings": "6"})
    assert b'name="servings"\n                                   min="1" value="6"' in client.get("/recipes/add").data


# --- spend --------------------------------------------------------------------

def test_same_basket_id_at_two_stores_is_two_purchases(db):
    from services.spend_import import import_purchases
    rows_a, _ = parse_purchase_csv("date,basket_id,product_name,quantity,unit_price\n2026-09-01,1,Milk,1,3\n", "Aldi")
    rows_b, _ = parse_purchase_csv("date,basket_id,product_name,quantity,unit_price\n2026-09-02,1,Milk,1,3\n", "Coles")
    assert import_purchases(db, rows_a) == (1, 0)
    assert import_purchases(db, rows_b) == (1, 0)
    assert analyze_purchases(rows_a + rows_b)["total_shops"] == 2


def test_old_purchase_constraint_is_migrated(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE purchases (
        id INTEGER PRIMARY KEY AUTOINCREMENT, order_date TEXT NOT NULL, basket_id TEXT NOT NULL,
        store TEXT NOT NULL DEFAULT 'Woolworths', channel TEXT, product_name TEXT NOT NULL,
        quantity REAL NOT NULL DEFAULT 1, unit_price REAL, line_total REAL NOT NULL DEFAULT 0,
        cup_price TEXT, stockcode TEXT, ingredient_id INTEGER, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (basket_id, product_name))""")
    conn.execute("INSERT INTO purchases (order_date, basket_id, product_name, line_total) VALUES ('2026-01-01', 'b', 'Milk', 3)")
    conn.commit()
    conn.close()
    monkeypatch.setenv("DATABASE_PATH", str(path))
    from database import init_db
    init_db()
    conn = sqlite3.connect(path)
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'purchases'").fetchone()[0]
    assert "UNIQUE (store, basket_id, product_name)" in sql
    assert conn.execute("SELECT COUNT(*) FROM purchases").fetchone()[0] == 1
    conn.close()


def test_new_ingredient_uses_purchase_store(client, db):
    login(client)
    csv = "date,product_name,quantity,unit_price\n2026-09-01,Eggs,1,6\n"
    client.post("/spend/import", data={"file": (io.BytesIO(csv.encode()), "a.csv"), "store": "Aldi"},
                content_type="multipart/form-data")
    client.post("/spend/products/link", data={"product_name": "Eggs", "ingredient_id": "new"})
    assert db.execute("SELECT store FROM ingredients WHERE name = 'Eggs'").fetchone()[0] == "Aldi"


@pytest.mark.parametrize("text", ["$1 / .G", "$1 / 0G", "$. / 100G"])
def test_malformed_cup_prices_rejected(text):
    assert parse_cup_price(text) is None


@pytest.mark.parametrize("qty", ["", "abc", "0", "-2"])
def test_bad_quantity_rows_rejected(qty):
    rows, errors = parse_purchase_csv(f"date,product_name,quantity,unit_price\n2026-09-01,Eggs,{qty},6\n")
    assert rows == []
    assert "quantity must be a positive number" in errors[0]


def test_nice_date_filter(app):
    """Plan dates render as 'Wed 30 Sep 2026'; bad input passes through."""
    f = app.jinja_env.filters["nice_date"]
    assert f("2026-09-30") == "Wed 30 Sep 2026"
    assert f("not-a-date") == "not-a-date"
    assert f("") == ""


def test_static_urls_are_cache_busted(app):
    """Static URLs carry a version so a deploy's new CSS/JS isn't hidden by browser caches."""
    with app.test_request_context():
        from flask import url_for
        assert "?v=" in url_for("static", filename="css/style.css")


def test_today_uses_app_timezone(monkeypatch):
    """'Today' follows APP_TIMEZONE so evenings in Australia aren't shown as yesterday (UTC)."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    import models
    monkeypatch.setenv("APP_TIMEZONE", "Pacific/Kiritimati")  # UTC+14
    assert models.today() == datetime.now(ZoneInfo("Pacific/Kiritimati")).date()
    monkeypatch.delenv("APP_TIMEZONE")
    assert models.today() == datetime.now(ZoneInfo("Australia/Sydney")).date()
