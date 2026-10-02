"""Unit tests for shopping list generation service."""

import pytest

from services.shopping_list_generator import (
    convert_to_display_unit,
    generate_shopping_list,
    make_plan_entry,
    make_recipe_dict,
    make_recipe_ingredient,
    list_totals,
    make_ingredient,
    pack_candidates,
    pack_options,
    plan_list_lines,
    recipes_using_spares,
    spares,
    packs_to_buy,
    parse_pack_size,
)


# ============================================================================
# Unit conversion
# ============================================================================

class TestConvertToDisplayUnit:
    """Test metric/imperial unit conversion for display."""

    def test_metric_no_conversion(self):
        result = convert_to_display_unit(2.0, "kg", "metric")
        assert result["quantity"] == 2.0
        assert result["unit"] == "kg"

    def test_imperial_kg_to_lb(self):
        result = convert_to_display_unit(1.0, "kg", "imperial")
        assert result["unit"] == "lb"
        assert result["quantity"] == pytest.approx(2.20462, abs=0.01)

    def test_imperial_g_to_oz(self):
        result = convert_to_display_unit(100.0, "g", "imperial")
        assert result["unit"] == "oz"
        assert result["quantity"] == pytest.approx(3.53, abs=0.01)

    def test_imperial_L_to_gal(self):
        result = convert_to_display_unit(1.0, "L", "imperial")
        assert result["unit"] == "gal"
        assert result["quantity"] == pytest.approx(0.26, abs=0.01)

    def test_imperial_mL_to_fl_oz(self):
        result = convert_to_display_unit(100.0, "mL", "imperial")
        assert result["unit"] == "fl_oz"
        assert result["quantity"] == pytest.approx(3.38, abs=0.01)

    def test_count_units_not_converted(self):
        for unit in ["each", "pack", "bunch", "can", "slice"]:
            result = convert_to_display_unit(5.0, unit, "imperial")
            assert result["unit"] == unit
            assert result["quantity"] == 5.0


# ============================================================================
# Shopping list aggregation
# ============================================================================

