"""Spend tracking: CSV parsing, cup prices, analysis, and routes."""

import io

import pytest

from conftest import login
from services.spend_analysis import analyze_purchases
from services.spend_import import (
    import_purchases, parse_cup_price, parse_purchase_csv, price_per_unit, product_url,
)

WOOLIES_CSV = """date,basket_id,channel,product_name,quantity,unit_price,cup_price,stockcode
2026-08-24,b1,online,"Beef Mince 500g",2,8.00,"$16.00 / 1KG",1
2026-08-24,b1,online,"Bananas",6,0.50,"$0.50 / 1EA",2
2026-09-07,b2,instore,"Beef Mince 500g",1,8.50,"$17.00 / 1KG",1
2026-09-07,b2,instore,"Milk 2L",1,3.10,"$1.55 / 1L",3
"""


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("$1.51 / 100G", (1.51, 100.0, "g")),
    ("$16.00 / 1KG", (16.0, 1.0, "kg")),
    ("$0.50 / 1EA", (0.5, 1.0, "each")),
    ("$3.20 / 1L", (3.2, 1.0, "L")),
    ("$0.44 / 100ML", (0.44, 100.0, "mL")),
    ("$0.10 / 1 sheets", None),
    ("", None),
    (None, None),
])
def test_parse_cup_price(text, expected):
    assert parse_cup_price(text) == expected


def test_price_per_unit_converts_cup_price():
    assert price_per_unit("$1.51 / 100G", 5.0, "kg") == pytest.approx(15.1)
    assert price_per_unit("$1.51 / 100G", 5.0, "g") == pytest.approx(0.0151)
    assert price_per_unit("$1.55 / 1L", 3.1, "mL") == pytest.approx(0.00155)


def test_price_per_unit_falls_back_to_pack_price_for_count_units():
    assert price_per_unit(None, 4.5, "pack") == 4.5
    assert price_per_unit("$16.00 / 1KG", 8.0, "each") == 8.0
    assert price_per_unit(None, 4.5, "kg") is None  # can't guess a weight price


def test_product_url():
    assert product_url("Woolworths", "55613") == "https://www.woolworths.com.au/shop/productdetails/55613"
    assert product_url("woolworths", 3408) == "https://www.woolworths.com.au/shop/productdetails/3408"
    assert product_url("Coles", "55613") is None
    assert product_url("Woolworths", "") is None
    assert product_url("Woolworths", "abc") is None


def test_parse_purchase_csv():
    rows, errors = parse_purchase_csv(WOOLIES_CSV)
    assert errors == []
    assert len(rows) == 4
    assert rows[0]["line_total"] == 16.0
    assert rows[0]["order_date"] == "2026-08-24"


def test_parse_purchase_csv_minimal_columns_and_bad_rows():
    text = "date,product_name,quantity,line_total\n01/09/2026,Eggs,1,6.5\nnot-a-date,Bread,1,3\n2026-09-01,Gone,1,null\n"
    rows, errors = parse_purchase_csv(text, store="Aldi")
    assert len(rows) == 1
    assert rows[0]["order_date"] == "2026-09-01"
    assert rows[0]["basket_id"] == "Aldi-2026-09-01"
    assert len(errors) == 2
    assert "Line 3" in errors[0]
    assert errors[1].startswith("1 line(s) had no price")


def test_parse_purchase_csv_missing_columns():
    rows, errors = parse_purchase_csv("date,name\n2026-01-01,x\n")
    assert rows == []
    assert "missing required column" in errors[0]


def test_import_is_idempotent(db):
    rows, _ = parse_purchase_csv(WOOLIES_CSV)
    assert import_purchases(db, rows) == (4, 0)
    assert import_purchases(db, rows) == (0, 4)
    assert db.execute("SELECT COUNT(*) FROM purchases").fetchone()[0] == 4


