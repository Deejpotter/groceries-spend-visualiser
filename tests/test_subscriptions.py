"""Tests for services/subscriptions.py — pure functions, no Flask."""

from datetime import date, timedelta

from services.subscriptions import (
    advance_next_due,
    fortnight_for_date,
    fortnightly_subscription_total,
    format_frequency,
    is_due_in_range,
    next_occurrence,
    subscription_category_label,
    subscriptions_due_in_range,
    suggest_from_purchases,
    suggest_from_unclassified,
)


# ---------------------------------------------------------------------------
# fortnight_for_date
# ---------------------------------------------------------------------------

class TestFortnightForDate:
    def test_monday_start(self):
        # Monday 12 Oct 2026
        d = date(2026, 10, 12)
        start, end = fortnight_for_date(d)
        assert start == date(2026, 10, 12)
        assert end == date(2026, 10, 25)

    def test_sunday_end(self):
        # Sunday 25 Oct 2026
        d = date(2026, 10, 25)
        start, end = fortnight_for_date(d)
        assert start == date(2026, 10, 12)
        assert end == date(2026, 10, 25)

    def test_midweek(self):
        # Wednesday 14 Oct 2026
        d = date(2026, 10, 14)
        start, end = fortnight_for_date(d)
        assert start == date(2026, 10, 12)
        assert end == date(2026, 10, 25)

    def test_saturday(self):
        # Saturday 17 Oct 2026
        d = date(2026, 10, 17)
        start, end = fortnight_for_date(d)
        assert start == date(2026, 10, 12)
        assert end == date(2026, 10, 25)


# ---------------------------------------------------------------------------
# is_due_in_range
# ---------------------------------------------------------------------------

class TestIsDueInRange:
    def test_weekly_in_range(self):
        sub = {"next_due": "2026-10-14", "frequency": "weekly", "active": 1, "amount": 10}
        assert is_due_in_range(sub, date(2026, 10, 12), date(2026, 10, 25))

    def test_weekly_out_of_range(self):
        sub = {"next_due": "2026-11-01", "frequency": "weekly", "active": 1, "amount": 10}
        assert not is_due_in_range(sub, date(2026, 10, 12), date(2026, 10, 25))

    def test_monthly_in_range(self):
        sub = {"next_due": "2026-10-20", "frequency": "monthly", "active": 1, "amount": 100}
        assert is_due_in_range(sub, date(2026, 10, 12), date(2026, 10, 25))

    def test_monthly_out_of_range(self):
        sub = {"next_due": "2026-11-15", "frequency": "monthly", "active": 1, "amount": 100}
        assert not is_due_in_range(sub, date(2026, 10, 12), date(2026, 10, 25))

    def test_inactive_not_due(self):
        sub = {"next_due": "2026-10-14", "frequency": "weekly", "active": 0, "amount": 10}
        assert not is_due_in_range(sub, date(2026, 10, 12), date(2026, 10, 25))

    def test_fortnightly_in_range(self):
        sub = {"next_due": "2026-10-15", "frequency": "fortnightly", "active": 1, "amount": 50}
        assert is_due_in_range(sub, date(2026, 10, 12), date(2026, 10, 25))

    def test_quarterly_in_range(self):
        sub = {"next_due": "2026-10-20", "frequency": "quarterly", "active": 1, "amount": 300}
        assert is_due_in_range(sub, date(2026, 10, 12), date(2026, 10, 25))

    def test_yearly_in_range(self):
        sub = {"next_due": "2026-10-20", "frequency": "yearly", "active": 1, "amount": 1200}
        assert is_due_in_range(sub, date(2026, 10, 12), date(2026, 10, 25))

    def test_no_next_due(self):
        sub = {"next_due": None, "frequency": "monthly", "active": 1, "amount": 100}
        assert not is_due_in_range(sub, date(2026, 10, 12), date(2026, 10, 25))

    def test_invalid_frequency(self):
        sub = {"next_due": "2026-10-14", "frequency": "daily", "active": 1, "amount": 10}
        assert not is_due_in_range(sub, date(2026, 10, 12), date(2026, 10, 25))


# ---------------------------------------------------------------------------
# subscriptions_due_in_range
# ---------------------------------------------------------------------------