class TestGenerateShoppingList:
    """Test shopping list generation from plan entries."""

    def test_empty_entries_returns_empty(self):
        result = generate_shopping_list([], {}, {}, {}, "metric", False, None)
        assert result == {}

    def test_single_recipe_single_serving(self):
        plan = [make_plan_entry("2026-10-05", "dinner", recipe_id=1, servings=4)]
        recipes = {1: make_recipe_dict(1, "Spaghetti Bolognese", servings=4)}
        ri = {
            1: [
                make_recipe_ingredient(ingredient_id=1, quantity=500, unit_override="g"),
            ]
        }
        ingredients = {
            1: make_ingredient(1, "Minced Beef", unit="g", category="meat"),
        }
        result = generate_shopping_list(plan, recipes, ri, ingredients, "metric", False, None)
        assert "meat" in result
        items = result["meat"]
        assert len(items) == 1
        assert items[0]["ingredient_name"] == "Minced Beef"
        assert items[0]["quantity"] == 500
        assert items[0]["unit"] == "g"

    def test_servings_scaling(self):
        """Servings override scales ingredient quantities."""
        plan = [make_plan_entry("2026-10-05", "dinner", recipe_id=1, servings=2)]
        recipes = {1: make_recipe_dict(1, "Curry", servings=4)}
        ri = {
            1: [
                make_recipe_ingredient(ingredient_id=1, quantity=1000, unit_override="g"),
            ]
        }
        ingredients = {1: make_ingredient(1, "Chicken", unit="g", category="meat")}
        result = generate_shopping_list(plan, recipes, ri, ingredients, "metric", False, None)
        items = result["meat"]
        assert items[0]["quantity"] == 500  # 1000 * (2/4)

    def test_multiple_recipes_same_ingredient_combined(self):
        """Same ingredient from different recipes is combined."""
        plan = [
            make_plan_entry("2026-10-05", "dinner", recipe_id=1, servings=4),
            make_plan_entry("2026-10-06", "dinner", recipe_id=2, servings=4),
        ]
        recipes = {
            1: make_recipe_dict(1, "Pasta Tomato", servings=4),
            2: make_recipe_dict(2, "Tomato Soup", servings=4),
        }
        ri = {
            1: [make_recipe_ingredient(1, 400, unit_override="g")],
            2: [make_recipe_ingredient(1, 300, unit_override="g")],
        }
        ingredients = {1: make_ingredient(1, "Canned Tomatoes", unit="g", category="pantry")}
        result = generate_shopping_list(plan, recipes, ri, ingredients, "metric", False, None)
        items = result["pantry"]
        assert len(items) == 1
        assert items[0]["quantity"] == 700  # 400 + 300
        assert "Pasta Tomato" in items[0]["recipe_refs"]
        assert "Tomato Soup" in items[0]["recipe_refs"]

    def test_unit_override_used(self):
        """Recipe ingredient unit_override takes precedence over ingredient unit."""
        plan = [make_plan_entry("2026-10-05", "dinner", recipe_id=1, servings=4)]
        recipes = {1: make_recipe_dict(1, "Salad", servings=4)}
        ri = {
            1: [
                make_recipe_ingredient(ingredient_id=1, quantity=2, unit_override="each"),
            ]
        }
        ingredients = {1: make_ingredient(1, "Tomatoes", unit="kg", category="produce")}
        result = generate_shopping_list(plan, recipes, ri, ingredients, "metric", False, None)
        items = result["produce"]
        assert items[0]["unit"] == "each"  # override used
        assert items[0]["quantity"] == 2

    def test_categories_grouped(self):
        """Items are grouped by category in the result."""
        plan = [
            make_plan_entry("2026-10-05", "dinner", recipe_id=1, servings=4),
        ]
        recipes = {1: make_recipe_dict(1, "Mixed Meal", servings=4)}
        ri = {
            1: [
                make_recipe_ingredient(1, 500, unit_override="g"),   # meat
                make_recipe_ingredient(2, 200, unit_override="g"),   # produce
            ]
        }
        ingredients = {
            1: make_ingredient(1, "Beef", unit="g", category="meat"),
            2: make_ingredient(2, "Onion", unit="g", category="produce"),
        }
        result = generate_shopping_list(plan, recipes, ri, ingredients, "metric", False, None)
        assert "meat" in result
        assert "produce" in result
        assert len(result) == 2

    def test_imperial_conversion_applied(self):
        """Imperial preference converts kg to lb."""
        plan = [make_plan_entry("2026-10-05", "dinner", recipe_id=1, servings=4)]
        recipes = {1: make_recipe_dict(1, "Steak", servings=4)}
        ri = {1: [make_recipe_ingredient(1, 1, unit_override="kg")]}
        ingredients = {1: make_ingredient(1, "Ribeye", unit="kg", category="meat")}
        result = generate_shopping_list(plan, recipes, ri, ingredients, "imperial", False, None)
        items = result["meat"]
        assert items[0]["unit"] == "lb"
        assert items[0]["quantity"] == pytest.approx(2.20, abs=0.01)

    def test_imperial_no_conversion_for_count_units(self):
        """Count units stay as-is in imperial mode."""
        plan = [make_plan_entry("2026-10-05", "dinner", recipe_id=1, servings=4)]
        recipes = {1: make_recipe_dict(1, "Sandwich", servings=4)}
        ri = {1: [make_recipe_ingredient(1, 4, unit_override="each")]}
        ingredients = {1: make_ingredient(1, "Bread Rolls", unit="each", category="bakery")}
        result = generate_shopping_list(plan, recipes, ri, ingredients, "imperial", False, None)
        items = result["bakery"]
        assert items[0]["unit"] == "each"
        assert items[0]["quantity"] == 4

    def test_pantry_subtraction(self):
        """Pantry items subtract from shopping list quantities."""
        plan = [make_plan_entry("2026-10-05", "dinner", recipe_id=1, servings=4)]
        recipes = {1: make_recipe_dict(1, "Rice Bowl", servings=4)}
        ri = {1: [make_recipe_ingredient(1, 1000, unit_override="g")]}
        ingredients = {1: make_ingredient(1, "Rice", unit="g", category="pantry")}
        pantry = {"Rice": 400}  # already have 400g
        result = generate_shopping_list(
            plan, recipes, ri, ingredients, "metric", True, pantry
        )
        items = result["pantry"]
        assert items[0]["quantity"] == 600  # 1000 - 400

    def test_pantry_subtraction_removes_if_enough(self):
        """If pantry covers the full amount, item is removed."""
        plan = [make_plan_entry("2026-10-05", "dinner", recipe_id=1, servings=4)]
        recipes = {1: make_recipe_dict(1, "Rice Bowl", servings=4)}
        ri = {1: [make_recipe_ingredient(1, 500, unit_override="g")]}
        ingredients = {1: make_ingredient(1, "Rice", unit="g", category="pantry")}
        pantry = {"Rice": 600}  # more than enough
        result = generate_shopping_list(
            plan, recipes, ri, ingredients, "metric", True, pantry
        )
        assert "pantry" not in result  # removed entirely

    def test_pantry_subtraction_negative_threshold(self):
        """Pantry subtraction doesn't go below zero."""
        plan = [make_plan_entry("2026-10-05", "dinner", recipe_id=1, servings=4)]
        recipes = {1: make_recipe_dict(1, "Rice Bowl", servings=4)}
        ri = {1: [make_recipe_ingredient(1, 100, unit_override="g")]}
        ingredients = {1: make_ingredient(1, "Rice", unit="g", category="pantry")}
        pantry = {"Rice": 200}
        result = generate_shopping_list(
            plan, recipes, ri, ingredients, "metric", True, pantry
        )
        assert "pantry" not in result

    def test_pantry_subtraction_disabled(self):
        """When disabled, pantry is not subtracted even if provided."""
        plan = [make_plan_entry("2026-10-05", "dinner", recipe_id=1, servings=4)]
        recipes = {1: make_recipe_dict(1, "Rice Bowl", servings=4)}
        ri = {1: [make_recipe_ingredient(1, 1000, unit_override="g")]}
        ingredients = {1: make_ingredient(1, "Rice", unit="g", category="pantry")}
        pantry = {"Rice": 500}
        result = generate_shopping_list(
            plan, recipes, ri, ingredients, "metric", False, pantry
        )
        items = result["pantry"]
        assert items[0]["quantity"] == 1000  # not subtracted

    def test_rounding_of_quantities(self):
        """Quantities are rounded to 2 decimal places, integers shown as ints."""
        plan = [make_plan_entry("2026-10-05", "dinner", recipe_id=1, servings=3)]
        recipes = {1: make_recipe_dict(1, "Stew", servings=4)}
        ri = {1: [make_recipe_ingredient(1, 1000, unit_override="g")]}
        ingredients = {1: make_ingredient(1, "Potatoes", unit="g", category="produce")}
        result = generate_shopping_list(plan, recipes, ri, ingredients, "metric", False, None)
        items = result["produce"]
        assert items[0]["quantity"] == 750  # 1000 * 3/4 = 750.0 → int

    def test_multiple_meal_types_same_recipe(self):
        """Same recipe used for lunch and dinner both contribute."""
        plan = [
            make_plan_entry("2026-10-05", "lunch", recipe_id=1, servings=2),
            make_plan_entry("2026-10-05", "dinner", recipe_id=1, servings=4),
        ]
        recipes = {1: make_recipe_dict(1, "Pasta", servings=4)}
        ri = {1: [make_recipe_ingredient(1, 400, unit_override="g")]}
        ingredients = {1: make_ingredient(1, "Pasta", unit="g", category="pantry")}
        result = generate_shopping_list(plan, recipes, ri, ingredients, "metric", False, None)
        items = result["pantry"]
        # lunch: 400 * (2/4) = 200; dinner: 400 * (4/4) = 400; total = 600
        assert items[0]["quantity"] == 600

    def test_sorted_categories(self):
        """Categories in result are sorted alphabetically."""
        plan = [make_plan_entry("2026-10-05", "dinner", recipe_id=1, servings=4)]
        recipes = {1: make_recipe_dict(1, "Meal", servings=4)}
        # Insert in reverse alphabetical order to verify sorting
        ri = {
            1: [
                make_recipe_ingredient(1, 1, unit_override="each"),  # meat
                make_recipe_ingredient(2, 1, unit_override="each"),  # snacks
            ]
        }
        ingredients = {
            1: make_ingredient(1, "Chicken", unit="each", category="meat"),
            2: make_ingredient(2, "Chips", unit="each", category="snacks"),
        }
        result = generate_shopping_list(plan, recipes, ri, ingredients, "metric", False, None)
        categories = list(result.keys())
        assert categories == sorted(categories)

    def test_items_sorted_by_name_within_category(self):
        """Items within a category are sorted by name."""
        plan = [make_plan_entry("2026-10-05", "dinner", recipe_id=1, servings=4)]
        recipes = {1: make_recipe_dict(1, "Meal", servings=4)}
        ri = {
            1: [
                make_recipe_ingredient(2, 1, unit_override="each"),  # Zucchini
                make_recipe_ingredient(1, 1, unit_override="each"),  # Apple
            ]
        }
        ingredients = {
            1: make_ingredient(1, "Apple", unit="each", category="produce"),
            2: make_ingredient(2, "Zucchini", unit="each", category="produce"),
        }
        result = generate_shopping_list(plan, recipes, ri, ingredients, "metric", False, None)
        items = result["produce"]
        assert [i["ingredient_name"] for i in items] == ["Apple", "Zucchini"]


