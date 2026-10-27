"""Tests for services/cycle_list.py."""

from datetime import date

from services.cycle_list import generate_cycle_list, source_for_role, totals_by_source, trim_to_budget


def _need(key, label, role="food", ingredient_id=None, cost=4.0, rhythm="regular", first_due="2026-10-27"):
    return {"key": key, "label": label, "ingredient_id": ingredient_id, "products": [label], "role": role,
            "rhythm": rhythm, "interval_days": 7, "last_bought": "2026-10-20", "times": 1, "quantity": 1,
            "estimated_cost": cost, "first_due": first_due, "category": None, "typical_quantity": 1,
            "typical_spend": cost}


MEALS = {"meat": [{"ingredient_id": 1, "ingredient_name": "Mince", "quantity": 1, "unit": "kg",
                   "category": "meat", "estimated_cost": 12.0, "recipe_refs": ["Tacos"], "meal_date": "2026-10-28"}],
         "dairy": [{"ingredient_id": 2, "ingredient_name": "Milk", "quantity": 1, "unit": "L",
                    "category": "dairy", "estimated_cost": 2.0, "recipe_refs": ["Pancakes"], "meal_date": "2026-10-29"}]}
NEEDS = [_need("ing:2", "Milk", ingredient_id=2), _need("prod:Bread", "Bread"),
         _need("prod:Chips", "Chips", role="treat", rhythm="occasional"),
         _need("prod:Finish Tablets", "Finish Tablets", role="household", cost=15.0)]


def _names(lines):
    return sorted(l["ingredient_name"] for l in lines)


class TestModes:
    def test_full_combines_everything_without_duplicates(self):
        lines = generate_cycle_list(MEALS, NEEDS, "full")
        assert _names(lines) == ["Bread", "Chips", "Finish Tablets", "Milk", "Mince"]
        milk = next(l for l in lines if l["ingredient_name"] == "Milk")
        assert milk["source"] == "meal" and "also a regular buy" in milk["reason"]

    def test_meals_only(self):
        assert _names(generate_cycle_list(MEALS, NEEDS, "meals")) == ["Milk", "Mince"]

    def test_nonmeal_excludes_meal_ingredients(self):
        assert _names(generate_cycle_list(MEALS, NEEDS, "nonmeal")) == ["Bread", "Chips", "Finish Tablets"]

    def test_household_and_staples(self):
        assert _names(generate_cycle_list(MEALS, NEEDS, "household")) == ["Finish Tablets"]
        assert _names(generate_cycle_list(MEALS, NEEDS, "staples")) == ["Bread"]

    def test_tight_drops_treats_and_occasional(self):
        assert _names(generate_cycle_list(MEALS, NEEDS, "tight")) == ["Bread", "Milk", "Mince"]

    def test_topup_only_items_due_soon(self):
        needs = NEEDS + [_need("prod:Rice", "Rice", first_due="2026-11-05")]
        lines = generate_cycle_list(MEALS, needs, "topup", due_by=date(2026, 10, 31))
        assert "Rice" not in _names(lines) and "Mince" not in _names(lines)

    def test_unknown_mode_falls_back_to_full(self):
        assert len(generate_cycle_list(MEALS, NEEDS, "bogus")) == 5


class TestFiltering:
    def test_pantry_stock_skips_staples(self):
        needs = [_need("ing:9", "Rice", ingredient_id=9)]
        assert generate_cycle_list({}, needs, "full", in_stock={9}) == []

    def test_never_feedback_suppresses(self):
        feedback = [{"item_key": "prod:Bread", "action": "never"}]
        assert "Bread" not in _names(generate_cycle_list({}, NEEDS, "full", feedback=feedback))

    def test_always_feedback_adds_items_not_due(self):
        pattern = _need("prod:Coffee", "Coffee")
        feedback = [{"item_key": "prod:Coffee", "action": "always"}]
        lines = generate_cycle_list({}, [], "full", feedback=feedback, patterns=[pattern])
        assert _names(lines) == ["Coffee"]

    def test_staple_lines_carry_reason_and_source(self):
        line = generate_cycle_list({}, [_need("prod:Bread", "Bread")], "full")[0]
        assert line["source"] == "staple" and line["unit"] == "pack"
        assert "about weekly" in line["reason"]

    def test_source_for_role(self):
        assert source_for_role("pet") == "household"
        assert source_for_role("treat") == "treat"
        assert source_for_role("food") == "staple"


class TestTotals:
    def test_totals_by_source_with_budget(self):
        lines = generate_cycle_list(MEALS, NEEDS, "full")
        totals = totals_by_source(lines, budget=30)
        assert totals["sources"]["household"] == 15.0
        assert totals["total"] == 37.0
        assert totals["remaining"] == -7.0

    def test_trim_drops_treats_first_and_never_meals(self):
        lines = generate_cycle_list(MEALS, NEEDS, "full")
        trim = trim_to_budget(lines, 33)
        assert trim["over_by"] == 4.0
        assert trim["drop"] == ["Chips"]
        assert trim_to_budget(lines, 100)["drop"] == []
