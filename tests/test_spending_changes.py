"""What changed in spending: the last 30 days against the usual month."""

from datetime import date, timedelta

from conftest import login
from services.bank_spending import spending_changes
from services.savings_advice import make_txn

END = date(2026, 10, 31)


def _day(days_ago):
  return (END - timedelta(days=days_ago)).isoformat()


def _steady(days_back=150):
  """$200 groceries and $100 power every 30 days, back to days_back."""
  rows = []
  for start in range(0, days_back, 30):
    rows.append(make_txn(_day(start + 5), 200, "WOOLWORTHS 1234", status="grocery"))
    rows.append(make_txn(_day(start + 10), 100, "AGL ENERGY BILL"))
  return rows


def test_steady_spending_has_no_changes():
  assert spending_changes(_steady()) is None


def test_too_little_history_gives_none():
  rows = [make_txn(_day(3), 500, "BUNNINGS 552"), make_txn(_day(20), 10, "BUNNINGS 552")]
  assert spending_changes(rows) is None


def test_category_that_went_up_names_the_merchant():
  rows = _steady() + [make_txn(_day(2), 300, "BUNNINGS 552"), make_txn(_day(4), 20, "KMART 22")]
  changes = spending_changes(rows)
  assert changes["down"] == []
  [item] = changes["up"]
  assert item["code"] == "other"
  assert item["recent"] == 320
  assert item["usual"] == 0
  assert item["percent"] is None
  assert item["driver"].lower().startswith("bunnings")


def test_category_that_went_down():
  rows = [r for r in _steady() if not (r["description"].startswith("WOOL") and r["txn_date"] == _day(5))]
  rows.append(make_txn(_day(0), 40, "WOOLWORTHS 1234", status="grocery"))
  changes = spending_changes(rows)
  [item] = changes["down"]
  assert item["code"] == "groceries"
  assert item["delta"] == -160
  assert item["percent"] == -80


def test_small_moves_are_ignored():
  rows = _steady() + [make_txn(_day(2), 40, "BUNNINGS 552")]
  assert spending_changes(rows) is None


def test_moves_between_own_accounts_are_ignored():
  rows = _steady() + [make_txn(_day(2), 900, "Transfer to xx9910 CommBank app")]
  assert spending_changes(rows) is None


def test_spend_page_shows_changes(client, app):
  login(client)
  from database import get_db
  with app.app_context():
    db = get_db()
    today = date.today()
    for days_ago, amount, desc, status in [
        (100, 200, "WOOLWORTHS 1234", "grocery"), (70, 200, "WOOLWORTHS 1234", "grocery"),
        (40, 200, "WOOLWORTHS 1234", "grocery"), (5, 200, "WOOLWORTHS 1234", "grocery"),
        (3, 400, "BUNNINGS 552", "other")]:
      db.execute(
        "INSERT INTO bank_transactions (source, external_id, txn_date, description, merchant_key, amount,"
        " direction, status) VALUES ('csv', ?, ?, ?, ?, ?, 'debit', ?)",
        (f"t{days_ago}", (today - timedelta(days=days_ago)).isoformat(), desc, desc.split()[0].lower(), amount, status))
    db.commit()
  page = client.get("/spend").get_data(as_text=True)
  assert "What changed" in page
  assert "Bunnings" in page