# ============================================================================
# Integration-style: full workflow
# ============================================================================

class TestShoppingListWorkflow:
    """Simulate a realistic shopping list generation."""

    def test_week_of_meals_generates_list(self):
        """A week of meals generates a properly aggregated shopping list."""
        # Plan: Mon-Fri dinners (5 entries)
        plan = []
        for i in range(5):
            plan.append(make_plan_entry(
                date=f"2026-10-0{i+5}",
                meal_type="dinner",
                recipe_id=1,
                servings=4,
            ))

        recipes = {
            1: make_recipe_dict(1, "Spaghetti Bolognese", servings=4),
        }
        ri = {
            1: [
                make_recipe_ingredient(1, 500, unit_override="g"),   # Beef
                make_recipe_ingredient(2, 400, unit_override="g"),   # Tomatoes
                make_recipe_ingredient(3, 2, unit_override="each"),  # Garlic bulbs
            ]
        }
        ingredients = {
            1: make_ingredient(1, "Minced Beef", unit="g", category="meat", price=0.012),
            2: make_ingredient(2, "Canned Tomatoes", unit="g", category="pantry"),
            3: make_ingredient(3, "Garlic", unit="each", category="produce"),
        }

        result = generate_shopping_list(plan, recipes, ri, ingredients, "metric", False, None)

        # 5 servings of 500g = 2500g beef
        meat = result["meat"]
        assert len(meat) == 1
        assert meat[0]["ingredient_name"] == "Minced Beef"
        assert meat[0]["quantity"] == 2500

        # 5 servings of 400g = 2000g tomatoes
        pantry = result["pantry"]
        assert len(pantry) == 1
        assert pantry[0]["quantity"] == 2000

        # 5 servings of 2 = 10 garlic bulbs
        produce = result["produce"]
        assert len(produce) == 1
        assert produce[0]["quantity"] == 10

    def test_pantry_aware_weekly_list(self):
        """Weekly list with pantry subtraction gives net quantities."""
        plan = [
            make_plan_entry("2026-10-05", "dinner", recipe_id=1, servings=4),
            make_plan_entry("2026-10-06", "dinner", recipe_id=1, servings=4),
        ]
        recipes = {1: make_recipe_dict(1, "Pasta", servings=4)}
        ri = {1: [make_recipe_ingredient(1, 400, unit_override="g")]}
        ingredients = {1: make_ingredient(1, "Pasta", unit="g", category="pantry")}
        pantry = {"Pasta": 500}

        result = generate_shopping_list(
            plan, recipes, ri, ingredients, "metric", True, pantry
        )
        # Need 800g total, have 500g, buy 300g
        assert result["pantry"][0]["quantity"] == 300


