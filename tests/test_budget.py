"""Monthly budget pacing and fortnightly proration."""

from datetime import date, timedelta

from conftest import login
from services.budget import budget_status, payday_split, prorate_estimate


def _rows(*pairs):
    return [{"order_date": d, "line_total": t} for d, t in pairs]


def test_no_target_returns_none():
    assert budget_status(_rows(("2026-10-01", 100)), None) is None
    assert budget_status(_rows(("2026-10-01", 100)), 0) is None


def test_on_track():
    # Oct 11 of 31 days = 35% elapsed; $100 of $800 = 12.5% spent
    rows = _rows(("2026-10-01", 60), ("2026-10-05", 40),
                   ("2026-09-30", 999))  # last month excluded
    status = budget_status(rows, 800, today=date(2026, 10, 11))
    assert status["tone"] == "ok"
    assert status["spent"] == 100
    assert status["month"] == "2026-10"
    assert status["days_in_month"] == 31
    assert status["days_left"] == 20
    assert status["projected"] == round(100 / (11 / 31), 2)
    assert status["variance"] == round(800 - 100 / (11 / 31), 2)


def test_watch_tone_within_ten_percent():
    # Oct 11: $290 of $800 -> projected $817, within 10% of target
    rows = _rows(("2026-10-01", 290))
    status = budget_status(rows, 800, today=date(2026, 10, 11))
    assert status["tone"] == "warn"
    assert status["projected"] > 800


def test_over_tone():
    # Oct 11: $500 of $800 -> projected $1409, well over target
    rows = _rows(("2026-10-01", 300), ("2026-10-10", 200))
    status = budget_status(rows, 800, today=date(2026, 10, 11))
    assert status["tone"] == "danger"
    assert status["variance"] < 0


def test_first_day_of_month():
    # elapsed_pct is tiny but nonzero: projection still works
    status = budget_status(_rows(("2026-10-01", 10)), 800,
                             today=date(2026, 10, 1))
    assert status["elapsed_pct"] == round(1 / 31, 4)
    assert status["projected"] == 310.0
    assert status["tone"] == "ok"


def test_no_spend_yet():
    status = budget_status([], 800, today=date(2026, 10, 11))
    assert status["spent"] == 0
    assert status["projected"] == 0
    assert status["tone"] == "ok"


def test_budget_tile_on_dashboard(client, db):
    from conftest import login
    from database import set_setting
    login(client)
    db.execute(
        "INSERT INTO purchases (order_date, basket_id, store,"
        " product_name, quantity, unit_price, line_total, origin)"
        " VALUES ('2026-10-05', 'b1', 'Woolworths', 'WOOLWORTHS',"
        " 1, 100.00, 100.00, 'csv')")
    set_setting("monthly_budget", "800")
    db.commit()
    resp = client.get("/spend")
    assert resp.status_code == 200
    assert b"This month" in resp.data
    assert b"on track" in resp.data


def test_budget_tile_hidden_without_target(client, db):
    from conftest import login
    login(client)
    resp = client.get("/spend")
    assert resp.status_code == 200
    assert b"This month" not in resp.data


def test_save_budget_setting(client, db):
    from conftest import login
    login(client)
    resp = client.post("/settings/save", data={
        "action": "save_budget", "monthly_budget": "850.50",
    })
    assert resp.status_code == 302
    assert db.execute(
        "SELECT value FROM settings WHERE key = 'monthly_budget'"
    ).fetchone()["value"] == "850.50"
    resp = client.post("/settings/save", data={
        "action": "save_budget", "monthly_budget": "abc",
    })
    assert resp.status_code == 200  # re-rendered with the error
    resp = client.post("/settings/save", data={
        "action": "save_budget", "monthly_budget": "",
    })
    assert resp.status_code == 302
    assert db.execute(
        "SELECT value FROM settings WHERE key = 'monthly_budget'"
    ).fetchone()["value"] == ""


def test_prorate_full_overlap():
    value, plan_days, overlap = prorate_estimate(
        100, "2026-10-12", "2026-10-25", "2026-10-12", "2026-10-25")
    assert (value, plan_days, overlap) == (100, 14, 14)


def test_prorate_partial_overlap():
    # 28-day plan, fortnight covers the first 14 days: half the cost.
    value, plan_days, overlap = prorate_estimate(
        200, "2026-10-12", "2026-11-08", "2026-10-12", "2026-10-25")
    assert (value, plan_days, overlap) == (100, 28, 14)


