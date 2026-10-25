"""Bank statement import: NetBank CSV parsing, classification, dedupe."""

import io

import pytest

from conftest import login
from services.bank_import import (
    classify_merchant, force_import_duplicate, import_bank_transactions,
    load_merchant_patterns, parse_netbank_csv, store_bank_transactions,
    to_purchases,
)

AMOUNT_LAYOUT = """Date,Description,Amount,Balance
01/10/2026,WOOLWORTHS MT WAVERLEY 3456,-82.40,1200.00
02/10/2026,COLES EXPRESS SOUTH YARRA,-23.10,1176.90
03/10/2026,MY OB PAYROLL,1500.00,2676.90
04/10/2026,UNKNOWN SHOPPE,-10.00,2666.90
05/10/2026,INTEREST PAID,0.00,2666.90
"""

DEBIT_CREDIT_LAYOUT = """Date,Description,Debit,Credit
2026-10-01,ALDI BELCONNEN,45.20,
2026-10-02,IGA PARKES,12.50,
"""

PATTERNS = [("woolworths", "Woolworths"), ("coles", "Coles"),
            ("aldi", "ALDI"), ("iga", "IGA")]


def test_parse_amount_layout():
    rows, errors = parse_netbank_csv(AMOUNT_LAYOUT)
    assert len(rows) == 4  # $0.00 line is not a purchase
    assert rows[0] == {"order_date": "2026-10-01",
                       "description": "WOOLWORTHS MT WAVERLEY 3456",
                       "amount": 82.40,
                       "direction": "debit"}
    assert rows[2]["amount"] == 1500.00  # money in comes back positive
    assert rows[2]["direction"] == "credit"
    assert errors == []


def test_parse_debit_credit_layout():
    rows, errors = parse_netbank_csv(DEBIT_CREDIT_LAYOUT)
    assert len(rows) == 2
    assert rows[0]["amount"] == 45.20
    assert rows[1]["order_date"] == "2026-10-02"
    assert errors == []


FROLLO_LAYOUT = """Date,Description,Amount,Balance,Category,Account Name
01/10/2026,WOOLWORTHS MT WAVERLEY 3456,-82.40,1200.00,Groceries,Everyday
02/10/2026,COLES EXPRESS,-23.10,1176.90,Groceries,Everyday
03/10/2026,NETFLIX.COM,-18.99,1157.91,Entertainment,Visa
"""


def test_parse_frollo_layout():
    rows, errors = parse_netbank_csv(FROLLO_LAYOUT)
    assert len(rows) == 3
    assert rows[0]["description"] == "WOOLWORTHS MT WAVERLEY 3456"
    assert rows[0]["amount"] == 82.40
    assert errors == []


def test_parse_rejects_missing_columns():
    rows, errors = parse_netbank_csv("Date,Amount\n2026-10-01,1.00\n")
    assert rows == []
    assert "Description" in errors[0]


def test_parse_skips_invalid_lines():
    text = "Date,Description,Amount\nnot-a-date,WOOLWORTHS,x\n2026-10-01,,5.00\n"
    rows, errors = parse_netbank_csv(text)
    assert rows == []
    assert len(errors) == 2


def test_classify_merchant():
    assert classify_merchant("WOOLWORTHS MT WAVERLEY 3456", PATTERNS) == "Woolworths"
    assert classify_merchant("coles online", PATTERNS) == "Coles"
    assert classify_merchant("UNKNOWN SHOPPE", PATTERNS) is None


def test_to_purchases_splits_unclassified():
    rows, _ = parse_netbank_csv(AMOUNT_LAYOUT)
    purchases, unclassified = to_purchases(rows, PATTERNS)
    assert len(purchases) == 2
    # The salary credit is money in, so it is neither a purchase nor classified.
    assert len(unclassified) == 1
    assert purchases[0]["store"] == "Woolworths"
    assert purchases[0]["product_name"] == "WOOLWORTHS MT WAVERLEY 3456"
    assert purchases[0]["line_total"] == 82.40
    assert purchases[0]["origin"] == "csv"


def test_basket_id_is_stable():
    rows, _ = parse_netbank_csv(AMOUNT_LAYOUT)
    first, _ = to_purchases(rows, PATTERNS)
    second, _ = to_purchases(rows, PATTERNS)
    assert [p["basket_id"] for p in first] == [p["basket_id"] for p in second]


def test_store_bank_transactions_keeps_every_row(db):
    rows, _ = parse_netbank_csv(AMOUNT_LAYOUT)
    added = store_bank_transactions(db, rows, "csv", PATTERNS)
    assert added == 4  # purchases, an unknown shop and the salary credit

    stored = {
        row["description"]: dict(row)
        for row in db.execute("SELECT * FROM bank_transactions").fetchall()
    }
    assert stored["MY OB PAYROLL"]["direction"] == "credit"
    assert stored["MY OB PAYROLL"]["merchant_key"] == "my"
    assert stored["WOOLWORTHS MT WAVERLEY 3456"]["status"] == "grocery"
    assert stored["UNKNOWN SHOPPE"]["status"] == "other"
    assert stored["UNKNOWN SHOPPE"]["merchant_key"] == "unknown"

    # Re-importing the same statement adds nothing.
    assert store_bank_transactions(db, rows, "csv", PATTERNS) == 0
    assert db.execute("SELECT COUNT(*) FROM bank_transactions").fetchone()[0] == 4