@pytest.mark.parametrize("name,unit,expected", [
    ("Woolworths Pork & Beef Mince 500g", "kg", 0.5),
    ("The Odd Bunch Carrots 1.5kg", "kg", 1.5),
    ("Liddells yoghurt blueberry 140g x 4 pack", "kg", 0.56),
    ("Pepsi max cans 30x375ml", "L", 11.25),
    ("Golden crumpet squares 6 pack", "each", 6),
    ("Milk UHT 1L", "L", 1.0),
    ("Onion Brown each", "each", None),
    ("Woolworths 6 Extra Large Free Range Eggs 350g", "each", None),
    ("Spaghetti 500g", "L", None),
    ("", "kg", None),
])
def test_parse_pack_size(name, unit, expected):
    result = parse_pack_size(name, unit)
    assert result == (pytest.approx(expected) if expected is not None else None)


def test_packs_to_buy():
    assert packs_to_buy(3, "kg", 0.5, "kg") == 6
    assert packs_to_buy(0.3, "kg", 1.5, "kg") == 1
    assert packs_to_buy(1500, "g", 0.5, "kg") == 3
    assert packs_to_buy(1.0000001, "kg", 0.5, "kg") == 2
    assert packs_to_buy(1, "kg", None, "kg") is None
    assert packs_to_buy(1, "kg", 0.5, "L") is None


