"""Unit tests for plan generation service."""

import pytest
from datetime import datetime, timedelta

from services.plan_generator import (
    rule_matches_day,
    select_recipe_for_rule,
    generate_plan_entries,
    make_rule,
    make_recipe,
)


# ============================================================================
# rule_matches_day
# ============================================================================

class TestRuleMatchesDay:
    """Test day-of-week matching for meal rules."""

    def test_exact_day_match_mon(self):
        d = datetime(2026, 10, 5)  # Monday
        assert rule_matches_day("mon", d) is True
        assert rule_matches_day("tue", d) is False

    def test_exact_day_match_fri(self):
        d = datetime(2026, 10, 9)  # Friday
        assert rule_matches_day("fri", d) is True
        assert rule_matches_day("sat", d) is False

    def test_weekday_match(self):
        d = datetime(2026, 10, 5)  # Monday
        assert rule_matches_day("weekday", d) is True
        assert rule_matches_day("weekend", d) is False

    def test_weekend_match(self):
        d = datetime(2026, 10, 10)  # Saturday
        assert rule_matches_day("weekend", d) is True
        assert rule_matches_day("weekday", d) is False

    def test_all_days_match(self):
        for d in [
            datetime(2026, 10, 5),   # Mon
            datetime(2026, 10, 10),  # Sat
            datetime(2026, 10, 11),  # Sun
        ]:
            assert rule_matches_day("all", d) is True


# ============================================================================
# select_recipe_for_rule
# ============================================================================

class TestSelectRecipeForRule:
    """Test recipe selection for a rule."""

    def test_any_recipe_no_filter(self):
        recipes = [
            make_recipe(id=1, name="Pasta"),
            make_recipe(id=2, name="Curry"),
        ]
        result = select_recipe_for_rule(None, recipes)
        assert result is not None
        assert result["id"] == 1  # first in list

    def test_tag_filter_match(self):
        recipes = [
            make_recipe(id=1, name="Pasta", tags="italian,dinner"),
            make_recipe(id=2, name="Curry", tags="indian,dinner"),
        ]
        result = select_recipe_for_rule("italian", recipes)
        assert result is not None
        assert result["id"] == 1

    def test_tag_filter_no_match(self):
        recipes = [
            make_recipe(id=1, name="Pasta", tags="italian"),
            make_recipe(id=2, name="Curry", tags="indian"),
        ]
        result = select_recipe_for_rule("thai", recipes)
        assert result is None

    def test_empty_recipe_list(self):
        assert select_recipe_for_rule("italian", []) is None

    def test_nonexistent_tag_with_no_filter(self):
        # No tag filter means all recipes are candidates
        recipes = [make_recipe(id=1, name="Pasta")]
        assert select_recipe_for_rule(None, recipes) is not None


# ============================================================================
# generate_plan_entries
# ============================================================================