def test_import_then_dedupe(db):
    rows, _ = parse_netbank_csv(AMOUNT_LAYOUT)
    purchases, _ = to_purchases(rows, PATTERNS)
    added, duplicates = import_bank_transactions(db, purchases)
    assert added == 2 and duplicates == []
    added, duplicates = import_bank_transactions(db, purchases)
    assert added == 0 and len(duplicates) == 2  # re-import is fully idempotent


def test_dedupe_is_cross_source(db):
    rows, _ = parse_netbank_csv(AMOUNT_LAYOUT)
    purchases, _ = to_purchases(rows, PATTERNS, source="csv")
    import_bank_transactions(db, purchases)
    bank_rows, _ = parse_netbank_csv(AMOUNT_LAYOUT)
    bank_purchases, _ = to_purchases(bank_rows, PATTERNS, source="banksync")
    assert bank_purchases[0]["basket_id"] != purchases[0]["basket_id"]
    added, duplicates = import_bank_transactions(db, bank_purchases)
    assert added == 0 and len(duplicates) == 2  # same shop/day/total, any source


def test_force_import_duplicate(db):
    rows, _ = parse_netbank_csv(AMOUNT_LAYOUT)
    purchases, _ = to_purchases(rows, PATTERNS)
    import_bank_transactions(db, purchases)
    dup = purchases[0]
    assert force_import_duplicate(db, dup["order_date"], dup["product_name"],
                                  dup["line_total"])
    ids = [r["basket_id"] for r in db.execute(
        "SELECT basket_id FROM purchases WHERE product_name = ?",
        (dup["product_name"],)).fetchall()]
    assert len(ids) == 2 and ids[0] != ids[1]
    force_import_duplicate(db, dup["order_date"], dup["product_name"],
                           dup["line_total"])
    ids = [r["basket_id"] for r in db.execute(
        "SELECT basket_id FROM purchases WHERE product_name = ?",
        (dup["product_name"],)).fetchall()]
    assert len(ids) == 3 and len(set(ids)) == 3  # suffixed, never collides


def test_merchant_patterns_from_settings(db):
    assert load_merchant_patterns(db) == [
        ("woolworths", "Woolworths"), ("coles", "Coles"),
        ("aldi", "ALDI"), ("iga", "IGA"), ("foodland", "Foodland"),
        ("drakes", "Drakes"), ("foodworks", "Foodworks"),
        ("spar", "Spar"), ("costco", "Costco"),
    ]
    db.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        ("bank_merchant_patterns", "metcash=Metcash\n# comment\nsupa iga"),
    )
    db.commit()
    assert load_merchant_patterns(db) == [
        ("metcash", "Metcash"), ("supa iga", "Supa Iga"),
    ]


def test_import_bank_csv_route(client, db):
    login(client)
    resp = client.post("/spend/import-bank", data={
        "bank_file": (io.BytesIO(AMOUNT_LAYOUT.encode()), "statement.csv"),
    }, content_type="multipart/form-data")
    assert resp.status_code == 200
    assert db.execute("SELECT COUNT(*) FROM purchases").fetchone()[0] == 2
    # Every row is kept for recurring-payment detection, not just purchases.
    assert db.execute("SELECT COUNT(*) FROM bank_transactions").fetchone()[0] == 4
    # re-import: everything lands on the duplicates review page
    resp = client.post("/spend/import-bank", data={
        "bank_file": (io.BytesIO(AMOUNT_LAYOUT.encode()), "statement.csv"),
    }, content_type="multipart/form-data")
    assert b"Possible duplicates" in resp.data
    assert db.execute("SELECT COUNT(*) FROM purchases").fetchone()[0] == 2


def test_import_bank_duplicates_route(client, db):
    login(client)
    client.post("/spend/import-bank", data={
        "bank_file": (io.BytesIO(AMOUNT_LAYOUT.encode()), "statement.csv"),
    }, content_type="multipart/form-data")
    resp = client.post("/spend/import-bank", data={
        "bank_file": (io.BytesIO(AMOUNT_LAYOUT.encode()), "statement.csv"),
    }, content_type="multipart/form-data")
    assert resp.status_code == 200
    # force-import the first duplicate (WOOLWORTHS row)
    resp = client.post("/spend/import-bank/duplicates", data={
        "keep": ["0"],
        "d_date": ["2026-10-01", "2026-10-02"],
        "d_description": ["WOOLWORTHS MT WAVERLEY 3456",
                          "COLES EXPRESS SOUTH YARRA"],
        "d_amount": ["82.40", "23.10"],
        "d_source": ["csv", "csv"],
    })
    assert resp.status_code == 302
    assert db.execute("SELECT COUNT(*) FROM purchases").fetchone()[0] == 3


def test_import_bank_csv_route_rejects_non_csv(client, db):
    login(client)
    resp = client.post("/spend/import-bank", data={
        "bank_file": (io.BytesIO(b"\x00\x01\x02"), "statement.pdf"),
    }, content_type="multipart/form-data")
    assert resp.status_code == 302  # redirected with an error flash
    assert db.execute("SELECT COUNT(*) FROM purchases").fetchone()[0] == 0


def test_import_bank_csv_route_bad_headers(client, db):
    login(client)
    resp = client.post("/spend/import-bank", data={
        "bank_file": (io.BytesIO(b"Date,Amount\n2026-10-01,1.00\n"),
                      "statement.csv"),
    }, content_type="multipart/form-data")
    assert resp.status_code == 302  # missing Description column -> error flash
