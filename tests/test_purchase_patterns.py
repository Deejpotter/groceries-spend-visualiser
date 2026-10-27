"""Tests for services/purchase_patterns.py (pure, no Flask)."""

from datetime import date

from services.purchase_patterns import (
    build_patterns, classify_rhythm, due_dates, feedback_suppressed, make_history, make_purchase,
    predict_needs, prediction_accuracy,
)

TODAY = date(2026, 10, 27)


def _pattern(rows, **kwargs):
    patterns = build_patterns(rows, TODAY, **kwargs)
    assert len(patterns) == 1
    return patterns[0]


class TestBuildPatterns:
    def test_weekly_buy_is_regular(self):
        p = _pattern(make_history("Milk 2L", "2026-09-01", 7, 8))
        assert p["rhythm"] == "regular"
        assert p["interval_days"] == 7
        assert p["buys"] == 8
        assert p["next_due"] == "2026-10-27"

    def test_one_purchase_is_one_off(self):
        assert _pattern([make_purchase("2026-10-01")])["rhythm"] == "one_off"

    def test_irregular_buys_are_occasional(self):
        rows = [make_purchase(d, basket_id=d) for d in ("2026-08-01", "2026-08-04", "2026-09-20", "2026-10-20")]
        assert _pattern(rows)["rhythm"] == "occasional"

    def test_long_gap_since_last_buy_is_stopped(self):
        assert _pattern(make_history("Milk 2L", "2026-05-01", 7, 6))["rhythm"] == "stopped"

    def test_linked_products_share_one_pattern(self):
        rows = (make_history("Brand A Milk 2L", "2026-09-01", 14, 4, ingredient_id=3)
                + make_history("Brand B Milk 2L", "2026-09-08", 14, 4, ingredient_id=3))
        p = _pattern(rows, ingredients={3: {"name": "Milk", "category": "dairy"}})
        assert p["key"] == "ing:3"
        assert p["label"] == "Milk"
        assert p["interval_days"] == 7
        assert p["category"] == "dairy"
        assert set(p["products"]) == {"Brand A Milk 2L", "Brand B Milk 2L"}

    def test_ignored_products_are_left_out(self):
        rows = make_history("Milk 2L", "2026-09-01", 7, 4)
        assert build_patterns(rows, TODAY, decisions={"Milk 2L": {"ignored": 1}}) == []

    def test_old_history_is_outside_the_window(self):
        assert build_patterns(make_history("Milk", "2025-01-01", 7, 4), TODAY) == []

    def test_role_comes_from_the_product_name(self):
        assert _pattern(make_history("Finish Dishwasher Tablets", "2026-09-01", 21, 3))["role"] == "household"

    def test_typical_quantity_and_spend_use_the_median_visit(self):
        rows = [make_purchase(d, quantity=q, line_total=q * 3, basket_id=d)
                for d, q in (("2026-10-01", 1), ("2026-10-08", 2), ("2026-10-15", 2))]
        p = _pattern(rows)
        assert p["typical_quantity"] == 2 and p["typical_spend"] == 6


class TestClassifyRhythm:
    def test_rules(self):
        assert classify_rhythm(1, None, 0, 3) == "one_off"
        assert classify_rhythm(4, 7, 0.1, 3) == "regular"
        assert classify_rhythm(2, 7, 0.1, 3) == "occasional"
        assert classify_rhythm(4, 7, 0.1, 30) == "stopped"


class TestPrediction:
    def test_due_dates_in_range(self):
        p = _pattern(make_history("Milk", "2026-09-01", 7, 8))  # next due 2026-10-27
        assert [d.isoformat() for d in due_dates(p, date(2026, 10, 27), date(2026, 11, 9))] == [
            "2026-10-27", "2026-11-03"]

    def test_overdue_item_counts_once_at_start(self):
        p = _pattern(make_history("Milk", "2026-09-01", 7, 7))  # next due 2026-10-20
        dates = due_dates(p, date(2026, 10, 27), date(2026, 10, 30))
        assert [d.isoformat() for d in dates] == ["2026-10-27"]

    def test_predict_needs_scales_quantity_and_cost(self):
        needs = predict_needs(build_patterns(make_history("Milk", "2026-09-01", 7, 8), TODAY),
                              date(2026, 10, 27), date(2026, 11, 9))
        assert needs[0]["times"] == 2
        assert needs[0]["quantity"] == 2
        assert needs[0]["estimated_cost"] == 6.2

    def test_occasional_can_be_excluded(self):
        rows = [make_purchase(d, basket_id=d) for d in ("2026-08-01", "2026-08-04", "2026-09-20", "2026-10-20")]
        patterns = build_patterns(rows, TODAY)
        assert predict_needs(patterns, TODAY, date(2026, 12, 31), include_occasional=False) == []

    def test_accuracy_counts_hits_and_misses(self):
        rows = (make_history("Milk", "2026-08-01", 7, 12)
                + make_history("Bread", "2026-08-01", 7, 10))  # bread stops being bought
        result = prediction_accuracy(rows, date(2026, 10, 17), date(2026, 10, 23))
        assert result["predicted"] == 2
        assert result["hits"] == 1
        assert result["extra"] == ["Bread"]


class TestFeedback:
    def test_never_and_always(self):
        rows = [{"item_key": "k", "action": "never"}]
        assert feedback_suppressed(rows, "k") == (True, False)
        rows.append({"item_key": "k", "action": "always"})
        assert feedback_suppressed(rows, "k") == (False, True)

    def test_two_removals_suppress_until_kept(self):
        rows = [{"item_key": "k", "action": "removed"}, {"item_key": "k", "action": "removed"}]
        assert feedback_suppressed(rows, "k")[0]
        rows.append({"item_key": "k", "action": "kept"})
        assert not feedback_suppressed(rows, "k")[0]

    def test_other_keys_are_ignored(self):
        assert feedback_suppressed([{"item_key": "x", "action": "never"}], "k") == (False, False)