class TestGeneratePlanEntries:
    """Test the full plan generation algorithm."""

    def test_invalid_date_range(self):
        rules = [make_rule()]
        recipes = [make_recipe()]
        entries, err = generate_plan_entries("2026-10-10", "2026-10-01", rules, recipes, [])
        assert entries == []
        assert err is not None

    def test_empty_rules_produces_no_entries(self):
        recipes = [make_recipe(id=1, name="Pasta")]
        entries, err = generate_plan_entries("2026-10-05", "2026-10-05", [], recipes, [])
        assert entries == []
        assert err is None

    def test_single_day_single_rule(self):
        """One dinner rule on Monday generates one entry."""
        rules = [make_rule(day_of_week="mon", meal_type="dinner")]
        recipes = [make_recipe(id=1, name="Spaghetti")]
        entries, err = generate_plan_entries("2026-10-05", "2026-10-05", rules, recipes, [])
        assert err is None
        assert len(entries) == 1
        assert entries[0]["meal_type"] == "dinner"
        assert entries[0]["recipe_id"] == 1

    def test_all_meal_types_filled(self):
        """One rule per meal type on Monday fills all 4 slots."""
        rules = [
            make_rule(day_of_week="mon", meal_type="breakfast", id=1),
            make_rule(day_of_week="mon", meal_type="lunch", id=2),
            make_rule(day_of_week="mon", meal_type="dinner", id=3),
            make_rule(day_of_week="mon", meal_type="snack", id=4),
        ]
        recipes = [
            make_recipe(id=1, name="Oatmeal"),
            make_recipe(id=2, name="Sandwich"),
            make_recipe(id=3, name="Steak"),
            make_recipe(id=4, name="Fruit"),
        ]
        entries, err = generate_plan_entries("2026-10-05", "2026-10-05", rules, recipes, [])
        assert err is None
        assert len(entries) == 4
        types = {e["meal_type"] for e in entries}
        assert types == {"breakfast", "lunch", "dinner", "snack"}

    def test_recipe_covers_multiple_days(self):
        """A recipe covering multiple days fills that many same-type slots."""
        rules = [
            make_rule(day_of_week="mon", meal_type="dinner", id=1),
        ]
        recipes = [
            make_recipe(id=1, name="Bolognese", covers_days=2),
        ]
        existing = []
        entries, err = generate_plan_entries("2026-10-05", "2026-10-07", rules, recipes, existing)
        assert err is None
        dinner_entries = [e for e in entries if e["meal_type"] == "dinner"]
        assert len(dinner_entries) == 2
        assert dinner_entries[0]["date"] == "2026-10-05"
        assert dinner_entries[1]["date"] == "2026-10-06"
        assert dinner_entries[1]["continuation_of"] == ("2026-10-05", "dinner")

    def test_continuation_skips_occupied_same_type_slot(self):
        """A continuation uses the next free slot without overwriting an entry."""
        rules = [
            make_rule(day_of_week="mon", meal_type="dinner", id=1),
        ]
        recipes = [make_recipe(id=1, name="Bolognese", covers_days=2)]
        existing = [{"date": "2026-10-06", "meal_type": "dinner"}]
        entries, err = generate_plan_entries("2026-10-05", "2026-10-07", rules, recipes, existing)
        assert err is None
        dinner_entries = [e for e in entries if e["meal_type"] == "dinner"]
        assert [e["date"] for e in dinner_entries] == ["2026-10-05", "2026-10-07"]
        assert dinner_entries[1]["is_continuation"] == 1

    def test_three_day_recipe_skips_occupied_slots_and_keeps_servings(self):
        rules = [make_rule(day_of_week="mon", meal_type="dinner", id=1)]
        recipes = [make_recipe(id=1, name="Batch stew", servings=6, covers_days=3)]
        occupied = [{"date": "2026-10-06", "meal_type": "dinner"}]

        entries, err = generate_plan_entries(
            "2026-10-05", "2026-10-08", rules, recipes, occupied,
        )

        assert err is None
        assert [(e["date"], e["servings"], e["is_continuation"]) for e in entries] == [
            ("2026-10-05", 6, 0),
            ("2026-10-07", 6, 1),
            ("2026-10-08", 6, 1),
        ]

    def test_manual_override_respected(self):
        """Existing manual entry blocks auto-generation for that slot."""
        rules = [
            make_rule(day_of_week="mon", meal_type="dinner", id=1),
        ]
        recipes = [make_recipe(id=1, name="Spaghetti")]
        existing = [{"date": "2026-10-05", "meal_type": "dinner"}]
        entries, err = generate_plan_entries("2026-10-05", "2026-10-05", rules, recipes, existing)
        assert err is None
        assert len(entries) == 0  # blocked by manual override

    def test_rule_sort_order(self):
        """Rules evaluated in sort_order — first match wins."""
        rules = [
            make_rule(day_of_week="mon", meal_type="dinner", id=1, sort_order=10, tag_filter="pasta"),
            make_rule(day_of_week="mon", meal_type="dinner", id=2, sort_order=0, tag_filter="italian"),
        ]
        recipes = [
            make_recipe(id=1, name="Spaghetti", tags="pasta,italian"),
            make_recipe(id=2, name="Risotto", tags="italian"),
        ]
        entries, err = generate_plan_entries("2026-10-05", "2026-10-05", rules, recipes, [])
        assert err is None
        assert len(entries) == 1
        # First rule (tag_filter="pasta") matches Spaghetti (id=1), sorted first
        assert entries[0]["recipe_id"] == 1

    def test_tag_filter_selects_correct_recipe(self):
        """Rule with tag_filter picks matching recipe, not random."""
        rules = [make_rule(day_of_week="mon", meal_type="dinner", id=1, tag_filter="italian")]
        recipes = [
            make_recipe(id=1, name="Curry", tags="indian"),
            make_recipe(id=2, name="Pasta", tags="italian"),
            make_recipe(id=3, name="Tacos", tags="mexican"),
        ]
        entries, err = generate_plan_entries("2026-10-05", "2026-10-05", rules, recipes, [])
        assert err is None
        assert len(entries) == 1
        assert entries[0]["recipe_id"] == 2  # Pasta has "italian" tag

    def test_multi_day_range(self):
        """5-day range with one dinner rule per day fills 5 entries."""
        rules = [
            make_rule(day_of_week="mon", meal_type="dinner", id=1),
            make_rule(day_of_week="tue", meal_type="dinner", id=2),
            make_rule(day_of_week="wed", meal_type="dinner", id=3),
            make_rule(day_of_week="thu", meal_type="dinner", id=4),
            make_rule(day_of_week="fri", meal_type="dinner", id=5),
        ]
        recipes = [
            make_recipe(id=i, name=f"Meal{i}") for i in range(1, 6)
        ]
        # Mon-Fri: 2026-10-05 to 2026-10-09
        entries, err = generate_plan_entries("2026-10-05", "2026-10-09", rules, recipes, [])
        assert err is None
        assert len(entries) == 5

    def test_servings_override(self):
        """Rule servings_override takes precedence over recipe servings."""
        rules = [
            make_rule(day_of_week="mon", meal_type="dinner", id=1, servings_override=6),
        ]
        recipes = [make_recipe(id=1, name="Curry", servings=4)]
        entries, err = generate_plan_entries("2026-10-05", "2026-10-05", rules, recipes, [])
        assert err is None
        assert entries[0]["servings"] == 6

    def test_no_servings_override_uses_recipe_default(self):
        """Without override, recipe servings are used."""
        rules = [make_rule(day_of_week="mon", meal_type="dinner", id=1)]
        recipes = [make_recipe(id=1, name="Curry", servings=4)]
        entries, err = generate_plan_entries("2026-10-05", "2026-10-05", rules, recipes, [])
        assert err is None
        assert entries[0]["servings"] == 4

    def test_inactive_rule_skipped(self):
        """Inactive rules (is_active=0) are not used."""
        rules = [
            make_rule(day_of_week="mon", meal_type="dinner", id=1, is_active=0),
        ]
        recipes = [make_recipe(id=1, name="Pasta")]
        entries, err = generate_plan_entries("2026-10-05", "2026-10-05", rules, recipes, [])
        assert err is None
        assert len(entries) == 0

    def test_weekday_only_rule_skips_weekend(self):
        """A weekday-only rule doesn't fire on Saturday."""
        rules = [make_rule(day_of_week="weekday", meal_type="dinner", id=1)]
        recipes = [make_recipe(id=1, name="Pasta")]
        entries, err = generate_plan_entries("2026-10-10", "2026-10-10", rules, recipes, [])
        assert err is None  # Sat Oct 10 2026
        assert len(entries) == 0

    def test_weekend_only_rule_skips_weekday(self):
        """A weekend-only rule doesn't fire on Wednesday."""
        rules = [make_rule(day_of_week="weekend", meal_type="dinner", id=1)]
        recipes = [make_recipe(id=1, name="Pizza")]
        entries, err = generate_plan_entries("2026-10-07", "2026-10-07", rules, recipes, [])
        assert err is None  # Wed Oct 7 2026
        assert len(entries) == 0


