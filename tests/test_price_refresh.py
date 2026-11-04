"""Ingredient prices follow what was last paid for linked products."""

import base64
import io

import pytest

from conftest import login
from services.spend_import import (
    commit_import_batch, create_import_batch, import_purchases, ingredient_unit_price,
    price_change, price_refresh_messages, refresh_linked_prices,
)


@pytest.mark.parametrize("name,cup,unit_price,unit,pack,expected", [
    ("Beef Mince 500g", "$16.00 / 1KG", 8.0, "kg", None, 16.0),
    ("Pasta Sauce 500g", None, 3.3, "kg", None, 6.6),
    ("Sour Cream", None, 6.75, "kg", 0.3, 22.5),
    ("Free Range Eggs 12 Pack", None, 6.7, "each", None, 0.558333),
    ("Wraps", None, 5.6, "each", 8, 0.7),
    ("Bananas", "$0.50 / 1EA", 0.5, "each", None, 0.5),
    ("Lettuce", None, 2.5, "each", None, 2.5),
    ("Salami", None, 7.2, "kg", None, None),
    ("Wraps 8 pack", "$1.10 / 100G", 5.6, "each", None, 0.7),
    ("Chicken Thigh", "$19.49 / 1KG", 10.39, "kg", None, 19.49),
    ("Bread", None, None, "each", None, None),
    ("Woolworths beef chuck steak medium pdm 380g - 800g", None, 10.39, "kg", 0.5, None),
    ("Woolworths beef chuck steak medium pdm 380g - 800g", "$19.49 / 1KG", 10.39, "kg", None, 19.49),
])
def test_ingredient_unit_price(name, cup, unit_price, unit, pack, expected):
    result = ingredient_unit_price(name, cup, unit_price, unit, pack)
    assert result == (pytest.approx(expected, abs=1e-4) if expected is not None else None)


@pytest.mark.parametrize("old,new,expected", [
    (None, 2.0, "update"),
    (2.0, 2.5, "update"),
    (2.0, 2.001, None),
    (2.0, None, None),
    (0.56, 6.7, "hold"),
    (19.0, 5.0, "hold"),
])
def test_price_change(old, new, expected):
    assert price_change(old, new) == expected


def _ingredient(db, name, unit, price=None, pack_size=None):
    cur = db.execute("INSERT INTO ingredients (name, category, unit, price, pack_size) VALUES (?, 'other', ?, ?, ?)",
                     (name, unit, price, pack_size))
    return cur.lastrowid


def _purchase(db, date, basket, name, unit_price, ingredient_id=None, cup=None):
    db.execute("INSERT INTO purchases (order_date, basket_id, store, product_name, quantity, unit_price,"
               " line_total, cup_price, ingredient_id) VALUES (?, ?, 'Woolworths', ?, 1, ?, ?, ?, ?)",
               (date, basket, name, unit_price, unit_price, cup, ingredient_id))


def test_refresh_uses_latest_linked_purchase(db):
    eggs = _ingredient(db, "Eggs", "each", 0.5, 12)
    _purchase(db, "2026-09-01", "a", "Eggs 12 Pack", 6.0, eggs)
    _purchase(db, "2026-10-01", "b", "Eggs 12 Pack", 7.2, eggs)
    db.commit()
    result = refresh_linked_prices(db, ["Eggs 12 Pack"])
    assert result["updated"] == [{"name": "Eggs", "unit": "each", "old": 0.5, "new": 0.6}]
    assert db.execute("SELECT price FROM ingredients WHERE id = ?", (eggs,)).fetchone()[0] == 0.6
    assert refresh_linked_prices(db, ["Eggs 12 Pack"]) == {"updated": [], "held": []}