class TestSubscriptionsDueInRange:
    def test_filters_correctly(self):
        subs = [
            {"next_due": "2026-10-14", "frequency": "weekly", "active": 1, "amount": 10},
            {"next_due": "2026-11-01", "frequency": "monthly", "active": 1, "amount": 100},
            {"next_due": "2026-10-20", "frequency": "monthly", "active": 0, "amount": 50},
        ]
        due = subscriptions_due_in_range(subs, date(2026, 10, 12), date(2026, 10, 25))
        assert len(due) == 1
        assert due[0]["amount"] == 10

    def test_empty_list(self):
        assert subscriptions_due_in_range([], date(2026, 10, 12), date(2026, 10, 25)) == []


# ---------------------------------------------------------------------------
# fortnightly_subscription_total
# ---------------------------------------------------------------------------

class TestFortnightlySubscriptionTotal:
    def test_sums_due_subscriptions(self):
        subs = [
            {"next_due": "2026-10-14", "frequency": "weekly", "active": 1, "amount": 10},
            {"next_due": "2026-10-20", "frequency": "monthly", "active": 1, "amount": 100},
            {"next_due": "2026-11-01", "frequency": "monthly", "active": 1, "amount": 50},
        ]
        total = fortnightly_subscription_total(subs, date(2026, 10, 12), date(2026, 10, 25))
        assert total == 110.0

    def test_no_subscriptions(self):
        assert fortnightly_subscription_total([], date(2026, 10, 12), date(2026, 10, 25)) == 0.0


# ---------------------------------------------------------------------------
# next_occurrence
# ---------------------------------------------------------------------------

class TestNextOccurrence:
    def test_weekly_next(self):
        sub = {"next_due": "2026-10-14", "frequency": "weekly"}
        assert next_occurrence(sub, date(2026, 10, 20)) == date(2026, 10, 21)

    def test_monthly_next(self):
        sub = {"next_due": "2026-10-14", "frequency": "monthly"}
        assert next_occurrence(sub, date(2026, 11, 1)) == date(2026, 11, 14)

    def test_no_anchor(self):
        sub = {"next_due": None, "frequency": "monthly"}
        assert next_occurrence(sub, date(2026, 10, 12)) is None

    def test_invalid_frequency(self):
        sub = {"next_due": "2026-10-14", "frequency": "daily"}
        assert next_occurrence(sub, date(2026, 10, 12)) is None


# ---------------------------------------------------------------------------
# advance_next_due
# ---------------------------------------------------------------------------

class TestAdvanceNextDue:
    def test_weekly_advance(self):
        sub = {"next_due": "2026-10-14", "frequency": "weekly"}
        assert advance_next_due(sub) == "2026-10-21"

    def test_monthly_advance(self):
        sub = {"next_due": "2026-10-14", "frequency": "monthly"}
        assert advance_next_due(sub) == "2026-11-14"

    def test_fortnightly_advance(self):
        sub = {"next_due": "2026-10-14", "frequency": "fortnightly"}
        assert advance_next_due(sub) == "2026-10-28"

    def test_quarterly_advance(self):
        sub = {"next_due": "2026-10-14", "frequency": "quarterly"}
        assert advance_next_due(sub) == "2027-01-14"

    def test_yearly_advance(self):
        sub = {"next_due": "2026-10-14", "frequency": "yearly"}
        assert advance_next_due(sub) == "2027-10-14"


# ---------------------------------------------------------------------------
# format_frequency
# ---------------------------------------------------------------------------

class TestFormatFrequency:
    def test_known_frequencies(self):
        assert format_frequency("weekly") == "Weekly"
        assert format_frequency("fortnightly") == "Fortnightly"
        assert format_frequency("monthly") == "Monthly"
        assert format_frequency("quarterly") == "Quarterly"
        assert format_frequency("yearly") == "Yearly"

    def test_unknown_frequency(self):
        assert format_frequency("daily") == "Daily"

    def test_empty_frequency(self):
        assert format_frequency("") == ""


# ---------------------------------------------------------------------------
# subscription_category_label
# ---------------------------------------------------------------------------

class TestSubscriptionCategoryLabel:
    def test_known_categories(self):
        assert subscription_category_label("bills") == "Bills"
        assert subscription_category_label("entertainment") == "Entertainment"
        assert subscription_category_label("rent") == "Rent & Housing"

    def test_unknown_category(self):
        assert subscription_category_label("unknown") == "unknown"


