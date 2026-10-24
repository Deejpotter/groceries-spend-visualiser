"""HTTP API (/api/v1): bearer auth, read endpoints, auto-commit import.

The API is the same services the web UI and MCP server use, exposed over
HTTP with a bearer key so agents (and the rebuilt MCP server) can work
against a remote deployment instead of a local SQLite file.
"""

import base64
import io

import pytest

from conftest import login
from services.spend_import import parse_woolworths_invoice_pdf, parse_woolworths_invoice_text
from tests.test_spend_pdf import _minimal_pdf

# Same shape as test_spend_pdf.py: synthetic Woolworths invoice text.
# Subtotal: 5.20 + 2.20 + 4.50 + 7.60 + 5.50 = 25.00.
INVOICE_TEXT = """Sub Total:Sub Total:
Thank you for working with us towards a greener future.  Reusable bags: Reusable bags:
Delivery Fee:Delivery Fee:
Invoice Total:Invoice Total:
Invoice total includes GST of:Invoice total includes GST of:
Paid Amount:Paid Amount:
SuppliedSupplied
LineLine DescriptionDescription OrderedOrdered SuppliedSupplied PricePrice AmountAmount
BabyBaby
1 * Test baby bath
500ml 1 1 $8.80 $8.80
BakeryBakery
2 Golden crumpets round 6 pack 1 1 $2.20 $2.20
2     (Sub) Test raisin toast 520G 0 1 $4.50 $4.50
3 Missing loaf 600g 1 0 $4.40 $0.00
Out Of StockOut Of Stock
4 Free sticker each 1 1 $0.00 $0.00
Serviced DeliServiced Deli
5 Test bacon per kg 0.40 0.40 $19.00 $7.60
MeatMeat
6 Test chicken fillet 0.50 0.50 kg $11.00 $5.50
$25.00
$0.00
$0.00
$25.00
$2.01
$25.00
Tax Invoice Page 1 of 1
Invoice/Order Number: 999001999Invoice/Order Number: 999001999
Date: 07 Jul 2023Date: 07 Jul 2023
"""

CSV_TEXT = (
    "date,basket_id,product_name,quantity,unit_price\n"
    "2026-01-10,api-basket-1,API test milk 2L,2,3.50\n"
    "2026-01-10,api-basket-1,API test bread,1,4.20\n"
    "2026-01-11,api-basket-2,API test eggs,1,6.00\n"
)


@pytest.fixture
def api_client(app):
    """Test client with an API key set and a user row present."""
    from database import get_db, set_setting
    with app.app_context():
        set_setting("mcp_api_key", "test-api-key-123")
        get_db().commit()
        db = get_db()
        from werkzeug.security import generate_password_hash
        db.execute("INSERT OR IGNORE INTO users (username, password_hash) VALUES (?, ?)",
                   ("tester", generate_password_hash("test12345")))
        db.commit()
    client = app.test_client()
    client.environ_base["HTTP_AUTHORIZATION"] = "Bearer test-api-key-123"
    return client


def _pdf_bytes():
    """Minimal one-page PDF carrying INVOICE_TEXT (xref-correct, pypdf-readable)."""
    return _minimal_pdf(INVOICE_TEXT.splitlines())


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def test_health_needs_no_auth(client):
    """The health check stays unauthenticated (CI + deploy checks use it)."""
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.get_json() == {"status": "ok"}


def test_missing_key_is_401(client):
    r = client.get("/api/v1/server-info")
    assert r.status_code == 401
    body = r.get_json()
    assert body["error"]["code"] == "unauthorized"


def test_wrong_key_is_401(client):
    r = client.get("/api/v1/server-info",
                   headers={"Authorization": "Bearer wrong-key"})
    assert r.status_code == 401
    assert r.get_json()["error"]["code"] == "unauthorized"


def test_valid_key_passes(api_client):
    r = api_client.get("/api/v1/server-info")
    assert r.status_code == 200
    body = r.get_json()
    assert body["server"] == "groceries"
    assert "counts" in body


# ---------------------------------------------------------------------------
# Read endpoints
# ---------------------------------------------------------------------------

