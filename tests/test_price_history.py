"""Tests for subscription price history."""

from services.price_history import (
    group_price_history,
    payment_rows,
    summarise,
)


def txn(date, description, amount, direction="debit"):
    return {
        "txn_date": date,
        "description": description,
        "amount": amount,
        "direction": direction,
        "merchant_key": description.lower(),
    }


class TestGroupPriceHistory:
    def test_groups_sorts_and_skips_credits(self):
        rows = [
            txn("2026-10-01", "binge", 18.00),
            txn("2026-08-01", "binge", 16.00),
            txn("2026-09-01", "binge", 16.00),
            txn("2026-09-01", "binge", 16.00, direction="credit"),
            txn("2026-09-01", "other", 5.00),
        ]
        groups = group_price_history(rows)
        assert [p["amount"] for p in groups["binge"]] == [16.00, 16.00, 18.00]
        assert groups["binge"][0]["date"] == "2026-08-01"

    def test_ignores_rows_without_a_key_or_date(self):
        rows = [
            {"txn_date": "2026-09-01", "amount": 10, "direction": "debit",
             "merchant_key": None},
            {"txn_date": "not-a-date", "amount": 10, "direction": "debit",
             "merchant_key": "x"},
        ]
        assert group_price_history(rows) == {}


class TestSummarise:
    def test_up(self):
        summary = summarise([
            {"date": "2026-05-01", "amount": 16.00},
            {"date": "2026-09-01", "amount": 18.00},
        ])
        assert summary["direction"] == "up"
        assert summary["change"] == 2.00
        assert summary["pct"] == 12.5
        assert summary["first"] == 16.00
        assert summary["latest"] == 18.00

    def test_down(self):
        summary = summarise([
            {"date": "2026-05-01", "amount": 20.00},
            {"date": "2026-09-01", "amount": 15.00},
        ])
        assert summary["direction"] == "down"
        assert summary["change"] == -5.00
        assert summary["pct"] == 25.0

    def test_flat(self):
        summary = summarise([
            {"date": "2026-05-01", "amount": 16.00},
            {"date": "2026-09-01", "amount": 16.00},
        ])
        assert summary["direction"] == "flat"
        assert summary["change"] == 0

    def test_empty(self):
        assert summarise([]) is None


class TestPaymentRows:
    def test_marks_each_change(self):
        rows = payment_rows([
            {"date": "2026-05-01", "amount": 16.00},
            {"date": "2026-06-01", "amount": 16.00},
            {"date": "2026-07-01", "amount": 18.00},
        ])
        assert [r["direction"] for r in rows] == ["flat", "flat", "up"]
        assert rows[2]["change"] == 2.00
