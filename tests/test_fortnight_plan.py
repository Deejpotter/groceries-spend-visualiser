"""Pay-cycle fortnight plans: batch nights, Friday meals, ate-out skips and rollover."""

from datetime import date

import pytest

from conftest import login
from services.pay_cycle import pay_cycle
from services.plan_generator import (
    generate_plan_entries, make_recipe, make_rule, rollover_dates, skip_and_shift,
)
from services.shopping_list_generator import (
    generate_shopping_list, make_ingredient, make_recipe_dict, make_recipe_ingredient,
)

BATCH_DAYS = "mon,tue,wed,thu,sat,sun"
RULES = [
    make_rule(id=1, day_of_week=BATCH_DAYS, tag_filter="batch"),
    make_rule(id=2, day_of_week="fri", tag_filter="friday"),
]
RECIPES = [make_recipe(id=i, name=f"Batch {i}", servings=8, covers_days=2, tags="dinner, batch")
           for i in range(1, 8)] + [
    make_recipe(id=20, name="Tacos", tags="dinner, friday"),
    make_recipe(id=21, name="Hotdogs", tags="dinner, friday"),
]


def test_next_pay_cycle_range():
    cycle = pay_cycle("2026-11-09", "fortnightly", date(2026, 11, 5))
    assert (cycle["next_cycle_start"], cycle["next_cycle_end"]) == ("2026-11-09", "2026-11-22")
    monthly = pay_cycle("2026-10-15", "monthly", date(2026, 11, 5))
    assert (monthly["next_cycle_start"], monthly["next_cycle_end"]) == ("2026-11-15", "2026-12-14")


def test_fortnight_is_six_batch_meals_and_two_friday_meals():
    entries, err = generate_plan_entries("2026-11-09", "2026-11-22", RULES, RECIPES, [])
    assert err is None
    by_date = {e["date"]: e for e in entries}
    assert len(by_date) == 14
    fridays = [by_date["2026-11-13"], by_date["2026-11-20"]]
    assert {e["recipe_id"] for e in fridays} == {20, 21}
    originals = [e for e in entries if e["source_rule_id"] == 1 and not e["is_continuation"]]
    assert [e["date"] for e in originals] == [
        "2026-11-09", "2026-11-11", "2026-11-14", "2026-11-16", "2026-11-18", "2026-11-21"]


def test_leftovers_never_land_on_friday():
    recipes = [make_recipe(id=1, name="Stew", servings=8, covers_days=2, tags="batch"),
               make_recipe(id=20, name="Tacos", tags="friday")]
    entries, _ = generate_plan_entries("2026-11-12", "2026-11-14", RULES, recipes, [])
    assert [(e["date"], e["recipe_id"], e["is_continuation"]) for e in entries] == [
        ("2026-11-12", 1, 0), ("2026-11-14", 1, 1), ("2026-11-13", 20, 0)]


def _plan():
    return [
        {"id": 1, "date": "2026-11-09", "meal_type": "dinner", "source_rule_id": 1},
        {"id": 2, "date": "2026-11-10", "meal_type": "dinner", "source_rule_id": 1},
        {"id": 3, "date": "2026-11-11", "meal_type": "dinner", "source_rule_id": 1},
        {"id": 4, "date": "2026-11-12", "meal_type": "dinner", "source_rule_id": 1},
        {"id": 5, "date": "2026-11-13", "meal_type": "dinner", "source_rule_id": 2},
        {"id": 6, "date": "2026-11-14", "meal_type": "dinner", "source_rule_id": 1},
        {"id": 7, "date": "2026-11-15", "meal_type": "dinner", "source_rule_id": 1},
        {"id": 8, "date": "2026-11-10", "meal_type": "lunch", "source_rule_id": 3},
    ]


class TestSkipAndShift:
    def test_batch_meals_shift_past_friday(self):
        result = skip_and_shift(_plan(), "2026-11-10", "dinner")
        assert result == {"moves": [(2, "2026-11-11"), (3, "2026-11-12"), (4, "2026-11-14"), (6, "2026-11-15")],
                          "overflow": [7]}

    def test_friday_skip_rolls_the_friday_meal(self):
        assert skip_and_shift(_plan(), "2026-11-13", "dinner") == {"moves": [], "overflow": [5]}

    def test_empty_slot_moves_nothing(self):
        assert skip_and_shift(_plan(), "2026-11-16", "dinner") == {"moves": [], "overflow": []}


