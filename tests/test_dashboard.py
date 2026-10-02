"""Tests for the pure dashboard metric functions."""

from datetime import date

from services.dashboard import (
    pantry_alerts,
    plan_coverage,
    shopping_summary,
    spend_window,
    trend,
)


def test_plan_coverage_counts_days_with_meals():
    entries = [
        {"date": "2026-10-05"},
        {"date": "2026-10-06"},
        {"date": "2026-10-06"},  # continuation entry on this day.
        {"date": "2026-10-08"},
        {"date": "2026-10-20"},  # outside the range: ignored
    ]
    result = plan_coverage(entries, "2026-10-05", "2026-10-11")
    assert result["days_total"] == 7
    assert result["days_planned"] == 3
    assert result["pct"] == 43


def test_plan_coverage_empty_or_inverted_range():
    assert plan_coverage([], "2026-10-05", "2026-10-11") == {
        "days_total": 7, "days_planned": 0, "pct": 0}
    assert plan_coverage([], "2026-10-11", "2026-10-05")["days_total"] == 0


def test_shopping_summary_counts_items_cost_and_ticks():
    rows = [
        {"estimated_cost": 2.5, "checked": 1, "is_manual": 0},
        {"estimated_cost": 4.25, "checked": 0, "is_manual": 0},
        {"estimated_cost": None, "checked": 0, "is_manual": 1},  # no cost
    ]
    result = shopping_summary(rows)
    assert result["item_count"] == 3
    assert result["checked"] == 1
    assert result["cost"] == 6.75
    assert result["pct"] == 33


def test_shopping_summary_empty():
    assert shopping_summary([]) == {"item_count": 0, "checked": 0, "cost": 0, "pct": 0}


def test_pantry_alerts_split_expired_soon_and_low():
    today = date(2026, 10, 10)
    items = [
        {"id": 1, "quantity": 1, "expiry_date": "2026-10-08", "minimum_stock": 0},  # expired
        {"id": 2, "quantity": 1, "expiry_date": "2026-10-12", "minimum_stock": 0},  # soon
        {"id": 3, "quantity": 1, "expiry_date": "2026-10-20", "minimum_stock": 0},  # fine
        {"id": 4, "quantity": 1, "expiry_date": None, "minimum_stock": 3},          # low stock
    ]
    result = pantry_alerts(items, today=today, soon_days=3)
    assert [r["id"] for r in result["expired"]] == [1]
    assert [r["id"] for r in result["expiring"]] == [2]
    assert [r["id"] for r in result["low"]] == [4]
    assert result["expired"][0]["status"] == "expired"
    assert result["expiring"][0]["days_left"] == 2
    assert result["tone"] == "danger"


def test_pantry_alerts_tone():
    today = date(2026, 10, 10)
    soon = [{"id": 1, "quantity": 1, "expiry_date": "2026-10-11", "minimum_stock": 0}]
    low = [{"id": 1, "quantity": 1, "expiry_date": None, "minimum_stock": 5}]
    fine = [{"id": 1, "quantity": 9, "expiry_date": "2026-11-01", "minimum_stock": 0}]
    assert pantry_alerts(soon, today=today)["tone"] == "warn"
    assert pantry_alerts(low, today=today)["tone"] == "warn"
    assert pantry_alerts(fine, today=today)["tone"] == "ok"


def test_spend_window_counts_shops_and_average():
    rows = [
        {"store": "Woolworths", "basket_id": "b1", "line_total": 10.0},
        {"store": "Woolworths", "basket_id": "b1", "line_total": 5.0},
        {"store": "Woolworths", "basket_id": "b2", "line_total": 20.0},
    ]
    result = spend_window(rows)
    assert result["total"] == 35.0
    assert result["shops"] == 2
    assert result["avg_per_shop"] == 17.5


def test_spend_window_empty():
    assert spend_window([]) == {"total": 0, "shops": 0, "avg_per_shop": None}


def test_trend_direction_and_percent():
    assert trend(120, 100) == {"pct": 20, "direction": "up"}
    assert trend(80, 100) == {"pct": 20, "direction": "down"}
    assert trend(100, 100) == {"pct": 0, "direction": "flat"}


def test_trend_without_a_previous_value():
    assert trend(100, None) is None
    assert trend(None, 100) is None
    assert trend(100, 0) is None


def test_plan_coverage_skips_bad_entry_dates():
    entries = [
        {"date": "2026-10-05"},
        {"date": "not-a-date"},
        {"date": None},
    ]
    result = plan_coverage(entries, "2026-10-05", "2026-10-11")
    assert result["days_planned"] == 1


def test_plan_coverage_bad_range_returns_empty():
    assert plan_coverage([], "bad-date", "2026-10-11")["days_total"] == 0


def test_pantry_alerts_skips_bad_expiry_dates():
    today = date(2026, 10, 10)
    items = [
        {"id": 1, "quantity": 1, "expiry_date": "not-a-date", "minimum_stock": 0},
        {"id": 2, "quantity": 1, "expiry_date": "2026-10-08", "minimum_stock": 0},
    ]
    result = pantry_alerts(items, today=today)
    assert [r["id"] for r in result["expired"]] == [2]
    assert result["tone"] == "danger"