def test_import_inherits_existing_product_link(db):
    db.execute("INSERT INTO ingredients (id, name, category, unit) VALUES (7, 'Mince', 'meat', 'kg')")
    rows, _ = parse_purchase_csv(WOOLIES_CSV)
    import_purchases(db, rows[:1])
    db.execute("UPDATE purchases SET ingredient_id = 7")
    import_purchases(db, rows[2:3])  # a later purchase of the same product
    linked = db.execute("SELECT COUNT(*) FROM purchases WHERE ingredient_id = 7").fetchone()[0]
    assert linked == 2


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def test_analyze_purchases_empty():
    assert analyze_purchases([]) is None


def test_analyze_purchases_stats():
    rows, _ = parse_purchase_csv(WOOLIES_CSV)
    stats = analyze_purchases(rows)
    assert stats["total_spend"] == pytest.approx(16 + 3 + 8.5 + 3.1)
    assert stats["total_shops"] == 2
    assert stats["avg_spend_per_shop"] == pytest.approx(30.6 / 2)
    assert stats["avg_days_between_shops"] == 14
    assert stats["monthly_spend"] == [("2026-08", 19.0), ("2026-09", 11.6)]
    top = stats["top_by_frequency"][0]
    assert top["product_name"] == "Beef Mince 500g"
    assert top["orders"] == 2
    assert stats["top_by_spend"][0]["total_spend"] == 24.5


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def _upload(client, text=WOOLIES_CSV):
    return client.post("/spend/import", data={"file": (io.BytesIO(text.encode()), "orders.csv"), "store": "Woolworths"},
                       content_type="multipart/form-data", follow_redirects=True)


def test_spend_dashboard_empty(client):
    login(client)
    resp = client.get("/spend")
    assert resp.status_code == 200
    assert b"No purchase history yet" in resp.data


def test_upload_and_dashboard(client, db):
    login(client)
    resp = _upload(client)
    assert b"Imported 4 purchase lines" in resp.data
    assert b"Beef Mince 500g" in resp.data
    assert b"$30.60" in resp.data
    for rng in ("3m", "6m", "12m", "all", "bogus"):
        assert client.get(f"/spend?range={rng}").status_code == 200


def test_link_product_updates_ingredient_price(client, db):
    login(client)
    _upload(client)
    db.execute("INSERT INTO ingredients (id, name, category, unit, price) VALUES (1, 'Mince', 'meat', 'kg', 10)")
    db.commit()
    client.post("/spend/products/link", data={"product_name": "Beef Mince 500g", "ingredient_id": "1", "update_price": "1"})
    assert db.execute("SELECT price FROM ingredients WHERE id = 1").fetchone()[0] == pytest.approx(17.0)
    assert db.execute("SELECT COUNT(*) FROM purchases WHERE ingredient_id = 1").fetchone()[0] == 2
    # Category spend now shows the linked category
    assert b"Meat &amp; Seafood" in client.get("/spend?range=all").data


def test_link_product_create_new_ingredient(client, db):
    login(client)
    _upload(client)
    client.post("/spend/products/link", data={"product_name": "Milk 2L", "ingredient_id": "new"})
    ing = db.execute("SELECT * FROM ingredients WHERE name = 'Milk 2L'").fetchone()
    assert ing["unit"] == "L"
    assert ing["price"] == pytest.approx(1.55)
    assert ing["url"] == "https://www.woolworths.com.au/shop/productdetails/3"


def test_products_page_filters(client, db):
    login(client)
    _upload(client)
    resp = client.get("/spend/products?q=milk")
    assert b"Milk 2L" in resp.data and b"Bananas" not in resp.data


def test_dashboard_and_shopping_list_show_spend(client, db):
    login(client)
    _upload(client)
    assert b"Recent spend" in client.get("/").data
    assert b"Your average shop" in client.get("/shopping-list").data


def test_cli_import(app, tmp_path):
    csv_path = tmp_path / "orders.csv"
    csv_path.write_text(WOOLIES_CSV, encoding="utf-8")
    result = app.test_cli_runner().invoke(args=["import-purchases", str(csv_path)])
    assert "Imported 4 lines" in result.output
