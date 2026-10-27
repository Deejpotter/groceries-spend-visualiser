"""Integration tests for product roles, purchase patterns, pay-cycle lists and meal suggestions."""

from datetime import timedelta

import pytest

from conftest import login
from models import today as local_today

TODAY = local_today()


def _day(offset):
    return (TODAY + timedelta(days=offset)).isoformat()


def _buy(db, product, offset, basket, ingredient_id=None, total=3.0):
    db.execute(
        "INSERT INTO purchases (order_date, basket_id, product_name, quantity, unit_price, line_total, ingredient_id)"
        " VALUES (?, ?, ?, 1, ?, ?, ?)", (_day(offset), basket, product, total, total, ingredient_id))


@pytest.fixture
def shopper(app, client, db):
    """Logged-in client with six weeks of weekly milk, bread and dishwasher tablets."""
    login(client)
    db.execute("INSERT INTO ingredients (id, name, unit, category, price) VALUES (1, 'Milk', 'L', 'dairy', 1.6)")
    db.execute("INSERT INTO ingredients (id, name, unit, category, price) VALUES (2, 'Mince', 'kg', 'meat', 12)")
    for week in range(6):
        offset = -7 * (6 - week)
        _buy(db, "Woolworths Full Cream Milk 2L", offset, f"b{week}")
        _buy(db, "Helga's Wholemeal Bread", offset, f"b{week}")
        if week % 2 == 0:
            _buy(db, "Finish Dishwasher Tablets 50 Pack", offset, f"b{week}", total=15)
    db.execute("INSERT INTO recipes (id, name, servings) VALUES (1, 'Tacos', 4)")
    db.execute("INSERT INTO recipes (id, name, servings) VALUES (2, 'Curry', 4)")
    db.execute("INSERT INTO recipe_ingredients (recipe_id, ingredient_id, quantity) VALUES (1, 2, 0.5)")
    db.execute("INSERT INTO recipe_ingredients (recipe_id, ingredient_id, quantity) VALUES (2, 2, 0.5)")
    db.execute("INSERT INTO settings (key, value) VALUES ('payday_date', ?)", (_day(7),))
    db.execute("INSERT INTO settings (key, value) VALUES ('pay_frequency', 'fortnightly')")
    db.commit()
    return client


class TestProducts:
    def test_non_food_hidden_from_food_tab(self, shopper):
        page = shopper.get("/spend/products").get_data(as_text=True)
        assert "Helga" in page and "Finish Dishwasher" not in page
        page = shopper.get("/spend/products?show=nonfood").get_data(as_text=True)
        assert "Finish Dishwasher" in page and "Helga" not in page

    def test_set_role_and_ignore(self, shopper, db):
        shopper.post("/spend/products/role", data={"product_name": "Helga's Wholemeal Bread", "role": "treat"},
                     follow_redirects=True)
        row = db.execute("SELECT role, ignored FROM product_decisions").fetchone()
        assert (row["role"], row["ignored"]) == ("treat", 0)
        shopper.post("/spend/products/role", data={"product_name": "Helga's Wholemeal Bread", "ignored": "1"},
                     follow_redirects=True)
        assert db.execute("SELECT ignored, role FROM product_decisions").fetchone()["role"] == "treat"
        assert "Helga" not in shopper.get("/spend/products").get_data(as_text=True)
        assert "Helga" in shopper.get("/spend/products?show=ignored").get_data(as_text=True)

    def test_invalid_role_rejected(self, shopper, db):
        shopper.post("/spend/products/role", data={"product_name": "X", "role": "spaceship"})
        assert db.execute("SELECT COUNT(*) FROM product_decisions").fetchone()[0] == 0

    def test_accept_links_only_high_confidence(self, shopper, db):
        db.execute("INSERT INTO ingredients (id, name, unit) VALUES (3, 'Wholemeal bread', 'each')")
        db.commit()
        shopper.post("/spend/products/accept-suggestions")
        linked = {r["product_name"] for r in db.execute(
            "SELECT DISTINCT product_name FROM purchases WHERE ingredient_id IS NOT NULL")}
        # Bread is a high-confidence match; milk is only medium (0.8) so it waits for review.
        assert linked == {"Helga's Wholemeal Bread"}

    def test_suggest_shows_confidence(self, shopper):
        page = shopper.get("/spend/products?suggest=1").get_data(as_text=True)
        assert "medium confidence suggestion" in page

    def test_ai_review_needs_a_key(self, shopper):
        res = shopper.post("/spend/products/ai-review", follow_redirects=True)
        assert "OpenRouter key" in res.get_data(as_text=True)


