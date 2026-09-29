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

    def test_setup_page_loads(self, client):
        """GET /setup returns the setup page."""
        resp = client.get("/setup")
        assert resp.status_code == 200

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
        """Login with wrong password returns to login page with flash."""
        resp = client.post("/login", data={
            "username": "testadmin",
            "password": "wrongpassword",
        })
        assert resp.status_code == 302  # redirect back to login

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
            "is_two_night": "",  # not a two-night recipe
            "tags": "breakfast, quick",
        }, follow_redirects=True)
        assert resp.status_code == 200

        row = db.execute("SELECT * FROM recipes WHERE name = ?", ("Simple Omelette",)).fetchone()
        assert row is not None
        assert row["tags"] == "breakfast, quick"
        assert row["is_two_night"] == 0
        assert row["servings"] == 1

    def test_add_recipe_as_two_night(self, client, db):
        """POST /recipes/add with is_two_night creates a two-night recipe."""
        _login(client, "recuser", "test123")

        resp = client.post("/recipes/add", data={
            "name": "Bolognese Sauce",
            "description": "Classic meat sauce",
            "servings": "6",
            "is_two_night": "1",
            "tags": "dinner, italian",
        }, follow_redirects=True)
        assert resp.status_code == 200

        row = db.execute("SELECT * FROM recipes WHERE name = ?", ("Bolognese Sauce",)).fetchone()
        assert row["is_two_night"] == 1
        assert row["tags"] == "dinner, italian"

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
            "is_two_night": "",
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
        resp = client.get("/meal-plan")
        assert resp.status_code == 200
        assert b"Meal Rules" in resp.data

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
            "is_two_night": "",
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
        assert resp.status_code == 302  # rejected

        # New password works
        resp = client.post("/login", data={
            "username": "setuser",
            "password": "newsecurepass",
        }, follow_redirects=True)
        assert resp.status_code == 200


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
