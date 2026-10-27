"""Tests for services/pay_cycle.py."""

from datetime import date

from services.pay_cycle import detect_payday, pay_cycle, paydays_around


class TestPaydays:
    def test_fortnightly(self):
        last, nxt = paydays_around(date(2026, 10, 1), "fortnightly", date(2026, 10, 27))
        assert (last.isoformat(), nxt.isoformat()) == ("2026-10-15", "2026-10-29")

    def test_on_payday_itself(self):
        last, nxt = paydays_around(date(2026, 10, 1), "weekly", date(2026, 10, 29))
        assert (last.isoformat(), nxt.isoformat()) == ("2026-10-29", "2026-11-05")

    def test_anchor_in_the_future_still_works(self):
        last, nxt = paydays_around(date(2026, 12, 3), "fortnightly", date(2026, 10, 27))
        assert last <= date(2026, 10, 27) < nxt
        assert (nxt - last).days == 14

    def test_monthly_clamps_to_month_end(self):
        last, nxt = paydays_around(date(2026, 1, 31), "monthly", date(2026, 2, 15))
        assert (last.isoformat(), nxt.isoformat()) == ("2026-01-31", "2026-02-28")

    def test_monthly_before_payday_in_month(self):
        last, nxt = paydays_around(date(2026, 1, 20), "monthly", date(2026, 10, 10))
        assert (last.isoformat(), nxt.isoformat()) == ("2026-09-20", "2026-10-20")


class TestPayCycle:
    def test_range_until_the_day_before_payday(self):
        cycle = pay_cycle("2026-10-01", "fortnightly", date(2026, 10, 27))
        assert cycle["cycle_end"] == "2026-10-28"
        assert cycle["until_payday_start"] == "2026-10-27"
        assert cycle["days_left"] == 2

    def test_unset_or_invalid_returns_none(self):
        assert pay_cycle("", "fortnightly", date(2026, 10, 27)) is None
        assert pay_cycle("not a date", "fortnightly", date(2026, 10, 27)) is None
        assert pay_cycle("2026-10-01", "daily", date(2026, 10, 27)) is None


def _credit(day, amount, key="acme", description="ACME PTY LTD SALARY"):
    return {"txn_date": day, "amount": amount, "merchant_key": key, "description": description,
            "direction": "credit"}


class TestDetectPayday:
    def test_fortnightly_salary(self):
        rows = [_credit(d, 2400) for d in ("2026-09-03", "2026-09-17", "2026-10-01", "2026-10-15")]
        found = detect_payday(rows)
        assert found["frequency"] == "fortnightly"
        assert found["last_payday"] == "2026-10-15"
        assert found["amount"] == 2400

    def test_small_and_debit_rows_ignored(self):
        rows = [_credit(d, 50) for d in ("2026-09-03", "2026-09-17", "2026-10-01")]
        rows += [{**_credit("2026-10-02", 3000), "direction": "debit"}]
        assert detect_payday(rows) is None

    def test_salary_wording_beats_a_bigger_transfer(self):
        rows = [_credit(d, 2000) for d in ("2026-09-03", "2026-09-17", "2026-10-01")]
        rows += [_credit(d, 5000, key="savings", description="TRANSFER FROM SAVINGS")
                 for d in ("2026-09-05", "2026-09-19", "2026-10-03")]
        assert detect_payday(rows)["merchant_key"] == "acme"
