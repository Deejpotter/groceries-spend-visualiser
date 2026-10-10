"""MCP query functions against a throwaway database (no running server)."""

import pytest

from database import get_db, set_setting
from mcp_server.server import (
    get_recipe, list_ingredients, list_rules, pantry_status, require_api_key,
    shopping_list, spend_summary, week_plan,
)


def _seed(db):
    db.execute("INSERT INTO ingredients (id, name, category, unit, price, minimum_stock)"
               " VALUES (1, 'Mince', 'meat', 'kg', 17.0, 1)")
    db.execute("INSERT INTO recipes (id, name, servings) VALUES (1, 'Bolognese', 4)")
    db.execute("INSERT INTO recipe_ingredients (recipe_id, ingredient_id, quantity)"
               " VALUES (1, 1, 500)")
    db.commit()


def _set_dates(db, start="2026-10-05", end="2026-10-09"):
    set_setting("plan_start_date", start)
    set_setting("plan_end_date", end)
    db.commit()


def test_week_plan_empty(db):
    assert week_plan() == {"plan_start": None, "plan_end": None, "days": []}


def test_week_plan_lists_meals(db):
    _seed(db)
    _set_dates(db)
    db.execute("INSERT INTO meal_plan_entries (date, meal_type, recipe_id, servings, is_auto_generated)"
               " VALUES ('2026-10-05', 'dinner', 1, 4, 0)")
    db.commit()
    plan = week_plan()
    assert (plan["plan_start"], plan["plan_end"]) == ("2026-10-05", "2026-10-09")
    assert len(plan["days"]) == 5
    monday = plan["days"][0]
    assert monday["date"] == "2026-10-05"
    assert monday["meals"]["dinner"] == {
        "recipe": "Bolognese", "servings": 4, "manual": True, "leftovers": False}
    assert plan["days"][1]["meals"] == {}


def test_shopping_list_empty(db):
    assert shopping_list() == {
        "groups": {}, "totals": {"packs_total": 0, "exact_total": 0},
        "unit_preference": "metric", "subtract_pantry": False}


def test_pantry_status_flags_low_stock(db):
    _seed(db)
    db.execute("INSERT INTO pantry_items (ingredient_id, quantity, unit, location)"
               " VALUES (1, 0.2, 'kg', 'fridge')")
    db.commit()
    status = pantry_status()
    assert status["low_count"] == 1
    assert status["expired_count"] == 0


def test_spend_summary_empty(db):
    assert spend_summary() == {"purchases": 0, "note": "No purchases yet."}


def test_spend_summary_totals(db):
    db.execute("INSERT INTO purchases (order_date, basket_id, store, product_name, quantity,"
               " unit_price, line_total) VALUES ('2026-10-01', 'b1', 'Woolworths', 'Milk', 1, 3.0, 3.0)")
    db.commit()
    summary = spend_summary()
    assert summary["total_spend"] == pytest.approx(3.0)
    assert summary["total_shops"] == 1


def test_list_rules_order(db):
    db.execute("INSERT INTO meal_rules (day_of_week, meal_type, is_active, sort_order)"
               " VALUES ('mon', 'dinner', 1, 0)")
    db.commit()
    rules = list_rules()["rules"]
    assert [(r["days"], r["meal"], r["active"]) for r in rules] == [("mon", "dinner", True)]


def test_search_ingredients_and_read_recipe(db):
    _seed(db)
    assert list_ingredients("inc")["count"] == 1
    assert list_ingredients("nope")["count"] == 0
    by_id = get_recipe("1")
    assert by_id["name"] == "Bolognese"
    assert by_id["recipe_ingredients"][0]["quantity"] == 500
    assert get_recipe("Bolognese")["name"] == "Bolognese"
    assert "error" in get_recipe("Nope")


def test_require_api_key_gate(db, monkeypatch):
    monkeypatch.delenv("GROCERIES_API_KEY", raising=False)
    with pytest.raises(SystemExit):
        require_api_key()
    monkeypatch.setenv("GROCERIES_API_KEY", "gx_wrong")
    set_setting("mcp_api_key", "gx_right")
    get_db().commit()
    with pytest.raises(SystemExit):
        require_api_key()
    monkeypatch.setenv("GROCERIES_API_KEY", "gx_right")
    require_api_key()  # matches: no exit
