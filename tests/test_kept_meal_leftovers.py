"""A multi-day meal chosen by hand gets its leftover nights, like a generated one."""

from database import set_setting
from services.mutations import place_meal, swap_meal
from services.plan_generator import generate_plan_entries, make_recipe, make_rule

RULES = [make_rule(id=1, day_of_week="all", meal_type="dinner")]
STIR_FRY = make_recipe(id=1, name="Stir Fry", servings=4, covers_days=2)
LAKSA = make_recipe(id=2, name="Laksa", servings=8, covers_days=2)
CURRY = make_recipe(id=3, name="Curry", servings=8, covers_days=1)


def test_regenerate_gives_a_kept_meal_its_leftovers():
  kept = [{"id": 7, "date": "2026-11-08", "meal_type": "dinner", "recipe_id": 1, "servings": 4}]
  entries, _ = generate_plan_entries("2026-11-08", "2026-11-11", RULES, [STIR_FRY, LAKSA, CURRY], kept)
  by_day = {e["date"]: e for e in entries}
  monday = by_day["2026-11-09"]
  assert monday["recipe_id"] == 1 and monday["is_continuation"] == 1
  assert monday["continuation_of"] == ("2026-11-08", "dinner") and monday["servings"] == 4
  assert by_day["2026-11-10"]["recipe_id"] != 1


def test_kept_leftovers_skip_a_night_another_rule_plans():
  rules = [make_rule(id=1, day_of_week="sun,mon,tue,wed,thu,sat", meal_type="dinner"),
           make_rule(id=2, day_of_week="fri", meal_type="dinner", tag_filter="friday")]
  kept = [{"id": 7, "date": "2026-11-12", "meal_type": "dinner", "recipe_id": 1, "servings": 4}]
  entries, _ = generate_plan_entries("2026-11-12", "2026-11-14", rules, [STIR_FRY, CURRY], kept)
  leftovers = [e["date"] for e in entries if e["is_continuation"] and e["recipe_id"] == 1]
  assert leftovers == ["2026-11-14"]  # Thursday's stir fry skips the Friday-rule night


def test_kept_leftover_continuation_is_not_planned_twice():
  kept = [{"id": 7, "date": "2026-11-08", "meal_type": "dinner", "recipe_id": 1, "servings": 4},
          {"id": 8, "date": "2026-11-09", "meal_type": "dinner", "recipe_id": 1, "servings": 4,
           "is_continuation": 1, "continuation_of": 7}]
  entries, _ = generate_plan_entries("2026-11-08", "2026-11-10", RULES, [STIR_FRY, CURRY], kept)
  assert [e for e in entries if e["recipe_id"] == 1] == []


def _seed(db, start="2026-11-08", end="2026-11-22"):
  for recipe in (STIR_FRY, LAKSA, CURRY):
    db.execute("INSERT INTO recipes (id, name, servings, covers_days) VALUES (?, ?, ?, ?)",
               (recipe["id"], recipe["name"], recipe["servings"], recipe["covers_days"]))
  db.execute("INSERT INTO meal_rules (id, day_of_week, meal_type) VALUES (1, 'all', 'dinner')")
  set_setting("plan_start_date", start)
  set_setting("plan_end_date", end)
  db.commit()


def _dinners(db):
  return {r["date"]: dict(r) for r in db.execute(
    "SELECT * FROM meal_plan_entries WHERE meal_type = 'dinner' ORDER BY date")}


def test_swap_hands_the_old_leftover_night_to_the_new_meal(db):
  _seed(db)
  cur = db.execute("INSERT INTO meal_plan_entries (date, meal_type, recipe_id, servings) "
                   "VALUES ('2026-11-08', 'dinner', 2, 8)")
  sunday = cur.lastrowid
  db.execute("INSERT INTO meal_plan_entries (date, meal_type, recipe_id, servings, is_continuation, "
             "continuation_of) VALUES ('2026-11-09', 'dinner', 2, 8, 1, ?)", (sunday,))
  db.commit()
  result = swap_meal(db, sunday, 1, servings=4)
  assert result["leftovers"] == 1
  monday = _dinners(db)["2026-11-09"]
  assert (monday["recipe_id"], monday["is_continuation"], monday["continuation_of"], monday["servings"]) \
    == (1, 1, sunday, 4)


def test_swap_to_a_one_night_meal_keeps_old_leftovers_as_a_meal(db):
  _seed(db)
  cur = db.execute("INSERT INTO meal_plan_entries (date, meal_type, recipe_id, servings) "
                   "VALUES ('2026-11-08', 'dinner', 2, 8)")
  db.execute("INSERT INTO meal_plan_entries (date, meal_type, recipe_id, servings, is_continuation, "
             "continuation_of) VALUES ('2026-11-09', 'dinner', 2, 8, 1, ?)", (cur.lastrowid,))
  db.commit()
  swap_meal(db, cur.lastrowid, 3)
  monday = _dinners(db)["2026-11-09"]
  assert (monday["recipe_id"], monday["is_continuation"], monday["continuation_of"]) == (2, 0, None)


def test_adding_a_two_night_meal_fills_the_free_next_night(db):
  _seed(db)
  result = place_meal(db, "2026-11-08", "dinner", 1)
  assert result["leftovers"] == 1
  dinners = _dinners(db)
  assert dinners["2026-11-09"]["recipe_id"] == 1 and dinners["2026-11-09"]["is_continuation"] == 1
  assert dinners["2026-11-09"]["continuation_of"] == result["entry_id"]


def test_adding_never_takes_a_night_that_is_already_planned(db):
  _seed(db)
  db.execute("INSERT INTO meal_plan_entries (date, meal_type, recipe_id, servings, is_auto_generated) "
             "VALUES ('2026-11-09', 'dinner', 3, 8, 0)")
  db.commit()
  place_meal(db, "2026-11-08", "dinner", 1)
  dinners = _dinners(db)
  assert dinners["2026-11-09"]["recipe_id"] == 3
  assert dinners["2026-11-10"]["recipe_id"] == 1 and dinners["2026-11-10"]["is_continuation"] == 1


def test_regenerate_route_links_leftovers_to_the_kept_meal(db):
  from routes.meal_plan import generate_plan
  _seed(db, end="2026-11-10")
  cur = db.execute("INSERT INTO meal_plan_entries (date, meal_type, recipe_id, servings, is_auto_generated) "
                   "VALUES ('2026-11-08', 'dinner', 1, 4, 0)")
  db.commit()
  generate_plan("2026-11-08", "2026-11-10", chooser=lambda c: c[0])
  monday = _dinners(db)["2026-11-09"]
  assert (monday["recipe_id"], monday["is_continuation"], monday["continuation_of"]) == (1, 1, cur.lastrowid)
