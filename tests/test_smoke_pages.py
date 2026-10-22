"""Page-level smoke coverage: every blueprint index renders 200 when logged in.

Guards the class of bug where a page 500s on a code path no other test
touches (dashboard with subscriptions, fortnight with date objects,
import page structure). Seeded minimally: one active subscription, one
purchase, plan dates set.
"""

from conftest import login


def _seed(client, db):
    login(client, "smokeuser", "test12345")
    db.execute("INSERT INTO ingredients (name, category, unit) VALUES ('Milk', 'dairy', 'L')")
    db.execute("INSERT INTO recipes (name, servings) VALUES ('Soup', 2)")
    db.execute(
        "INSERT INTO meal_rules (day_of_week, meal_type, tag_filter, sort_order, is_active)"
        " VALUES ('all', 'dinner', 'standard', 1, 1)")
    db.execute(
        "INSERT INTO subscriptions (name, amount, frequency, category, next_due, active)"
        " VALUES ('Netflix', 18.99, 'monthly', 'entertainment', '2026-10-20', 1)")
    db.execute(
        "INSERT INTO purchases (order_date, basket_id, store, product_name, quantity,"
        " unit_price, line_total, origin)"
        " VALUES ('2026-10-05', 'b1', 'Woolworths', 'WOOLWORTHS', 1, 10.00, 10.00, 'csv')")
    db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('plan_start_date', '2026-10-12')")
    db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('plan_end_date', '2026-10-25')")
    db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('monthly_budget', '800')")
    db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('fortnightly_income', '2400')")
    db.commit()


PAGES = [
    "/",
    "/meal-plan",
    "/meal-plan?tab=rules",
    "/shopping-list",
    "/pantry",
    "/pantry/add",
    "/ingredients",
    "/ingredients/add",
    "/recipes",
    "/recipes/add",
    "/recipes?favourites=1",
    "/search",
    "/search?q=milk",
    "/spend",
    "/spend/import",
    "/spend/products",
    "/subscriptions",
    "/subscriptions/add",
    "/subscriptions/fortnight",
    "/subscriptions/suggest",
    "/settings",
    "/api/v1/health",
]


def test_every_index_page_renders_200(client, db):
    _seed(client, db)
    failures = []
    for page in PAGES:
        try:
            resp = client.get(page)
        except Exception as exc:  # testing=True propagates template errors
            failures.append(f"{page} -> RAISES {type(exc).__name__}: {exc}")
            continue
        if resp.status_code != 200:
            failures.append(f"{page} -> {resp.status_code}")
    assert not failures, "pages failing: " + "; ".join(failures)


def test_meal_rules_shortcuts_redirect_to_tab(client, db):
    """The /meal-rules shortcuts redirect into the tabbed plan view."""
    _seed(client, db)
    for page in ("/meal-rules", "/meal-rules/add"):
        resp = client.get(page)
        assert resp.status_code == 302
        assert "tab=rules" in resp.headers["Location"]
    assert client.get("/meal-plan?tab=rules").status_code == 200


def test_dashboard_with_subscriptions_shows_spare(client, db):
    """C1 regression: the dashboard must render while subs are active."""
    _seed(client, db)
    resp = client.get("/")
    assert resp.status_code == 200


def test_fortnight_renders_date_range(client, db):
    """C2 regression: date objects must survive the nice_date filter."""
    _seed(client, db)
    resp = client.get("/subscriptions/fortnight")
    assert resp.status_code == 200
    assert b"Fortnightly Budget" in resp.data


def test_import_page_has_both_working_forms(client, db):
    """C3 regression: bank form closed with submit; BankSync form separate."""
    _seed(client, db)
    html = client.get("/spend/import").data.decode()
    assert html.count("<form") == html.count("</form>"), "unbalanced forms"
    assert 'action="/spend/import-bank"' in html or "import-bank" in html
    assert "Import bank CSV" in html
    assert "Sync now" in html or "Open settings" in html