# ============================================================================
# Integration-style: full workflow simulation
# ============================================================================

class TestPlanGenerationWorkflow:
    """Simulate a realistic meal plan setup."""

    def test_standard_household_setup(self):
        """A realistic setup: weekday dinners, Friday lunch, weekend breakfast."""
        rules = [
            # Weekday dinners — any recipe (Mon-Fri = 5 days)
            make_rule(id=1, day_of_week="weekday", meal_type="dinner", sort_order=0),
            # Friday lunch — "quick" tagged recipes
            make_rule(id=2, day_of_week="fri", meal_type="lunch", tag_filter="quick", sort_order=0),
            # Weekend breakfast — "brunchy" tagged recipes
            make_rule(id=3, day_of_week="weekend", meal_type="breakfast", tag_filter="brunchy", sort_order=0),
        ]
        recipes = [
            make_recipe(id=1, name="Roast Chicken", tags="standard"),
            make_recipe(id=2, name="Tacos", tags="quick, mexican"),
            make_recipe(id=3, name="Pancakes", tags="brunchy, breakfast"),
            make_recipe(id=4, name="Stir Fry", tags="quick, asian"),
            make_recipe(id=5, name="Eggs Benedict", tags="brunchy"),
        ]

        # Mon-Fri: 2026-10-05 (Mon) to 2026-10-09 (Fri)
        entries, err = generate_plan_entries("2026-10-05", "2026-10-09", rules, recipes, [])
        assert err is None

        # Weekday dinners: Mon-Fri (5 days, since "weekday" includes Friday)
        dinner_entries = [e for e in entries if e["meal_type"] == "dinner"]
        assert len(dinner_entries) == 5

        # Friday lunch: 1 entry
        lunch_entries = [e for e in entries if e["meal_type"] == "lunch"]
        assert len(lunch_entries) == 1
        assert lunch_entries[0]["recipe_id"] in (2, 4)  # quick-tagged

        # Weekend breakfast: no weekend days in Mon-Fri range (0 entries)
        breakfast_entries = [e for e in entries if e["meal_type"] == "breakfast"]
        assert len(breakfast_entries) == 0  # Mon-Fri has no weekend days

    def test_multi_day_weekday_dinner_chain(self):
        """Multi-day recipes fill coverage before another recipe is selected."""
        rules = [
            make_rule(id=1, day_of_week="weekday", meal_type="dinner", sort_order=0),
        ]
        recipes = [
            make_recipe(id=1, name="Bolognese", covers_days=2, tags="standard"),
            make_recipe(id=2, name="Grilled Fish", tags="standard"),
        ]
        # Mon-Wed: 2026-10-05 to 2026-10-07
        entries, err = generate_plan_entries("2026-10-05", "2026-10-07", rules, recipes, [])
        assert err is None
        dinners = [e for e in entries if e["meal_type"] == "dinner"]
        # Monday and Tuesday are covered by Bolognese; Wednesday gets another recipe.
        assert len(dinners) == 3
        by_date = {d["date"]: d["recipe_id"] for d in dinners}
        assert by_date == {"2026-10-05": 1, "2026-10-06": 1, "2026-10-07": 2}

    def test_coverage_continuation_prevents_rule_on_occupied_slot(self):
        """A continuation blocks independent generation in its occupied slot."""
        rules = [
            make_rule(id=1, day_of_week="mon", meal_type="dinner", tag_filter="pasta", sort_order=0),
            make_rule(id=2, day_of_week="tue", meal_type="dinner", tag_filter="fish", sort_order=0),
        ]
        recipes = [
            make_recipe(id=1, name="Bolognese", covers_days=2, tags="pasta"),
            make_recipe(id=2, name="Fish Tacos", tags="fish"),
        ]
        # Mon-Tue: 2026-10-05 to 2026-10-06
        entries, err = generate_plan_entries("2026-10-05", "2026-10-06", rules, recipes, [])
        assert err is None
        dinners = [e for e in entries if e["meal_type"] == "dinner"]
        # Monday's Bolognese continuation occupies Tuesday before the fish rule runs.
        assert len(dinners) == 2
        assert dinners[0]["recipe_id"] == 1  # Bolognese on Monday
        assert dinners[1]["recipe_id"] == 1  # Bolognese cascade on Tuesday


