"""Integration tests for Grocery Visualiser — full workflow via Flask test client.

These tests exercise the actual routes: login, add ingredient, add recipe,
create rules, set dates, generate plan, view shopping list.
"""

from conftest import create_user as _create_user, login as _login


# ============================================================================
# Authentication
# ============================================================================

class TestAuthentication:
    """Test login, logout, and protected route access."""

    def test_login_page_loads(self, client):
        """GET /login returns the login form once an account exists."""
        _create_user(client, "someone", "test12345")
        resp = client.get("/login")
        assert resp.status_code == 200
        assert b"Username" in resp.data
        assert b"Password" in resp.data
        assert b"Show password" in resp.data
        assert b"Need to set up an account?" not in resp.data

    def test_dashboard_more_link_and_checklist(self, client, db):
        """Capped lists link onward; checklist stays until dates exist (B5)."""
        _login(client, "dashuser", "test123")
        for i in range(9):
            db.execute("INSERT INTO shopping_list_items (ingredient_name, quantity, unit, category, checked, is_manual)"
                       f" VALUES ('Item{i}', 1, 'each', 'other', 0, 1)")
        db.commit()
        client.post("/meal-rules/add", data={
            "day_of_week": "mon",
            "meal_type": "dinner",
            "tag_filter": "",
            "is_active": "1",
        }, follow_redirects=True)
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"+1 more" in resp.data
        assert b"Getting started" in resp.data

    def test_setup_page_loads(self, client):
        """GET /setup returns the setup page."""
        resp = client.get("/setup")
        assert resp.status_code == 200
        assert b"Create Admin Account" in resp.data

    def test_unauthenticated_access_redirects(self, client):
        """Protected routes redirect to login when not authenticated."""
        _create_user(client, "someone", "test12345")
        for path in ["/", "/ingredients", "/recipes", "/meal-rules"]:
            resp = client.get(path)
            assert resp.status_code == 302
            assert "/login" in resp.headers["Location"]

    def test_login_with_correct_credentials(self, client, db):
        """POST /login with correct credentials sets session and redirects."""
        # First create admin via setup
        resp = client.post("/setup", data={
            "username": "testadmin",
            "password": "testpass123",
            "confirm_password": "testpass123",
        }, follow_redirects=True)
        assert resp.status_code == 200

        # Login
        resp = client.post("/login", data={
            "username": "testadmin",
            "password": "testpass123",
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b"Dashboard" in resp.data or b"Quick Actions" in resp.data

    def test_login_wrong_password(self, client):
        """Login with wrong password re-shows the form with the username kept."""
        _create_user(client, "testadmin", "testpass123")
        resp = client.post("/login", data={
            "username": "testadmin",
            "password": "wrongpassword",
        })
        assert resp.status_code == 200
        assert b"Invalid username or password." in resp.data
        assert b'value="testadmin"' in resp.data

    def test_setup_error_preserves_username(self, client, db):
        """Setup errors re-show the form with the username kept (B1b)."""
        resp = client.post("/setup", data={
            "username": "newadmin",
            "password": "testpass123",
            "confirm_password": "different123",
        })
        assert resp.status_code == 200
        assert b"Passwords do not match." in resp.data
        assert b'value="newadmin"' in resp.data
        assert db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0

    def test_logout_clears_session(self, client):
        """POST /logout clears the session."""
        # Login first
        client.post("/setup", data={
            "username": "logoutuser",
            "password": "test123",
            "confirm_password": "test123",
        })
        client.post("/login", data={
            "username": "logoutuser",
            "password": "test123",
        })

        # Logout
        resp = client.post("/logout", follow_redirects=True)
        assert resp.status_code == 200
        # After logout, dashboard should redirect to login
        resp = client.get("/")
        assert resp.status_code == 302


# ============================================================================
# Ingredient CRUD
# ============================================================================

class TestIngredients:
    """Test ingredient management routes."""

    def test_ingredient_list_page_loads(self, client):
        """Authenticated user can view the ingredient list."""
        _login(client, "inguser", "test123")
        resp = client.get("/ingredients")
        assert resp.status_code == 200
        assert b"Ingredients" in resp.data

    def test_add_ingredient(self, client, db):
        """POST /ingredients/add creates a new ingredient."""
        _login(client, "inguser", "test123")

        resp = client.post("/ingredients/add", data={
            "name": "Chicken Breast",
            "category": "meat",
            "unit": "g",
            "price": "12.50",
            "url": "https://example.com/chicken",
            "store": "Woolworths",
            "minimum_stock": "500",
        }, follow_redirects=True)
        assert resp.status_code == 200

        # Verify in DB
        row = db.execute("SELECT * FROM ingredients WHERE name = ?", ("Chicken Breast",)).fetchone()
        assert row is not None
        assert row["category"] == "meat"
        assert row["price"] == 12.50

    def test_ingredient_error_shows_inline_and_preserves(self, client, db):
        """Ingredient errors render inline with submitted values kept (B1a)."""
        _login(client, "inguser", "test123")
        resp = client.post("/ingredients/add", data={
            "name": "",
            "category": "meat",
            "unit": "g",
            "store": "Woolies",
        })
        assert resp.status_code == 200
        assert b"Name is required." in resp.data
        assert b"Woolies" in resp.data
        assert b"Add Ingredient" in resp.data
        assert db.execute("SELECT COUNT(*) FROM ingredients").fetchone()[0] == 0

    def test_add_ingredient_with_url_and_store(self, client, db):
        """Ingredient can have URL and store fields."""
        _login(client, "inguser", "test123")
        client.post("/ingredients/add", data={
            "name": "Organic Spinach",
            "category": "produce",
            "unit": "bunch",
            "price": "4.99",
            "url": "https://coles.com.au/organic-spinach",
            "store": "Coles",
            "minimum_stock": "2",
        }, follow_redirects=True)

        row = db.execute("SELECT * FROM ingredients WHERE name = ?", ("Organic Spinach",)).fetchone()
        assert row["url"] == "https://coles.com.au/organic-spinach"
        assert row["store"] == "Coles"

    def test_edit_ingredient(self, client, db):
        """POST /ingredients/edit/<id> updates an existing ingredient."""
        _login(client, "inguser", "test123")

        # Create ingredient first
        client.post("/ingredients/add", data={
            "name": "Old Name Ingredient",
            "category": "pantry",
            "unit": "pack",
            "price": "5.00",
        }, follow_redirects=True)

        row = db.execute("SELECT id FROM ingredients WHERE name = ?", ("Old Name Ingredient",)).fetchone()
        ingredient_id = row["id"]

        # Edit
        resp = client.post(f"/ingredients/edit/{ingredient_id}", data={
            "name": "Renamed Ingredient",
            "category": "pantry",
            "unit": "pack",
            "price": "7.50",
        }, follow_redirects=True)
        assert resp.status_code == 200

        updated = db.execute("SELECT * FROM ingredients WHERE id = ?", (ingredient_id,)).fetchone()
        assert updated["name"] == "Renamed Ingredient"
        assert updated["price"] == 7.50

    def test_delete_ingredient(self, client, db):
        """POST /ingredients/delete/<id> removes the ingredient."""
        _login(client, "inguser", "test123")

        # Create
        client.post("/ingredients/add", data={
            "name": "To Delete",
            "category": "other",
            "unit": "each",
            "price": "1.00",
        }, follow_redirects=True)

        row = db.execute("SELECT id FROM ingredients WHERE name = ?", ("To Delete",)).fetchone()
        ingredient_id = row["id"]

        # Delete
        resp = client.post(f"/ingredients/delete/{ingredient_id}", follow_redirects=True)
        assert resp.status_code == 200

        deleted = db.execute("SELECT * FROM ingredients WHERE id = ?", (ingredient_id,)).fetchone()
        assert deleted is None

    def test_ingredient_list_after_add(self, client, db):
        """After adding, the list page shows the new ingredient."""
        _login(client, "inguser", "test123")

        client.post("/ingredients/add", data={
            "name": "Fresh Lemon",
            "category": "produce",
            "unit": "each",
            "price": "0.99",
        }, follow_redirects=True)

        resp = client.get("/ingredients")
        assert resp.status_code == 200
        assert b"Fresh Lemon" in resp.data


# ============================================================================
# Recipe CRUD
# ============================================================================

class TestRecipes:
    """Test recipe management routes."""

    def test_recipe_list_page_loads(self, client):
        """Authenticated user can view the recipe list."""
        _login(client, "recuser", "test123")
        resp = client.get("/recipes")
        assert resp.status_code == 200
        assert b"Recipes" in resp.data

    def test_add_recipe_without_ingredients(self, client, db):
        """POST /recipes/add creates a recipe without linked ingredients."""
        _login(client, "recuser", "test123")

        resp = client.post("/recipes/add", data={
            "name": "Simple Omelette",
            "description": "Three egg omelette with cheese",
            "servings": "1",
            "prep_time": "5",
            "cook_time": "8",
            "source_url": "https://example.com/omelette",
            "covers_days": "1",  # single-day recipe
            "tags": "breakfast, quick",
        }, follow_redirects=True)
        assert resp.status_code == 200

        row = db.execute("SELECT * FROM recipes WHERE name = ?", ("Simple Omelette",)).fetchone()
        assert row is not None
        assert row["tags"] == "breakfast, quick"
        assert row["covers_days"] == 1
        assert row["servings"] == 1
        assert row["source_url"] == "https://example.com/omelette"

    def test_add_recipe_covering_multiple_days(self, client, db):
        """POST /recipes/add saves different multi-day coverage durations."""
        _login(client, "recuser", "test123")

        for name, covers_days in (("Bolognese Sauce", 3), ("Batch Soup", 2)):
            resp = client.post("/recipes/add", data={
                "name": name,
                "description": "Prepared ahead for multiple days",
                "servings": "6",
                "covers_days": str(covers_days),
                "tags": "dinner, batch-cook",
            }, follow_redirects=True)
            assert resp.status_code == 200

            row = db.execute(
                "SELECT covers_days, tags FROM recipes WHERE name = ?", (name,)
            ).fetchone()
            assert row is not None
            assert row["covers_days"] == covers_days
            assert row["tags"] == "dinner, batch-cook"

    def test_add_recipe_with_ingredient_link(self, client, db):
        """POST /recipes/add with ingredient rows links ingredients to the recipe."""
        _login(client, "recuser", "test123")

        # Create an ingredient first
        client.post("/ingredients/add", data={
            "name": "Pasta",
            "category": "pantry",
            "unit": "g",
            "price": "0.015",
        }, follow_redirects=True)

        ingredient_id = db.execute(
            "SELECT id FROM ingredients WHERE name = ?", ("Pasta",)
        ).fetchone()["id"]

        # Add recipe with ingredient link
        resp = client.post("/recipes/add", data={
            "name": "Pasta Aglio e Olio",
            "description": "Simple garlic pasta",
            "servings": "2",
            "covers_days": "1",
            "tags": "dinner, italian",
            "ingredient_ids": str(ingredient_id),
            "quantities": "200",
            "unit_overrides": "g",
        }, follow_redirects=True)
        assert resp.status_code == 200

        # Check recipe_ingredients table
        ri_rows = db.execute(
            "SELECT * FROM recipe_ingredients WHERE recipe_id = (SELECT id FROM recipes WHERE name = ?)",
            ("Pasta Aglio e Olio",),
        ).fetchall()
        assert len(ri_rows) == 1
        assert ri_rows[0]["ingredient_id"] == ingredient_id
        assert ri_rows[0]["quantity"] == 200
        assert ri_rows[0]["unit_override"] == "g"

    def test_zero_quantity_row_warns_but_saves_rest(self, client, db):
        """A zero-quantity row warns instead of vanishing silently (B2)."""
        _login(client, "recuser", "test123")
        client.post("/ingredients/add", data={
            "name": "Pasta", "category": "pantry", "unit": "g",
        }, follow_redirects=True)
        client.post("/ingredients/add", data={
            "name": "Salt", "category": "pantry", "unit": "g",
        }, follow_redirects=True)
        resp = client.post("/recipes/add", data={
            "name": "Salted Pasta",
            "servings": "2",
            "ingredient_ids": ["1", "2"],
            "quantities": ["200", "0"],
            "unit_overrides": ["g", "g"],
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b"with no quantity was skipped" in resp.data
        ri_rows = db.execute(
            "SELECT * FROM recipe_ingredients WHERE recipe_id = (SELECT id FROM recipes WHERE name = ?)",
            ("Salted Pasta",),
        ).fetchall()
        assert len(ri_rows) == 1
        assert ri_rows[0]["quantity"] == 200

    def test_duplicate_ingredient_row_errors(self, client, db):
        """The same ingredient twice is an error, not a silent double (B2)."""
        _login(client, "recuser", "test123")
        client.post("/ingredients/add", data={
            "name": "Pasta", "category": "pantry", "unit": "g",
        }, follow_redirects=True)
        resp = client.post("/recipes/add", data={
            "name": "Double Pasta",
            "servings": "2",
            "ingredient_ids": ["1", "1"],
            "quantities": ["200", "100"],
            "unit_overrides": ["g", "g"],
        })
        assert resp.status_code == 200
        assert b"listed twice" in resp.data
        assert db.execute("SELECT COUNT(*) FROM recipes").fetchone()[0] == 0

    def test_picker_lists_ingredients_without_javascript(self, client, db):
        """The ingredient picker is fully server-rendered (B3)."""
        _login(client, "recuser", "test123")
        client.post("/ingredients/add", data={
            "name": "Pasta", "category": "pantry", "unit": "g",
        }, follow_redirects=True)
        resp = client.get("/recipes/add")
        assert resp.status_code == 200
        assert b'<option value="1"' in resp.data
        assert b"Pasta (g)" in resp.data

    def test_recipe_detail_page(self, client, db):
        """GET /recipes/<id> shows recipe detail with ingredients."""
        _login(client, "recuser", "test123")

        # Create ingredient
        client.post("/ingredients/add", data={
            "name": "Basil",
            "category": "produce",
            "unit": "bunch",
            "price": "3.50",
        }, follow_redirects=True)
        ing_id = db.execute("SELECT id FROM ingredients WHERE name = ?", ("Basil",)).fetchone()["id"]

        # Create recipe
        client.post("/recipes/add", data={
            "name": "Pesto Pasta",
            "description": "Fresh basil pesto",
            "servings": "4",
            "tags": "dinner",
            "ingredient_ids": str(ing_id),
            "quantities": "1",
            "unit_overrides": "bunch",
        }, follow_redirects=True)

        recipe_id = db.execute("SELECT id FROM recipes WHERE name = ?", ("Pesto Pasta",)).fetchone()["id"]

        resp = client.get(f"/recipes/{recipe_id}")
        assert resp.status_code == 200
        assert b"Pesto Pasta" in resp.data
        assert b"Basil" in resp.data

    def test_edit_recipe(self, client, db):
        """POST /recipes/edit/<id> updates a recipe."""
        _login(client, "recuser", "test123")

        client.post("/recipes/add", data={
            "name": "Editable Recipe",
            "description": "Original description",
            "servings": "2",
            "tags": "lunch",
        }, follow_redirects=True)

        recipe_id = db.execute(
            "SELECT id FROM recipes WHERE name = ?", ("Editable Recipe",)
        ).fetchone()["id"]

        resp = client.post(f"/recipes/edit/{recipe_id}", data={
            "name": "Updated Recipe Name",
            "description": "Updated description",
            "servings": "3",
            "tags": "dinner, updated",
        }, follow_redirects=True)
        assert resp.status_code == 200

        updated = db.execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
        assert updated["name"] == "Updated Recipe Name"
        assert updated["servings"] == 3
        assert updated["tags"] == "dinner, updated"

    def test_delete_recipe(self, client, db):
        """POST /recipes/delete/<id> removes a recipe."""
        _login(client, "recuser", "test123")

        client.post("/recipes/add", data={
            "name": "To Delete Recipe",
            "description": "Will be deleted",
            "servings": "1",
        }, follow_redirects=True)

        recipe_id = db.execute(
            "SELECT id FROM recipes WHERE name = ?", ("To Delete Recipe",)
        ).fetchone()["id"]

        resp = client.post(f"/recipes/delete/{recipe_id}", follow_redirects=True)
        assert resp.status_code == 200

        deleted = db.execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
        assert deleted is None


# ============================================================================
# Meal Rules
# ============================================================================

class TestMealRules:
    """Test meal rule management routes."""

    def test_meal_rule_list_page_loads(self, client):
        """Authenticated user can view meal rules on the meal plan page."""
        _login(client, "ruleuser", "test123")
        resp = client.get("/meal-plan?tab=rules")
        assert resp.status_code == 200
        assert b"How rules work" in resp.data
        assert b"How rules work" not in client.get("/meal-plan").data

    def test_add_meal_rule(self, client, db):
        """POST /meal-rules/add creates a new meal rule."""
        _login(client, "ruleuser", "test123")

        resp = client.post("/meal-rules/add", data={
            "day_of_week": "mon",
            "meal_type": "dinner",
            "tag_filter": "",
            "servings_override": "",
            "is_active": "1",
            "sort_order": "0",
        }, follow_redirects=True)
        assert resp.status_code == 200

        row = db.execute("SELECT * FROM meal_rules WHERE day_of_week = ? AND meal_type = ?",
                         ("mon", "dinner")).fetchone()
        assert row is not None
        assert row["is_active"] == 1
        assert row["sort_order"] == 0

    def test_add_meal_rule_with_tag_filter(self, client, db):
        """Meal rule can have a tag filter."""
        _login(client, "ruleuser", "test123")

        client.post("/meal-rules/add", data={
            "day_of_week": "fri",
            "meal_type": "lunch",
            "tag_filter": "quick",
            "servings_override": "2",
            "is_active": "1",
            "sort_order": "1",
        }, follow_redirects=True)

        row = db.execute(
            "SELECT * FROM meal_rules WHERE day_of_week = ? AND meal_type = ?",
            ("fri", "lunch"),
        ).fetchone()
        assert row["tag_filter"] == "quick"
        assert row["servings_override"] == 2

    def test_rule_error_preserves_input(self, client, db):
        """Rule errors re-show the rules tab with typed values kept (B1b)."""
        _login(client, "ruleuser", "test123")
        resp = client.post("/meal-rules/add", data={
            "day_of_week": "",
            "meal_type": "lunch",
            "tag_filter": "quick",
            "is_active": "1",
        })
        assert resp.status_code == 200
        assert b"Choose which day(s) the rule applies to." in resp.data
        assert b"quick" in resp.data
        assert b"Add Rule" in resp.data
        assert db.execute("SELECT COUNT(*) FROM meal_rules").fetchone()[0] == 0

    def test_add_meal_rule_weekday(self, client, db):
        """Meal rule with day_of_week='weekday' matches Mon-Fri."""
        _login(client, "ruleuser", "test123")

        client.post("/meal-rules/add", data={
            "day_of_week": "weekday",
            "meal_type": "breakfast",
            "tag_filter": "",
            "is_active": "1",
        }, follow_redirects=True)

        row = db.execute(
            "SELECT * FROM meal_rules WHERE day_of_week = ?", ("weekday",)
        ).fetchone()
        assert row is not None

    def test_edit_meal_rule(self, client, db):
        """POST /meal-rules/edit/<id> updates a rule."""
        _login(client, "ruleuser", "test123")

        client.post("/meal-rules/add", data={
            "day_of_week": "tue",
            "meal_type": "dinner",
            "tag_filter": "pasta",
            "is_active": "1",
        }, follow_redirects=True)

        rule_id = db.execute(
            "SELECT id FROM meal_rules WHERE day_of_week = ? AND meal_type = ?",
            ("tue", "dinner"),
        ).fetchone()["id"]

        resp = client.post(f"/meal-rules/edit/{rule_id}", data={
            "day_of_week": "tue",
            "meal_type": "dinner",
            "tag_filter": "italian",
            "servings_override": "4",
            "is_active": "1",
        }, follow_redirects=True)
        assert resp.status_code == 200

        updated = db.execute("SELECT * FROM meal_rules WHERE id = ?", (rule_id,)).fetchone()
        assert updated["tag_filter"] == "italian"
        assert updated["servings_override"] == 4

    def test_delete_meal_rule(self, client, db):
        """POST /meal-rules/delete/<id> removes a rule."""
        _login(client, "ruleuser", "test123")

        client.post("/meal-rules/add", data={
            "day_of_week": "sat",
            "meal_type": "breakfast",
            "tag_filter": "",
            "is_active": "1",
        }, follow_redirects=True)

        rule_id = db.execute(
            "SELECT id FROM meal_rules WHERE day_of_week = ? AND meal_type = ?",
            ("sat", "breakfast"),
        ).fetchone()["id"]

        resp = client.post(f"/meal-rules/delete/{rule_id}", follow_redirects=True)
        assert resp.status_code == 200

        deleted = db.execute("SELECT * FROM meal_rules WHERE id = ?", (rule_id,)).fetchone()
        assert deleted is None

    def test_rule_move_reorders(self, client, db):
        """Up/down buttons reorder rules in evaluation order (A4)."""
        _login(client, "ruleuser", "test123")
        for day in ("mon", "tue", "wed"):
            client.post("/meal-rules/add", data={
                "day_of_week": day,
                "meal_type": "dinner",
                "tag_filter": "",
                "is_active": "1",
            }, follow_redirects=True)
        ids = [r["id"] for r in db.execute("SELECT id FROM meal_rules ORDER BY sort_order, id")]
        assert len(ids) == 3
        client.post(f"/meal-rules/move/{ids[1]}/up", follow_redirects=True)
        assert [r["id"] for r in db.execute("SELECT id FROM meal_rules ORDER BY sort_order, id")] == [ids[1], ids[0], ids[2]]
        # Boundary: moving the first rule up is a harmless no-op.
        assert client.post(f"/meal-rules/move/{ids[1]}/up").status_code == 302
        assert [r["id"] for r in db.execute("SELECT id FROM meal_rules ORDER BY sort_order, id")] == [ids[1], ids[0], ids[2]]

    def test_rule_add_accepts_day_checkboxes(self, client, db):
        """Day checkboxes post natively without the hidden field (A5)."""
        _login(client, "ruleuser", "test123")
        client.post("/meal-rules/add", data={
            "meal_type": "dinner",
            "tag_filter": "",
            "is_active": "1",
        }, follow_redirects=True)
        assert db.execute("SELECT COUNT(*) FROM meal_rules").fetchone()[0] == 0
        resp = client.post("/meal-rules/add", data={
            "day_toggle": ["mon", "wed"],
            "meal_type": "dinner",
            "tag_filter": "",
            "is_active": "1",
        }, follow_redirects=True)
        assert resp.status_code == 200
        row = db.execute("SELECT * FROM meal_rules").fetchone()
        assert row["day_of_week"] == "mon,wed"


# ============================================================================
# Meal Plan Generation
# ============================================================================

class TestMealPlanGeneration:
    """Test meal plan generation and viewing."""

    def test_meal_plan_page_requires_dates(self, client):
        """Meal plan page shows message when no dates set."""
        _login(client, "planuser", "test123")
        resp = client.get("/meal-plan")
        assert resp.status_code == 200
        assert b"No plan dates set" in resp.data

    def test_generate_plan_without_rules_shows_empty(self, client, db):
        """Generating a plan with no rules produces no entries."""
        _login(client, "planuser", "test123")

        # Set dates
        client.post("/settings/save", data={
            "action": "save_dates",
            "plan_start": "2026-10-05",
            "plan_end": "2026-10-09",
            "unit_preference": "metric",
            "default_servings": "4",
        }, follow_redirects=True)

        # Generate (no rules exist)
        client.post("/meal-plan/generate", data={
            "plan_start": "2026-10-05",
            "plan_end": "2026-10-09",
        }, follow_redirects=True)

        # View plan — should have no entries
        resp = client.get("/meal-plan")
        assert resp.status_code == 200
        # No entries because no rules
        assert b"Nothing planned yet" in resp.data
        assert db.execute("SELECT COUNT(*) FROM meal_plan_entries").fetchone()[0] == 0

    def test_hidden_meal_types_offer_add_picker(self, client, db):
        """Dinner-only setup still allows adding lunch by hand (A1)."""
        _login(client, "planuser", "test123")
        client.post("/recipes/add", data={
            "name": "Toast",
            "servings": "2",
            "tags": "breakfast",
        }, follow_redirects=True)
        client.post("/meal-rules/add", data={
            "day_of_week": "weekday",
            "meal_type": "dinner",
            "tag_filter": "",
            "servings_override": "",
            "is_active": "1",
            "sort_order": "0",
        }, follow_redirects=True)
        client.post("/settings/save", data={
            "action": "save_dates",
            "plan_start": "2026-10-05",
            "plan_end": "2026-10-09",
            "unit_preference": "metric",
            "default_servings": "2",
        }, follow_redirects=True)
        resp = client.get("/meal-plan")
        assert resp.status_code == 200
        assert b'value="lunch"' in resp.data
        client.post("/meal-plan/add", data={
            "date": "2026-10-05",
            "meal_type": "lunch",
            "recipe_id": "1",
        }, follow_redirects=True)
        assert db.execute("SELECT COUNT(*) FROM meal_plan_entries WHERE meal_type = 'lunch'").fetchone()[0] == 1
        resp = client.get("/meal-plan")
        assert b"Lunch" in resp.data

    def test_add_meal_accepts_servings(self, client, db):
        """Manual adds can set servings like swaps (A8)."""
        _login(client, "planuser", "test123")
        client.post("/recipes/add", data={
            "name": "Toast",
            "servings": "2",
        }, follow_redirects=True)
        client.post("/meal-plan/add", data={
            "date": "2026-10-05",
            "meal_type": "lunch",
            "recipe_id": "1",
            "servings": "6",
        }, follow_redirects=True)
        assert db.execute("SELECT servings FROM meal_plan_entries").fetchone()[0] == 6
        client.post("/meal-plan/add", data={
            "date": "2026-10-06",
            "meal_type": "lunch",
            "recipe_id": "1",
        }, follow_redirects=True)
        assert db.execute("SELECT servings FROM meal_plan_entries WHERE date = '2026-10-06'").fetchone()[0] == 2

    def test_plan_tabs_are_plain_links(self, client, db):
        """Plan tabs don't fake a tablist pattern (D)."""
        _login(client, "planuser", "test123")
        assert b'role="tablist"' not in client.get("/meal-plan").data

    def test_swap_servings_label_names_slot(self, client, db):
        """Swap servings inputs name their day and meal (D)."""
        _login(client, "planuser", "test123")
        client.post("/recipes/add", data={"name": "Toast", "servings": "2"}, follow_redirects=True)
        client.post("/meal-plan/add", data={
            "date": "2026-10-05", "meal_type": "dinner", "recipe_id": "1",
        }, follow_redirects=True)
        client.post("/settings/save", data={
            "action": "save_dates", "plan_start": "2026-10-05", "plan_end": "2026-10-09",
            "unit_preference": "metric", "default_servings": "2",
        }, follow_redirects=True)
        assert b"Servings for " in client.get("/meal-plan").data

    def test_generate_inline_dates_update_range(self, client, db):
        """Posting new dates to generate re-ranges the plan (A2)."""
        _login(client, "planuser", "test123")
        client.post("/settings/save", data={
            "action": "save_dates",
            "plan_start": "2026-10-05",
            "plan_end": "2026-10-09",
            "unit_preference": "metric",
            "default_servings": "2",
        }, follow_redirects=True)
        client.post("/meal-plan/generate", data={
            "plan_start": "2026-10-13",
            "plan_end": "2026-10-17",
        }, follow_redirects=True)
        assert db.execute("SELECT value FROM settings WHERE key = 'plan_start_date'").fetchone()[0] == "2026-10-13"
        assert db.execute("SELECT value FROM settings WHERE key = 'plan_end_date'").fetchone()[0] == "2026-10-17"
        resp = client.get("/meal-plan")
        assert b'name="plan_start"' in resp.data
        assert b'value="2026-10-13"' in resp.data

    def test_regenerate_summary_counts(self, client, db):
        """Plan page states what regenerating will replace and keep (A3)."""
        _login(client, "planuser", "test123")
        client.post("/recipes/add", data={
            "name": "Toast",
            "servings": "2",
            "tags": "breakfast",
        }, follow_redirects=True)
        client.post("/meal-rules/add", data={
            "day_of_week": "weekday",
            "meal_type": "dinner",
            "tag_filter": "",
            "servings_override": "",
            "is_active": "1",
            "sort_order": "0",
        }, follow_redirects=True)
        client.post("/settings/save", data={
            "action": "save_dates",
            "plan_start": "2026-10-05",
            "plan_end": "2026-10-09",
            "unit_preference": "metric",
            "default_servings": "2",
        }, follow_redirects=True)
        client.post("/meal-plan/generate", data={
            "plan_start": "2026-10-05",
            "plan_end": "2026-10-09",
        }, follow_redirects=True)
        client.post("/meal-plan/add", data={
            "date": "2026-10-05",
            "meal_type": "lunch",
            "recipe_id": "1",
        }, follow_redirects=True)
        resp = client.get("/meal-plan")
        assert b"Regenerating replaces" in resp.data
        assert b"manual meal" in resp.data

    def test_generate_plan_with_rules_and_recipes(self, client, db):
        """Full workflow: add ingredient → recipe → rule → set dates → generate plan."""
        _login(client, "planuser", "test123")

        # 1. Add ingredient
        client.post("/ingredients/add", data={
            "name": "Spaghetti",
            "category": "pantry",
            "unit": "g",
            "price": "0.012",
        }, follow_redirects=True)

        # 2. Add recipe with ingredient link
        client.post("/recipes/add", data={
            "name": "Spaghetti Bolognese",
            "description": "Classic spaghetti",
            "servings": "4",
            "covers_days": "1",
            "tags": "dinner, italian",
            "ingredient_ids": "1",
            "quantities": "400",
            "unit_overrides": "g",
        }, follow_redirects=True)

        # 3. Add another recipe
        client.post("/recipes/add", data={
            "name": "Chicken Stir Fry",
            "description": "Quick stir fry",
            "servings": "4",
            "tags": "dinner, asian",
        }, follow_redirects=True)

        # 4. Add meal rule: weekday dinners, any recipe
        client.post("/meal-rules/add", data={
            "day_of_week": "weekday",
            "meal_type": "dinner",
            "tag_filter": "",
            "servings_override": "",
            "is_active": "1",
            "sort_order": "0",
        }, follow_redirects=True)

        # 5. Set plan dates
        client.post("/settings/save", data={
            "action": "save_dates",
            "plan_start": "2026-10-05",
            "plan_end": "2026-10-09",
            "unit_preference": "metric",
            "default_servings": "4",
        }, follow_redirects=True)

        # 6. Generate plan
        resp = client.post("/meal-plan/generate", data={
            "plan_start": "2026-10-05",
            "plan_end": "2026-10-09",
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b"generated" in resp.data.lower() or b"Meal Plan" in resp.data

        # 7. Verify entries in DB
        entry_count = db.execute("SELECT COUNT(*) as cnt FROM meal_plan_entries").fetchone()["cnt"]
        assert entry_count > 0, "Expected meal plan entries to be created"

        # 8. View plan page
        resp = client.get("/meal-plan")
        assert resp.status_code == 200
        assert b"Spaghetti Bolognese" in resp.data or b"Chicken Stir Fry" in resp.data

    def test_generate_plan_respects_manual_override(self, client, db):
        """Generated plan does not overwrite manually created entries."""
        _login(client, "planuser", "test123")

        # Add ingredient and recipe
        client.post("/ingredients/add", data={
            "name": "Rice",
            "category": "pantry",
            "unit": "g",
            "price": "0.008",
        }, follow_redirects=True)

        client.post("/recipes/add", data={
            "name": "Fried Rice",
            "description": "Wok-fried rice",
            "servings": "4",
            "tags": "dinner",
        }, follow_redirects=True)

        # Add rule
        client.post("/meal-rules/add", data={
            "day_of_week": "mon",
            "meal_type": "dinner",
            "tag_filter": "",
            "is_active": "1",
        }, follow_redirects=True)

        # Set dates
        client.post("/settings/save", data={
            "action": "save_dates",
            "plan_start": "2026-10-05",
            "plan_end": "2026-10-09",
            "unit_preference": "metric",
            "default_servings": "4",
        }, follow_redirects=True)

        # Create a manual entry (is_auto_generated=0)
        db.execute(
            "INSERT INTO meal_plan_entries (date, meal_type, recipe_id, servings, is_auto_generated, source_rule_id) VALUES (?, ?, ?, ?, 0, NULL)",
            ("2026-10-05", "dinner", 1, 2),
        )
        db.commit()

        # Generate plan
        client.post("/meal-plan/generate", data={
            "plan_start": "2026-10-05",
            "plan_end": "2026-10-09",
        }, follow_redirects=True)

        # The manual entry should still exist and not be overwritten
        monday_entry = db.execute(
            "SELECT * FROM meal_plan_entries WHERE date = ? AND meal_type = ?",
            ("2026-10-05", "dinner"),
        ).fetchone()
        assert monday_entry is not None
        assert monday_entry["is_auto_generated"] == 0, (
            f"Manual entry was overwritten: is_auto_generated={monday_entry['is_auto_generated']}"
        )

    def test_meal_plan_swap_entry(self, client, db):
        """POST /meal-plan/swap/<id> changes the recipe for a plan entry."""
        _login(client, "planuser", "test123")

        # Set up data
        client.post("/ingredients/add", data={
            "name": "Ingredient A",
            "category": "produce",
            "unit": "each",
            "price": "1.00",
        }, follow_redirects=True)

        client.post("/recipes/add", data={
            "name": "Recipe A",
            "description": "First recipe",
            "servings": "4",
            "tags": "dinner",
        }, follow_redirects=True)

        client.post("/recipes/add", data={
            "name": "Recipe B",
            "description": "Second recipe",
            "servings": "4",
            "tags": "dinner",
        }, follow_redirects=True)

        # Add a meal rule so generation creates entries
        client.post("/meal-rules/add", data={
            "day_of_week": "all",
            "meal_type": "dinner",
            "tag_filter": "",
            "is_active": "1",
        }, follow_redirects=True)

        client.post("/settings/save", data={
            "action": "save_dates",
            "plan_start": "2026-10-05",
            "plan_end": "2026-10-09",
            "unit_preference": "metric",
        }, follow_redirects=True)

        # Generate plan
        client.post("/meal-plan/generate", data={
            "plan_start": "2026-10-05",
            "plan_end": "2026-10-09",
        }, follow_redirects=True)

        # Get an entry ID
        entry_id = db.execute(
            "SELECT id FROM meal_plan_entries LIMIT 1"
        ).fetchone()["id"]

        # Get recipe B ID
        recipe_b_id = db.execute(
            "SELECT id FROM recipes WHERE name = ?", ("Recipe B",)
        ).fetchone()["id"]

        # Swap
        client.post(f"/meal-plan/swap/{entry_id}", data={
            "new_recipe_id": str(recipe_b_id),
        }, follow_redirects=True)

        updated = db.execute(
            "SELECT recipe_id FROM meal_plan_entries WHERE id = ?", (entry_id,)
        ).fetchone()
        assert updated["recipe_id"] == recipe_b_id

    def test_meal_plan_remove_entry(self, client, db):
        """POST /meal-plan/remove/<id> deletes a plan entry."""
        _login(client, "planuser", "test123")

        # Set up and generate
        client.post("/recipes/add", data={
            "name": "Any Recipe",
            "description": "Will be removed",
            "servings": "4",
            "tags": "dinner",
        }, follow_redirects=True)

        # Add a meal rule so generation creates entries
        client.post("/meal-rules/add", data={
            "day_of_week": "all",
            "meal_type": "dinner",
            "tag_filter": "",
            "is_active": "1",
        }, follow_redirects=True)

        client.post("/settings/save", data={
            "action": "save_dates",
            "plan_start": "2026-10-05",
            "plan_end": "2026-10-09",
            "unit_preference": "metric",
        }, follow_redirects=True)

        client.post("/meal-plan/generate", data={
            "plan_start": "2026-10-05",
            "plan_end": "2026-10-09",
        }, follow_redirects=True)

        entry_id = db.execute(
            "SELECT id FROM meal_plan_entries LIMIT 1"
        ).fetchone()["id"]

        client.post(f"/meal-plan/remove/{entry_id}", follow_redirects=True)

        deleted = db.execute(
            "SELECT * FROM meal_plan_entries WHERE id = ?", (entry_id,)
        ).fetchone()
        assert deleted is None

    def test_meal_plan_edit_servings(self, client, db):
        """POST /meal-plan/edit-servings/<id> changes servings for an entry."""
        _login(client, "planuser", "test123")

        client.post("/recipes/add", data={
            "name": "Servings Test Recipe",
            "description": "Recipe for servings test",
            "servings": "4",
            "tags": "dinner",
        }, follow_redirects=True)

        # Add a meal rule so generation creates entries
        client.post("/meal-rules/add", data={
            "day_of_week": "all",
            "meal_type": "dinner",
            "tag_filter": "",
            "is_active": "1",
        }, follow_redirects=True)

        client.post("/settings/save", data={
            "action": "save_dates",
            "plan_start": "2026-10-05",
            "plan_end": "2026-10-09",
            "unit_preference": "metric",
        }, follow_redirects=True)

        client.post("/meal-plan/generate", data={
            "plan_start": "2026-10-05",
            "plan_end": "2026-10-09",
        }, follow_redirects=True)

        entry_id = db.execute(
            "SELECT id FROM meal_plan_entries LIMIT 1"
        ).fetchone()["id"]

        client.post(f"/meal-plan/edit-servings/{entry_id}", data={
            "servings": "6",
        }, follow_redirects=True)

        updated = db.execute(
            "SELECT servings FROM meal_plan_entries WHERE id = ?", (entry_id,)
        ).fetchone()
        assert updated["servings"] == 6


# ============================================================================
# Shopping List
# ============================================================================

class TestShoppingList:
    """Test shopping list generation and management."""

    def test_shopping_list_page_requires_dates(self, client):
        """Shopping list page shows empty state when no dates set."""
        _login(client, "sluser", "test123")
        resp = client.get("/shopping-list")
        assert resp.status_code == 200

    def test_generate_shopping_list(self, client, db):
        """Full workflow: plan → generate shopping list → view items."""
        _login(client, "sluser", "test123")

        # Set up ingredient + recipe + rule + dates + plan
        client.post("/ingredients/add", data={
            "name": "Minced Beef",
            "category": "meat",
            "unit": "g",
            "price": "0.012",
        }, follow_redirects=True)

        client.post("/recipes/add", data={
            "name": "Spaghetti Bolognese",
            "description": "Bolognese",
            "servings": "4",
            "tags": "dinner, italian",
            "ingredient_ids": "1",
            "quantities": "500",
            "unit_overrides": "g",
        }, follow_redirects=True)

        client.post("/meal-rules/add", data={
            "day_of_week": "all",
            "meal_type": "dinner",
            "tag_filter": "",
            "is_active": "1",
        }, follow_redirects=True)

        client.post("/settings/save", data={
            "action": "save_dates",
            "plan_start": "2026-10-05",
            "plan_end": "2026-10-09",
            "unit_preference": "metric",
            "default_servings": "4",
        }, follow_redirects=True)

        # Generate plan
        client.post("/meal-plan/generate", data={
            "plan_start": "2026-10-05",
            "plan_end": "2026-10-09",
        }, follow_redirects=True)

        # Generate shopping list
        client.post("/shopping-list/generate", data={
            "plan_start": "2026-10-05",
            "plan_end": "2026-10-09",
        }, follow_redirects=True)

        # View shopping list
        resp = client.get("/shopping-list")
        assert resp.status_code == 200
        assert b"Minced Beef" in resp.data
        assert b"2.5 kg Minced Beef" in resp.data  # 5 days * 500 g, shown in kg

    def test_toggle_form_has_noscript_fallback(self, client, db):
        """Tick rows carry a no-JS Save button (A6)."""
        _login(client, "sluser", "test123")
        db.execute("INSERT INTO shopping_list_items (ingredient_name, quantity, unit, category, checked, is_manual)"
                   " VALUES ('Milk', 2, 'L', 'dairy', 0, 0)")
        db.commit()
        resp = client.get("/shopping-list")
        assert resp.status_code == 200
        assert b"<noscript>" in resp.data

    def test_pack_pick_persists_and_resets(self, client, db):
        """Choosing a pack offer sticks; unknown packs are rejected (A7)."""
        _login(client, "sluser", "test123")
        db.execute("INSERT INTO ingredients (id, name, category, unit)"
                   " VALUES (1, 'Cheese Pizza Blend 225g', 'dairy', 'kg')")
        db.execute("INSERT INTO purchases (order_date, basket_id, store, product_name, quantity,"
                   " unit_price, line_total, ingredient_id)"
                   " VALUES ('2026-10-01', 'b1', 'Woolworths', 'Cheese Block 1kg', 1, 20.0, 20.0, 1)")
        db.execute("INSERT INTO shopping_list_items (ingredient_id, ingredient_name, quantity, unit,"
                   " category, checked, is_manual)"
                   " VALUES (1, 'Cheese', 0.2, 'kg', 'dairy', 0, 0)")
        db.commit()
        item_id = db.execute("SELECT id FROM shopping_list_items").fetchone()[0]
        resp = client.post(f"/shopping-list/pack/{item_id}",
                           data={"product_name": "Cheese Block 1kg"}, follow_redirects=True)
        assert resp.status_code == 200
        assert db.execute("SELECT pack_pick FROM shopping_list_items WHERE id = ?",
                          (item_id,)).fetchone()[0] == "Cheese Block 1kg"
        assert b"back to automatic (Cheese Block 1kg)" in resp.data
        resp = client.post(f"/shopping-list/pack/{item_id}",
                           data={"product_name": "No Such Pack"}, follow_redirects=True)
        assert b"no longer offered" in resp.data
        assert db.execute("SELECT pack_pick FROM shopping_list_items WHERE id = ?",
                          (item_id,)).fetchone()[0] == "Cheese Block 1kg"

    def test_preferences_indicator_renders(self, client, db):
        """List states its unit preference and pantry handling (A7)."""
        _login(client, "sluser", "test123")
        client.post("/settings/save", data={
            "action": "save_dates",
            "plan_start": "2026-10-05",
            "plan_end": "2026-10-09",
            "unit_preference": "metric",
            "default_servings": "2",
        }, follow_redirects=True)
        resp = client.get("/shopping-list")
        assert resp.status_code == 200
        assert b"Quantities in metric" in resp.data
        assert b"pantry stock" in resp.data

    def test_remove_ticked_label(self, client, db):
        """The destructive clear action is labelled Remove (A8)."""
        _login(client, "sluser", "test123")
        db.execute("INSERT INTO shopping_list_items (ingredient_name, quantity, unit, category, checked, is_manual)"
                   " VALUES ('Milk', 2, 'L', 'dairy', 1, 0)")
        db.commit()
        resp = client.get("/shopping-list")
        assert b"Remove ticked items" in resp.data
        assert b"Clear ticked" not in resp.data

    def test_static_assets_carry_d_fixes(self, client):
        """CSS chevrons and print/loading JS ship (D)."""
        css = client.get("/static/css/style.css").data
        assert b"details[open].slot-edit" in css
        assert b".table-sticky-first" in css
        js = client.get("/static/js/app.js").data
        assert b"beforeprint" in js
        assert b"spinner-border" in js

    def test_bootstrap5_no_jquery(self, client):
        """Layout uses Bootstrap 5 (no jQuery, data-bs-*) (S1)."""
        _login(client, "sluser", "test123")
        html = client.get("/").data
        assert b"bootstrap@5" in html
        assert b"jquery" not in html.lower()
        assert b"data-bs-toggle" in client.get("/meal-plan").data

    def test_dark_mode_plumbing(self, client):
        """Theme toggle, theme attr and dark tokens ship (S2)."""
        _login(client, "sluser", "test123")
        html = client.get("/").data
        assert b'data-bs-theme' in html
        assert b'id="theme-toggle"' in html
        assert b"gv-theme" in html
        css = client.get("/static/css/style.css").data
        assert b'[data-bs-theme="dark"]' in css
        assert b"main h1" in css

    def test_mobile_navigation_helpers(self, client, db):
        """Bottom nav, sticky bars and sticky table columns ship (S3)."""
        _login(client, "sluser", "test123")
        html = client.get("/shopping-list").data
        assert b"bottom-nav" in html
        assert b"sticky-actions" in html
        assert b'aria-current="page"' in html
        css = client.get("/static/css/style.css").data
        assert b".bottom-nav" in css
        assert b".sticky-actions" in css

    def test_inline_svg_icons_no_font_awesome(self, client, db):
        """Icons come from the inline sprite, not the FA CDN (S4)."""
        _login(client, "sluser", "test123")
        html = client.get("/").data
        assert b"font-awesome" not in html.lower()
        assert b"<symbol" in html
        assert b'<use href="#i-' in html

    def test_regenerate_summary_states_consequences(self, client, db):
        """Shopping page states what regenerating rebuilds and keeps (A3)."""
        _login(client, "sluser", "test123")
        db.execute("INSERT INTO shopping_list_items (ingredient_id, ingredient_name, quantity, unit,"
                   " category, checked, is_manual) VALUES (1, 'Milk', 2, 'L', 'dairy', 1, 0)")
        db.execute("INSERT INTO shopping_list_items (ingredient_name, quantity, unit, category,"
                   " checked, is_manual) VALUES ('Notes', 1, 'each', 'other', 0, 1)")
        db.commit()
        resp = client.get("/shopping-list")
        assert resp.status_code == 200
        assert b"deleted plan items return" in resp.data
        assert b"manual items stay" in resp.data

    def test_shopping_list_toggle_check(self, client, db):
        """POST /shopping-list/toggle/<id> toggles the checked flag."""
        _login(client, "sluser", "test123")

        # Generate a shopping list first
        client.post("/ingredients/add", data={
            "name": "Test Item",
            "category": "pantry",
            "unit": "each",
            "price": "1.00",
        }, follow_redirects=True)

        client.post("/recipes/add", data={
            "name": "Test Recipe",
            "description": "Recipe",
            "servings": "4",
            "tags": "dinner",
            "ingredient_ids": "1",
            "quantities": "1",
        }, follow_redirects=True)

        client.post("/settings/save", data={
            "action": "save_dates",
            "plan_start": "2026-10-05",
            "plan_end": "2026-10-05",
            "unit_preference": "metric",
        }, follow_redirects=True)

        # Add a meal rule so generation creates entries
        client.post("/meal-rules/add", data={
            "day_of_week": "all",
            "meal_type": "dinner",
            "tag_filter": "",
            "is_active": "1",
        }, follow_redirects=True)

        client.post("/meal-plan/generate", data={
            "plan_start": "2026-10-05",
            "plan_end": "2026-10-05",
        }, follow_redirects=True)

        client.post("/shopping-list/generate", data={
            "plan_start": "2026-10-05",
            "plan_end": "2026-10-05",
        }, follow_redirects=True)

        # Add a meal rule so generation creates entries
        client.post("/meal-rules/add", data={
            "day_of_week": "all",
            "meal_type": "dinner",
            "tag_filter": "",
            "is_active": "1",
        }, follow_redirects=True)

        # Get a shopping list item ID
        item_id = db.execute(
            "SELECT id FROM shopping_list_items LIMIT 1"
        ).fetchone()["id"]

        # Toggle
        client.post(f"/shopping-list/toggle/{item_id}")
        toggled = db.execute(
            "SELECT checked FROM shopping_list_items WHERE id = ?", (item_id,)
        ).fetchone()
        assert toggled["checked"] == 1

        # Toggle again
        client.post(f"/shopping-list/toggle/{item_id}")
        toggled = db.execute(
            "SELECT checked FROM shopping_list_items WHERE id = ?", (item_id,)
        ).fetchone()
        assert toggled["checked"] == 0

        # JSON toggle (used by the in-page checkbox) returns the new state instead of redirecting
        resp = client.post(f"/shopping-list/toggle/{item_id}", headers={"Accept": "application/json"})
        assert resp.status_code == 200
        assert resp.get_json() == {"checked": True}

    def test_shopping_list_delete_item(self, client, db):
        """POST /shopping-list/delete/<id> removes a persistent item."""
        _login(client, "sluser", "test123")

        # Generate shopping list
        client.post("/ingredients/add", data={
            "name": "Delete Me Item",
            "category": "pantry",
            "unit": "each",
            "price": "1.00",
        }, follow_redirects=True)

        client.post("/recipes/add", data={
            "name": "Test Recipe",
            "description": "Recipe",
            "servings": "4",
            "tags": "dinner",
            "ingredient_ids": "1",
            "quantities": "1",
        }, follow_redirects=True)

        client.post("/settings/save", data={
            "action": "save_dates",
            "plan_start": "2026-10-05",
            "plan_end": "2026-10-05",
            "unit_preference": "metric",
        }, follow_redirects=True)

        # Add a meal rule so generation creates entries
        client.post("/meal-rules/add", data={
            "day_of_week": "all",
            "meal_type": "dinner",
            "tag_filter": "",
            "is_active": "1",
        }, follow_redirects=True)

        client.post("/meal-plan/generate", data={
            "plan_start": "2026-10-05",
            "plan_end": "2026-10-05",
        }, follow_redirects=True)

        client.post("/shopping-list/generate", data={
            "plan_start": "2026-10-05",
            "plan_end": "2026-10-05",
        }, follow_redirects=True)

        item_id = db.execute(
            "SELECT id FROM shopping_list_items LIMIT 1"
        ).fetchone()["id"]

        client.post(f"/shopping-list/delete/{item_id}", follow_redirects=True)

        deleted = db.execute(
            "SELECT * FROM shopping_list_items WHERE id = ?", (item_id,)
        ).fetchone()
        assert deleted is None

    def test_add_manual_shopping_item(self, client, db):
        """POST /shopping-list/add-manual adds a manually created item."""
        _login(client, "sluser", "test123")

        resp = client.post("/shopping-list/add-manual", data={
            "ingredient_name": "Handwritten Note Pad",
            "quantity": "2",
            "unit": "pack",
            "category": "household",
            "notes": "For the pantry list",
        }, follow_redirects=True)
        assert resp.status_code == 200

        row = db.execute(
            "SELECT * FROM shopping_list_items WHERE ingredient_name = ?",
            ("Handwritten Note Pad",),
        ).fetchone()
        assert row is not None
        assert row["is_manual"] == 1
        assert row["category"] == "household"

    def test_clear_checked_items(self, client, db):
        """POST /shopping-list/clear-checked removes all checked items."""
        _login(client, "sluser", "test123")

        # Generate list and check an item
        client.post("/ingredients/add", data={
            "name": "Checkable Item",
            "category": "pantry",
            "unit": "each",
            "price": "1.00",
        }, follow_redirects=True)

        client.post("/recipes/add", data={
            "name": "Test Recipe",
            "description": "Recipe",
            "servings": "4",
            "tags": "dinner",
            "ingredient_ids": "1",
            "quantities": "1",
        }, follow_redirects=True)

        client.post("/settings/save", data={
            "action": "save_dates",
            "plan_start": "2026-10-05",
            "plan_end": "2026-10-05",
            "unit_preference": "metric",
        }, follow_redirects=True)

        # Add a meal rule so generation creates entries
        client.post("/meal-rules/add", data={
            "day_of_week": "all",
            "meal_type": "dinner",
            "tag_filter": "",
            "is_active": "1",
        }, follow_redirects=True)

        client.post("/meal-plan/generate", data={
            "plan_start": "2026-10-05",
            "plan_end": "2026-10-05",
        }, follow_redirects=True)

        client.post("/shopping-list/generate", data={
            "plan_start": "2026-10-05",
            "plan_end": "2026-10-05",
        }, follow_redirects=True)

        # Check an item
        item_id = db.execute(
            "SELECT id FROM shopping_list_items LIMIT 1"
        ).fetchone()["id"]
        client.post(f"/shopping-list/toggle/{item_id}")

        # Clear checked
        client.post("/shopping-list/clear-checked", follow_redirects=True)

        remaining = db.execute("SELECT COUNT(*) as cnt FROM shopping_list_items").fetchone()["cnt"]
        assert remaining == 0


# ============================================================================
# Pantry
# ============================================================================

class TestPantry:
    """Test pantry inventory management."""

    def test_pantry_list_page_loads(self, client):
        """Authenticated user can view the pantry list."""
        _login(client, "panuser", "test123")
        resp = client.get("/pantry")
        assert resp.status_code == 200
        assert b"Pantry" in resp.data

    def test_add_pantry_item(self, client, db):
        """POST /pantry/add creates a pantry item linked to an ingredient."""
        _login(client, "panuser", "test123")

        # Add ingredient first
        client.post("/ingredients/add", data={
            "name": "Rice",
            "category": "pantry",
            "unit": "g",
            "price": "0.008",
        }, follow_redirects=True)

        # Add pantry item
        resp = client.post("/pantry/add", data={
            "ingredient_id": "1",
            "quantity": "2000",
            "unit": "g",
            "expiry_date": "2027-01-01",
            "location": "Cupboard",
            "notes": "",
        }, follow_redirects=True)
        assert resp.status_code == 200

        row = db.execute(
            "SELECT * FROM pantry_items WHERE ingredient_id = 1"
        ).fetchone()
        assert row is not None
        assert row["quantity"] == 2000
        assert row["location"] == "Cupboard"

    def test_location_filter_submits_without_js(self, client, db):
        """Location filter is a plain GET form (A6/B3)."""
        _login(client, "panuser", "test123")
        client.post("/ingredients/add", data={
            "name": "Milk",
            "category": "dairy",
            "unit": "L",
        }, follow_redirects=True)
        client.post("/pantry/add", data={
            "ingredient_id": "1",
            "quantity": "1",
            "unit": "L",
            "location": "fridge",
        }, follow_redirects=True)
        assert b"Milk" in client.get("/pantry?location=fridge").data
        assert b"Milk" not in client.get("/pantry?location=freezer").data
        assert b'name="location"' in client.get("/pantry").data

    def test_zero_quantity_pantry_rejected(self, client, db):
        """A zero-quantity pantry row is an error, not an empty row (B2)."""
        _login(client, "panuser", "test123")
        client.post("/ingredients/add", data={
            "name": "Rice", "category": "pantry", "unit": "g",
        }, follow_redirects=True)
        resp = client.post("/pantry/add", data={
            "ingredient_id": "1",
            "quantity": "0",
            "unit": "g",
            "location": "pantry",
        })
        assert resp.status_code == 200
        assert b"Quantity must be greater than zero." in resp.data
        assert db.execute("SELECT COUNT(*) FROM pantry_items").fetchone()[0] == 0

    def test_pantry_add_error_preserves_input(self, client, db):
        """Pantry errors render inline with typed values kept (B1a)."""
        _login(client, "panuser", "test123")
        resp = client.post("/pantry/add", data={
            "ingredient_id": "",
            "quantity": "abc",
            "unit": "g",
            "location": "fridge",
        })
        assert resp.status_code == 200
        assert b"Ingredient is required." in resp.data
        assert b"Quantity must be a number." in resp.data
        assert b'value="abc"' in resp.data
        assert b"Add Pantry Item" in resp.data
        assert db.execute("SELECT COUNT(*) FROM pantry_items").fetchone()[0] == 0

    def test_consume_pantry_item(self, client, db):
        """POST /pantry/consume/<id> reduces pantry quantity."""
        _login(client, "panuser", "test123")

        client.post("/ingredients/add", data={
            "name": "Milk",
            "category": "dairy",
            "unit": "mL",
            "price": "0.005",
        }, follow_redirects=True)

        client.post("/pantry/add", data={
            "ingredient_id": "1",
            "quantity": "2000",
            "unit": "mL",
            "expiry_date": "2027-01-01",
        }, follow_redirects=True)

        pantry_id = db.execute(
            "SELECT id FROM pantry_items WHERE ingredient_id = 1"
        ).fetchone()["id"]

        # Consume 500mL
        resp = client.post(f"/pantry/consume/{pantry_id}", data={
            "quantity": "500",
            "unit": "mL",
        }, follow_redirects=True)
        assert resp.status_code == 200

        updated = db.execute(
            "SELECT quantity FROM pantry_items WHERE id = ?", (pantry_id,)
        ).fetchone()
        assert updated["quantity"] == 1500

    def test_edit_pantry_item(self, client, db):
        """POST /pantry/edit/<id> updates pantry item details."""
        _login(client, "panuser", "test123")

        client.post("/ingredients/add", data={
            "name": "Olive Oil",
            "category": "pantry",
            "unit": "mL",
            "price": "0.015",
        }, follow_redirects=True)

        client.post("/pantry/add", data={
            "ingredient_id": "1",
            "quantity": "500",
            "unit": "mL",
            "expiry_date": "2027-01-01",
            "location": "Old location",
        }, follow_redirects=True)

        pantry_id = db.execute(
            "SELECT id FROM pantry_items WHERE ingredient_id = 1"
        ).fetchone()["id"]

        resp = client.post(f"/pantry/edit/{pantry_id}", data={
            "ingredient_id": "1",
            "quantity": "750",
            "unit": "mL",
            "expiry_date": "2027-06-01",
            "location": "New location",
        }, follow_redirects=True)
        assert resp.status_code == 200

        updated = db.execute(
            "SELECT * FROM pantry_items WHERE id = ?", (pantry_id,)
        ).fetchone()
        assert updated["quantity"] == 750
        assert updated["location"] == "New location"
        assert updated["expiry_date"] == "2027-06-01"

    def test_delete_pantry_item(self, client, db):
        """POST /pantry/delete/<id> removes a pantry item."""
        _login(client, "panuser", "test123")

        client.post("/ingredients/add", data={
            "name": "To Delete Pantry",
            "category": "other",
            "unit": "each",
        }, follow_redirects=True)

        client.post("/pantry/add", data={
            "ingredient_id": "1",
            "quantity": "10",
            "unit": "each",
        }, follow_redirects=True)

        pantry_id = db.execute(
            "SELECT id FROM pantry_items WHERE ingredient_id = 1"
        ).fetchone()["id"]

        client.post(f"/pantry/delete/{pantry_id}", follow_redirects=True)

        deleted = db.execute(
            "SELECT * FROM pantry_items WHERE id = ?", (pantry_id,)
        ).fetchone()
        assert deleted is None


# ============================================================================
# Settings
# ============================================================================

class TestSettings:
    """Test app settings."""

    def test_settings_page_loads(self, client):
        """Authenticated user can view settings."""
        _login(client, "setuser", "test123")
        resp = client.get("/settings")
        assert resp.status_code == 200
        assert b"Settings" in resp.data

    def test_save_settings(self, client, db):
        """POST /settings/save stores app settings."""
        _login(client, "setuser", "test123")

        resp = client.post("/settings/save", data={
            "action": "save_dates",
            "plan_start": "2026-10-05",
            "plan_end": "2026-10-18",
        }, follow_redirects=True)
        assert resp.status_code == 200
        resp = client.post("/settings/save", data={
            "action": "save_prefs",
            "unit_preference": "imperial",
            "default_servings": "2",
            "subtract_pantry": "on",
        }, follow_redirects=True)
        assert resp.status_code == 200

        # Verify in DB
        start = db.execute("SELECT value FROM settings WHERE key = ?",
                           ("plan_start_date",)).fetchone()
        assert start["value"] == "2026-10-05"

        end = db.execute("SELECT value FROM settings WHERE key = ?",
                         ("plan_end_date",)).fetchone()
        assert end["value"] == "2026-10-18"

        unit = db.execute("SELECT value FROM settings WHERE key = ?",
                          ("unit_preference",)).fetchone()
        assert unit["value"] == "imperial"

        pantry = db.execute("SELECT value FROM settings WHERE key = ?",
                            ("subtract_pantry_from_list",)).fetchone()
        assert pantry["value"] == "1"

    def test_save_ai_settings(self, client, db):
        _login(client, "setuser", "test123")
        resp = client.post("/settings/save", data={
            "action": "save_ai",
            "openrouter_api_key": "sk-or-test",
            "openrouter_model": "openrouter/free",
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b"AI review settings saved." in resp.data
        key = db.execute("SELECT value FROM settings WHERE key = 'openrouter_api_key'").fetchone()
        assert key["value"] == "sk-or-test"
        model = db.execute("SELECT value FROM settings WHERE key = 'openrouter_model'").fetchone()
        assert model["value"] == "openrouter/free"

        client.post("/settings/save", data={
            "action": "save_ai",
            "openrouter_api_key": "",
            "openrouter_model": "openrouter/free",
            "remove_ai_key": "on",
        }, follow_redirects=True)
        key = db.execute("SELECT value FROM settings WHERE key = 'openrouter_api_key'").fetchone()
        assert key["value"] == ""

    def test_save_settings_metric_default(self, client, db):
        """Metric is the default unit preference."""
        _login(client, "setuser", "test123")

        client.post("/settings/save", data={
            "action": "save_prefs",
            "unit_preference": "metric",
            "default_servings": "4",
        }, follow_redirects=True)

        unit = db.execute("SELECT value FROM settings WHERE key = ?",
                          ("unit_preference",)).fetchone()
        assert unit["value"] == "metric"

    def test_settings_dates_error_preserves_input(self, client, db):
        """Bad dates re-show settings with typed values kept (B1b)."""
        _login(client, "setuser", "test123")
        resp = client.post("/settings/save", data={
            "action": "save_dates",
            "plan_start": "2026-10-20",
            "plan_end": "2026-10-05",
        })
        assert resp.status_code == 200
        assert b"The end date must be on or after the start date." in resp.data
        assert b'value="2026-10-20"' in resp.data
        assert db.execute("SELECT COUNT(*) FROM settings").fetchone()[0] == 0

    def test_settings_prefs_error_preserves_input(self, client, db):
        """Bad prefs re-show settings with typed values kept (B1b)."""
        _login(client, "setuser", "test123")
        resp = client.post("/settings/save", data={
            "action": "save_prefs",
            "unit_preference": "imperial",
            "default_servings": "0",
        })
        assert resp.status_code == 200
        assert b"Default servings must be at least 1." in resp.data
        assert b'value="imperial" selected' in resp.data
        assert b'value="0"' in resp.data

    def test_change_password(self, client, db):
        """POST /settings/change-password updates the admin password."""
        _login(client, "setuser", "test123")

        resp = client.post("/settings/change-password", data={
            "current_password": "test123",
            "new_password": "newsecurepass",
            "confirm_password": "newsecurepass",
        }, follow_redirects=True)
        assert resp.status_code == 200

        # Verify old password no longer works
        resp = client.post("/login", data={
            "username": "setuser",
            "password": "test123",
        })
        assert resp.status_code == 200  # re-shown with an error, not redirected
        assert b"Invalid username or password." in resp.data

        # New password works
        resp = client.post("/login", data={
            "username": "setuser",
            "password": "newsecurepass",
        }, follow_redirects=True)
        assert resp.status_code == 200


# ============================================================================
# Subscriptions
# ============================================================================

class TestSubscriptions:
    """Pause/resume/purge, catch-up mark-paid, suggestion accept (H2, M1, M2)."""

    def _add(self, client, name="Netflix", amount="18.99", frequency="monthly",
             next_due="2026-10-20"):
        return client.post("/subscriptions/add", data={
            "name": name, "amount": amount, "frequency": frequency,
            "category": "entertainment", "next_due": next_due, "notes": "",
        }, follow_redirects=True)

    def test_pause_resume_purge_round_trip(self, client, db):
        _login(client, "subuser", "test123")
        assert self._add(client).status_code == 200
        sub_id = db.execute("SELECT id FROM subscriptions").fetchone()["id"]
        # Pause moves it to the Paused section.
        assert client.post(f"/subscriptions/{sub_id}/toggle").status_code == 302
        resp = client.get("/subscriptions")
        assert b"Paused" in resp.data
        assert b"Netflix" in resp.data
        # Resume brings it back.
        assert client.post(f"/subscriptions/{sub_id}/resume").status_code == 302
        assert db.execute("SELECT active FROM subscriptions WHERE id = ?",
                          (sub_id,)).fetchone()["active"] == 1
        # Purge deletes the row for good.
        client.post(f"/subscriptions/{sub_id}/toggle")
        assert client.post(f"/subscriptions/{sub_id}/purge").status_code == 302
        assert db.execute("SELECT COUNT(*) FROM subscriptions").fetchone()[0] == 0

    def test_resume_missing_is_404_redirect(self, client, db):
        _login(client, "subuser", "test123")
        assert client.post("/subscriptions/9999/resume").status_code == 302
        assert client.post("/subscriptions/9999/purge").status_code == 302

    def test_mark_paid_catches_up_overdue(self, client, db):
        _login(client, "subuser", "test123")
        self._add(client, frequency="weekly", next_due="2026-09-01")
        sub_id = db.execute("SELECT id FROM subscriptions").fetchone()["id"]
        client.post(f"/subscriptions/{sub_id}/mark-paid")
        next_due = db.execute("SELECT next_due FROM subscriptions WHERE id = ?",
                              (sub_id,)).fetchone()["next_due"]
        from datetime import date
        assert next_due >= date.today().isoformat()

    def test_accept_suggestion_uses_detected_date(self, client, db):
        _login(client, "subuser", "test123")
        resp = client.post("/subscriptions/suggest/accept", data={
            "name": "Milk", "amount": "4.2", "frequency": "monthly",
            "category": "other", "next_due": "2026-11-01",
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert db.execute("SELECT next_due FROM subscriptions").fetchone()["next_due"] == "2026-11-01"
        # Invalid dates fall back to today rather than 500ing.
        client.post("/subscriptions/suggest/accept", data={
            "name": "Bread", "amount": "3.0", "frequency": "monthly",
            "category": "other", "next_due": "not-a-date",
        })
        from datetime import date
        assert db.execute("SELECT next_due FROM subscriptions WHERE name = 'Bread'").fetchone()["next_due"] == date.today().isoformat()

    def test_suggest_ignores_legacy_unclassified_json(self, client, db):
        """The old banksync_unclassified cache is no longer a suggestion source."""
        _login(client, "subuser", "test123")
        import json
        db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            ("banksync_unclassified", json.dumps([
                {"description": "LEGACY MERCHANT", "amount": 18.99},
            ])))
        db.commit()
        resp = client.get("/subscriptions/suggest")
        assert resp.status_code == 200
        assert b"LEGACY MERCHANT" not in resp.data

    def _bank_rows(self, db, description, amount, dates,
                   direction="debit", status="other"):
        from services.recurring import normalise_merchant
        for i, txn_date in enumerate(dates):
            db.execute(
                """INSERT INTO bank_transactions
                   (source, external_id, txn_date, description, merchant_key,
                    amount, direction, status)
                   VALUES ('csv', ?, ?, ?, ?, ?, ?, ?)""",
                (f"{description}-{txn_date}-{i}", txn_date, description,
                 normalise_merchant(description), amount, direction, status),
            )
        db.commit()

    def test_list_shows_monthly_cost_and_upcoming_charts(self, client, db):
        from datetime import date, timedelta
        _login(client, "subuser", "test123")
        next_due = (date.today() + timedelta(days=2)).isoformat()
        self._add(client, name="Netflix", amount="18.99", next_due=next_due)
        resp = client.get("/subscriptions")
        assert resp.status_code == 200
        assert b"Bills per month" in resp.data
        assert b"billCategoryChart" in resp.data
        assert b"billWeekChart" in resp.data
        assert b"How to read this" in resp.data
        assert b"gv:themechange" in resp.data
        assert b"htmx:afterRequest" in resp.data
        assert b"Netflix" in resp.data

    def test_suggest_detects_recurring_bank_transactions(self, client, db):
        _login(client, "subuser", "test123")
        self._bank_rows(db, "BINGE.COM", 16.00,
                        ["2026-01-10", "2026-02-10", "2026-03-10", "2026-04-10"])
        resp = client.get("/subscriptions/suggest")
        assert resp.status_code == 200
        assert b"Binge" in resp.data
        assert b"4 payments" in resp.data
        assert b"subscription" in resp.data
        assert b"Monthly" in resp.data

    def test_accepting_a_detected_payment_links_the_merchant(self, client, db):
        _login(client, "subuser", "test123")
        self._bank_rows(db, "BINGE.COM", 16.00,
                        ["2026-01-10", "2026-02-10", "2026-03-10", "2026-04-10"])
        resp = client.post("/subscriptions/suggest/accept", data={
            "merchant_key": "binge", "name": "Binge", "amount": "16.00",
            "frequency": "monthly", "category": "entertainment",
            "next_due": "2026-05-10",
        }, follow_redirects=True)
        assert resp.status_code == 200
        sub = db.execute("SELECT * FROM subscriptions").fetchone()
        assert sub["merchant_key"] == "binge"
        assert sub["next_due"] == "2026-05-10"
        decision = db.execute("SELECT * FROM merchant_decisions").fetchone()
        assert decision["decision"] == "accepted"
        assert decision["subscription_id"] == sub["id"]
        # Accepted merchants are not suggested again.
        resp = client.get("/subscriptions/suggest")
        assert b"4 payments" not in resp.data

    def test_dismiss_and_restore_a_detected_payment(self, client, db):
        _login(client, "subuser", "test123")
        self._bank_rows(db, "BINGE.COM", 16.00,
                        ["2026-01-10", "2026-02-10", "2026-03-10"])
        client.post("/subscriptions/suggest/ignore", data={
            "merchant_key": "binge", "name": "Binge", "amount": "16.00",
            "frequency": "monthly",
        })
        resp = client.get("/subscriptions/suggest")
        assert b"Dismissed" in resp.data
        assert b"3 payments" not in resp.data

        decision_id = db.execute("SELECT id FROM merchant_decisions").fetchone()["id"]
        assert client.post(
            f"/subscriptions/suggest/restore/{decision_id}",
        ).status_code == 302
        resp = client.get("/subscriptions/suggest")
        assert b"3 payments" in resp.data
        assert db.execute("SELECT COUNT(*) FROM merchant_decisions").fetchone()[0] == 0

    def test_existing_subscription_is_not_suggested_again(self, client, db):
        _login(client, "subuser", "test123")
        self._add(client, name="Binge", amount="16.00")
        self._bank_rows(db, "BINGE.COM", 16.00,
                        ["2026-01-10", "2026-02-10", "2026-03-10", "2026-04-10"])
        resp = client.get("/subscriptions/suggest")
        assert b"4 payments" not in resp.data

    def test_htmx_row_updates_render_without_a_500(self, client, db):
        """Mark paid, pause and resume each return a usable HTMX response."""
        _login(client, "subuser", "test123")
        self._add(client, frequency="weekly", next_due="2026-09-01")
        sub_id = db.execute("SELECT id FROM subscriptions").fetchone()["id"]
        headers = {"HX-Request": "true"}

        mark = client.post(f"/subscriptions/{sub_id}/mark-paid", headers=headers)
        assert mark.status_code == 200
        assert b"Mark paid" in mark.data

        pause = client.post(f"/subscriptions/{sub_id}/toggle", headers=headers)
        assert pause.status_code == 204

        resume = client.post(f"/subscriptions/{sub_id}/resume", headers=headers)
        assert resume.status_code == 200
        assert b"Mark paid" in resume.data

    def test_purging_a_linked_bill_allows_it_to_be_suggested_again(self, client, db):
        _login(client, "subuser", "test123")
        self._bank_rows(db, "BINGE.COM", 16.00,
                        ["2026-01-10", "2026-02-10", "2026-03-10"])
        client.post("/subscriptions/suggest/accept", data={
            "merchant_key": "binge", "name": "Binge", "amount": "16.00",
            "frequency": "monthly", "category": "entertainment",
        })
        sub_id = db.execute("SELECT id FROM subscriptions").fetchone()["id"]
        client.post(f"/subscriptions/{sub_id}/toggle")
        client.post(f"/subscriptions/{sub_id}/purge")
        assert db.execute("SELECT COUNT(*) FROM merchant_decisions").fetchone()[0] == 0
        resp = client.get("/subscriptions/suggest")
        assert b"3 payments" in resp.data

    def test_ai_review_requires_a_key(self, client, db):
        _login(client, "subuser", "test123")
        self._bank_rows(db, "BINGE.COM", 16.00,
                        ["2026-01-10", "2026-02-10"])
        resp = client.post("/subscriptions/suggest/review", follow_redirects=True)
        assert b"Add an OpenRouter API key" in resp.data

    def test_ai_review_filters_and_promotes(self, client, db, monkeypatch):
        _login(client, "subuser", "test123")
        db.execute("INSERT OR REPLACE INTO settings (key, value)"
                   " VALUES ('openrouter_api_key', 'test-key')")
        db.execute("INSERT OR REPLACE INTO settings (key, value)"
                   " VALUES ('openrouter_model', 'openrouter/free')")
        db.commit()
        self._bank_rows(db, "BINGE.COM", 16.00,
                        ["2026-01-10", "2026-02-10", "2026-03-10"])
        self._bank_rows(db, "AMAZON AU MARKETPLACE", 45.00,
                        ["2026-01-05", "2026-02-05"])
        self._bank_rows(db, "CITY COUNCIL RATES", 300.00,
                        ["2026-08-01", "2026-11-01"])
        self._bank_rows(db, "AUTO SMART SAVINGS DEBIT", 112.00,
                        ["2026-09-01", "2026-10-01"])

        def fake_review(candidates, api_key, model, timeout=60):
            assert api_key == "test-key"
            reviews = []
            for candidate in candidates:
                key = candidate["merchant_key"]
                if key == "binge":
                    reviews.append({
                        "merchant_key": key,
                        "evidence_hash": candidate["evidence_hash"],
                        "bill": True, "name": "Binge Streaming",
                        "category": "entertainment", "frequency": "monthly",
                        "reason": "Streaming subscription.",
                    })
                elif key == "amazon":
                    reviews.append({
                        "merchant_key": key,
                        "evidence_hash": candidate["evidence_hash"],
                        "bill": False, "name": "Amazon",
                        "category": "other", "frequency": None,
                        "reason": "Marketplace shopping, not a bill.",
                    })
                elif key == "city":
                    reviews.append({
                        "merchant_key": key,
                        "evidence_hash": candidate["evidence_hash"],
                        "bill": True, "name": "Council Rates",
                        "category": "bills", "frequency": "quarterly",
                        "reason": "Council rates notice.",
                    })
            return reviews

        monkeypatch.setattr("routes.subscriptions.review_candidates", fake_review)
        resp = client.post("/subscriptions/suggest/review", follow_redirects=True)
        assert b"AI reviewed 3 merchants: 2 kept, 1 dismissed" in resp.data
        assert b"Binge Streaming" in resp.data
        assert b"Council Rates" in resp.data
        assert b"Amazon" in resp.data  # listed under "What the AI dismissed"
        assert b"Marketplace shopping" in resp.data
        assert b"AUTO SMART SAVINGS" not in resp.data
        assert b"What the AI dismissed" in resp.data
        assert b"Marketplace shopping" in resp.data
        # Verdicts are cached: a later page load keeps them without another call.
        resp = client.get("/subscriptions/suggest")
        assert b"Binge Streaming" in resp.data
        assert b"Review again" in resp.data

    def test_bill_detail_shows_price_history_and_can_use_latest(self, client, db):
        _login(client, "subuser", "test123")
        self._bank_rows(db, "BINGE.COM", 16.00,
                        ["2026-01-10", "2026-02-10"])
        self._bank_rows(db, "BINGE.COM", 18.00, ["2026-03-10"])
        client.post("/subscriptions/suggest/accept", data={
            "merchant_key": "binge", "name": "Binge", "amount": "16.67",
            "frequency": "monthly", "category": "entertainment",
        })
        sub_id = db.execute("SELECT id FROM subscriptions").fetchone()["id"]

        resp = client.get(f"/subscriptions/{sub_id}")
        assert resp.status_code == 200
        assert b"Price over time" in resp.data
        assert b"priceChart" in resp.data
        assert b"12.5%" in resp.data
        assert b"Payment history" in resp.data

        resp = client.get("/subscriptions")
        assert b"bill-spark" in resp.data
        assert b"12.5%" in resp.data

        client.post(f"/subscriptions/{sub_id}/use-latest", follow_redirects=True)
        amount = db.execute("SELECT amount FROM subscriptions WHERE id = ?",
                            (sub_id,)).fetchone()["amount"]
        assert amount == 18.00

    def test_bill_detail_without_linked_history(self, client, db):
        _login(client, "subuser", "test123")
        self._add(client, name="Manual Bill", amount="20.00")
        sub_id = db.execute("SELECT id FROM subscriptions").fetchone()["id"]
        resp = client.get(f"/subscriptions/{sub_id}")
        assert resp.status_code == 200
        assert b"No price history yet" in resp.data

    def test_suggest_prompts_for_history_backfill(self, client, db):
        _login(client, "subuser", "test123")
        db.execute("INSERT OR REPLACE INTO settings (key, value)"
                   " VALUES ('banksync_api_key', 'bsk_test')")
        db.commit()
        resp = client.get("/subscriptions/suggest")
        assert b"Backfill 12 months of bank history" in resp.data

    def test_repeat_groceries_are_not_offered_as_bills(self, client, db):
        _login(client, "subuser", "test123")
        from datetime import date, timedelta
        base = date(2026, 8, 3)
        for i in range(6):
            day = (base + timedelta(days=7 * i)).isoformat()
            db.execute(
                "INSERT INTO purchases (order_date, basket_id, store, product_name,"
                " quantity, unit_price, line_total, origin)"
                " VALUES (?, ?, 'Woolworths', 'Weekly Milk', 1, 3.10, 3.10, 'csv')",
                (day, f"milk-{i}"),
            )
        db.commit()
        resp = client.get("/subscriptions/suggest")
        assert b"Often bought" in resp.data
        assert b"Weekly Milk" in resp.data
        assert b"No suggestions right now" not in resp.data
        assert b">Add</button>" not in resp.data

    def test_suggest_ignores_ancient_purchases(self, client, db):
        _login(client, "subuser", "test123")
        for i, d in enumerate(("2020-01-01", "2020-02-01", "2020-03-01")):
            db.execute(
                "INSERT INTO purchases (order_date, basket_id, store, product_name,"
                " quantity, unit_price, line_total, origin)"
                " VALUES (?, ?, 'Woolworths', 'Old staple', 1, 5.00, 5.00, 'csv')", (d, f"old-{i}"))
        db.commit()
        resp = client.get("/subscriptions/suggest")
        assert resp.status_code == 200
        assert b"Old staple" not in resp.data


# ============================================================================
# Helper
# ============================================================================

def _login(client, username, password):
    """Helper to create a user and log them in."""
    from werkzeug.security import generate_password_hash
    from database import get_db
    with client.application.app_context():
        db = get_db()
        existing = db.execute(
            "SELECT id FROM users WHERE username = ?", (username,),
        ).fetchone()
        if not existing:
            db.execute(
                "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                (username, generate_password_hash(password)),
            )
            db.commit()
    client.post("/login", data={
        "username": username,
        "password": password,
    }, follow_redirects=True)

# ============================================================================
# Meal plan quick start
# ============================================================================

class TestMealQuickStart:
    """Starter rule sets so a plan can be generated without learning rules."""

    def test_quick_start_creates_rules(self, client, db):
        _login(client, "quickuser", "test123")
        db.execute("INSERT INTO recipes (name, servings) VALUES ('Quick Recipe', 2)")
        db.commit()

        resp = client.post(
            "/meal-plan/rules/quick-start",
            data={"template": "three_meals"},
            follow_redirects=True,
        )
        assert resp.status_code == 200

        rules = db.execute(
            "SELECT meal_type, day_of_week, is_active FROM meal_rules ORDER BY sort_order"
        ).fetchall()
        assert [r["meal_type"] for r in rules] == ["breakfast", "lunch", "dinner"]
        assert all(r["day_of_week"] == "all" and r["is_active"] == 1 for r in rules)

    def test_quick_start_refuses_when_rules_exist(self, client, db):
        _login(client, "quickuser2", "test123")
        db.execute("INSERT INTO recipes (name, servings) VALUES ('Quick Recipe', 2)")
        db.execute(
            "INSERT INTO meal_rules (day_of_week, meal_type, tag_filter, is_active, sort_order)"
            " VALUES ('mon', 'dinner', '', 1, 1)"
        )
        db.commit()

        client.post(
            "/meal-plan/rules/quick-start",
            data={"template": "dinners"},
            follow_redirects=True,
        )
        count = db.execute("SELECT COUNT(*) FROM meal_rules").fetchone()[0]
        assert count == 1

    def test_quick_start_requires_a_recipe(self, client, db):
        _login(client, "quickuser3", "test123")
        client.post(
            "/meal-plan/rules/quick-start",
            data={"template": "dinners"},
            follow_redirects=True,
        )
        count = db.execute("SELECT COUNT(*) FROM meal_rules").fetchone()[0]
        assert count == 0

    def test_quick_start_ignores_unknown_template(self, client, db):
        _login(client, "quickuser4", "test123")
        db.execute("INSERT INTO recipes (name, servings) VALUES ('Quick Recipe', 2)")
        db.commit()

        client.post(
            "/meal-plan/rules/quick-start",
            data={"template": "nonsense"},
            follow_redirects=True,
        )
        count = db.execute("SELECT COUNT(*) FROM meal_rules").fetchone()[0]
        assert count == 0

# ============================================================================
# Recipe scaling
# ============================================================================

class TestRecipeScaling:
    """The recipe detail page can be scaled to a different serving count."""

    def _make_recipe(self, db, servings=2, quantity=200):
        db.execute(
            "INSERT INTO recipes (name, servings) VALUES ('Scalable Stew', ?)", (servings,)
        )
        recipe_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.execute("INSERT INTO ingredients (name, category, unit) VALUES ('Rice', 'pantry', 'g')")
        ing_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.execute(
            "INSERT INTO recipe_ingredients (recipe_id, ingredient_id, quantity) VALUES (?, ?, ?)",
            (recipe_id, ing_id, quantity),
        )
        db.commit()
        return recipe_id

    def test_detail_shows_base_quantities(self, client, db):
        _login(client, "scaleuser", "test123")
        recipe_id = self._make_recipe(db)
        resp = client.get(f"/recipes/{recipe_id}")
        assert resp.status_code == 200
        assert b"200" in resp.data

    def test_detail_scales_up(self, client, db):
        _login(client, "scaleuser2", "test123")
        recipe_id = self._make_recipe(db)
        resp = client.get(f"/recipes/{recipe_id}?servings=4")
        assert resp.status_code == 200
        assert b"400" in resp.data
        assert b"scaled" in resp.data

    def test_detail_scales_down(self, client, db):
        _login(client, "scaleuser3", "test123")
        recipe_id = self._make_recipe(db, servings=4, quantity=400)
        resp = client.get(f"/recipes/{recipe_id}?servings=2")
        assert resp.status_code == 200
        assert b"200" in resp.data

    def test_detail_tolerates_bad_servings(self, client, db):
        _login(client, "scaleuser4", "test123")
        recipe_id = self._make_recipe(db)
        resp = client.get(f"/recipes/{recipe_id}?servings=abc")
        assert resp.status_code == 200
        assert b"200" in resp.data

# ============================================================================
# Undo deletes
# ============================================================================

class TestUndoDelete:
    """Deletes can be undone for a few minutes."""

    @staticmethod
    def _token(resp):
        import re
        match = re.search(r"undo=(\d+)", resp.headers.get("Location", ""))
        return int(match.group(1)) if match else None

    def _ingredient(self, db, name="Undo Carrot"):
        db.execute("INSERT INTO ingredients (name, category, unit) VALUES (?, 'produce', 'g')", (name,))
        ing_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.execute("INSERT INTO recipes (name, servings) VALUES ('Undo Recipe', 2)")
        recipe_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.execute(
            "INSERT INTO recipe_ingredients (recipe_id, ingredient_id, quantity) VALUES (?, ?, 100)",
            (recipe_id, ing_id),
        )
        db.execute(
            "INSERT INTO pantry_items (ingredient_id, quantity, unit) VALUES (?, 50, 'g')", (ing_id,)
        )
        db.commit()
        return ing_id

    def test_ingredient_delete_returns_undo_token(self, client, db):
        _login(client, "undouser", "test123")
        ing_id = self._ingredient(db)
        resp = client.post(f"/ingredients/delete/{ing_id}")
        assert resp.status_code == 302
        assert self._token(resp) is not None
        assert db.execute("SELECT COUNT(*) FROM ingredients WHERE id = ?", (ing_id,)).fetchone()[0] == 0

    def test_undo_restores_ingredient_links_and_stock(self, client, db):
        _login(client, "undouser2", "test123")
        ing_id = self._ingredient(db)
        token = self._token(client.post(f"/ingredients/delete/{ing_id}"))

        resp = client.post(f"/undo/{token}", data={"kind": "ingredient"}, follow_redirects=True)
        assert resp.status_code == 200
        assert db.execute("SELECT COUNT(*) FROM ingredients WHERE id = ?", (ing_id,)).fetchone()[0] == 1
        assert db.execute(
            "SELECT COUNT(*) FROM recipe_ingredients WHERE ingredient_id = ?", (ing_id,)
        ).fetchone()[0] == 1
        assert db.execute(
            "SELECT COUNT(*) FROM pantry_items WHERE ingredient_id = ?", (ing_id,)
        ).fetchone()[0] == 1

    def test_undo_restores_recipe(self, client, db):
        _login(client, "undouser3", "test123")
        ing_id = self._ingredient(db)
        recipe_id = db.execute("SELECT id FROM recipes WHERE name = 'Undo Recipe'").fetchone()[0]
        token = self._token(client.post(f"/recipes/delete/{recipe_id}"))

        client.post(f"/undo/{token}", data={"kind": "recipe"}, follow_redirects=True)
        assert db.execute("SELECT COUNT(*) FROM recipes WHERE id = ?", (recipe_id,)).fetchone()[0] == 1
        assert db.execute(
            "SELECT COUNT(*) FROM recipe_ingredients WHERE recipe_id = ?", (recipe_id,)
        ).fetchone()[0] == 1

    def test_undo_restores_pantry_item(self, client, db):
        _login(client, "undouser4", "test123")
        ing_id = self._ingredient(db)
        item_id = db.execute("SELECT id FROM pantry_items WHERE ingredient_id = ?", (ing_id,)).fetchone()[0]
        token = self._token(client.post(f"/pantry/delete/{item_id}"))

        client.post(f"/undo/{token}", data={"kind": "pantry"}, follow_redirects=True)
        assert db.execute("SELECT COUNT(*) FROM pantry_items WHERE id = ?", (item_id,)).fetchone()[0] == 1

    def test_bad_token_is_reported_not_crashed(self, client, db):
        _login(client, "undouser5", "test123")
        resp = client.post("/undo/999999", data={"kind": "ingredient"}, follow_redirects=True)
        assert resp.status_code == 200
        assert b"can no longer be undone" in resp.data

    def test_expired_token_is_not_restored(self, client, db):
        _login(client, "undouser6", "test123")
        ing_id = self._ingredient(db)
        token = self._token(client.post(f"/ingredients/delete/{ing_id}"))
        db.execute(
            "UPDATE deleted_items SET deleted_at = '2000-01-01 00:00:00' WHERE id = ?", (token,)
        )
        db.commit()

        client.post(f"/undo/{token}", data={"kind": "ingredient"}, follow_redirects=True)
        assert db.execute("SELECT COUNT(*) FROM ingredients WHERE id = ?", (ing_id,)).fetchone()[0] == 0

    def test_list_page_shows_undo_button(self, client, db):
        _login(client, "undouser7", "test123")
        ing_id = self._ingredient(db)
        resp = client.post(f"/ingredients/delete/{ing_id}", follow_redirects=True)
        assert b"Undo delete" in resp.data

# ============================================================================
# Product link suggestions
# ============================================================================

class TestProductSuggestions:
    """The products page can pre-select likely ingredient matches."""

    def _seed(self, db):
        db.execute(
            "INSERT INTO ingredients (name, category, unit) VALUES ('Milk (Full Cream)', 'dairy', 'L')"
        )
        db.execute(
            """INSERT INTO purchases
               (order_date, basket_id, store, product_name, quantity, unit_price, line_total)
               VALUES ('2026-10-01', 'basket-1', 'Woolworths',
                       'Woolworths Full Cream Milk 2L', 1, 3.5, 3.5)"""
        )
        db.commit()

    def test_suggest_flag_preselects_a_match(self, client, db):
        _login(client, "suggestuser", "test123")
        self._seed(db)
        resp = client.get("/spend/products?suggest=1")
        assert resp.status_code == 200
        assert b"suggested" in resp.data

    def test_page_without_suggest_has_no_suggestion_badge(self, client, db):
        _login(client, "suggestuser2", "test123")
        self._seed(db)
        resp = client.get("/spend/products")
        assert resp.status_code == 200
        assert b"Pre-selected" not in resp.data

    def test_htmx_csrf_hook_is_registered_on_document(self, client, db):
        """The hook runs in <head>, where document.body is null — regression guard."""
        _login(client, "suggestuser3", "test123")
        resp = client.get("/spend/products")
        assert b"document.addEventListener('htmx:configRequest'" in resp.data
        assert b"document.body.addEventListener('htmx:configRequest'" not in resp.data

# ============================================================================
# Global search
# ============================================================================

class TestGlobalSearch:
    """Search finds ingredients, recipes, products and bills in one place."""

    def _seed(self, db):
        db.execute("INSERT INTO ingredients (name, category, unit) VALUES ('Cheddar Cheese', 'dairy', 'g')")
        db.execute("INSERT INTO recipes (name, servings, tags) VALUES ('Cheese Toastie', 1, 'lunch, quick')")
        db.execute(
            """INSERT INTO purchases
               (order_date, basket_id, store, product_name, quantity, unit_price, line_total)
               VALUES ('2026-10-02', 'basket-9', 'Woolworths', 'Tasty Cheese Block 500g', 1, 6.0, 6.0)"""
        )
        db.execute(
            "INSERT INTO subscriptions (name, amount, frequency) VALUES ('Cheese Club', 9.99, 'monthly')"
        )
        db.commit()

    def test_search_page_without_query(self, client, db):
        _login(client, "searchuser", "test123")
        resp = client.get("/search")
        assert resp.status_code == 200
        assert b"Search ingredients, recipes" in resp.data

    def test_search_matches_all_groups(self, client, db):
        _login(client, "searchuser2", "test123")
        self._seed(db)
        resp = client.get("/search?q=cheese")
        assert resp.status_code == 200
        assert b"Cheddar Cheese" in resp.data
        assert b"Cheese Toastie" in resp.data
        assert b"Tasty Cheese Block 500g" in resp.data
        assert b"Cheese Club" in resp.data

    def test_search_no_results_message(self, client, db):
        _login(client, "searchuser3", "test123")
        resp = client.get("/search?q=zzzznothing")
        assert resp.status_code == 200
        assert b"Nothing matched" in resp.data

    def test_search_requires_login(self, client, db):
        resp = client.get("/search?q=cheese")
        assert resp.status_code in (301, 302)

    def test_navbar_has_search_and_shortcut_hint(self, client, db):
        _login(client, "searchuser4", "test123")
        resp = client.get("/")
        assert b'id="navbar-search"' in resp.data
        assert b"Ctrl+K" in resp.data
        assert b"'k'" in resp.data

# ============================================================================
# Recipe favourites and image upload
# ============================================================================

class TestRecipeFavourites:
    def test_toggle_marks_favourite(self, client, db):
        _login(client, "favuser", "test123")
        db.execute("INSERT INTO recipes (name, servings) VALUES ('Fav Dish', 2)")
        db.commit()
        recipe_id = db.execute("SELECT id FROM recipes WHERE name = 'Fav Dish'").fetchone()[0]

        resp = client.post(f"/recipes/{recipe_id}/favourite", follow_redirects=True)
        assert resp.status_code == 200
        assert db.execute("SELECT is_favourite FROM recipes WHERE id = ?", (recipe_id,)).fetchone()[0] == 1

        client.post(f"/recipes/{recipe_id}/favourite", follow_redirects=True)
        assert db.execute("SELECT is_favourite FROM recipes WHERE id = ?", (recipe_id,)).fetchone()[0] == 0

    def test_favourites_filter_lists_only_starred(self, client, db):
        _login(client, "favuser2", "test123")
        db.execute("INSERT INTO recipes (name, servings, is_favourite) VALUES ('Starred', 1, 1)")
        db.execute("INSERT INTO recipes (name, servings, is_favourite) VALUES ('Plain', 1, 0)")
        db.commit()

        resp = client.get("/recipes?favourites=1")
        assert resp.status_code == 200
        assert b"Starred" in resp.data
        assert b"Plain" not in resp.data

    def test_toggle_missing_recipe_is_handled(self, client, db):
        _login(client, "favuser3", "test123")
        resp = client.post("/recipes/999999/favourite", follow_redirects=True)
        assert resp.status_code == 200
        assert b"Recipe not found" in resp.data


class TestRecipeImageUpload:
    PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64

    def test_upload_stores_image_and_serves_it(self, client, db):
        import io
        _login(client, "imguser", "test123")
        data = {
            "name": "Photo Dish",
            "servings": "2",
            "image_file": (io.BytesIO(self.PNG), "dish.png"),
        }
        resp = client.post("/recipes/add", data=data, content_type="multipart/form-data",
                           follow_redirects=True)
        assert resp.status_code == 200

        row = db.execute("SELECT id, image_url FROM recipes WHERE name = 'Photo Dish'").fetchone()
        assert row["image_url"].startswith("/recipe-images/")

        image = client.get(row["image_url"])
        assert image.status_code == 200
        assert image.data == self.PNG

    def test_rejects_non_image_file(self, client, db):
        import io
        _login(client, "imguser2", "test123")
        data = {
            "name": "Bad Image Dish",
            "servings": "2",
            "image_file": (io.BytesIO(b"not an image"), "notes.txt"),
        }
        resp = client.post("/recipes/add", data=data, content_type="multipart/form-data")
        assert resp.status_code == 200
        assert b"must be PNG, JPG, WEBP or GIF" in resp.data
        assert db.execute("SELECT COUNT(*) FROM recipes WHERE name = 'Bad Image Dish'").fetchone()[0] == 0

    def test_rejects_spoofed_extension(self, client, db):
        import io
        _login(client, "imguser3", "test123")
        data = {
            "name": "Spoof Dish",
            "servings": "2",
            "image_file": (io.BytesIO(b"still not an image"), "fake.png"),
        }
        resp = client.post("/recipes/add", data=data, content_type="multipart/form-data")
        assert resp.status_code == 200
        assert b"not a recognised image" in resp.data

    def test_image_route_requires_login(self, client, db):
        resp = client.get("/recipe-images/whatever.png")
        assert resp.status_code in (301, 302)

    def test_rules_tab_offers_quick_start_when_empty(self, client, db):
        _login(client, "quickuser5", "test123")
        db.execute("INSERT INTO recipes (name, servings) VALUES ('Quick Recipe', 2)")
        db.commit()
        resp = client.get("/meal-plan?tab=rules")
        assert resp.status_code == 200
        assert b"Quick start" in resp.data
        assert b"Dinners every night" in resp.data

    def test_rules_tab_hides_quick_start_once_rules_exist(self, client, db):
        _login(client, "quickuser6", "test123")
        db.execute(
            "INSERT INTO meal_rules (day_of_week, meal_type, tag_filter, is_active, sort_order)"
            " VALUES ('all', 'dinner', '', 1, 1)"
        )
        db.commit()
        resp = client.get("/meal-plan?tab=rules")
        assert resp.status_code == 200
        assert b"Create rules" not in resp.data

    def test_toggle_returns_fragment_for_htmx(self, client, db):
        """The star swaps in place, so the HTMX branch must render a button."""
        _login(client, "favuser4", "test123")
        db.execute("INSERT INTO recipes (name, servings) VALUES ('Fav HTMX', 2)")
        db.commit()
        recipe_id = db.execute("SELECT id FROM recipes WHERE name = 'Fav HTMX'").fetchone()[0]

        resp = client.post(
            f"/recipes/{recipe_id}/favourite",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert b"<form" in resp.data
        assert "★".encode() in resp.data
        assert db.execute("SELECT is_favourite FROM recipes WHERE id = ?", (recipe_id,)).fetchone()[0] == 1

        resp = client.post(
            f"/recipes/{recipe_id}/favourite",
            headers={"HX-Request": "true"},
        )
        assert "☆".encode() in resp.data
        assert db.execute("SELECT is_favourite FROM recipes WHERE id = ?", (recipe_id,)).fetchone()[0] == 0