def test_prorate_no_overlap_and_bad_ranges():
    assert prorate_estimate(200, "2026-10-12", "2026-11-08",
                            "2026-12-01", "2026-12-14") == (0.0, 28, 0)
    assert prorate_estimate(200, "", "2026-11-08",
                            "2026-10-12", "2026-10-25") == (0.0, 0, 0)
    assert prorate_estimate(200, "2026-11-08", "2026-10-12",
                            "2026-10-12", "2026-10-25") == (0.0, 0, 0)


def test_prorate_accepts_date_objects():
    value, plan_days, overlap = prorate_estimate(
        100, date(2026, 10, 12), date(2026, 10, 25),
        date(2026, 10, 12), date(2026, 10, 25))
    assert (value, plan_days, overlap) == (100, 14, 14)


def test_fortnight_prorates_long_plan(client, db):
    """H1: a 28-day plan shows half its groceries in a fully-overlapped fortnight."""
    from conftest import login
    from services.budget import prorate_estimate as _prorate
    from services.repository import load_shopping_inputs
    from services.shopping_list_generator import generate_shopping_list, total_cost
    login(client)
    today = date.today()
    start = today.isoformat()
    end = (today + timedelta(days=27)).isoformat()
    db.execute("INSERT INTO ingredients (name, category, unit, price)"
               " VALUES ('Rice', 'pantry', 'kg', 4.00)")
    db.execute("INSERT INTO recipes (name, servings) VALUES ('Rice bowl', 2)")
    db.execute("INSERT INTO recipe_ingredients (recipe_id, ingredient_id, quantity)"
               " VALUES (1, 1, 1)")
    db.execute("INSERT INTO meal_plan_entries (date, meal_type, recipe_id, servings,"
               " is_auto_generated) VALUES (?, 'dinner', 1, 2, 0)", (start,))
    db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('plan_start_date', ?)", (start,))
    db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('plan_end_date', ?)", (end,))
    db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('fortnightly_income', '2400')")
    db.commit()
    entries, recipes, ring, ing = load_shopping_inputs(start, end)
    full = total_cost(generate_shopping_list(entries, recipes, ring, ing))
    assert full > 0
    from services.subscriptions import fortnight_for_date
    f_start, f_end = fortnight_for_date(today)
    expected, plan_days, overlap = _prorate(full, start, end, f_start, f_end)
    resp = client.get("/subscriptions/fortnight")
    assert resp.status_code == 200
    assert f"${expected:.2f}".encode() in resp.data
    if overlap != plan_days:
        assert b"prorated" in resp.data
    else:
        assert b"prorated" not in resp.data


def _summary(income=2000.0, groceries=600.0, bills=500.0):
    return {"has_income": bool(income), "income": income, "groceries": groceries,
            "subscription_total": bills}


def test_payday_split_needs_income():
    assert payday_split(_summary(income=None)) is None


def test_payday_split_sets_savings_aside_before_spending():
    split = payday_split(_summary(), 200, 50)
    assert split["bills"] == 500
    assert split["spending"] == 1250
    assert split["left_to_spend"] == 650
    assert split["overcommitted"] is False


def test_payday_split_accepts_saved_setting_strings():
    assert payday_split(_summary(), "200.00", "")["savings"] == 200


def test_payday_split_flags_overcommitted_pay():
    split = payday_split(_summary(), 1000, 0)
    assert split["left_to_spend"] == -100
    assert split["overcommitted"] is True


def test_fortnight_page_shows_payday_split(client, db):
    login(client, "splituser", "test123")
    for key, value in (("fortnightly_income", "2100"), ("savings_per_pay", "200")):
        db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    db.commit()
    resp = client.get("/subscriptions/fortnight")
    assert resp.status_code == 200
    assert b"Payday split" in resp.data
    assert b"$200.00" in resp.data


def test_settings_saves_payday_amounts_and_rejects_bad_ones(client, db):
    login(client, "savesplit", "test123")
    client.post("/settings/save", data={"action": "save_budget", "savings_per_pay": "$150"})
    row = db.execute("SELECT value FROM settings WHERE key = 'savings_per_pay'").fetchone()
    assert row["value"] == "150.00"
    resp = client.post("/settings/save", data={"action": "save_budget", "savings_per_pay": "lots"})
    assert b"Savings each payday must be a dollar amount." in resp.data
