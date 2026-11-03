"""Spending limits per pay, set from the savings suggestions."""

import datetime as dt

from conftest import login
from services.bank_spending import limit_for_lever, limits_progress, merchant_key_for
from services.savings_advice import make_txn
from test_savings_levers import _day, _rows


def _pay_rows():
  return _rows() + [make_txn(_day(n), 3000, "ACME PAYROLL", "credit", merchant_key="acme payroll")
                    for n in range(0, 84, 14)]


def test_limit_for_lever_rounds_down_to_five():
  assert limit_for_lever({"per_pay": 180.0, "cut": 35.0}) == 145
  assert limit_for_lever({"per_pay": 59.58, "cut": 10.0}) == 45
  assert limit_for_lever({"per_pay": 3.0, "cut": 10.0}) == 0


def test_progress_counts_only_since_payday():
  rows = _rows() + [make_txn(_day(0), 50, "KFC FRANKSTON")]
  progress = limits_progress(rows, {"eating_out": 100.0, "shopping": 200.0}, _day(1))
  by_code = {i["code"]: i for i in progress["items"]}
  assert by_code["eating_out"]["spent"] == 230
  assert by_code["eating_out"]["over"] and by_code["eating_out"]["left"] == -130
  assert by_code["shopping"]["spent"] == 0 and by_code["shopping"]["percent"] == 0
  assert [i["code"] for i in progress["over"]] == ["eating_out"]
  assert progress["items"][0]["code"] == "eating_out"


def test_progress_respects_merchant_choices():
  key = merchant_key_for({"description": "KMART 1044"})
  progress = limits_progress(_rows(), {"kids": 50.0}, _day(14), {key: "kids"})
  assert progress["items"][0]["spent"] == 90


def test_no_limits_or_bad_date_gives_none():
  assert limits_progress(_rows(), {}, _day(1)) is None
  assert limits_progress(_rows(), {"fuel": 10.0}, "not a date") is None


def _fixed_today(monkeypatch):
  import services.suggestions as suggestions

  class Fixed(dt.date):
    @classmethod
    def today(cls):
      return cls(2026, 11, 2)

  monkeypatch.setattr(suggestions, "date", Fixed)


def test_set_change_and_remove_a_limit(client, db, monkeypatch):
  from test_regular_payments import _store
  _fixed_today(monkeypatch)
  login(client)
  _store(db, _pay_rows())
  page = client.get("/subscriptions/fortnight").get_data(as_text=True)
  assert "Set a limit of $145.00 a pay" in page
  resp = client.post("/subscriptions/fortnight/limit", data={"category": "eating_out", "per_pay": "145.00"},
                     follow_redirects=True)
  page = resp.get_data(as_text=True)
  assert "Eating out is limited to $145.00 a pay" in page
  assert 'id="spending-limits"' in page and "$120.00 of $145.00" in page and "limit set" in page
  client.post("/subscriptions/fortnight/limit", data={"category": "eating_out", "per_pay": "100"})
  assert db.execute("SELECT per_pay FROM spending_limits").fetchone()[0] == 100
  dash = client.get("/").get_data(as_text=True)
  assert "Over your limit: Eating out" in dash
  client.post("/subscriptions/fortnight/limit", data={"category": "eating_out", "per_pay": "100", "remove": "1"})
  assert db.execute("SELECT COUNT(*) FROM spending_limits").fetchone()[0] == 0


def test_bad_limits_are_rejected(client, db):
  login(client)
  client.post("/subscriptions/fortnight/limit", data={"category": "bogus", "per_pay": "50"})
  client.post("/subscriptions/fortnight/limit", data={"category": "fuel", "per_pay": "-5"})
  client.post("/subscriptions/fortnight/limit", data={"category": "fuel", "per_pay": "abc"})
  assert db.execute("SELECT COUNT(*) FROM spending_limits").fetchone()[0] == 0


def test_limit_route_is_post_only_and_needs_login(client):
  assert client.get("/subscriptions/fortnight/limit").status_code == 405
  assert client.post("/subscriptions/fortnight/limit", data={"category": "fuel", "per_pay": "5"}).status_code in (302, 401)
