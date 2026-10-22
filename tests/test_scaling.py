"""Unit tests for recipe portion scaling (pure functions)."""

from services.scaling import normalise_servings, scale_factor, scale_ingredients


class TestScaleFactor:
    def test_doubles_when_target_is_twice_base(self):
        assert scale_factor(2, 4) == 2

    def test_halves_when_target_is_half_base(self):
        assert scale_factor(4, 2) == 0.5

    def test_one_when_unchanged(self):
        assert scale_factor(3, 3) == 1.0

    def test_one_for_missing_or_invalid(self):
        assert scale_factor(None, 4) == 1.0
        assert scale_factor(2, None) == 1.0
        assert scale_factor(0, 4) == 1.0
        assert scale_factor("abc", 4) == 1.0


class TestScaleIngredients:
    def test_scales_quantities_without_mutating_input(self):
        rows = [{"quantity": 200, "name": "Rice"}, {"quantity": 1.5, "name": "Stock"}]
        scaled = scale_ingredients(rows, 2, 4)
        assert [r["quantity"] for r in scaled] == [400, 3.0]
        assert rows[0]["quantity"] == 200

    def test_unchanged_servings_keep_values(self):
        rows = [{"quantity": 3}]
        assert scale_ingredients(rows, 2, 2)[0]["quantity"] == 3

    def test_empty_and_missing_quantities_are_safe(self):
        assert scale_ingredients([], 2, 4) == []
        assert scale_ingredients([{"quantity": None}], 2, 4)[0]["quantity"] == 0


class TestNormaliseServings:
    def test_keeps_valid_value(self):
        assert normalise_servings("6", 2) == 6

    def test_falls_back_for_garbage(self):
        assert normalise_servings("abc", 2) == 2
        assert normalise_servings(None, 2) == 2

    def test_clamps_to_sane_range(self):
        assert normalise_servings(0, 2) == 1
        assert normalise_servings(-5, 2) == 1
        assert normalise_servings(100000, 2) == 100
