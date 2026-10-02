"""Security behaviour: setup lockout, safe redirects, CSRF, escaping."""

from conftest import create_user, login


def test_setup_redirects_when_no_account(client):
    resp = client.get("/ingredients")
    assert resp.status_code == 302
    assert "/setup" in resp.headers["Location"]


def test_setup_cannot_reset_existing_admin(client, db):
    create_user(client, "admin", "original-pass")
    resp = client.post("/setup", data={
        "username": "admin", "password": "hijacked-pass", "confirm_password": "hijacked-pass",
    })
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]
    # Original password still works; the attacker's doesn't.
    bad = client.post("/login", data={"username": "admin", "password": "hijacked-pass"})
    assert "/login" in bad.headers["Location"]
    good = client.post("/login", data={"username": "admin", "password": "original-pass"})
    assert good.headers["Location"].endswith("/")


def test_setup_cannot_add_second_user(client, db):
    create_user(client, "admin", "original-pass")
    client.post("/setup", data={"username": "intruder", "password": "12345678", "confirm_password": "12345678"})
    assert db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1


def test_login_ignores_external_next(client):
    create_user(client, "admin", "password123")
    for target in ("https://evil.example/", "//evil.example/", "javascript:alert(1)"):
        resp = client.post(f"/login?next={target}", data={"username": "admin", "password": "password123"})
        assert resp.headers["Location"] == "/", target


def test_login_follows_local_next(client):
    create_user(client, "admin", "password123")
    resp = client.post("/login?next=/recipes", data={"username": "admin", "password": "password123"})
    assert resp.headers["Location"] == "/recipes"


def test_env_admin_created_on_first_request(client, db, monkeypatch):
    monkeypatch.setenv("ADMIN_USERNAME", "envadmin")
    monkeypatch.setenv("ADMIN_PASSWORD", "envpass123")
    resp = client.get("/login")
    assert resp.status_code == 200
    assert db.execute("SELECT username FROM users").fetchone()["username"] == "envadmin"


def test_csrf_enabled_outside_tests():
    from app import create_app
    production_like = create_app(testing=False)
    assert production_like.config["WTF_CSRF_ENABLED"] is True
    c = production_like.test_client()
    resp = c.post("/setup", data={"username": "x", "password": "12345678", "confirm_password": "12345678"})
    assert resp.status_code == 400  # rejected: no CSRF token


def test_ingredient_options_are_json_not_html(client, db):
    login(client)
    db.execute("INSERT INTO ingredients (name, category, unit) VALUES ('<script>x</script>', 'other', 'each')")
    db.commit()
    resp = client.get("/ingredients/options.json")
    assert resp.is_json
    assert resp.get_json()[0]["name"] == "<script>x</script>"


def test_state_changes_reject_get(client, db):
    login(client)
    for path in ("/recipes/delete/1", "/ingredients/delete/1", "/meal-plan/generate",
                 "/shopping-list/toggle/1", "/pantry/delete/1", "/meal-rules/delete/1",
                 "/logout"):
        assert client.get(path).status_code == 405, path


def test_recipe_rejects_non_http_urls(client, db):
    login(client)
    for field in ("source_url", "image_url"):
        resp = client.post("/recipes/add", data={
            "name": "Bad links", "servings": "2", "covers_days": "1",
            field: "javascript:alert(1)",
        })
        assert resp.status_code == 200
        assert db.execute("SELECT COUNT(*) FROM recipes").fetchone()[0] == 0, field


def test_recipe_rejects_negative_times(client, db):
    login(client)
    resp = client.post("/recipes/add", data={
        "name": "Bad times", "servings": "2", "covers_days": "1",
        "prep_time": "-5", "cook_time": "-1",
    })
    assert resp.status_code == 200
    assert db.execute("SELECT COUNT(*) FROM recipes").fetchone()[0] == 0


def test_manual_shopping_item_requires_positive_quantity(client, db):
    login(client)
    for qty in ("0", "-2", "abc"):
        resp = client.post("/shopping-list/add-manual", data={
            "name": "Milk", "quantity": qty, "unit": "each", "category": "dairy",
        })
        assert resp.status_code == 302
    assert db.execute("SELECT COUNT(*) FROM shopping_list_items").fetchone()[0] == 0


