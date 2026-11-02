"""Regular transfers to people are bills, and spending categories drill down."""

from datetime import date, timedelta

from conftest import login
from services.bank_spending import bank_spending, category_detail
from services.recurring import detect_recurring, guess_category, normalise_merchant, transfer_payee
from services.savings_advice import make_txn

KELLY = "Transfer To Kelly Pearson, CommBank App {note}"
NANA = "Transfer To Nana, CommBank App car loan"


def _rows():
    """Fortnightly $800 to Kelly and $200 to Nana, mixed with other people's transfers."""
    rows = []
    start = date(2026, 5, 1)
    notes = ["Love you", "rent", "xx", "bills", "Love you", "groceries", "hi", "rent", "Love you", "x", "car"]
    for n in range(11):
        day = start + timedelta(days=14 * n)
        rows.append(make_txn(day.isoformat(), 800, KELLY.format(note=notes[n])))
        rows.append(make_txn((day + timedelta(days=2)).isoformat(), 200, NANA))
    for day, amount, who in [("2026-05-09", 780, "J Smith"), ("2026-06-20", 820, "Bob Lee"),
                             ("2026-07-03", 800, "Mia Chen"), ("2026-08-11", 790, "Ann Ray"),
                             ("2026-09-02", 810, "Tom Fox")]:
        rows.append(make_txn(day, amount, f"Transfer To {who}, CommBank App bbq"))
    return rows


def test_transfer_payee():
    assert transfer_payee(KELLY.format(note="Love you")) == "Kelly Pearson"
    assert transfer_payee(NANA) == "Nana"
    assert transfer_payee("Transfer to xx9910 CommBank app") is None
    assert transfer_payee("Transfer to savings") is None
    assert transfer_payee("WOOLWORTHS 1234") is None


def test_transfers_are_keyed_by_payee():
    assert normalise_merchant(KELLY.format(note="a")) == normalise_merchant(KELLY.format(note="b")) == "to kelly pearson"
    assert normalise_merchant(NANA) == "to nana"
    assert normalise_merchant("Transfer to xx9910 CommBank app") == "transfer"


def test_categories_for_transfers():
    assert guess_category(KELLY.format(note="Love you")) == "transfers"
    assert guess_category(NANA) == "loans"


def test_regular_transfers_to_people_are_detected():
    found = {s["name"]: s for s in detect_recurring(_rows())}
    assert "Transfer to Kelly Pearson" in found
    assert found["Transfer to Kelly Pearson"]["frequency"] == "fortnightly"
    assert found["Transfer to Kelly Pearson"]["amount"] == 800
    assert "Transfer to Nana" in found
    assert found["Transfer to Nana"]["amount"] == 200
    assert not any("J Smith" in name for name in found)


def test_category_detail_groups_by_payee():
    detail = category_detail(_rows(), "transfers")
    names = [m["name"] for m in detail["merchants"]]
    assert names[0] == "Transfer to Kelly Pearson"
    kelly = detail["merchants"][0]
    assert kelly["amount"] == 8800 and kelly["count"] == 11
    assert kelly["rows"][0]["date"] > kelly["rows"][-1]["date"]
    assert detail["label"] == "Transfers to people"
    loans = category_detail(_rows(), "loans")
    assert loans["merchants"][0]["name"] == "Transfer to Nana"
    assert category_detail(_rows(), "streaming") is None


def test_bank_spending_has_transfers_category():
    codes = {c["code"] for c in bank_spending(_rows())["categories"]}
    assert {"transfers", "loans"} <= codes


def _store(db, rows):
    for n, row in enumerate(rows):
        db.execute(
            "INSERT INTO bank_transactions (source, external_id, txn_date, description, merchant_key,"
            " amount, direction, status) VALUES ('csv', ?, ?, ?, ?, ?, ?, ?)",
            (str(n), row["txn_date"], row["description"], row["merchant_key"], row["amount"],
             row["direction"], row["status"]))
    db.commit()


def test_category_page_and_links(client, db):
    login(client)
    _store(db, _rows())
    html = client.get("/spend?range=all").get_data(as_text=True)
    assert "/spend/category/transfers?range=all" in html
    page = client.get("/spend/category/transfers?range=all").get_data(as_text=True)
    assert "Transfers to people" in page
    assert "Transfer to Kelly Pearson" in page
    assert "Transfer To Kelly Pearson, CommBank App Love you" in page
    assert "$8,800.00" in page


def test_category_page_empty(client):
    login(client)
    page = client.get("/spend/category/streaming").get_data(as_text=True)
    assert "No bank transactions in this category" in page


def test_suggestions_page_shows_transfers(client, db):
    login(client)
    _store(db, _rows())
    page = client.get("/subscriptions/suggest").get_data(as_text=True)
    assert "Transfer to Kelly Pearson" in page
    assert "Transfer to Nana" in page
