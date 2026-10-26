"""Tests for services/recurring.py — recurring payment detection."""

from services.recurring import (
    classify_frequency,
    detect_recurring,
    normalise_merchant,
)


def make_transaction(date, description, amount, *,
                     direction="debit", status="other", merchant_key=None):
    """A bank transaction dict shaped like a bank_transactions row."""
    return {
        "txn_date": date,
        "description": description,
        "amount": amount,
        "direction": direction,
        "status": status,
        "merchant_key": merchant_key or normalise_merchant(description),
    }


def monthly(description, amount, dates=None):
    dates = dates or ["2026-01-15", "2026-02-15", "2026-03-15", "2026-04-15"]
    return [make_transaction(d, description, amount) for d in dates]


class TestNormaliseMerchant:
    def test_strips_payment_processor_prefix(self):
        assert normalise_merchant("PAYPAL *SPOTIFY") == "spotify"
        assert normalise_merchant("SQ *COFFEE SHOP") == "coffee"

    def test_strips_reference_numbers_and_noise_words(self):
        assert normalise_merchant("NETFLIX.COM 1234") == "netflix"
        assert normalise_merchant("TELSTRA PTE LTD") == "telstra"

    def test_empty_description(self):
        assert normalise_merchant("") == ""
        assert normalise_merchant("   ") == ""

    def test_same_merchant_variants_share_a_key(self):
        keys = {
            normalise_merchant("NETFLIX.COM"),
            normalise_merchant("NETFLIX.COM 4471 SYDNEY"),
            normalise_merchant("PAYPAL *NETFLIX"),
        }
        assert keys == {"netflix"}


class TestMergeSimilarGroups:
    def test_merges_a_longer_variant(self):
        from services.recurring import merge_similar_groups
        merged = merge_similar_groups({
            "amazon": [{"amount": 10}],
            "amazonau": [{"amount": 20}],
            "red": [{"amount": 5}],
            "redenergy": [{"amount": 6}],
        })
        assert set(merged) == {"amazon", "red", "redenergy"}
        assert len(merged["amazon"]) == 2
        assert len(merged["redenergy"]) == 1  # too short to fold

    def test_short_codes_stay_separate(self):
        from services.recurring import merge_similar_groups
        merged = merge_similar_groups({"agl": [1], "aglenergy": [2]})
        assert set(merged) == {"agl", "aglenergy"}


class TestClassifyFrequency:
    def test_known_cadences(self):
        assert classify_frequency([7, 7]) == "weekly"
        assert classify_frequency([14, 14]) == "fortnightly"
        assert classify_frequency([31, 28, 31]) == "monthly"
        assert classify_frequency([91, 92]) == "quarterly"
        assert classify_frequency([365, 365]) == "yearly"

    def test_irregular_intervals_do_not_match(self):
        assert classify_frequency([4, 35]) is None
        assert classify_frequency([]) is None


