"""Choosing a spending category for a merchant, and the everyday categories."""

from datetime import date, timedelta

from conftest import login
from services.bank_spending import (
  bank_spending, category_choices, category_detail, category_for, guess_spend_category,
  merchant_key_for, spending_changes,
)
from services.savings_advice import make_txn


def test_everyday_guesses():
  assert guess_spend_category("MCDONALDS CARRUM DOWNS") == "eating_out"
  assert guess_spend_category("UBER EATS SYDNEY") == "eating_out"
  assert guess_spend_category("AMPOL FOODARY 123") == "fuel"
  assert guess_spend_category("7-ELEVEN 2201") == "fuel"
  assert guess_spend_category("KMART 1044") == "shopping"
  assert guess_spend_category("CANVIEW OPERATIONS") is None
  assert guess_spend_category("BPAY COUNCIL") is None


def test_everyday_guess_only_when_nothing_else_matched():
  assert category_for({"description": "BUNNINGS 552"}) == "shopping"
  assert category_for({"description": "SHELL ENERGY BILL"}) == "utilities"
  assert category_for({"description": "WOOLWORTHS 1234", "status": "grocery"}) == "groceries"


def test_choice_beats_every_guess():
  row = {"description": "WOOLWORTHS 1234", "status": "grocery"}
  assert category_for(row, {merchant_key_for(row): "shopping"}) == "shopping"


def test_choices_list_has_groceries_everyday_and_bills():
  codes = [c for c, _ in category_choices()]
  assert codes[0] == "groceries"
  assert {"eating_out", "fuel", "shopping", "utilities", "transfers"} <= set(codes)
  assert codes[-1] == "other"
  assert len(codes) == len(set(codes))


def _rows():
  return [
    make_txn("2026-10-01", 50, "CANVIEW OPERATIONS VARSITY LAKES"),
    make_txn("2026-10-08", 70, "CANVIEW OPERATIONS VARSITY LAKES"),
    make_txn("2026-10-09", 20, "MYSTERY SHOP"),
  ]


def test_choice_moves_spending_between_categories():
  rows = _rows()
  key = merchant_key_for(rows[0])
  before = {c["code"]: c["amount"] for c in bank_spending(rows)["categories"]}
  assert before == {"other": 140}
  after = {c["code"]: c["amount"] for c in bank_spending(rows, overrides={key: "eating_out"})["categories"]}
  assert after == {"eating_out": 120, "other": 20}


def test_category_detail_marks_chosen_merchants():
  rows = _rows()
  key = merchant_key_for(rows[0])
  detail = category_detail(rows, "eating_out", overrides={key: "eating_out"})
  [merchant] = detail["merchants"]
  assert merchant["key"] == key and merchant["chosen"]
  assert category_detail(rows, "other", overrides={key: "eating_out"})["merchants"][0]["chosen"] is False


def test_spending_changes_uses_choices():
  end = date(2026, 10, 31)
  rows = [make_txn((end - timedelta(days=d)).isoformat(), 100, "AGL ENERGY BILL") for d in (10, 40, 70, 100, 130)]
  rows.append(make_txn(end.isoformat(), 300, "CANVIEW OPERATIONS"))
  key = merchant_key_for(rows[-1])
  [item] = spending_changes(rows, overrides={key: "eating_out"})["up"]
  assert item["code"] == "eating_out"


def _add_bank_row(app, description, amount=40.0):
  from database import get_db
  with app.app_context():
    db = get_db()
    db.execute(
      "INSERT INTO bank_transactions (source, external_id, txn_date, description, merchant_key, amount,"
      " direction, status) VALUES ('csv', ?, ?, ?, 'canview', ?, 'debit', 'other')",
      (description, date.today().isoformat(), description, amount))
    db.commit()


def test_route_saves_and_clears_a_choice(client, app):
  login(client)
  _add_bank_row(app, "CANVIEW OPERATIONS VARSITY LAKES")
  page = client.get("/spend/category/other").get_data(as_text=True)
  assert 'name="category"' in page and "Eating out" in page
  key = merchant_key_for({"description": "CANVIEW OPERATIONS VARSITY LAKES"})
  resp = client.post("/spend/merchant-category", data={
    "merchant_key": key, "category": "eating_out", "name": "Canview", "back": "other", "range": "3m"},
    follow_redirects=True)
  body = resp.get_data(as_text=True)
  assert "now counts as Eating out" in body
  assert "Canview" in client.get("/spend/category/eating_out").get_data(as_text=True)
  assert "you chose this" in client.get("/spend/category/eating_out").get_data(as_text=True)
  client.post("/spend/merchant-category", data={
    "merchant_key": key, "category": "auto", "name": "Canview", "back": "eating_out"})
  assert "No bank transactions" in client.get("/spend/category/eating_out").get_data(as_text=True)


def test_route_rejects_unknown_category(client, app):
  login(client)
  resp = client.post("/spend/merchant-category", data={"merchant_key": "x", "category": "bogus"},
                     follow_redirects=True)
  assert "Choose a category" in resp.get_data(as_text=True)
  from database import get_db
  with app.app_context():
    assert get_db().execute("SELECT COUNT(*) FROM merchant_categories").fetchone()[0] == 0


def test_route_needs_login(client):
  resp = client.post("/spend/merchant-category", data={"merchant_key": "x", "category": "fuel"})
  assert resp.status_code in (302, 401)