def test_refresh_holds_implausible_jumps_and_ignores_unlinked(db):
    bacon = _ingredient(db, "Bacon", "kg", 19.0, 0.25)
    _purchase(db, "2026-10-01", "a", "Bacon 250g", 47.5, bacon)
    _purchase(db, "2026-10-01", "a", "Chips", 4.0)
    db.commit()
    result = refresh_linked_prices(db, ["Bacon 250g", "Chips"])
    assert result["updated"] == []
    assert result["held"][0]["name"] == "Bacon"
    assert db.execute("SELECT price FROM ingredients WHERE id = ?", (bacon,)).fetchone()[0] == 19.0
    assert price_refresh_messages(result)[0][0] == "warning"


def test_refresh_messages_summarise_updates():
    items = [{"name": f"Item {i}", "unit": "each", "old": 1.0, "new": 1.5} for i in range(7)]
    (level, message), = price_refresh_messages({"updated": items, "held": []})
    assert level == "success"
    assert message.startswith("Updated 7 ingredient prices: Item 0 $1.00 → $1.50/each")
    assert message.endswith("and 2 more.")


def test_invoice_commit_keeps_cup_price_and_reprices(db):
    chicken = _ingredient(db, "Chicken Thigh", "kg", 18.0)
    _purchase(db, "2026-09-01", "old", "Woolworths chicken thigh fillets", 9.0, chicken, "$18.00 / 1KG")
    db.commit()
    meta = {"invoice_number": "900", "order_date": "2026-10-20", "subtotal": 10.39, "skipped": []}
    rows = [{"product_name": "Woolworths chicken thigh fillets", "quantity": 1, "unit_price": 10.39,
             "line_total": 10.39, "cup_price": "$19.49 / 1KG", "stockcode": "123", "channel": None}]
    assert commit_import_batch(db, create_import_batch(db, meta, rows)) == (1, 0)
    saved = db.execute("SELECT cup_price, stockcode FROM purchases WHERE basket_id = '900'").fetchone()
    assert tuple(saved) == ("$19.49 / 1KG", "123")
    refresh_linked_prices(db, ["Woolworths chicken thigh fillets"])
    assert db.execute("SELECT price FROM ingredients WHERE id = ?", (chicken,)).fetchone()[0] == 19.49


def test_csv_import_route_reprices_linked_ingredient(client, db):
    login(client)
    milk = _ingredient(db, "Milk", "L", 1.5)
    _purchase(db, "2026-09-01", "old", "Milk 2L", 3.0, milk)
    db.commit()
    csv = "date,basket_id,product_name,quantity,unit_price\n2026-10-01,new,Milk 2L,1,3.4\n"
    resp = client.post("/spend/import", data={"file": (io.BytesIO(csv.encode()), "a.csv")},
                       content_type="multipart/form-data", follow_redirects=True)
    assert "Updated 1 ingredient price: Milk $1.50 → $1.70/L.".encode() in resp.data
    assert db.execute("SELECT price FROM ingredients WHERE id = ?", (milk,)).fetchone()[0] == 1.7


def test_api_import_reports_price_changes(client, db):
    from database import set_setting
    set_setting("mcp_api_key", "price-test-key")
    milk = _ingredient(db, "Milk", "L", 1.5)
    _purchase(db, "2026-09-01", "old", "Milk 2L", 3.0, milk)
    db.commit()
    csv = "date,basket_id,product_name,quantity,unit_price\n2026-10-01,new,Milk 2L,1,3.4\n"
    resp = client.post("/api/v1/import", headers={"Authorization": "Bearer price-test-key"},
                       json={"files": [{"filename": "a.csv",
                                        "content_base64": base64.b64encode(csv.encode()).decode()}]})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["files"][0]["prices_updated"] == [
        {"name": "Milk", "unit": "L", "old": 1.5, "new": 1.7}]


def test_link_product_prices_multipack_per_item(db):
    from services.mutations import link_product
    eggs = _ingredient(db, "Eggs", "each", None, 12)
    _purchase(db, "2026-10-01", "a", "Free Range Eggs 12 Pack", 6.7)
    db.commit()
    assert link_product(db, "Free Range Eggs 12 Pack", str(eggs), True)[2] is True
    assert db.execute("SELECT price FROM ingredients WHERE id = ?", (eggs,)).fetchone()[0] == pytest.approx(0.558333)