class TestPatterns:
    def test_patterns_page_lists_regular_buys(self, shopper):
        page = shopper.get("/spend/patterns").get_data(as_text=True)
        assert "Regular buys" in page and "Helga" in page

    def test_feedback_never(self, shopper, db):
        shopper.post("/spend/patterns/feedback", data={"key": "prod:Helga's Wholemeal Bread", "action": "never"})
        assert db.execute("SELECT action FROM suggestion_feedback").fetchone()["action"] == "never"

    def test_feedback_rejects_bad_key(self, shopper, db):
        shopper.post("/spend/patterns/feedback", data={"key": "drop table", "action": "never"})
        assert db.execute("SELECT COUNT(*) FROM suggestion_feedback").fetchone()[0] == 0


class TestCycleList:
    def test_until_payday_full_list(self, shopper, db):
        db.execute("INSERT INTO meal_plan_entries (date, meal_type, recipe_id, servings) VALUES (?, 'dinner', 1, 4)",
                   (_day(1),))
        db.commit()
        shopper.post("/shopping-list/generate", data={"mode": "full", "range": "payday"})
        rows = {r["ingredient_name"]: r["source"] for r in db.execute("SELECT * FROM shopping_list_items")}
        assert rows["Mince"] == "meal"
        assert rows["Helga's Wholemeal Bread"] == "staple"
        assert rows["Finish Dishwasher Tablets 50 Pack"] == "household"
        page = shopper.get("/shopping-list?show=nonmeal").get_data(as_text=True)
        assert "Helga" in page and "Mince" not in page

    def test_nonmeal_mode_has_no_meal_lines(self, shopper, db):
        db.execute("INSERT INTO meal_plan_entries (date, meal_type, recipe_id, servings) VALUES (?, 'dinner', 1, 4)",
                   (_day(1),))
        db.commit()
        shopper.post("/shopping-list/generate", data={"mode": "nonmeal", "range": "payday"})
        sources = {r["source"] for r in db.execute("SELECT source FROM shopping_list_items")}
        assert "meal" not in sources and "staple" in sources

    def test_payday_needed(self, shopper, db):
        db.execute("DELETE FROM settings WHERE key = 'payday_date'")
        db.commit()
        res = shopper.post("/shopping-list/generate", data={"mode": "full", "range": "payday"},
                           follow_redirects=True)
        assert "Set your payday" in res.get_data(as_text=True)

    def test_never_suggest_removes_and_remembers(self, shopper, db):
        shopper.post("/shopping-list/generate", data={"mode": "staples", "range": "payday"})
        row = db.execute("SELECT id FROM shopping_list_items WHERE ingredient_name LIKE 'Helga%'").fetchone()
        shopper.post(f"/shopping-list/staple/{row['id']}", data={"action": "never"})
        shopper.post("/shopping-list/generate", data={"mode": "staples", "range": "payday"})
        assert db.execute("SELECT COUNT(*) FROM shopping_list_items WHERE ingredient_name LIKE 'Helga%'"
                          ).fetchone()[0] == 0

    def test_deleting_a_staple_twice_stops_it(self, shopper, db):
        for _ in range(2):
            shopper.post("/shopping-list/generate", data={"mode": "staples", "range": "payday"})
            row = db.execute("SELECT id FROM shopping_list_items WHERE ingredient_name LIKE 'Helga%'").fetchone()
            shopper.post(f"/shopping-list/delete/{row['id']}")
        shopper.post("/shopping-list/generate", data={"mode": "staples", "range": "payday"})
        assert db.execute("SELECT COUNT(*) FROM shopping_list_items WHERE ingredient_name LIKE 'Helga%'"
                          ).fetchone()[0] == 0

    def test_legacy_generate_still_meals_only(self, shopper, db):
        db.execute("INSERT INTO settings (key, value) VALUES ('plan_start_date', ?), ('plan_end_date', ?)",
                   (_day(0), _day(6)))
        db.execute("INSERT INTO meal_plan_entries (date, meal_type, recipe_id, servings) VALUES (?, 'dinner', 1, 4)",
                   (_day(1),))
        db.commit()
        shopper.post("/shopping-list/generate")
        names = {r["ingredient_name"] for r in db.execute("SELECT ingredient_name FROM shopping_list_items")}
        assert names == {"Mince"}

    def test_list_page_shows_builder_and_budget(self, shopper, db):
        db.execute("INSERT INTO settings (key, value) VALUES ('cycle_grocery_budget', '10')")
        db.commit()
        shopper.post("/shopping-list/generate", data={"mode": "full", "range": "payday"})
        page = shopper.get("/shopping-list").get_data(as_text=True)
        assert "Until payday" in page and "pay-cycle budget" in page