def test_server_info_reports_counts(api_client, db):
    db.execute("INSERT INTO ingredients (name, category, unit) VALUES ('API milk', 'dairy', 'L')")
    db.commit()
    body = api_client.get("/api/v1/server-info").get_json()
    assert body["counts"]["ingredients"] >= 1
    assert "version" in body


def test_week_plan_endpoint(api_client, db):
    from database import get_db, set_setting
    from datetime import date, timedelta
    today = date.today()
    with api_client.application.app_context():
        set_setting("plan_start_date", today.isoformat())
        set_setting("plan_end_date", (today + timedelta(days=6)).isoformat())
        get_db().commit()
    db.execute("INSERT INTO recipes (name, servings) VALUES ('API soup', 2)")
    db.commit()
    db.execute("INSERT INTO meal_plan_entries (date, meal_type, recipe_id, servings)"
               " VALUES (?, 'dinner', 1, 2)", (today.isoformat(),))
    db.commit()
    body = api_client.get("/api/v1/plan").get_json()
    assert body["plan_start"] == today.isoformat()
    assert len(body["days"]) == 7
    assert body["days"][0]["meals"]["dinner"]["recipe"] == "API soup"


def test_shopping_list_endpoint(api_client, db):
    db.execute("INSERT INTO shopping_list_items (ingredient_name, quantity, unit,"
               " category, is_manual) VALUES ('API item', 1, 'each', 'other', 1)")
    db.commit()
    body = api_client.get("/api/v1/shopping-list").get_json()
    names = [row["name"] for rows in body["groups"].values() for row in rows]
    assert "API item" in names


def test_spend_summary_endpoint(api_client, db):
    from services.spend_import import import_purchases
    _, rows, _ = parse_woolworths_invoice_text(INVOICE_TEXT)
    import_purchases(db, rows)
    body = api_client.get("/api/v1/spend/summary").get_json()
    assert body["total_shops"] == 1
    assert body["total_spend"] > 0


def test_rules_endpoint(api_client, db):
    db.execute("INSERT INTO meal_rules (day_of_week, meal_type, tag_filter, is_active, sort_order)"
               " VALUES ('all', 'dinner', 'standard', 1, 1)")
    db.commit()
    body = api_client.get("/api/v1/rules").get_json()
    assert len(body["rules"]) == 1
    assert body["rules"][0]["meal"] == "dinner"


def test_pantry_endpoint(api_client, db):
    db.execute("INSERT INTO ingredients (name, category, unit) VALUES ('API pantry ing', 'dairy', 'each')")
    db.execute("INSERT INTO pantry_items (ingredient_id, quantity, unit, location)"
               " VALUES (1, 3, 'each', 'pantry')")
    db.commit()
    body = api_client.get("/api/v1/pantry").get_json()
    assert isinstance(body, dict)


def test_ingredients_search_endpoint(api_client, db):
    db.execute("INSERT INTO ingredients (name, category, unit) VALUES ('API zucchini', 'produce', 'each')")
    db.execute("INSERT INTO ingredients (name, category, unit) VALUES ('API toothpaste', 'other', 'each')")
    db.commit()
    body = api_client.get("/api/v1/ingredients?search=zucchini").get_json()
    assert body["count"] == 1
    assert body["ingredients"][0]["name"] == "API zucchini"


def test_recipe_endpoint(api_client, db):
    db.execute("INSERT INTO recipes (name, servings) VALUES ('API curry', 4)")
    db.commit()
    body = api_client.get("/api/v1/recipes/API curry").get_json()
    assert body["name"] == "API curry"


def test_recipe_endpoint_404(api_client):
    r = api_client.get("/api/v1/recipes/nope-not-here")
    assert r.status_code == 404
    assert r.get_json()["error"]["code"] == "not_found"


# ---------------------------------------------------------------------------
# Import endpoint — auto-commit decisions
# ---------------------------------------------------------------------------

def test_import_csv_commits(api_client, db):
    """A CSV upload parses, dedupes and commits in one call."""
    payload = {"files": [{
        "filename": "history.csv",
        "content_base64": base64.b64encode(CSV_TEXT.encode()).decode(),
    }]}
    r = api_client.post("/api/v1/import", json=payload)
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body["added"] == 3
    assert body["skipped"] == 0
    assert body["files"][0]["rows"] == 3
    assert db.execute("SELECT COUNT(*) FROM purchases").fetchone()[0] == 3