class TestDetectRecurring:
    def test_fixed_monthly_payment_is_a_subscription(self):
        result = detect_recurring(monthly("NETFLIX.COM", 18.99))
        assert len(result) == 1
        suggestion = result[0]
        assert suggestion["name"] == "Netflix"
        assert suggestion["merchant_key"] == "netflix"
        assert suggestion["frequency"] == "monthly"
        assert suggestion["kind"] == "subscription"
        assert suggestion["category"] == "entertainment"
        assert suggestion["amount"] == 18.99
        assert suggestion["monthly_cost"] == 18.99
        assert suggestion["occurrences"] == 4
        assert suggestion["confidence"] == "high"
        assert suggestion["next_due"] == "2026-05-15"

    def test_price_fields_track_a_price_rise(self):
        rows = [
            make_transaction("2026-01-15", "NETFLIX.COM", 16.00),
            make_transaction("2026-02-15", "NETFLIX.COM", 16.00),
            make_transaction("2026-03-15", "NETFLIX.COM", 18.00),
        ]
        suggestion = detect_recurring(rows)[0]
        assert suggestion["first_amount"] == 16.00
        assert suggestion["latest_amount"] == 18.00
        assert suggestion["price_change"] == 2.00
        assert suggestion["price_direction"] == "up"
        assert suggestion["price_pct"] == 12.5

    def test_dismissed_merchant_revives_after_a_new_pattern(self):
        rows = monthly("NETFLIX.COM", 18.99,
                       dates=["2026-01-15", "2026-02-15", "2026-03-15",
                              "2026-05-15", "2026-06-15", "2026-07-15"])
        result = detect_recurring(
            rows, excluded_keys=["netflix"],
            revive_after={"netflix": "2026-04-30"})
        assert len(result) == 1
        assert result[0]["revived"] is True
        assert result[0]["revived_since"] == "2026-04-30"
        assert result[0]["occurrences"] == 3
        assert result[0]["first_seen"] == "2026-05-15"

    def test_two_payments_after_dismissal_are_not_enough(self):
        rows = monthly("NETFLIX.COM", 18.99,
                       dates=["2026-01-15", "2026-02-15", "2026-03-15",
                              "2026-05-15", "2026-06-15"])
        assert detect_recurring(
            rows, excluded_keys=["netflix"],
            revive_after={"netflix": "2026-04-30"}) == []

    def test_two_amount_streams_become_two_bills(self):
        rows = []
        for month in range(1, 13):
            rows.append(make_transaction(
                f"2026-{month:02d}-01", "PAYPAL *BELONG AU", 90.00))
            rows.append(make_transaction(
                f"2026-{month:02d}-15", "PAYPAL *BELONG AU", 25.00))
        result = detect_recurring(rows)
        assert len(result) == 2
        by_amount = {s["amount"]: s for s in result}
        assert set(by_amount) == {25.00, 90.00}
        assert by_amount[90.00]["frequency"] == "monthly"
        assert by_amount[25.00]["frequency"] == "monthly"
        assert by_amount[90.00]["name"] == "Belong (90.00)"
        assert by_amount[25.00]["name"] == "Belong (25.00)"
        assert by_amount[90.00]["stream_key"].startswith("belong#")
        assert by_amount[25.00]["stream_key"].startswith("belong#")
        assert by_amount[90.00]["stream_key"] != by_amount[25.00]["stream_key"]

    def test_variable_amount_is_a_bill(self):
        dates = ["2026-01-20", "2026-02-20", "2026-03-20", "2026-04-20"]
        rows = [
            make_transaction("2026-01-20", "AGL ENERGY", 120.00),
            make_transaction("2026-02-20", "AGL ENERGY", 180.00),
            make_transaction("2026-03-20", "AGL ENERGY", 150.00),
            make_transaction("2026-04-20", "AGL ENERGY", 165.00),
        ]
        assert len(dates) == len(rows)
        result = detect_recurring(rows)
        assert len(result) == 1
        assert result[0]["kind"] == "bill"
        assert result[0]["category"] == "utilities"
        assert result[0]["amount"] == 153.75

    def test_requires_three_sightings(self):
        rows = monthly("NETFLIX.COM", 18.99,
                       dates=["2026-02-15", "2026-03-15"])
        assert detect_recurring(rows) == []

    def test_internal_transfers_are_ignored(self):
        rows = monthly("TRANSFER TO SAVINGS", 500.00)
        assert detect_recurring(rows) == []

    def test_monthly_needs_three_sightings(self):
        rows = monthly("NETFLIX.COM", 18.99, dates=["2026-09-15", "2026-10-15"])
        assert detect_recurring(rows) == []

    def test_yearly_bill_needs_only_two_sightings(self):
        rows = [
            make_transaction("2025-11-01", "AAMI INSURANCE", 780.00),
            make_transaction("2026-11-01", "AAMI INSURANCE", 790.00),
        ]
        result = detect_recurring(rows)
        assert len(result) == 1
        assert result[0]["name"] == "AAMI Insurance"
        assert result[0]["frequency"] == "yearly"
        assert result[0]["confidence"] == "medium"

    def test_specific_gym_brand_beats_generic_keyword(self):
        rows = [
            make_transaction(d, "JETTS FITNESS 001", 9.95)
            for d in ["2026-09-07", "2026-09-14", "2026-09-21", "2026-09-28"]
        ]
        assert detect_recurring(rows)[0]["name"] == "Jetts Fitness"

    def test_credits_are_ignored(self):
        rows = [
            make_transaction("2026-01-01", "MY OB PAYROLL", 3000.00,
                             direction="credit")
            for _ in range(4)
        ]
        assert detect_recurring(rows) == []

    def test_income_with_unknown_direction_is_ignored(self):
        rows = [
            make_transaction(d, "MY OB PAYROLL", 3000.00, direction="unknown")
            for d in ["2026-01-01", "2026-01-15", "2026-01-29", "2026-02-12"]
        ]
        assert detect_recurring(rows) == []

    def test_unknown_direction_bill_is_still_detected(self):
        rows = [
            make_transaction(d, "AGL ENERGY", 140.00, direction="unknown")
            for d in ["2026-01-20", "2026-02-20", "2026-03-20"]
        ]
        assert detect_recurring(rows)[0]["name"] == "AGL Energy"

    def test_grocery_transactions_are_ignored(self):
        rows = monthly("WOOLWORTHS METRO", 85.00)
        for row in rows:
            row["status"] = "grocery"
        assert detect_recurring(rows) == []

    def test_excluded_merchants_are_ignored(self):
        rows = monthly("NETFLIX.COM", 18.99)
        assert detect_recurring(rows, excluded_keys=["netflix"]) == []

    def test_irregular_spending_is_not_recurring(self):
        rows = [
            make_transaction("2026-01-02", "RANDOM SHOP", 40.00),
            make_transaction("2026-01-06", "RANDOM SHOP", 42.00),
            make_transaction("2026-03-10", "RANDOM SHOP", 41.00),
        ]
        assert detect_recurring(rows) == []

    def test_wildly_variable_amounts_are_not_recurring(self):
        rows = [
            make_transaction("2026-01-10", "ODD MERCHANT", 10.00),
            make_transaction("2026-02-09", "ODD MERCHANT", 110.00),
            make_transaction("2026-03-11", "ODD MERCHANT", 260.00),
        ]
        assert detect_recurring(rows) == []

    def test_unknown_merchant_is_named_from_description(self):
        rows = monthly("CITY WASTE SERVICE 8821", 42.00)
        result = detect_recurring(rows)
        assert result[0]["name"] == "City Waste Service"
        assert result[0]["category"] == "other"

    def test_weekly_cadence_and_monthly_cost(self):
        rows = [
            make_transaction("2026-03-02", "THE COFFEE CLUB", 12.00),
            make_transaction("2026-03-09", "THE COFFEE CLUB", 12.00),
            make_transaction("2026-03-16", "THE COFFEE CLUB", 12.00),
            make_transaction("2026-03-23", "THE COFFEE CLUB", 12.00),
        ]
        result = detect_recurring(rows)
        assert result[0]["frequency"] == "weekly"
        assert result[0]["monthly_cost"] == 52.00

    def test_high_confidence_sorts_first(self):
        low = [
            make_transaction("2026-01-05", "SHAKY BILL", 20.00),
            make_transaction("2026-02-01", "SHAKY BILL", 20.00),
            make_transaction("2026-03-08", "SHAKY BILL", 20.00),
        ]
        high = monthly("NETFLIX.COM", 18.99)
        result = detect_recurring(low + high)
        assert result[0]["name"] == "Netflix"

    def test_accepts_order_date_alias(self):
        rows = [
            {"order_date": d, "description": "SPOTIFY", "amount": 11.99}
            for d in ["2026-01-15", "2026-02-15", "2026-03-15"]
        ]
        assert detect_recurring(rows)[0]["name"] == "Spotify"


