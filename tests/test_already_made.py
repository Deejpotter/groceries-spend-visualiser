"""Leftovers already cooked go into the plan as kept meals the shopping list skips."""

import pytest

from conftest import login
from database import set_setting
from services.mutations import add_already_made
from services.plan_generator import generate_plan_entries, make_recipe, make_rule, select_recipe_for_rule
from services.shopping_list_generator import generate_shopping_list


def test_lunch_tagged_recipe_is_never_a_dinner():
  curry = make_recipe(id=1, name="Curry", tags="lunch, batch")
  lasagne = make_recipe(id=2, name="Lasagne", tags="dinner, batch")
  plain = make_recipe(id=3, name="Soup", tags="batch")
  dinner = select_recipe_for_rule("batch", [curry], meal_type="dinner")
  assert dinner is None
  assert select_recipe_for_rule("batch", [curry, lasagne], used_ids={2}, meal_type="dinner")["id"] == 2
  assert select_recipe_for_rule("batch", [curry], meal_type="lunch")["id"] == 1
  assert select_recipe_for_rule("batch", [plain], meal_type="dinner")["id"] == 3


def test_generated_dinners_leave_lunch_recipes_alone():
  rules = [make_rule(id=1, day_of_week="all", meal_type="dinner", tag_filter="batch")]
  recipes = [make_recipe(id=1, name="Curry", tags="lunch, batch", covers_days=5),
             make_recipe(id=2, name="Lasagne", tags="dinner, batch", covers_days=2)]
  entries, _ = generate_plan_entries("2026-11-09", "2026-11-15", rules, recipes, [])
  assert {e["recipe_id"] for e in entries} == {2}


def test_already_made_meals_get_no_extra_leftovers_on_regenerate():
  rules = [make_rule(id=1, day_of_week="weekday", meal_type="lunch")]
  curry = make_recipe(id=1, name="Curry", tags="lunch", covers_days=5)
  pasta = make_recipe(id=2, name="Chorizo Pasta", tags="lunch", covers_days=5)
  kept = [{"id": 10, "date": "2026-11-09", "meal_type": "lunch", "recipe_id": 1, "servings": 1, "origin": "made"},
          {"id": 11, "date": "2026-11-10", "meal_type": "lunch", "recipe_id": 1, "servings": 1, "origin": "made",
           "is_continuation": 1, "continuation_of": 10}]
  entries, _ = generate_plan_entries("2026-11-09", "2026-11-13", rules, [curry, pasta], kept)
  assert [e for e in entries if e["recipe_id"] == 1] == []
  assert [e["date"] for e in entries if e["recipe_id"] == 2][0] == "2026-11-11"


def _seed(db):
  db.execute("INSERT INTO recipes (id, name, servings, covers_days, tags) VALUES (1, 'Curry', 8, 5, 'lunch, batch')")
  db.execute("INSERT INTO recipes (id, name, servings, covers_days, tags) VALUES (2, 'Chorizo Pasta', 8, 5, 'lunch')")
  db.execute("INSERT INTO ingredients (id, name, category, unit, price) VALUES (1, 'Sausages', 'meat', 'kg', 10)")
  db.execute("INSERT INTO recipe_ingredients (recipe_id, ingredient_id, quantity) VALUES (1, 1, 1)")
  db.execute("INSERT INTO meal_rules (id, day_of_week, meal_type) VALUES (1, 'weekday', 'lunch')")
  set_setting("plan_start_date", "2026-11-08")
  set_setting("plan_end_date", "2026-11-22")
  db.commit()


def _lunches(db):
  return [dict(r) for r in db.execute(
    "SELECT * FROM meal_plan_entries WHERE meal_type = 'lunch' ORDER BY date")]


def test_five_meals_left_fill_the_weekday_lunches_and_replace_suggestions(db):
  _seed(db)
  cur = db.execute("INSERT INTO meal_plan_entries (date, meal_type, recipe_id, servings) "
                   "VALUES ('2026-11-09', 'lunch', 2, 8)")
  for day in ("10", "11", "12", "13"):
    db.execute("INSERT INTO meal_plan_entries (date, meal_type, recipe_id, servings, is_continuation, "
               "continuation_of) VALUES (?, 'lunch', 2, 8, 1, ?)", (f"2026-11-{day}", cur.lastrowid))
  db.commit()
  result = add_already_made(db, 1, "lunch", "2026-11-08", 5)
  assert result["dates"] == ["2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13"]
  lunches = _lunches(db)
  assert [(l["recipe_id"], l["is_auto_generated"], l["origin"]) for l in lunches] == [(1, 0, "made")] * 5
  assert [l["is_continuation"] for l in lunches] == [0, 1, 1, 1, 1]
  assert {l["continuation_of"] for l in lunches[1:]} == {lunches[0]["id"]}


def test_already_made_skips_kept_meals_and_rejects_bad_input(db):
  _seed(db)
  db.execute("INSERT INTO meal_plan_entries (date, meal_type, recipe_id, servings, is_auto_generated) "
             "VALUES ('2026-11-10', 'lunch', 2, 8, 0)")
  db.commit()
  result = add_already_made(db, 1, "lunch", "2026-11-09", 2)
  assert result["dates"] == ["2026-11-09", "2026-11-11"]
  with pytest.raises(ValueError, match="between 1 and 60"):
    add_already_made(db, 1, "lunch", "2026-11-09", 0)
  with pytest.raises(ValueError, match="Choose a recipe"):
    add_already_made(db, None, "lunch", "2026-11-09", 2)


def test_already_made_is_left_off_the_shopping_list():
  recipes = {1: {"id": 1, "name": "Curry", "servings": 8}}
  links = {1: [{"ingredient_id": 1, "quantity": 1, "unit_override": None}]}
  ingredients = {1: {"id": 1, "name": "Sausages", "category": "meat", "unit": "kg", "price": 10}}
  made = [{"date": "2026-11-09", "meal_type": "lunch", "recipe_id": 1, "servings": 8, "origin": "made"}]
  cooked = [dict(made[0], origin=None)]
  assert generate_shopping_list(made, recipes, links, ingredients) == {}
  assert generate_shopping_list(cooked, recipes, links, ingredients) != {}


def test_route_locks_in_leftovers(client, app):
  login(client)
  with app.app_context():
    from database import get_db
    _seed(get_db())
  page = client.get("/meal-plan").get_data(as_text=True)
  assert "Lock in the leftovers" in page
  resp = client.post("/meal-plan/already-made", data={
    "recipe_id": 1, "meal_type": "lunch", "start": "2026-11-09", "meals": 5}, follow_redirects=True)
  body = resp.get_data(as_text=True)
  assert "locked in for 5 meals" in body and "Already made" in body