def test_import_csv_is_idempotent(api_client, db):
    payload = {"files": [{
        "filename": "history.csv",
        "content_base64": base64.b64encode(CSV_TEXT.encode()).decode(),
    }]}
    api_client.post("/api/v1/import", json=payload)
    r = api_client.post("/api/v1/import", json=payload)
    assert r.status_code == 200
    body = r.get_json()
    assert body["added"] == 0
    assert body["skipped"] == 3


def test_import_pdf_commits_and_skips_bad_lines(api_client, db):
    """The PDF path imports priced lines and skips unavailable/free ones."""
    payload = {"files": [{
        "filename": "invoice.pdf",
        "content_base64": base64.b64encode(_pdf_bytes()).decode(),
    }]}
    r = api_client.post("/api/v1/import", json=payload)
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body["added"] == 5  # 5 priced lines; unavailable + free skipped
    assert body["files"][0]["invoice_number"] == "999001999"
    assert body["files"][0]["order_date"] == "2023-07-07"
    warnings = body["files"][0]["warnings"]
    assert any("unavailable" in w for w in warnings)
    assert any("free" in w for w in warnings)


def test_import_pdf_dedupes(api_client, db):
    payload = {"files": [{
        "filename": "invoice.pdf",
        "content_base64": base64.b64encode(_pdf_bytes()).decode(),
    }]}
    api_client.post("/api/v1/import", json=payload)
    r = api_client.post("/api/v1/import", json=payload)
    body = r.get_json()
    assert body["added"] == 0
    assert body["skipped"] == 5


def test_import_mixed_files(api_client, db):
    payload = {"files": [
        {"filename": "invoice.pdf", "content_base64": base64.b64encode(_pdf_bytes()).decode()},
        {"filename": "history.csv", "content_base64": base64.b64encode(CSV_TEXT.encode()).decode()},
    ]}
    r = api_client.post("/api/v1/import", json=payload)
    assert r.status_code == 200
    body = r.get_json()
    assert body["added"] == 8  # 5 PDF + 3 CSV
    assert len(body["files"]) == 2


def test_import_rejects_bad_base64(api_client):
    payload = {"files": [{"filename": "x.csv", "content_base64": "!!!not-base64!!!"}]}
    r = api_client.post("/api/v1/import", json=payload)
    assert r.status_code == 400
    assert r.get_json()["error"]["code"] == "validation"


def test_import_rejects_missing_files(api_client):
    r = api_client.post("/api/v1/import", json={})
    assert r.status_code == 400
    assert r.get_json()["error"]["code"] == "validation"


def test_import_rejects_unreadable_pdf(api_client):
    payload = {"files": [{
        "filename": "junk.pdf",
        "content_base64": base64.b64encode(b"%PDF-1.4 not really a pdf").decode(),
    }]}
    r = api_client.post("/api/v1/import", json=payload)
    assert r.status_code == 200  # per-file error, not a request failure
    body = r.get_json()
    assert body["added"] == 0
    assert body["files"][0].get("error")


def test_import_requires_auth(client):
    r = client.post("/api/v1/import", json={"files": []})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Write endpoints (share services/mutations with the web UI)
# ---------------------------------------------------------------------------

def test_tick_items_endpoint(api_client, db):
    db.execute("INSERT INTO shopping_list_items (ingredient_name, quantity, unit, category, is_manual)"
               " VALUES ('API tick me', 1, 'each', 'other', 1)")
    db.commit()
    r = api_client.post("/api/v1/shopping-list/tick", json={"item_ids": [1], "checked": True})
    assert r.status_code == 200
    assert r.get_json()["updated"] == 1
    assert db.execute("SELECT checked FROM shopping_list_items WHERE id = 1").fetchone()[0] == 1


def test_add_shopping_item_endpoint(api_client, db):
    r = api_client.post("/api/v1/shopping-list/items",
                        json={"name": "API free item", "quantity": 2, "unit": "each"})
    assert r.status_code == 200
    assert r.get_json()["name"] == "API free item"


