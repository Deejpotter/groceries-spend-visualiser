"""MCP query functions against a throwaway database (no running server)."""

import pytest

from database import get_db, set_setting
from mcp_server.server import (
    add_list_item, add_meal, add_stock, generate_plan, get_recipe, import_status, link_product,
    list_ingredients, list_rules, pantry_status, remove_meal, server_info, set_item_ticked,
    shopping_list, spend_summary, stage_invoice_pdf, swap_meal, tick_many, use_pantry,
    week_plan, require_api_key,
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


def test_mcp_meal_writes_round_trip(db):
    _seed(db)
    _set_dates(db)
    placed = add_meal("2026-10-05", "dinner", "Bolognese")
    assert placed["servings"] == 4
    swapped = swap_meal(placed["entry_id"], "Bolognese", servings=6)
    assert swapped["servings"] == 6
    assert remove_meal(placed["entry_id"])["removed"] == placed["entry_id"]
    with pytest.raises(ValueError, match="No recipe found"):
        add_meal("2026-10-05", "dinner", "Nope")


def test_mcp_generate_preview_then_confirm(db):
    _seed(db)
    assert generate_plan()["error"] == "No plan dates set."
    _set_dates(db)
    db.execute("INSERT INTO meal_rules (day_of_week, meal_type, is_active, sort_order)"
               " VALUES ('mon', 'dinner', 1, 0)")
    db.commit()
    preview = generate_plan()
    assert preview["confirmed"] is False and preview["auto_entries"] >= 1
    done = generate_plan(confirm=True)
    assert done["confirmed"] is True and done["entries"] >= 1


def test_mcp_list_and_pantry_writes(db):
    _seed(db)
    item = add_list_item("Milk", 2, unit="L", category="dairy")
    assert set_item_ticked(item["item_id"], True)["checked"] is True
    db.execute("INSERT INTO pantry_items (ingredient_id, quantity, unit) VALUES (1, 5, 'kg')")
    db.commit()
    used = use_pantry(1, 2.0)
    assert (used["remaining"], used["depleted"]) == (3, False)


def test_server_info_and_bulk_ticks(db):
    info = server_info()
    assert info["server"] == "groceries" and info["counts"]["ingredients"] == 0
    assert info["plan_start"] is None
    _seed(db)
    _set_dates(db)
    info = server_info()
    assert (info["plan_start"], info["counts"]["ingredients"]) == ("2026-10-05", 1)
    for i in (1, 2):
        db.execute("INSERT INTO shopping_list_items (ingredient_name, quantity, unit, checked, is_manual)"
                   f" VALUES ('Item{i}', 1, 'each', 0, 0)")
    db.commit()
    assert tick_many([1, 2, 77], True) == {"updated": 2, "missing": [77]}
    added = add_stock("Mince", 2)
    assert (added["quantity"], added["unit"]) == (2, "kg")
    with pytest.raises(ValueError, match="No ingredient found"):
        add_stock("Nope", 1)


def test_tool_registry_matches_expectation():
    import asyncio
    from mcp_server import server
    names = sorted(t.name for t in asyncio.run(server.mcp.list_tools()))
    assert names == sorted([
        "get_week_plan", "get_shopping_list", "get_pantry_status", "get_spend_summary",
        "get_rules", "search_ingredients", "read_recipe", "plan_add_meal", "plan_swap_meal",
        "plan_remove_meal", "plan_generate", "tick_item", "add_shopping_item", "use_pantry",
        "get_server_info", "add_pantry_stock", "tick_items", "link_spend_product",
        "stage_invoice", "get_import_status",
    ])


def test_mcp_link_product_round_trip(db):
    _seed(db)
    db.execute("INSERT INTO purchases (order_date, basket_id, store, product_name, quantity,"
               " unit_price, line_total) VALUES ('2026-10-01', 'b1', 'Woolworths', 'Mince 500g', 1, 8.5, 8.5)")
    db.commit()
    linked = link_product("Mince 500g", "Mince")
    assert linked["level"] in ("success", "warning")  # no cup price: link holds, price warns
    assert db.execute("SELECT ingredient_id FROM purchases WHERE product_name = 'Mince 500g'").fetchone()[0] == 1
    created = link_product("Mince 500g", "new", new_category="dairy")
    assert "dairy" in created["message"].lower() or "Dairy" in created["message"]
    unlinked = link_product("Mince 500g", "")
    assert unlinked["level"] == "info"
    assert db.execute("SELECT ingredient_id FROM purchases WHERE product_name = 'Mince 500g'").fetchone()[0] is None


def test_mcp_stage_and_status_invoice(db):
    from test_spend_pdf import REVIEW_LINES, _minimal_pdf
    import base64
    raw = _minimal_pdf(REVIEW_LINES)
    staged = stage_invoice_pdf(base64.b64encode(raw).decode())
    assert staged["invoice_number"] == "999001999"
    assert staged["staged_lines"] >= 1
    assert "note" in staged and "UI" in staged["note"]
    status = import_status(staged["token"])
    assert status["read_total"] == staged["read_total"]
    assert all("product_name" in line for line in status["lines"])
    assert import_status("bogus")["error"]
    assert stage_invoice_pdf("!!!not-base64!!!")["error"]