def test_rollover_dates_follow_rule_days_and_skip_taken():
    taken = {("2026-11-23", "dinner")}
    assert rollover_dates(2, "2026-11-22", BATCH_DAYS, "dinner", taken) == ["2026-11-24", "2026-11-25"]
    assert rollover_dates(1, "2026-11-22", "fri", "dinner", set()) == ["2026-11-27"]
    assert rollover_dates(1, "2026-11-22", None, "lunch", set()) == ["2026-11-23"]


def test_shopping_list_skips_rolled_over_meals():
    ingredients = {1: make_ingredient(1, "Mince", unit="kg", price=11.0)}
    links = {1: [make_recipe_ingredient(1, 0.5)]}
    entries = [{"date": "2026-11-23", "meal_type": "dinner", "recipe_id": 1, "servings": 4, "origin": "rollover"}]
    assert generate_shopping_list(entries, {1: make_recipe_dict(1)}, links, ingredients) == {}


@pytest.fixture
def fortnight(client, db, monkeypatch):
    login(client)
    db.executemany("INSERT INTO recipes (id, name, servings, covers_days, tags) VALUES (?, ?, ?, ?, ?)",
                   [(r["id"], r["name"], r["servings"], r["covers_days"], r["tags"]) for r in RECIPES])
    db.executemany("INSERT INTO meal_rules (id, day_of_week, meal_type, tag_filter, sort_order) VALUES (?, ?, 'dinner', ?, ?)",
                   [(1, BATCH_DAYS, "batch", 0), (2, "fri", "friday", 1)])
    db.commit()

    def at(day):
        monkeypatch.setattr("routes.meal_plan.local_today", lambda: date.fromisoformat(day))
    at("2026-11-08")
    client.post("/meal-plan/generate", data={"plan_start": "2026-11-09", "plan_end": "2026-11-22"})
    return at


def _dinners(db):
    return {r["date"]: dict(r) for r in db.execute(
        "SELECT date, recipe_id, is_continuation, origin, is_auto_generated FROM meal_plan_entries"
        " WHERE meal_type = 'dinner' ORDER BY date")}


def test_ate_out_after_the_shop_rolls_last_meal_over(client, db, fortnight):
    fortnight("2026-11-10")
    before = _dinners(db)
    resp = client.post("/meal-plan/skip", data={"date": "2026-11-10", "meal_type": "dinner"},
                       follow_redirects=True)
    after = _dinners(db)
    assert "2026-11-10" not in after
    assert after["2026-11-11"]["recipe_id"] == before["2026-11-10"]["recipe_id"]
    assert after["2026-11-13"] == before["2026-11-13"]  # Friday untouched
    rolled = after["2026-11-23"]
    assert rolled["recipe_id"] == before["2026-11-22"]["recipe_id"]
    assert rolled["origin"] == "rollover" and rolled["is_auto_generated"] == 0
    assert b"already bought" in resp.data
    assert b"Ate out" in resp.data

    client.post("/meal-plan/generate", data={"plan_start": "2026-11-09", "plan_end": "2026-11-22"})
    assert "2026-11-10" not in _dinners(db)  # the skip survives regeneration


def test_ate_out_before_the_shop_drops_the_overflow(client, db, fortnight):
    resp = client.post("/meal-plan/skip", data={"date": "2026-11-13", "meal_type": "dinner"},
                       follow_redirects=True)
    after = _dinners(db)
    assert "2026-11-13" not in after
    assert after["2026-11-20"]["recipe_id"] in (20, 21)
    assert not [d for d in after if d > "2026-11-22"]
    assert b"was removed (nothing bought yet)" in resp.data

    client.post("/meal-plan/unskip", data={"date": "2026-11-13", "meal_type": "dinner"})
    assert not db.execute("SELECT 1 FROM plan_skips").fetchone()


def test_next_pay_cycle_button_plans_the_fortnight(client, db, monkeypatch):
    login(client)
    db.execute("INSERT INTO settings (key, value) VALUES ('payday_date', '2026-11-09')")
    db.execute("INSERT INTO settings (key, value) VALUES ('pay_frequency', 'fortnightly')")
    db.commit()
    monkeypatch.setattr("routes.meal_plan.local_today", lambda: date(2026, 11, 5))
    client.post("/meal-plan/generate", data={"range": "next"})
    dates = [r[0] for r in db.execute("SELECT value FROM settings WHERE key IN ('plan_start_date', 'plan_end_date') ORDER BY key DESC")]
    assert dates == ["2026-11-09", "2026-11-22"]