# ---------------------------------------------------------------------------
# suggest_from_unclassified
# ---------------------------------------------------------------------------

class TestSuggestFromUnclassified:
    def test_known_merchant_netflix(self):
        rows = [{"description": "Netflix", "amount": 18.99}]
        suggestions = suggest_from_unclassified(rows)
        assert len(suggestions) == 1
        assert suggestions[0]["name"] == "Netflix"
        assert suggestions[0]["amount"] == 18.99
        assert suggestions[0]["frequency"] == "monthly"
        assert suggestions[0]["confidence"] == "high"

    def test_known_merchant_spotify(self):
        rows = [{"description": "Spotify", "amount": 8.51}]
        suggestions = suggest_from_unclassified(rows)
        assert len(suggestions) == 1
        assert suggestions[0]["name"] == "Spotify"

    def test_known_merchant_agl(self):
        rows = [{"description": "AGL Energy", "amount": 150.00}]
        suggestions = suggest_from_unclassified(rows)
        assert len(suggestions) == 1
        assert suggestions[0]["name"] == "AGL Energy"
        assert suggestions[0]["frequency"] == "quarterly"

    def test_recurring_description(self):
        rows = [
            {"description": "Mystery Merchant", "amount": 25.00},
            {"description": "Mystery Merchant", "amount": 25.00},
            {"description": "Mystery Merchant", "amount": 25.00},
        ]
        suggestions = suggest_from_unclassified(rows)
        assert len(suggestions) == 1
        assert suggestions[0]["name"] == "Mystery Merchant"
        assert suggestions[0]["confidence"] == "medium"

    def test_empty_list(self):
        assert suggest_from_unclassified([]) == []

    def test_no_amount(self):
        rows = [{"description": "Netflix", "amount": 0}]
        assert suggest_from_unclassified(rows) == []

    def test_case_insensitive_match(self):
        rows = [{"description": "NETFLIX", "amount": 18.99}]
        suggestions = suggest_from_unclassified(rows)
        assert len(suggestions) == 1
        assert suggestions[0]["name"] == "Netflix"


# ---------------------------------------------------------------------------
# suggest_from_purchases
# ---------------------------------------------------------------------------

class TestSuggestFromPurchases:
    def test_recurring_weekly_purchases(self):
        base = date(2026, 10, 1)
        rows = []
        for i in range(4):
            rows.append({
                "store": "Woolworths",
                "product_name": "Milk",
                "line_total": 3.50,
                "order_date": (base + timedelta(days=i * 7)).isoformat(),
            })
        suggestions = suggest_from_purchases(rows)
        assert len(suggestions) == 1
        assert suggestions[0]["name"] == "Milk"
        assert suggestions[0]["frequency"] == "weekly"
        assert suggestions[0]["amount"] == 3.50

    def test_recurring_monthly_purchases(self):
        base = date(2026, 10, 1)
        rows = []
        for i in range(3):
            rows.append({
                "store": "Woolworths",
                "product_name": "Bulk Rice",
                "line_total": 25.00,
                "order_date": (base + timedelta(days=i * 30)).isoformat(),
            })
        suggestions = suggest_from_purchases(rows)
        assert len(suggestions) == 1
        assert suggestions[0]["frequency"] == "monthly"

    def test_irregular_purchases_no_suggestion(self):
        base = date(2026, 10, 1)
        rows = []
        for i, days in enumerate([1, 5, 15, 40]):
            rows.append({
                "store": "Woolworths",
                "product_name": "Random Item",
                "line_total": 10.00,
                "order_date": (base + timedelta(days=days)).isoformat(),
            })
        suggestions = suggest_from_purchases(rows)
        assert len(suggestions) == 0

    def test_varying_amounts_no_suggestion(self):
        base = date(2026, 10, 1)
        rows = []
        for i, amount in enumerate([10.00, 20.00, 30.00]):
            rows.append({
                "store": "Woolworths",
                "product_name": "Variable Item",
                "line_total": amount,
                "order_date": (base + timedelta(days=i * 7)).isoformat(),
            })
        suggestions = suggest_from_purchases(rows)
        assert len(suggestions) == 0

    def test_empty_list(self):
        assert suggest_from_purchases([]) == []