# ============================================================================
# Edge cases
# ============================================================================

class TestPlanGenerationEdgeCases:
    """Edge cases and boundary conditions."""

    def test_single_day_range(self):
        entries, err = generate_plan_entries("2026-10-05", "2026-10-05", [], [], [])
        assert err is None
        assert entries == []

    def test_same_start_end_date(self):
        """Start == end is a valid 1-day range."""
        rules = [make_rule(day_of_week="mon", meal_type="dinner", id=1)]
        recipes = [make_recipe(id=1, name="Pasta")]
        entries, err = generate_plan_entries("2026-10-05", "2026-10-05", rules, recipes, [])
        assert err is None
        assert len(entries) == 1

    def test_no_recipes_for_tag_filter(self):
        """Tag filter with no matching recipes produces no entry."""
        rules = [make_rule(day_of_week="mon", meal_type="dinner", id=1, tag_filter="nonexistent")]
        recipes = [make_recipe(id=1, name="Pasta", tags="italian")]
        entries, err = generate_plan_entries("2026-10-05", "2026-10-05", rules, recipes, [])
        assert err is None
        assert len(entries) == 0

    def test_rule_without_tag_filter_uses_any_recipe(self):
        """Rule with no tag_filter can use any recipe."""
        rules = [make_rule(day_of_week="mon", meal_type="dinner", id=1, tag_filter=None)]
        recipes = [make_recipe(id=1, name="Curry", tags="spicy")]
        entries, err = generate_plan_entries("2026-10-05", "2026-10-05", rules, recipes, [])
        assert err is None
        assert len(entries) == 1
        assert entries[0]["recipe_id"] == 1

    def test_multiple_rules_same_slot_first_wins(self):
        """Multiple matching rules for same slot — first by sort_order wins."""
        rules = [
            make_rule(id=1, day_of_week="mon", meal_type="dinner", sort_order=0, tag_filter="a"),
            make_rule(id=2, day_of_week="mon", meal_type="dinner", sort_order=0, tag_filter="b"),
        ]
        recipes = [
            make_recipe(id=1, name="DishA", tags="a"),
            make_recipe(id=2, name="DishB", tags="b"),
        ]
        entries, err = generate_plan_entries("2026-10-05", "2026-10-05", rules, recipes, [])
        assert err is None
        assert len(entries) == 1  # only one entry per slot
        assert entries[0]["recipe_id"] == 1  # first matching rule wins


def test_plan_summary_counts_slots_recipes_and_today():
    from services.plan_generator import plan_summary
    dinner = {"recipe_id": 1, "recipe_name": "Bolognese", "servings": 4, "is_auto_generated": 1}
    manual = {"recipe_id": 2, "recipe_name": "Eggs", "servings": 2, "is_auto_generated": 0}
    days = [
        {"is_today": False, "meals": {"breakfast": None, "dinner": dinner}},
        {"is_today": True, "meals": {"breakfast": manual, "dinner": dinner}},
        {"is_today": False, "meals": {"breakfast": None, "dinner": None}},
    ]
    s = plan_summary(days, ["breakfast", "dinner"])
    assert (s["slots"], s["planned"], s["open"], s["recipes"], s["manual"]) == (6, 3, 3, 2, 1)
    assert s["today"] == [("breakfast", manual), ("dinner", dinner)]
    assert plan_summary(days[:1], ["dinner"])["today"] is None
