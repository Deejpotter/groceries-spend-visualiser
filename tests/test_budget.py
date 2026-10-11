"""Monthly budget pacing."""

from datetime import date

from services.budget import budget_status


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
