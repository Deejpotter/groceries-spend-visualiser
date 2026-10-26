"""Tests for bills that have stopped paying."""

from datetime import date

from services.subscription_health import ended_subscriptions, missed_cycles
from services.subscriptions import make_subscription


class TestMissedCycles:
    def test_monthly(self):
        assert missed_cycles("2026-01-15", "monthly", date(2026, 5, 15)) == 4
        assert missed_cycles("2026-01-15", "monthly", date(2026, 4, 14)) == 2

    def test_weekly(self):
        assert missed_cycles("2026-01-01", "weekly", date(2026, 1, 29)) == 4

    def test_quarterly_and_yearly(self):
        assert missed_cycles("2026-01-01", "quarterly", date(2026, 10, 1)) == 3
        assert missed_cycles("2023-01-01", "yearly", date(2026, 1, 1)) == 3

    def test_unknown_or_missing(self):
        assert missed_cycles(None, "monthly") == 0
        assert missed_cycles("2026-01-01", "daily") == 0


def subscription(points, ended_dismissed_at=None, **kwargs):
    sub = make_subscription(**kwargs)
    sub["price_points"] = points
    sub["ended_dismissed_at"] = ended_dismissed_at
    return sub


class TestEndedSubscriptions:
    TODAY = date(2026, 5, 15)

    def test_flags_three_or_more_missed(self):
        subs = [
            subscription([{"date": "2026-01-15", "amount": 10}],
                         id=1, name="Old Bill"),
            subscription([{"date": "2026-04-15", "amount": 10}],
                         id=2, name="Current Bill"),
        ]
        result = ended_subscriptions(subs, today=self.TODAY)
        assert [row["name"] for row in result] == ["Old Bill"]
        assert result[0]["missed_cycles"] == 4
        assert result[0]["last_seen"] == "2026-01-15"

    def test_ignores_inactive_and_unlinked(self):
        subs = [
            subscription([{"date": "2026-01-15", "amount": 10}],
                         id=1, name="Paused", active=0),
            subscription([], id=2, name="No history"),
        ]
        assert ended_subscriptions(subs, today=self.TODAY) == []

    def test_keep_tracking_suppresses_until_a_newer_payment(self):
        suppressed = subscription(
            [{"date": "2026-01-15", "amount": 10}],
            id=1, name="Old Bill", ended_dismissed_at="2026-05-01 00:00:00")
        assert ended_subscriptions([suppressed], today=self.TODAY) == []

        newer = subscription(
            [{"date": "2026-05-01", "amount": 10}],
            id=2, name="Still Paying", ended_dismissed_at="2026-02-01 00:00:00")
        assert ended_subscriptions([newer], today=self.TODAY) == []

    def test_sorted_by_missed_cycles(self):
        subs = [
            subscription([{"date": "2026-04-15", "amount": 10}],
                         id=1, name="One", frequency="weekly"),
            subscription([{"date": "2026-01-15", "amount": 10}],
                         id=2, name="Many", frequency="weekly"),
        ]
        result = ended_subscriptions(subs, today=self.TODAY)
        assert [row["name"] for row in result] == ["Many", "One"]
