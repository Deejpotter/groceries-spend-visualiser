"""Left to spend before the next payday."""

from datetime import date

from services.left_to_spend import bills_due, left_to_spend, next_payday, spent_since
from services.savings_advice import make_txn

PAY = {"merchant_key": "acme", "frequency": "fortnightly", "last_payday": "2026-10-22", "amount": 3000}
ADVICE = {"income": 3000.0, "savings": 200.0, "long_term": 200.0}


def _rows():
  return [
    make_txn("2026-10-20", 999, "BEFORE PAYDAY"),
    make_txn("2026-10-22", 3000, "ACME PAYROLL", "credit", merchant_key="acme"),
    make_txn("2026-10-23", 150, "WOOLWORTHS 1234", status="grocery"),
    make_txn("2026-10-24", 500, "Transfer to xx9910 CommBank app"),
    make_txn("2026-10-25", 50, "MYSTERY", direction="unknown"),
    make_txn("2026-10-26", 800, "Transfer to Kelly Pearson NetBank"),
  ]


def test_next_payday():
  assert next_payday(date(2026, 10, 22), "fortnightly") == date(2026, 11, 5)
  assert next_payday(date(2026, 10, 22), "weekly") == date(2026, 10, 29)
  assert next_payday(date(2026, 1, 31), "monthly") == date(2026, 2, 28)
  assert next_payday(date(2026, 12, 15), "monthly") == date(2027, 1, 15)


def test_spent_since_skips_moves_credits_unknown_and_earlier_rows():
  assert spent_since(_rows(), date(2026, 10, 22), "acme") == 950


def test_bills_due_before_next_payday_only():
  bills = [
    {"name": "Power", "amount": 120, "frequency": "monthly", "next_due": "2026-11-01", "active": 1},
    {"name": "Rent", "amount": 900, "frequency": "monthly", "next_due": "2026-11-05", "active": 1},
    {"name": "Gym", "amount": 20, "frequency": "weekly", "next_due": "2026-10-20", "active": 1},
    {"name": "Old", "amount": 50, "frequency": "monthly", "next_due": "2026-11-02", "active": 0},
  ]
  due = bills_due(bills, date(2026, 10, 28), date(2026, 11, 5))
  assert [(b["name"], b["date"]) for b in due] == [("Power", "2026-11-01"), ("Gym", "2026-11-03")]


def test_left_to_spend_adds_up():
  bills = [{"name": "Power", "amount": 120, "frequency": "monthly", "next_due": "2026-11-01", "active": 1}]
  left = left_to_spend(PAY, ADVICE, _rows(), bills, date(2026, 10, 29))
  assert left["spent"] == 950
  assert left["bills_total"] == 120
  assert left["left"] == 3000 - 400 - 950 - 120
  assert left["days_left"] == 7
  assert left["per_day"] == round(1530 / 7, 2)
  assert left["next_payday"] == "2026-11-05"
  assert not left["over"]


def test_overspent_shows_negative_and_zero_per_day():
  rows = _rows() + [make_txn("2026-10-27", 2000, "HARVEY NORMAN")]
  left = left_to_spend(PAY, ADVICE, rows, [], date(2026, 10, 29))
  assert left["over"] and left["left"] < 0 and left["per_day"] == 0


def test_bill_already_in_bank_rows_is_not_counted_again():
  rows = _rows() + [make_txn("2026-11-01", 120, "AGL ENERGY BILL")]
  bills = [{"name": "Power", "amount": 120, "frequency": "monthly", "next_due": "2026-11-01", "active": 1}]
  left = left_to_spend(PAY, ADVICE, rows, bills, date(2026, 10, 29))
  assert left["bills_total"] == 0
  assert left["spent"] == 1070


def test_none_when_payday_is_stale_or_missing():
  assert left_to_spend(None, ADVICE, _rows(), [], date(2026, 10, 29)) is None
  assert left_to_spend(PAY, None, _rows(), [], date(2026, 10, 29)) is None
  assert left_to_spend(PAY, ADVICE, _rows(), [], date(2026, 11, 20)) is None


def test_dashboard_shows_left_to_spend(client, db, monkeypatch):
  import datetime as dt
  import services.suggestions as suggestions
  from conftest import login
  from test_regular_payments import _store
  from test_savings_advice import _history

  class Fixed(dt.date):
    @classmethod
    def today(cls):
      return cls(2026, 11, 2)

  monkeypatch.setattr(suggestions, "date", Fixed)
  login(client)
  _store(db, _history())
  html = client.get("/").get_data(as_text=True)
  assert 'id="left-to-spend"' in html
  assert "Left to spend" in html
  assert "$1,000.00" in html
  assert "$410.00" in html
  assert "a day for 11 days" in html and "Less spent since" in html


def test_dashboard_without_bank_data_has_no_left_tile(client):
  from conftest import login
  login(client)
  assert 'id="left-to-spend"' not in client.get("/").get_data(as_text=True)
