"""Tests for services/meal_patterns.py and the MealScorer in plan_generator."""

from datetime import date, datetime

from services.meal_patterns import (
    baskets_cooked, history_slots, make_history_entry, meal_stats, median_cost, recipe_cost_per_serving,
)
from services.plan_generator import (
    MealScorer, generate_plan_entries, is_skip_rule, make_recipe, make_rule,
)

TODAY = date(2026, 10, 27)
RECIPES = {1: {"id": 1, "name": "Tacos", "servings": 4}, 2: {"id": 2, "name": "Curry", "servings": 4},
           3: {"id": 3, "name": "Soup", "servings": 2}}
LINKS = {1: [{"ingredient_id": 10, "quantity": 0.5, "unit_override": None},
             {"ingredient_id": 11, "quantity": 8, "unit_override": None}],
         2: [{"ingredient_id": 10, "quantity": 0.5, "unit_override": None},
             {"ingredient_id": 12, "quantity": 400, "unit_override": "g"}],
         3: [{"ingredient_id": 12, "quantity": 0.2, "unit_override": None}]}
INGREDIENTS = {10: {"unit": "kg", "price": 12.0}, 11: {"unit": "each", "price": 0.5},
               12: {"unit": "kg", "price": 5.0}}


class TestMealStats:
    def test_counts_and_slots(self):
        history = [make_history_entry("2026-10-05", 1), make_history_entry("2026-10-12", 1),
                   make_history_entry("2026-10-13", 1, is_continuation=1),
                   make_history_entry("2026-10-20", 2, meal_type="lunch")]
        stats = meal_stats(history, TODAY, RECIPES)
        assert stats[1]["eaten"] == 2
        assert stats[1]["last_eaten"] == "2026-10-12"
        assert stats[1]["days_since"] == 15
        assert stats[1]["weekdays"] == {"mon": 2}
        assert stats[2]["typical_meal_type"] == "lunch"
        assert stats[3]["eaten"] == 0

    def test_future_entries_are_not_history(self):
        stats = meal_stats([make_history_entry("2026-11-01", 1)], TODAY, RECIPES)
        assert stats[1]["eaten"] == 0

    def test_cost_per_serving(self):
        assert recipe_cost_per_serving(RECIPES[1], LINKS[1], INGREDIENTS) == 2.5
        assert recipe_cost_per_serving(RECIPES[2], LINKS[2], INGREDIENTS) == 2.0
        assert recipe_cost_per_serving(RECIPES[1], [], INGREDIENTS) is None

    def test_median_cost(self):
        stats = meal_stats([], TODAY, RECIPES, LINKS, INGREDIENTS)
        assert median_cost(stats) == 2.0

    def test_baskets_with_most_ingredients_count_as_cooked(self):
        purchases = [{"store": "W", "basket_id": "b1", "ingredient_id": 10},
                     {"store": "W", "basket_id": "b1", "ingredient_id": 11},
                     {"store": "W", "basket_id": "b2", "ingredient_id": 10}]
        assert baskets_cooked(LINKS, purchases) == {1: 1}

    def test_history_slots(self):
        history = [make_history_entry(d, 1) for d in ("2026-10-05", "2026-10-12", "2026-10-19")]
        history.append(make_history_entry("2026-10-21", 2, meal_type="lunch"))
        assert history_slots(history, TODAY) == {("mon", "dinner"): 3}


def _scorer(history=(), **kwargs):
    stats = meal_stats(list(history), TODAY, RECIPES, LINKS, INGREDIENTS)
    return MealScorer(stats, LINKS, median_cost=median_cost(stats), **kwargs)


MONDAY = datetime(2026, 11, 2)


class TestMealScorer:
    def test_frequently_eaten_recipe_ranks_first(self):
        history = [make_history_entry(d, 2) for d in ("2026-09-14", "2026-09-21", "2026-09-28", "2026-10-05")]
        ranked = _scorer(history).rank([make_recipe(1), make_recipe(2)], MONDAY, "dinner")
        assert ranked[0]["id"] == 2

    def test_recently_eaten_is_pushed_down(self):
        history = [make_history_entry("2026-10-26", 1)]
        ranked = _scorer(history).rank([make_recipe(1), make_recipe(3)], MONDAY, "dinner")
        assert ranked[0]["id"] == 3

    def test_favourite_and_reasons(self):
        score, reasons = _scorer(favourites={3}).score(make_recipe(3), MONDAY, "dinner")
        assert "Favourite" in reasons and score > 1

    def test_swapped_out_recipes_score_lower(self):
        plain = _scorer().score(make_recipe(1), MONDAY, "dinner")[0]
        swapped = _scorer(feedback={1: 2}).score(make_recipe(1), MONDAY, "dinner")[0]
        assert swapped < plain

    def test_shared_ingredients_with_the_plan_score_higher(self):
        scorer = _scorer()
        before = scorer.score(make_recipe(2), MONDAY, "dinner")[0]
        scorer.record(make_recipe(1), MONDAY)
        after, reasons = scorer.score(make_recipe(2), MONDAY, "dinner")
        assert after > before and any(r.startswith("Shares") for r in reasons)

    def test_long_weeknight_cook_scores_lower(self):
        quick = _scorer().score(make_recipe(1), MONDAY, "dinner")[0]
        slow = _scorer(effort={1: 90}).score(make_recipe(1), MONDAY, "dinner")[0]
        assert slow < quick

    def test_alternatives_exclude_and_limit(self):
        found = _scorer().alternatives([make_recipe(1), make_recipe(2), make_recipe(3)], MONDAY, "dinner",
                                       exclude={1}, limit=1)
        assert len(found) == 1 and found[0]["recipe"]["id"] != 1


class TestSuggestedGeneration:
    def test_scorer_steers_the_choice(self):
        history = [make_history_entry(d, 2) for d in ("2026-09-14", "2026-09-21", "2026-09-28", "2026-10-05")]
        entries, _ = generate_plan_entries("2026-11-02", "2026-11-02", [make_rule(day_of_week="all")],
                                           [make_recipe(1), make_recipe(2)], [], scorer=_scorer(history))
        assert entries[0]["recipe_id"] == 2

    def test_history_fills_slots_without_rules(self):
        history = [make_history_entry(d, 1) for d in ("2026-10-05", "2026-10-12", "2026-10-19")]
        scorer = _scorer(history)
        slots = history_slots(history, TODAY)
        entries, _ = generate_plan_entries("2026-11-02", "2026-11-03", [], [make_recipe(1), make_recipe(2)], [],
                                           scorer=scorer, history_slots=slots)
        assert [(e["date"], e["recipe_id"], e["origin"]) for e in entries] == [("2026-11-02", 1, "history")]

    def test_no_history_slots_without_scorer(self):
        entries, _ = generate_plan_entries("2026-11-02", "2026-11-02", [], [make_recipe(1)], [],
                                           history_slots={("mon", "dinner"): 3})
        assert entries == []

    def test_skip_rule_leaves_the_slot_empty(self):
        rules = [make_rule(id=1, day_of_week="fri", tag_filter="eat out", sort_order=0),
                 make_rule(id=2, day_of_week="all", sort_order=1)]
        entries, _ = generate_plan_entries("2026-11-06", "2026-11-07", rules, [make_recipe(1)], [])
        assert [e["date"] for e in entries] == ["2026-11-07"]
        assert is_skip_rule({"tag_filter": "Skip"})
