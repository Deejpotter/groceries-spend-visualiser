"""Bills move past a bank payment that paid them, so they stop showing overdue."""

import io

from conftest import login
from services.bank_import import advance_paid_bills
from services.price_history import stream_points
from services.recurring import normalise_merchant
from services.subscriptions import due_after_payment, make_subscription


def _pay(*days, amount=13.99):
    return [{"date": day, "amount": amount} for day in days]


class TestDueAfterPayment:
    def test_payment_on_due_date_moves_one_cycle(self):
        sub = make_subscription(next_due="2026-10-15", frequency="monthly")
        assert due_after_payment(sub, _pay("2026-10-15")) == "2026-11-15"

    def test_overdue_bill_lands_after_latest_payment(self):
        sub = make_subscription(next_due="2026-02-03", frequency="monthly")
        payments = _pay("2026-09-03", "2026-10-03", "2026-11-03")
        assert due_after_payment(sub, payments) == "2026-12-03"

    def test_payment_a_few_days_early_counts(self):
        sub = make_subscription(next_due="2026-11-06", frequency="monthly")
        assert due_after_payment(sub, _pay("2026-11-04")) == "2026-12-04"

    def test_payment_too_early_is_last_cycle(self):
        sub = make_subscription(next_due="2026-11-15", frequency="monthly")
        assert due_after_payment(sub, _pay("2026-10-15")) is None

    def test_weekly_uses_the_same_early_window(self):
        sub = make_subscription(next_due="2026-11-20", frequency="weekly")
        assert due_after_payment(sub, _pay("2026-11-13")) is None
        assert due_after_payment(sub, _pay("2026-11-17")) == "2026-11-24"

    def test_fortnightly(self):
        sub = make_subscription(next_due="2026-10-30", frequency="fortnightly")
        assert due_after_payment(sub, _pay("2026-10-30")) == "2026-11-13"

    def test_skips_recurring_inactive_and_missing_data(self):
        payments = _pay("2026-11-01")
        assert due_after_payment(
            {**make_subscription(next_due="2026-10-01"), "kind": "recurring"}, payments) is None
        assert due_after_payment(make_subscription(next_due="2026-10-01", active=0), payments) is None
        assert due_after_payment(make_subscription(next_due=None), payments) is None
        assert due_after_payment(make_subscription(next_due="2026-10-01", frequency="daily"), payments) is None
        assert due_after_payment(make_subscription(next_due="2026-10-01"), []) is None


class TestStreamPoints:
    def test_keeps_payments_near_the_bill_amount(self):
        points = _pay("2026-10-01", amount=65.0) + _pay("2026-10-05", amount=899.0)
        assert stream_points(points, 65.0) == points[:1]

    def test_no_amount_keeps_everything(self):
        points = _pay("2026-10-01")
        assert stream_points(points, None) == points


def _bill(db, name, next_due, merchant_key, amount=13.99, stream_amount=None, active=1):
    cur = db.execute(
        """INSERT INTO subscriptions (name, amount, frequency, next_due, active,
                                      merchant_key, stream_amount)
           VALUES (?, ?, 'monthly', ?, ?, ?, ?)""",
        (name, amount, next_due, active, merchant_key, stream_amount))
    db.commit()
    return cur.lastrowid


def _txn(db, day, description, amount, direction="debit"):
    db.execute(
        """INSERT INTO bank_transactions (source, external_id, txn_date, description,
                                          merchant_key, amount, direction)
           VALUES ('csv', ?, ?, ?, ?, ?, ?)""",
        (f"{day}-{description}-{amount}", day, description,
         normalise_merchant(description), amount, direction))
    db.commit()


class TestAdvancePaidBills:
    def test_moves_paid_bills_only(self, db):
        key = normalise_merchant("SPOTIFY P3A1B SYDNEY")
        paid = _bill(db, "Spotify", "2026-04-03", key)
        unpaid = _bill(db, "Gym", "2026-11-01", normalise_merchant("ANYTIME FITNESS"))
        manual = _bill(db, "Rent", "2026-10-01", None)
        _txn(db, "2026-10-03", "SPOTIFY P3A1B SYDNEY", 13.99)
        _txn(db, "2026-11-03", "SPOTIFY P3A1B SYDNEY", 13.99)
        _txn(db, "2026-09-01", "ANYTIME FITNESS", 30.0)

        moved = advance_paid_bills(db)

        assert moved == [{"name": "Spotify", "old": "2026-04-03", "new": "2026-12-03"}]
        due = dict(db.execute("SELECT id, next_due FROM subscriptions").fetchall())
        assert due == {paid: "2026-12-03", unpaid: "2026-11-01", manual: "2026-10-01"}
        assert advance_paid_bills(db) == []

    def test_ignores_refunds_and_other_streams(self, db):
        key = normalise_merchant("TELSTRA")
        bill = _bill(db, "Telstra", "2026-10-20", key, amount=65.0, stream_amount=65.0)
        _txn(db, "2026-10-21", "TELSTRA", 65.0, direction="credit")
        _txn(db, "2026-10-22", "TELSTRA", 899.0)

        assert advance_paid_bills(db) == []
        assert db.execute("SELECT next_due FROM subscriptions WHERE id = ?",
                          (bill,)).fetchone()[0] == "2026-10-20"

    def test_skips_paused_bills(self, db):
        key = normalise_merchant("NETFLIX.COM")
        _bill(db, "Netflix", "2026-10-01", key, active=0)
        _txn(db, "2026-10-01", "NETFLIX.COM", 13.99)
        assert advance_paid_bills(db) == []


def test_bank_csv_import_reports_bills_marked_paid(client, db):
    login(client)
    _bill(db, "Spotify", "2026-09-03", normalise_merchant("SPOTIFY P3A1B SYDNEY"))
    csv = "Date,Description,Amount,Balance\n03/10/2026,SPOTIFY P3A1B SYDNEY,-13.99,100.00\n"
    resp = client.post("/spend/import-bank", data={
        "bank_file": (io.BytesIO(csv.encode()), "statement.csv"),
    }, content_type="multipart/form-data")
    assert resp.status_code == 200
    assert b"1 bill marked paid from your bank payments" in resp.data
    assert b"Spotify: next due Tue 03 Nov 2026" in resp.data
    assert db.execute("SELECT next_due FROM subscriptions").fetchone()[0] == "2026-11-03"
