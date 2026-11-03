"""Where money could be freed up each pay."""

from datetime import date, timedelta

from services.bank_spending import merchant_key_for, savings_levers
from services.savings_advice import make_txn

END = date(2026, 10, 30)


def _day(n):
  return (END - timedelta(days=n)).isoformat()


def _rows():
  rows = [make_txn(_day(83), 1, "BANK FEE")]
  for n in range(0, 84, 14):
    rows += [
      make_txn(_day(n), 120, "MCDONALDS CARRUM DOWNS"),
      make_txn(_day(n + 1), 60, "UBER EATS SYDNEY"),
      make_txn(_day(n + 2), 90, "KMART 1044"),
      make_txn(_day(n + 3), 300, "WOOLWORTHS 1234", status="grocery"),
      make_txn(_day(n + 4), 400, "AGL ENERGY BILL"),
      make_txn(_day(n + 5), 800, "Transfer to Kelly Pearson NetBank"),
      make_txn(_day(n + 6), 10, "NETFLIX.COM"),
    ]
  return rows


def test_levers_are_flexible_categories_biggest_first():
  result = savings_levers(_rows(), 14)
  codes = [l["code"] for l in result["levers"]]
  assert codes == ["eating_out", "shopping"]
  eating = result["levers"][0]
  assert eating["per_pay"] == 180
  assert eating["cut"] == 35
  assert [m["name"].split()[0] for m in eating["merchants"]] == ["Mcdonalds", "Uber"]
  assert result["freed"] == 35 + 15
  assert result["covers_gap"] is None


def test_small_categories_are_left_out():
  codes = [l["code"] for l in savings_levers(_rows(), 14)["levers"]]
  assert "streaming" not in codes


def test_gap_coverage():
  assert savings_levers(_rows(), 14, gap=40)["covers_gap"] is True
  assert savings_levers(_rows(), 14, gap=104.46)["covers_gap"] is False


def test_user_choice_moves_a_merchant_out_of_levers():
  key = merchant_key_for({"description": "KMART 1044"})
  codes = [l["code"] for l in savings_levers(_rows(), 14, {key: "kids"})["levers"]]
  assert codes == ["eating_out"]


def test_none_without_flexible_spending():
  rows = [make_txn(_day(1), 400, "AGL ENERGY BILL"), make_txn(_day(40), 400, "AGL ENERGY BILL")]
  assert savings_levers(rows, 14) is None
  assert savings_levers([], 14) is None


def test_fortnight_page_shows_levers(client, db):
  from conftest import login
  from test_regular_payments import _store
  login(client)
  rows = _rows() + [make_txn(_day(n), 3000, "ACME PAYROLL", "credit", merchant_key="acme payroll")
                    for n in range(0, 84, 14)]
  _store(db, rows)
  html = client.get("/subscriptions/fortnight").get_data(as_text=True)
  assert 'id="savings-levers"' in html
  assert "Where you could free up money" in html
  assert "Eating out" in html and "cut $35.00" in html