def _best(quantity, unit, ingredient, products=()):
    options = pack_options(quantity, unit, pack_candidates(ingredient, list(products)))
    return options[0] if options else None


def test_best_pack_prefers_explicit_pack_size_and_shows_small_packs_in_grams():
    mince = {"name": "Pork & Beef Mince 500g", "unit": "kg", "pack_size": None, "price": None}
    best = _best(3, "kg", mince)
    assert (best["count"], best["size"], best["unit"], best["cost"], best["spare"]) == (6, 500, "g", None, 0)
    eggs = {"name": "6 Extra Large Eggs 350g", "unit": "each", "pack_size": 6, "price": None}
    assert (_best(12, "each", eggs)["count"], _best(12, "each", eggs)["size"]) == (2, 6)
    assert _best(2, "each", {"name": "Onion Brown each", "unit": "each", "pack_size": None}) is None


def test_best_pack_prices_whole_packs():
    carrots = {"name": "The Odd Bunch Carrots 1.5kg", "unit": "kg", "pack_size": None, "price": 1.87}
    best = _best(0.3, "kg", carrots)
    assert best["cost"] == pytest.approx(2.81)  # one 1.5 kg bag, not 0.3 kg
    assert best["spare"] == pytest.approx(1.2)
    eggs = {"name": "Eggs", "unit": "each", "pack_size": 6, "price": 0.82}
    assert _best(12, "each", eggs)["cost"] == pytest.approx(9.84)


def test_pack_options_pick_the_size_with_least_spare_then_cheapest():
    cheese = {"name": "Cheese Pizza Blend 225g", "unit": "kg", "pack_size": None, "price": 30.0, "url": "a"}
    products = [
        {"product_name": "Shredded Cheese 250g", "unit_price": 6.75, "url": "b"},
        {"product_name": "Cheese Block 1kg", "unit_price": 20.0},
        {"product_name": "Cheese Slices", "unit_price": 5.0},  # no size: skipped
    ]
    names = [o["name"] for o in pack_options(0.2, "kg", pack_candidates(cheese, products))]
    assert names == ["Cheese Pizza Blend 225g", "Shredded Cheese 250g", "Cheese Block 1kg"]
    # 0.45 kg: two 225 g packs leave nothing spare
    best = pack_options(0.45, "kg", pack_candidates(cheese, products))[0]
    assert (best["name"], best["count"], best["spare"]) == ("Cheese Pizza Blend 225g", 2, 0)
    # equal spare -> cheaper wins
    same = [{"name": "A 500g", "size": 0.5, "unit": "kg", "price": 5.0}, {"name": "B 500g", "size": 0.5, "unit": "kg", "price": 4.0}]
    assert pack_options(0.5, "kg", same)[0]["name"] == "B 500g"