def test_meal_plan_generate_rejects_huge_range(client, db):
    login(client)
    db.execute("INSERT INTO meal_rules (day_of_week, meal_type, tag_filter, is_active, sort_order) VALUES ('all', 'dinner', '', 1, 0)")
    db.commit()
    resp = client.post("/meal-plan/generate", data={
        "plan_start": "2026-01-01", "plan_end": "2027-06-01",
    })
    assert resp.status_code == 302
    assert db.execute("SELECT COUNT(*) FROM meal_plan_entries").fetchone()[0] == 0


def test_meal_plan_generate_rejects_inverted_range(client, db):
    login(client)
    resp = client.post("/meal-plan/generate", data={
        "plan_start": "2026-02-01", "plan_end": "2026-01-01",
    })
    assert resp.status_code == 302
    assert db.execute("SELECT COUNT(*) FROM meal_plan_entries").fetchone()[0] == 0


def test_spend_link_forged_product_creates_nothing(client, db):
    login(client)
    resp = client.post("/spend/products/link", data={
        "product_name": "No Such Product 999", "ingredient_id": "new",
    })
    assert resp.status_code == 302
    assert db.execute("SELECT COUNT(*) FROM ingredients").fetchone()[0] == 0


def test_shopping_regenerate_keeps_ticks_per_unit(client, db):
    login(client)
    db.execute("INSERT INTO ingredients (name, category, unit) VALUES ('Flour', 'pantry', 'kg')")
    ing_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.execute("INSERT INTO shopping_list_items (ingredient_id, ingredient_name, quantity, unit, category, checked, is_manual) VALUES (?, 'Flour', 1, 'kg', 'pantry', 1, 0)", (ing_id,))
    db.execute("INSERT INTO shopping_list_items (ingredient_id, ingredient_name, quantity, unit, category, checked, is_manual) VALUES (?, 'Flour', 1, 'each', 'pantry', 0, 0)", (ing_id,))
    db.execute("INSERT INTO recipes (name, servings) VALUES ('R', 1)")
    recipe_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.execute("INSERT INTO recipe_ingredients (recipe_id, ingredient_id, quantity) VALUES (?, ?, 1)", (recipe_id, ing_id))
    db.execute("INSERT INTO meal_plan_entries (date, meal_type, recipe_id, servings, is_auto_generated) VALUES ('2026-10-05', 'dinner', ?, 1, 1)", (recipe_id,))
    db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('plan_start_date', '2026-10-05')")
    db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('plan_end_date', '2026-10-05')")
    db.commit()
    client.post("/shopping-list/generate", data={"plan_start": "2026-10-05", "plan_end": "2026-10-05"})
    rows = db.execute("SELECT unit, checked FROM shopping_list_items WHERE is_manual = 0").fetchall()
    by_unit = {r["unit"]: r["checked"] for r in rows}
    assert by_unit.get("kg") == 1


def test_expired_pantry_stock_is_ignored(client, db):
    from datetime import date
    from services.repository import load_pantry_stock
    login(client)
    db.execute("INSERT INTO ingredients (name, category, unit) VALUES ('Flour', 'pantry', 'kg')")
    ing_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.execute("INSERT INTO pantry_items (ingredient_id, quantity, unit, expiry_date) VALUES (?, 5, 'kg', '2020-01-01')", (ing_id,))
    db.execute("INSERT INTO pantry_items (ingredient_id, quantity, unit, expiry_date) VALUES (?, 2, 'kg', '2099-01-01')", (ing_id,))
    db.commit()
    stock = load_pantry_stock(today=date(2026, 10, 10))
    assert stock[ing_id][0] == 2.0


def test_staging_banner_only_when_app_env_set(client, monkeypatch):
    login(client)
    assert b"env-banner" not in client.get("/").data
    monkeypatch.setenv("APP_ENV", "staging")
    assert b"STAGING" in client.get("/").data
