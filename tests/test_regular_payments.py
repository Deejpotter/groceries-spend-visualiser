"""Count detected regular payments as bills in one step, and see savings on the dashboard."""

from datetime import date, timedelta

from conftest import login
from services.savings_advice import make_txn
from test_savings_advice import _history

KELLY = "Transfer To Kelly Pearson, CommBank App Love you"
CAR = "Transfer to other Bank NetBank, Dan car"


def _rows():
    rows = _history()
    end = date(2026, 10, 30)
    for n in range(6):
        day = end - timedelta(days=14 * n + 3)
        rows.append(make_txn(day.isoformat(), 800, KELLY))
        rows.append(make_txn((day - timedelta(days=1)).isoformat(), 200, CAR))
    return rows


def _store(db, rows):
    for n, row in enumerate(rows):
        db.execute(
            "INSERT INTO bank_transactions (source, external_id, txn_date, description, merchant_key,"
            " amount, direction, status) VALUES ('csv', ?, ?, ?, ?, ?, ?, ?)",
            (str(n), row["txn_date"], row["description"], row["merchant_key"], row["amount"],
             row["direction"], row["status"]))
    db.commit()


def _untracked(app):
    from services.suggestions import untracked_regular_payments
    with app.app_context():
        return {r["name"]: r for r in untracked_regular_payments()}


def test_untracked_lists_regular_transfers(app, db):
    _store(db, _rows())
    found = _untracked(app)
    assert found["Transfer to Kelly Pearson"]["per_pay"] == 800
    assert found["Transfer to Other Bank (Dan Car)"]["per_pay"] == 200


def test_fortnight_page_offers_to_count_them(client, db):
    login(client)
    _store(db, _rows())
    html = client.get("/subscriptions/fortnight").get_data(as_text=True)
    assert "Regular payments not counted as bills" in html
    assert "Transfer to Kelly Pearson" in html
    assert "Count ticked as bills" in html


def test_count_ticked_adds_only_ticked_and_updates_savings(app, client, db):
    login(client)
    _store(db, _rows())
    kelly = _untracked(app)["Transfer to Kelly Pearson"]
    before = client.get("/subscriptions/fortnight").get_data(as_text=True)
    assert "$0.00 bills" in before
    r = client.post("/subscriptions/track-regular", data={"stream_key": [kelly["stream_key"]]})
    assert r.status_code == 302
    names = [row["name"] for row in db.execute("SELECT name FROM subscriptions")]
    assert names == ["Transfer to Kelly Pearson"]
    after = _untracked(app)
    assert "Transfer to Kelly Pearson" not in after
    assert "Transfer to Other Bank (Dan Car)" in after
    page = client.get("/subscriptions/fortnight").get_data(as_text=True)
    assert "$800.00 bills" in page


def test_amounts_come_from_the_server_not_the_form(app, client, db):
    login(client)
    _store(db, _rows())
    kelly = _untracked(app)["Transfer to Kelly Pearson"]
    client.post("/subscriptions/track-regular", data={"stream_key": [kelly["stream_key"]], "amount": "1"})
    assert db.execute("SELECT amount FROM subscriptions").fetchone()["amount"] == 800


def test_nothing_ticked(client, db):
    login(client)
    _store(db, _rows())
    client.post("/subscriptions/track-regular", data={})
    assert db.execute("SELECT COUNT(*) FROM subscriptions").fetchone()[0] == 0


def test_track_regular_is_post_only(client):
    login(client)
    assert client.get("/subscriptions/track-regular").status_code == 405


def test_dashboard_save_this_pay(client, db):
    login(client)
    _store(db, _rows())
    html = client.get("/").get_data(as_text=True)
    assert "Save this pay" in html
    assert "regular payment" in html and "not counted as bills yet" in html
    assert "#regular-payments" in html


def test_dashboard_without_bank_data(client):
    login(client)
    html = client.get("/").get_data(as_text=True)
    assert "Save this pay" not in html


def test_per_cycle_is_exact_for_matching_frequency():
    from services.savings_advice import bills_per_cycle, per_cycle
    assert per_cycle(800, "fortnightly", 14) == 800
    assert per_cycle(100, "weekly", 14) == 200
    assert per_cycle(365.25, "yearly", 14) == 14
    assert bills_per_cycle([{"amount": 800, "frequency": "fortnightly"},
                            {"amount": 200, "frequency": "fortnightly"}], 14) == 1000
