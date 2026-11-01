"""All spending from bank history on the Spend page."""

from conftest import login
from services.bank_spending import bank_spending, category_for
from services.savings_advice import make_txn


def _rows():
    return [
        make_txn("2026-09-02", 3000, "ACME PAYROLL", "credit"),
        make_txn("2026-09-03", 120, "WOOLWORTHS 1234", status="grocery"),
        make_txn("2026-09-05", 80, "AGL ENERGY BILL"),
        make_txn("2026-09-10", 500, "Transfer to xx9910 CommBank app"),
        make_txn("2026-10-03", 150, "WOOLWORTHS 1234", status="grocery"),
        make_txn("2026-10-06", 22.99, "NETFLIX.COM"),
        make_txn("2026-10-08", 60, "BUNNINGS 552"),
        make_txn("2026-10-09", 40, "MYSTERY THING", direction="unknown"),
    ]


def test_category_for():
    assert category_for({"status": "grocery", "description": "WOOLWORTHS"}) == "groceries"
    assert category_for({"description": "AGL ENERGY BILL"}) == "utilities"
    assert category_for({"description": "BUNNINGS 552"}) == "other"


def test_bank_spending_totals_and_groups():
    s = bank_spending(_rows())
    assert s["total"] == 432.99
    assert s["groceries"] == 270
    assert s["groceries_share"] == 62
    assert s["moved"] == 500
    assert s["unsure"] == 40
    codes = [c["code"] for c in s["categories"]]
    assert codes[0] == "groceries"
    assert set(codes) == {"groceries", "utilities", "streaming", "other"}
    assert [m["month"] for m in s["months"]] == ["2026-09", "2026-10"]
    assert s["months"][1] == {"month": "2026-10", "groceries": 150, "other": 82.99, "total": 232.99}
    assert s["top_merchants"][0]["amount"] == 270
    assert s["start"] == "2026-09-03" and s["end"] == "2026-10-09"


def test_bank_spending_window():
    s = bank_spending(_rows(), days=10)
    assert s["total"] == 232.99
    assert s["moved"] == 0


def test_bank_spending_empty():
    assert bank_spending([]) is None
    assert bank_spending([make_txn("2026-09-02", 3000, "ACME PAYROLL", "credit")]) is None


def test_spend_page_shows_all_spending(client, db):
    login(client)
    for n, row in enumerate(_rows()):
        db.execute(
            "INSERT INTO bank_transactions (source, external_id, txn_date, description, merchant_key,"
            " amount, direction, status) VALUES ('csv', ?, ?, ?, ?, ?, ?, ?)",
            (str(n), row["txn_date"], row["description"], row["merchant_key"], row["amount"],
             row["direction"], row["status"]))
    db.commit()
    html = client.get("/spend?range=all").get_data(as_text=True)
    assert "All spending" in html
    assert "$432.99" in html
    assert "Energy &amp; Water" in html


def test_spend_page_without_bank_rows(client):
    login(client)
    html = client.get("/spend").get_data(as_text=True)
    assert "All spending" not in html


def test_aldimobile_is_not_groceries():
    from services.bank_import import DEFAULT_MERCHANT_PATTERNS, classify_merchant
    assert classify_merchant("ALDIMOBILE CHATSWOOD AU", DEFAULT_MERCHANT_PATTERNS) is None
    assert classify_merchant("ALDI MOBILE RECHARGE", DEFAULT_MERCHANT_PATTERNS) is None
    assert classify_merchant("ALDI STORES CARRUM", DEFAULT_MERCHANT_PATTERNS) == "ALDI"


def test_ste_supermarket_is_groceries():
    from services.bank_import import DEFAULT_MERCHANT_PATTERNS, classify_merchant
    assert classify_merchant("STE SUPERMARKET DANDENONG", DEFAULT_MERCHANT_PATTERNS) == "STE Supermarket"


def test_restamp_grocery_follows_current_patterns():
    from services.bank_import import DEFAULT_MERCHANT_PATTERNS, restamp_grocery
    rows = [make_txn("2026-10-01", 30, "ALDIMOBILE AU", status="grocery"),
            make_txn("2026-10-02", 40, "STE SUPERMARKET DANDENONG"),
            make_txn("2026-10-03", 9, "NETFLIX.COM", status="other")]
    assert [r["status"] for r in restamp_grocery(rows, DEFAULT_MERCHANT_PATTERNS)] == ["other", "grocery", "other"]
    assert rows[0]["status"] == "grocery"


def test_spend_page_uses_current_patterns(client, db):
    login(client)
    rows = [make_txn("2026-10-01", 30, "ALDIMOBILE AU", status="grocery"),
            make_txn("2026-10-02", 40, "STE SUPERMARKET DANDENONG")]
    for n, row in enumerate(rows):
        db.execute(
            "INSERT INTO bank_transactions (source, external_id, txn_date, description, merchant_key,"
            " amount, direction, status) VALUES ('csv', ?, ?, ?, ?, ?, ?, ?)",
            (str(n), row["txn_date"], row["description"], row["merchant_key"], row["amount"],
             row["direction"], row["status"]))
    db.commit()
    html = client.get("/spend?range=all").get_data(as_text=True)
    assert "$40.00 of all spending" in html
