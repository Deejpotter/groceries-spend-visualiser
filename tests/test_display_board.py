"""Fridge display board: pure board builder and the /api/v1/display endpoint."""

from datetime import date, timedelta

from services.display_board import board_info, build_board, clamp_days, make_board_entry

TODAY = date(2026, 11, 8)  # a Sunday


def test_board_lists_every_day_with_gaps():
    board = build_board([make_board_entry("2026-11-09")], [], TODAY, days=3)
    assert [d["date"] for d in board["days"]] == ["2026-11-08", "2026-11-09", "2026-11-10"]
    assert board["days"][0]["meals"] == {}
    assert board["days"][0]["today"] is True and board["days"][1]["today"] is False
    assert board["days"][1]["meals"]["dinner"] == {"name": "Spag bol", "note": ""}
    assert (board["days"][0]["dow"], board["days"][0]["day"], board["days"][0]["mon"]) == ("Sun", 8, "Nov")


def test_notes_for_leftovers_made_and_rollover():
    entries = [make_board_entry("2026-11-08", is_continuation=1),
               make_board_entry("2026-11-09", origin="made"),
               make_board_entry("2026-11-10", origin="rollover")]
    notes = [d["meals"]["dinner"]["note"] for d in build_board(entries, [], TODAY, days=3)["days"]]
    assert notes == ["Leftovers", "Already made", "Already bought"]


def test_skip_shows_eating_out_and_meal_filter():
    entries = [make_board_entry("2026-11-08", meal_type="lunch", recipe_name="Wraps")]
    skips = [{"date": "2026-11-08", "meal_type": "dinner"}]
    day = build_board(entries, skips, TODAY, days=1, meal_types=["dinner"])["days"][0]
    assert day["meals"] == {"dinner": {"name": "Eating out", "note": ""}}


def test_long_names_are_shortened():
    name = build_board([make_board_entry(recipe_name="x" * 80)], [], TODAY, 1)["days"][0]["meals"]["dinner"]["name"]
    assert len(name) <= 40 and name.endswith("...")


def test_clamp_days():
    assert clamp_days(None) == 14
    assert clamp_days("abc") == 14
    assert clamp_days("0") == 1
    assert clamp_days("90") == 31
    assert clamp_days("28") == 28


def test_board_info():
    info = board_info({"next_payday": "2026-11-13"}, 4, TODAY)
    assert info == {"next_payday": "2026-11-13", "days_to_payday": 5, "shopping_left": 4}
    assert board_info(None, None, TODAY) == {}


def test_display_endpoint(app):
    from database import get_db, set_setting
    from models import today
    day = today()
    with app.app_context():
        set_setting("mcp_api_key", "display-key")
        db = get_db()
        rid = db.execute("INSERT INTO recipes (name, servings) VALUES ('Board curry', 4)").lastrowid
        db.execute("INSERT INTO meal_plan_entries (date, meal_type, recipe_id, servings) VALUES (?, 'dinner', ?, 4)",
                   ((day + timedelta(days=1)).isoformat(), rid))
        db.execute("INSERT INTO shopping_list_items (ingredient_name, quantity, unit) VALUES ('Rice', 1, 'kg')")
        db.commit()
    client = app.test_client()
    assert client.get("/api/v1/display").status_code == 401
    res = client.get("/api/v1/display?days=7&meals=dinner,bogus",
                     headers={"Authorization": "Bearer display-key"})
    assert res.status_code == 200
    body = res.get_json()
    assert len(body["days"]) == 7 and body["today"] == day.isoformat()
    assert body["days"][1]["meals"]["dinner"]["name"] == "Board curry"
    assert body["info"]["shopping_left"] == 1