class TestMealPlan:
    def test_suggest_generation_and_lock(self, shopper, db):
        for weeks in (1, 2, 3):
            db.execute("INSERT INTO meal_plan_entries (date, meal_type, recipe_id, servings) VALUES (?, 'dinner', 2, 4)",
                       (_day(-7 * weeks),))
        db.commit()
        shopper.post("/meal-plan/generate", data={"plan_start": _day(0), "plan_end": _day(6), "suggest": "1"})
        planned = db.execute("SELECT * FROM meal_plan_entries WHERE date >= ?", (_day(0),)).fetchall()
        assert planned, "history should fill the usual dinner slot without any rules"
        entry = planned[0]
        shopper.post(f"/meal-plan/lock/{entry['id']}", data={"locked": "1"})
        assert db.execute("SELECT is_auto_generated FROM meal_plan_entries WHERE id = ?",
                          (entry["id"],)).fetchone()[0] == 0
        page = shopper.get("/meal-plan").get_data(as_text=True)
        assert "Suggestions" in page and "Until payday" in page

    def test_swapping_a_suggested_meal_records_feedback(self, shopper, db):
        cur = db.execute("INSERT INTO meal_plan_entries (date, meal_type, recipe_id, servings, is_auto_generated)"
                         " VALUES (?, 'dinner', 1, 4, 1)", (_day(1),))
        db.commit()
        shopper.post(f"/meal-plan/swap/{cur.lastrowid}", data={"recipe_id": 2})
        row = db.execute("SELECT kind, item_key, action FROM suggestion_feedback").fetchone()
        assert tuple(row) == ("meal", "1", "swapped_out")

    def test_until_payday_plan_range(self, shopper, db):
        shopper.post("/meal-plan/generate", data={"range": "payday", "suggest": "1"})
        cycle_end = db.execute("SELECT value FROM settings WHERE key = 'plan_end_date'").fetchone()[0]
        assert cycle_end == _day(6)


class TestSettings:
    def test_save_payday(self, shopper, db):
        shopper.post("/settings/save", data={"action": "save_budget", "payday_date": "2026-10-01",
                                             "pay_frequency": "weekly", "cycle_grocery_budget": "$350"})
        values = dict(db.execute("SELECT key, value FROM settings WHERE key IN"
                                 " ('payday_date', 'pay_frequency', 'cycle_grocery_budget')").fetchall())
        assert values == {"payday_date": "2026-10-01", "pay_frequency": "weekly", "cycle_grocery_budget": "350.00"}

    def test_bad_payday_rejected(self, shopper):
        res = shopper.post("/settings/save", data={"action": "save_budget", "payday_date": "soon",
                                                   "pay_frequency": "weekly"})
        assert "Enter payday as a date" in res.get_data(as_text=True)


@pytest.fixture
def api(app, shopper):
    from database import get_db, set_setting
    with app.app_context():
        set_setting("mcp_api_key", "k-123")
        get_db().commit()
    client = app.test_client()
    client.environ_base["HTTP_AUTHORIZATION"] = "Bearer k-123"
    return client


class TestApi:
    def test_pay_cycle(self, api):
        cycle = api.get("/api/v1/pay-cycle").get_json()["pay_cycle"]
        assert cycle["next_payday"] == _day(7)

    def test_patterns(self, api):
        patterns = api.get("/api/v1/patterns?rhythm=regular").get_json()["patterns"]
        assert any(p["label"] == "Helga's Wholemeal Bread" for p in patterns)

    def test_generate_preview_then_confirm(self, api, db):
        body = api.post("/api/v1/shopping-list/generate", json={"mode": "nonmeal"}).get_json()
        assert body["confirmed"] is False and body["lines"]
        assert db.execute("SELECT COUNT(*) FROM shopping_list_items").fetchone()[0] == 0
        body = api.post("/api/v1/shopping-list/generate", json={"mode": "nonmeal", "confirm": True}).get_json()
        assert body["saved"] == len(body["lines"])
        items = api.get("/api/v1/shopping-list").get_json()["groups"]
        assert any(i["source"] == "staple" for group in items.values() for i in group)

    def test_generate_rejects_bad_mode(self, api):
        assert api.post("/api/v1/shopping-list/generate", json={"mode": "x"}).status_code == 400

    def test_plan_suggestions(self, api):
        body = api.get(f"/api/v1/plan/suggestions?date={_day(1)}&meal_type=dinner").get_json()
        assert {s["recipe"] for s in body["suggestions"]} == {"Tacos", "Curry"}
        assert api.get("/api/v1/plan/suggestions?date=bad").status_code == 400