class TestBankBoilerplate:
    BELONG = "PAYPAL BELONG AU AUS Card xx2287 Value Date: {}"
    CLOUDFLARE = "PAYPAL CLOUDFLARE CA USA Card xx2287 AUD 16.92 Value Date: {}"

    def test_paypal_without_star_keys_on_the_real_merchant(self):
        assert normalise_merchant(self.BELONG.format("20/10/2025")) == "belong"
        assert normalise_merchant(self.CLOUDFLARE.format("18/10/2025")) == "cloudflare"

    def test_paypal_merchants_are_not_grouped_together(self):
        rows = [make_transaction(d, self.BELONG.format(d), 60.0, merchant_key="paypal")
                        for d in ("2026-07-01", "2026-08-01", "2026-09-01", "2026-10-01")]
        rows += [make_transaction("2026-08-15", self.CLOUDFLARE.format("x"), 16.92,
                                                            merchant_key="paypal")]
        result = detect_recurring(rows)
        assert [s["name"] for s in result] == ["Belong"]
        assert result[0]["price_direction"] == "flat"

    def test_display_name_drops_card_currency_and_date(self):
        rows = monthly("VULTR BY CONSTANT MATAWAN NJ USA Card xx2287 USD 5.00 "
                                      "Value Date: 08/06/2026", 7.80)
        suggestion = detect_recurring(rows)[0]
        assert suggestion["name"] == "Vultr By Constant Matawan"
        assert suggestion["category"] == "other"

    def test_a_missed_month_does_not_hide_a_bill(self):
        rows = monthly("ACME INTERNET", 79.0,
                                      ["2026-01-05", "2026-02-05", "2026-04-05", "2026-05-05"])
        assert detect_recurring(rows)[0]["frequency"] == "monthly"

    def test_misspelt_merchant_variants_merge(self):
        from services.recurring import merge_similar_groups
        merged = merge_similar_groups({"telstra": [1], "telsrta": [2]})
        assert len(merged) == 1


class TestFixedMonthlyAmount:
    def test_same_amount_same_day_with_changing_descriptions(self):
        rows = [
            make_transaction("2026-06-03", "DIRECT DEBIT 88123 ACME", 49.95),
            make_transaction("2026-07-03", "DD 99001 ACMEPAY", 49.95),
            make_transaction("2026-08-04", "ACME HOLDINGS 77712", 49.95),
            make_transaction("2026-09-02", "DD 99001 ACMEPAY", 49.95),
        ]
        result = detect_recurring(rows)
        assert len(result) == 1
        assert result[0]["frequency"] == "monthly"
        assert result[0]["stream_key"] == "amount:49.95"
        assert "different descriptions" in result[0]["reason"]

    def test_wraps_around_month_end(self):
        from datetime import date
        from services.recurring import _day_spread
        assert _day_spread([date(2026, 1, 30), date(2026, 2, 1),
                            date(2026, 3, 31)]) == 2

    def test_same_amount_on_scattered_days_is_ignored(self):
        rows = [
            make_transaction("2026-06-03", "CAFE ONE", 12.00),
            make_transaction("2026-07-19", "CAFE TWO", 12.00),
            make_transaction("2026-08-11", "CAFE THREE", 12.00),
        ]
        assert detect_recurring(rows) == []