def test_plan_list_lines_and_spares():
    rows = [
        {"ingredient_id": 9, "ingredient_name": "Carrots", "quantity": 0.3, "unit": "kg", "estimated_cost": 0.56,
         "ing_name": "Carrots 1.5kg", "ing_unit": "kg", "ing_pack_size": None, "ing_price": 1.87, "product_url": None},
        {"ingredient_id": None, "ingredient_name": "Foil", "quantity": 1, "unit": "each", "estimated_cost": None,
         "ing_name": None, "is_manual": 1},
    ]
    lines = plan_list_lines(rows, {})
    assert lines[0]["line_cost"] == pytest.approx(2.81) and lines[1]["pack_choice"] is None
    assert spares(lines) == [{"ingredient_id": 9, "name": "Carrots", "quantity": pytest.approx(1.2), "unit": "kg",
                              "shown": pytest.approx(1.2), "shown_unit": "kg"}]
    # 25 g left of a 225 g pack is under 20% of a pack: not worth planning a meal around
    cheese = [{"ingredient_id": 5, "ingredient_name": "Cheese", "quantity": 0.2, "unit": "kg", "estimated_cost": None,
               "ing_name": "Cheese 225g", "ing_unit": "kg", "ing_pack_size": None, "ing_price": None}]
    lined = plan_list_lines(cheese, {})
    assert lined[0]["pack_choice"]["best"]["spare_shown"] == pytest.approx(25) and spares(lined) == []


def test_recipes_using_spares():
    spare = [{"ingredient_id": 9, "name": "Carrots", "quantity": 1.2, "unit": "kg"}]
    recipes = {1: {"name": "Carrot soup"}, 2: {"name": "Stir fry"}, 3: {"name": "Toast"}}
    links = {
        1: [{"ingredient_id": 9, "quantity": 800, "unit_override": "g"}],  # needs 0.8 kg: covered
        2: [{"ingredient_id": 9, "quantity": 5, "unit_override": None}],   # needs 5 kg: spare too small
        3: [{"ingredient_id": 4, "quantity": 2, "unit_override": None}],
    }
    ingredients = {9: {"unit": "kg"}, 4: {"unit": "each"}}
    assert recipes_using_spares(spare, recipes, links, ingredients) == [
        {"recipe_id": 1, "name": "Carrot soup", "uses": ["Carrots"]}]


def test_list_totals_uses_pack_cost_and_skips_manual_lines():
    items = [
        {"line_cost": 2.81, "estimated_cost": 0.56, "is_manual": 0},
        {"line_cost": 1.26, "estimated_cost": 1.26, "is_manual": 0},
        {"line_cost": None, "estimated_cost": None, "is_manual": 1},
    ]
    assert list_totals(items) == {"packs_total": 4.07, "exact_total": 1.82}
    assert list_totals([]) == {"packs_total": 0, "exact_total": 0}


def test_parse_pack_size_imperial_units():
    assert parse_pack_size("Flour 2lb", "lb") == pytest.approx(2.0)
    assert parse_pack_size("Sugar 8oz", "oz") == pytest.approx(8.0)
    assert parse_pack_size("Milk 1gal", "gal") == pytest.approx(1.0)
    assert parse_pack_size("Cream 8fl oz", "fl_oz") == pytest.approx(8.0)
    assert parse_pack_size("Cream 8fl_oz", "fl_oz") == pytest.approx(8.0)
    assert parse_pack_size("Mince 500g", "kg") == pytest.approx(0.5)
    assert parse_pack_size("Milk 2L", "gal") == pytest.approx(0.528, abs=0.01)
