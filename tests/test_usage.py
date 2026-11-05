"""Usage rates from purchases and stock counts, and what the pay-cycle list buys from them."""

from datetime import date, timedelta

from conftest import login
from services.purchase_patterns import build_patterns, make_purchase
from services.usage import (
    daily_packs, make_count, merge_needs, on_hand, packs_needed, stock_rows, usage_needs, usage_rate,
)

TODAY = date(2026, 11, 8)


def _milk(days_ago, qty, basket=None, **kw):
    day = (TODAY - timedelta(days=days_ago)).isoformat()
    return make_purchase(order_date=day, product_name="Lactose Free Milk 1L", quantity=qty,
                         ingredient_id=15, basket_id=basket or day, **kw)


# Milk bought on uneven days: 6, 4, 10, 8 ... packs, about 1 a day overall.
MILK = [_milk(d, q) for d, q in [(84, 8), (78, 6), (70, 8), (66, 4), (56, 10), (49, 8),
                                 (42, 6), (35, 8), (30, 4), (21, 10), (14, 8), (7, 8)]]


class TestDailyPacks:
    def test_double_imported_shop_counts_once(self):
        rows = [_milk(7, 8, basket="csv-1"), _milk(7, 8, basket="invoice-1")]
        assert daily_packs(rows) == {"ing:15": {TODAY - timedelta(days=7): 8.0}}


class TestUsageRate:
    def test_rate_from_purchases_ignores_uneven_gaps(self):
        rate = usage_rate(daily_packs(MILK)["ing:15"], [], TODAY)
        assert rate["source"] == "purchases"
        assert round(rate["per_day"], 2) == round(88 / 84, 2)

    def test_counts_win_over_purchases(self):
        bought = {date(2026, 11, 2): 8.0}
        counts = [make_count(counted_on="2026-11-01", quantity=3), make_count(counted_on="2026-11-08", quantity=4)]
        rate = usage_rate(bought, counts, TODAY)
        assert rate == {"per_day": 1.0, "source": "counts"}

    def test_impossible_span_is_ignored(self):
        counts = [make_count(counted_on="2026-11-01", quantity=1), make_count(counted_on="2026-11-08", quantity=9)]
        assert usage_rate({date(2026, 10, 1): 4.0}, counts, TODAY)["source"] == "purchases"

    def test_nothing_known(self):
        assert usage_rate({}, [], TODAY) is None


def test_on_hand_from_last_count():
    bought = {date(2026, 11, 3): 8.0}
    counts = [make_count(counted_on="2026-11-01", quantity=2)]
    assert on_hand(bought, counts, 1.0, TODAY) == 3.0
    assert on_hand(bought, [], 1.0, TODAY) is None
    assert on_hand({}, counts, 1.0, TODAY) == 0.0


class TestPacksNeeded:
    def test_buys_the_gap(self):
        assert packs_needed(1.0, 14, 3) == 11

    def test_cupboard_cap(self):
        assert packs_needed(1.0, 14, 3, max_on_hand=10) == 7

    def test_perishable_bought_for_how_long_it_keeps(self):
        assert packs_needed(0.5, 14, 0, keep_days=5) == 3

    def test_enough_left(self):
        assert packs_needed(1.0, 14, 20) == 0


def test_usage_needs_replace_rhythm_guess():
    patterns = build_patterns(MILK, TODAY)
    usage = usage_needs(patterns, MILK, {"ing:15": {"max_on_hand": 10}},
                        {"ing:15": [make_count("ing:15", "2026-11-08", 4)]},
                        date(2026, 11, 9), date(2026, 11, 22), TODAY)
    need = usage["ing:15"]
    assert need["quantity"] == 7  # cap: 10 on hand minus ~2.95 left on payday = 7.05, rounded down to stay under it
    assert "Uses about 7.3 a week" in need["usage_reason"] and "keep at most 10" in need["usage_reason"]
    rhythm = [{"key": "ing:15", "quantity": 99}, {"key": "prod:Bread", "quantity": 1}]
    assert [n["quantity"] for n in merge_needs(rhythm, usage)] == [1, 7]


def test_well_stocked_item_drops_off_the_list():
    patterns = build_patterns(MILK, TODAY)
    usage = usage_needs(patterns, MILK, {}, {"ing:15": [make_count("ing:15", "2026-11-08", 40)]},
                        date(2026, 11, 9), date(2026, 11, 22), TODAY)
    assert usage == {"ing:15": None}
    assert merge_needs([{"key": "ing:15"}], usage) == []


def test_rarely_bought_items_keep_the_rhythm_model():
    rows = [_milk(60, 1), _milk(30, 1)]
    assert usage_needs(build_patterns(rows, TODAY), rows, {}, {}, TODAY, TODAY + timedelta(days=13), TODAY) == {}


def test_stock_count_page_saves_counts_and_limits(client, db, monkeypatch):
    login(client)
    db.execute("INSERT INTO ingredients (id, name, category, unit) VALUES (15, 'Milk', 'dairy', 'L')")
    for row in MILK:
        db.execute("INSERT INTO purchases (order_date, basket_id, store, product_name, quantity, line_total, ingredient_id)"
                   " VALUES (?, ?, 'Woolworths', ?, ?, ?, 15)",
                   (row["order_date"], row["basket_id"], row["product_name"], row["quantity"], row["quantity"] * 2))
    db.commit()
    monkeypatch.setattr("routes.pantry.local_today", lambda: TODAY)
    page = client.get("/pantry/stock-count")
    assert b"Milk" in page.data and b'value="ing:15"' in page.data
    resp = client.post("/pantry/stock-count", data={
        "item_key": "ing:15", "count": "4", "max_on_hand": "10", "keep_days": ""}, follow_redirects=True)
    assert b"Saved 1 count" in resp.data
    assert dict(db.execute("SELECT max_on_hand, keep_days FROM item_usage WHERE item_key = 'ing:15'").fetchone()) == \
        {"max_on_hand": 10.0, "keep_days": None}
    assert db.execute("SELECT counted_on, quantity FROM stock_counts").fetchone()[:] == ("2026-11-08", 4.0)
    bad = client.post("/pantry/stock-count", data={"item_key": "ing:15", "count": "-1", "max_on_hand": "", "keep_days": ""},
                      follow_redirects=True)
    assert b"Not saved for: 15" in bad.data


def test_bank_payments_are_not_products():
    rows = [make_purchase(order_date=f"2026-10-{d:02d}", product_name="ALDI STORES CARRUM DOWNS", basket_id=f"csv:{d}")
            for d in range(1, 29, 4)]
    for row in rows:
        row["origin"] = "banksync"
    patterns = build_patterns(rows, TODAY)
    assert daily_packs(rows) == {}
    assert stock_rows(patterns, rows, {}, {}, TODAY) == []