def test_place_meal_endpoint(api_client, db):
    db.execute("INSERT INTO recipes (name, servings) VALUES ('API dinner', 2)")
    db.commit()
    from datetime import date
    r = api_client.post("/api/v1/plan/meals",
                        json={"date": date.today().isoformat(), "meal_type": "dinner",
                              "recipe": "API dinner"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["recipe"] == "API dinner"
    assert body["entry_id"]


def test_place_meal_validation_error(api_client, db):
    db.execute("INSERT INTO recipes (name, servings) VALUES ('API valid dinner', 2)")
    db.commit()
    r = api_client.post("/api/v1/plan/meals",
                        json={"date": "not-a-date", "meal_type": "dinner", "recipe": "API valid dinner"})
    assert r.status_code == 400
    assert r.get_json()["error"]["code"] == "validation"


def test_plan_generate_preview(api_client, db):
    from datetime import date, timedelta
    from database import get_db, set_setting
    today = date.today()
    with api_client.application.app_context():
        set_setting("plan_start_date", today.isoformat())
        set_setting("plan_end_date", (today + timedelta(days=6)).isoformat())
        get_db().commit()
    r = api_client.post("/api/v1/plan/generate", json={"confirm": False})
    assert r.status_code == 200
    assert r.get_json()["confirmed"] is False


def test_link_product_endpoint(api_client, db):
    from services.spend_import import import_purchases
    _, rows, _ = parse_woolworths_invoice_text(INVOICE_TEXT)
    import_purchases(db, rows)
    db.execute("INSERT INTO ingredients (name, category, unit) VALUES ('API test chicken fillet', 'meat', 'kg')")
    db.commit()
    r = api_client.post("/api/v1/spend/links",
                        json={"product": "Test chicken fillet", "ingredient": "API test chicken fillet"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["level"] in ("success", "warning")


# ---------------------------------------------------------------------------
# Write endpoints: ingredients and recipes (agent population)
# ---------------------------------------------------------------------------

def test_create_ingredient(api_client, db):
    """POST /ingredients creates an ingredient without a CSRF token."""
    r = api_client.post("/api/v1/ingredients", json={
        "name": "API Test Milk", "display_name": "Milk", "category": "dairy",
        "unit": "L", "price": 2.10, "store": "Woolworths",
        "minimum_stock": 2.0, "pack_size": 1.0,
    })
    assert r.status_code == 201
    body = r.get_json()
    assert body["id"] > 0
    row = db.execute("SELECT * FROM ingredients WHERE id = ?", (body["id"],)).fetchone()
    assert row["name"] == "API Test Milk"
    assert row["price"] == 2.10
    assert row["origin"] == "api"


def test_create_ingredient_dedupes_by_name(api_client, db):
    """A second create with the same name returns the existing row."""
    first = api_client.post("/api/v1/ingredients", json={"name": "API Dupe"})
    assert first.status_code == 201
    second = api_client.post("/api/v1/ingredients", json={"name": "API Dupe"})
    assert second.status_code == 200
    assert second.get_json()["id"] == first.get_json()["id"]
    assert second.get_json()["note"] == "already exists."
    count = db.execute("SELECT COUNT(*) FROM ingredients WHERE name = 'API Dupe'").fetchone()[0]
    assert count == 1


def test_create_ingredient_requires_name(api_client):
    r = api_client.post("/api/v1/ingredients", json={"category": "dairy"})
    assert r.status_code == 400
    assert r.get_json()["error"]["code"] == "validation"


def test_create_recipe(api_client, db):
    """POST /recipes creates a recipe; covers_days defaults to 1."""
    r = api_client.post("/api/v1/recipes", json={
        "name": "API Test Pasta", "description": "Test dish", "servings": 4,
        "tags": "dinner,quick",
    })
    assert r.status_code == 201
    body = r.get_json()
    row = db.execute("SELECT * FROM recipes WHERE id = ?", (body["id"],)).fetchone()
    assert row["name"] == "API Test Pasta"
    assert row["servings"] == 4
    assert row["covers_days"] == 1
    assert row["tags"] == "dinner,quick"


def test_create_recipe_dedupes_by_name(api_client, db):
    first = api_client.post("/api/v1/recipes", json={"name": "API Dupe Recipe"})
    assert first.status_code == 201
    second = api_client.post("/api/v1/recipes", json={"name": "API Dupe Recipe"})
    assert second.status_code == 200
    assert second.get_json()["id"] == first.get_json()["id"]


def test_create_recipe_requires_name(api_client):
    r = api_client.post("/api/v1/recipes", json={"servings": 4})
    assert r.status_code == 400
    assert r.get_json()["error"]["code"] == "validation"


def test_add_recipe_ingredient(api_client, db):
    """POST /recipes/<id>/ingredients links an ingredient by id or name."""
    ing = api_client.post("/api/v1/ingredients", json={"name": "API Link Ing", "unit": "kg"})
    ing_id = ing.get_json()["id"]
    rec = api_client.post("/api/v1/recipes", json={"name": "API Link Recipe"})
    rec_id = rec.get_json()["id"]

    r = api_client.post(f"/api/v1/recipes/{rec_id}/ingredients",
                        json={"ingredient": "API Link Ing", "quantity": 0.5, "unit_override": "kg"})
    assert r.status_code == 201
    assert r.get_json()["ingredient_id"] == ing_id

    row = db.execute(
        "SELECT * FROM recipe_ingredients WHERE recipe_id = ? AND ingredient_id = ?",
        (rec_id, ing_id)).fetchone()
    assert row["quantity"] == 0.5
    assert row["unit_override"] == "kg"


def test_add_recipe_ingredient_unknown_recipe(api_client):
    r = api_client.post("/api/v1/recipes/99999/ingredients",
                        json={"ingredient": "anything", "quantity": 1})
    assert r.status_code == 404
    assert r.get_json()["error"]["code"] == "not_found"


def test_add_recipe_ingredient_by_recipe_name(api_client, db):
    """The recipe can be referenced by exact name, not just id (MCP sends names)."""
    api_client.post("/api/v1/ingredients", json={"name": "API Name Link Ing", "unit": "kg"})
    api_client.post("/api/v1/recipes", json={"name": "API Name Link Recipe"})

    r = api_client.post("/api/v1/recipes/API Name Link Recipe/ingredients",
                        json={"ingredient": "API Name Link Ing", "quantity": 1.5, "unit_override": "kg"})
    assert r.status_code == 201, r.get_data(as_text=True)
    assert r.get_json()["quantity"] == 1.5


def test_add_recipe_ingredient_unknown_ingredient(api_client):
    rec = api_client.post("/api/v1/recipes", json={"name": "API Missing Ing Recipe"})
    rec_id = rec.get_json()["id"]
    r = api_client.post(f"/api/v1/recipes/{rec_id}/ingredients",
                        json={"ingredient": "Does Not Exist", "quantity": 1})
    assert r.status_code == 404
    assert r.get_json()["error"]["code"] == "not_found"


def test_write_endpoints_require_auth(client):
    """Creating ingredients and recipes still needs a valid key."""
    assert client.post("/api/v1/ingredients", json={"name": "x"}).status_code == 401
    assert client.post("/api/v1/recipes", json={"name": "x"}).status_code == 401


def test_delete_ingredient_by_name(api_client, db):
    api_client.post("/api/v1/ingredients", json={"name": "API Delete Me"})
    r = api_client.delete("/api/v1/ingredients/API Delete Me")
    assert r.status_code == 200
    assert r.get_json()["deleted"] > 0
    assert db.execute("SELECT COUNT(*) FROM ingredients WHERE name = 'API Delete Me'").fetchone()[0] == 0


def test_delete_ingredient_drops_recipe_links(api_client, db):
    ing = api_client.post("/api/v1/ingredients", json={"name": "API Linked Delete"})
    rec = api_client.post("/api/v1/recipes", json={"name": "API Link Owner"})
    api_client.post(f"/api/v1/recipes/{rec.get_json()['id']}/ingredients",
                    json={"ingredient": "API Linked Delete", "quantity": 1})
    api_client.delete("/api/v1/ingredients/API Linked Delete")
    remaining = db.execute("SELECT COUNT(*) FROM recipe_ingredients WHERE ingredient_id = ?",
                           (ing.get_json()["id"],)).fetchone()[0]
    assert remaining == 0


def test_delete_recipe_by_name(api_client, db):
    api_client.post("/api/v1/recipes", json={"name": "API Recipe Delete"})
    r = api_client.delete("/api/v1/recipes/API Recipe Delete")
    assert r.status_code == 200
    assert db.execute("SELECT COUNT(*) FROM recipes WHERE name = 'API Recipe Delete'").fetchone()[0] == 0


def test_delete_missing_is_404(api_client):
    assert api_client.delete("/api/v1/ingredients/Nope").status_code == 404
    assert api_client.delete("/api/v1/recipes/Nope").status_code == 404


def test_api_blueprint_is_csrf_exempt(app):
    """The API blueprint is registered as CSRF-exempt (bearer auth, not cookies)."""
    from routes.api import api_bp
    csrf = app.extensions["csrf"]
    assert api_bp in csrf._exempt_blueprints


def test_api_post_works_with_csrf_enabled(app, db):
    """With CSRF enforcement ON, a token-less API POST still succeeds.

    This is the production case: agents send JSON with a bearer key and no
    CSRF token, so the API blueprint must bypass Flask-WTF's protection.
    """
    from database import get_db, set_setting
    app.config["WTF_CSRF_ENABLED"] = True
    with app.app_context():
        set_setting("mcp_api_key", "csrf-test-key")
        get_db().commit()
    client = app.test_client()
    client.environ_base["HTTP_AUTHORIZATION"] = "Bearer csrf-test-key"
    r = client.post("/api/v1/ingredients", json={"name": "CSRF Test Ingredient"})
    assert r.status_code == 201, r.get_data(as_text=True)

# ---------------------------------------------------------------------------
# BankSync status and manual sync
# ---------------------------------------------------------------------------

def _set_banksync(db, key="bsk_test"):
    db.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES ('banksync_api_key', ?)",
        (key,))
    db.commit()


def test_banksync_status_when_unconfigured(api_client):
    r = api_client.get("/api/v1/banksync")
    assert r.status_code == 200
    body = r.get_json()
    assert body["configured"] is False
    assert body["accounts_tracked"] == 0


def test_banksync_status_reports_progress(api_client):
    from database import get_db
    with api_client.application.app_context():
        db = get_db()
        _set_banksync(db)
        db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('banksync_cursors', ?)",
            ('{"a1": {"date": "2026-10-05"}}',))
        db.execute(
            """INSERT INTO purchases
               (order_date, basket_id, store, product_name, quantity, unit_price, line_total, origin)
               VALUES ('2026-10-05', 'bs:txn-1', 'Woolworths', 'Milk 2L', 1, 3.0, 3.0, 'banksync')""")
        db.commit()
    r = api_client.get("/api/v1/banksync")
    body = r.get_json()
    assert body["configured"] is True
    assert body["accounts_tracked"] == 1
    assert body["account_cursors"] == {"a1": {"date": "2026-10-05"}}
    assert body["purchases_imported"] == 1
    assert body["latest_purchase_date"] == "2026-10-05"


def test_banksync_sync_without_key_is_400(api_client):
    r = api_client.post("/api/v1/banksync/sync")
    assert r.status_code == 400
    assert r.get_json()["error"]["code"] == "banksync_not_configured"


def test_banksync_sync_imports_and_reports(api_client, monkeypatch):
    from database import get_db
    from test_banksync import TXN_COLES, TXN_UNKNOWN, TXN_WOOLWORTHS, FakeBankSync
    with api_client.application.app_context():
        db = get_db()
        _set_banksync(db)
    monkeypatch.setattr(
        "services.banksync_client.BankSyncClient",
        lambda api_key: FakeBankSync([TXN_WOOLWORTHS, TXN_COLES, TXN_UNKNOWN]))
    r = api_client.post("/api/v1/banksync/sync")
    assert r.status_code == 200
    body = r.get_json()
    assert body["added"] == 2
    assert body["unclassified"] == 1
    assert body["errors"] == []
    assert body["status"]["purchases_imported"] == 2
    assert body["status"]["last_sync"]


def test_banksync_sync_surfaces_upstream_failure(api_client, monkeypatch):
    from database import get_db
    from services.banksync_client import BankSyncError
    with api_client.application.app_context():
        db = get_db()
        _set_banksync(db)

    class Broken:
        def __init__(self, api_key):
            pass

        def list_banks(self):
            raise BankSyncError("BankSync says the bank needs reconnecting (503).")

    monkeypatch.setattr("services.banksync_client.BankSyncClient", Broken)
    r = api_client.post("/api/v1/banksync/sync")
    assert r.status_code == 502
    assert r.get_json()["error"]["code"] == "banksync_failed"
    assert "reconnecting" in r.get_json()["error"]["message"]


# ---------------------------------------------------------------------------
# Purchases listing
# ---------------------------------------------------------------------------

def _seed_purchases(db):
    rows = [
        ("2026-10-19", "b1", "Woolworths", "Milk 2L", 2, 3.00, 6.00),
        ("2026-10-19", "b1", "Woolworths", "Bread Loaf", 1, 4.00, 4.00),
        ("2026-10-05", "b2", "Coles", "Milk 2L", 1, 3.50, 3.50),
        ("2026-09-20", "b3", "Woolworths", "Apples", 1, 5.00, 5.00),
    ]
    for order_date, basket, store, name, qty, unit, total in rows:
        db.execute(
            """INSERT INTO purchases
               (order_date, basket_id, store, product_name, quantity, unit_price,
                line_total, origin)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'banksync')""",
            (order_date, basket, store, name, qty, unit, total))
    db.commit()


def test_purchases_are_newest_first_with_window_totals(api_client):
    from database import get_db
    with api_client.application.app_context():
        _seed_purchases(get_db())
    r = api_client.get("/api/v1/purchases")
    assert r.status_code == 200
    body = r.get_json()
    assert [p["product_name"] for p in body["purchases"]] == [
        "Milk 2L", "Bread Loaf", "Milk 2L", "Apples"]
    assert body["total_rows"] == 4
    assert body["baskets"] == 3
    assert body["stores"] == 2
    assert body["spend"] == 18.5
    assert body["first_date"] == "2026-09-20"
    assert body["last_date"] == "2026-10-19"


def test_purchases_date_window(api_client):
    from database import get_db
    with api_client.application.app_context():
        _seed_purchases(get_db())
    body = api_client.get("/api/v1/purchases?from=2026-10-01&to=2026-10-19").get_json()
    assert body["total_rows"] == 3
    assert body["spend"] == 13.5
    assert all(p["order_date"] >= "2026-10-01" for p in body["purchases"])


def test_purchases_store_and_search_filters(api_client):
    from database import get_db
    with api_client.application.app_context():
        _seed_purchases(get_db())
    coles = api_client.get("/api/v1/purchases?store=Coles").get_json()
    assert coles["total_rows"] == 1
    milk = api_client.get("/api/v1/purchases?search=milk").get_json()
    assert milk["total_rows"] == 2
    assert milk["spend"] == 9.5


def test_purchases_limit_is_capped_and_validated(api_client):
    from database import get_db
    with api_client.application.app_context():
        _seed_purchases(get_db())
    limited = api_client.get("/api/v1/purchases?limit=1").get_json()
    assert limited["count"] == 1
    assert limited["total_rows"] == 4  # totals are the window, not the page
    assert api_client.get("/api/v1/purchases?limit=0").get_json()["limit"] == 1
    assert api_client.get("/api/v1/purchases?limit=9999").get_json()["limit"] == 500
    bad = api_client.get("/api/v1/purchases?limit=abc")
    assert bad.status_code == 400
    assert bad.get_json()["error"]["code"] == "bad_limit"


def test_purchases_reject_bad_dates(api_client):
    r = api_client.get("/api/v1/purchases?from=last-tuesday")
    assert r.status_code == 400
    assert r.get_json()["error"]["code"] == "bad_date"


def test_purchases_empty_window(api_client):
    body = api_client.get("/api/v1/purchases?from=2030-01-01").get_json()
    assert body["count"] == 0
    assert body["total_rows"] == 0
    assert body["spend"] == 0
