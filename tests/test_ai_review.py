"""Tests for the optional AI review layer."""

from services.ai_review import (
    _validate_reviews,
    build_candidates,
    cached_reviews,
    merge_suggestions,
    redact,
    save_reviews,
)
from services.recurring import normalise_merchant


def txn(date, description, amount, direction="debit", status="other"):
    return {
        "txn_date": date,
        "description": description,
        "amount": amount,
        "direction": direction,
        "status": status,
        "merchant_key": normalise_merchant(description),
    }


class TestRedact:
    def test_removes_card_and_long_numbers(self):
        cleaned = redact("AMAZON AU MARKETPLACE, Card xx2287, Value date: 17/05/2026")
        assert "2287" not in cleaned
        assert "2026" not in cleaned
        assert "AMAZON AU MARKETPLACE" in cleaned

    def test_leaves_normal_text(self):
        assert redact("NETFLIX.COM 1234") == "NETFLIX.COM [number]"


class TestBuildCandidates:
    def test_groups_and_requires_two_sightings(self):
        rows = [
            txn("2026-09-01", "BINGE.COM", 16.00),
            txn("2026-10-01", "BINGE.COM", 16.00),
            txn("2026-10-02", "ONE OFF SHOP", 9.00),
        ]
        candidates = build_candidates(rows)
        assert [c["merchant_key"] for c in candidates] == ["binge"]

    def test_skips_groceries_credits_transfers_and_fees(self):
        rows = [
            txn("2026-09-01", "WOOLWORTHS 123", 80.00, status="grocery"),
            txn("2026-09-08", "WOOLWORTHS 123", 80.00, status="grocery"),
            txn("2026-09-01", "MY OB PAYROLL", 3000.00, direction="credit"),
            txn("2026-09-15", "MY OB PAYROLL", 3000.00, direction="credit"),
            txn("2026-09-01", "AUTO SMART SAVINGS DEBIT", 112.00),
            txn("2026-10-01", "AUTO SMART SAVINGS DEBIT", 112.00),
            txn("2026-09-01", "INTERNATIONAL TRANSACTION FEE", 0.50),
            txn("2026-10-01", "INTERNATIONAL TRANSACTION FEE", 0.50),
        ]
        assert build_candidates(rows) == []

    def test_merges_similar_merchant_variants(self):
        rows = [
            txn("2026-09-01", "AMAZON AU MARKETPLACE", 45.00),
            txn("2026-10-01", "AMAZONAU PTY", 45.00),
        ]
        candidates = build_candidates(rows)
        assert len(candidates) == 1
        assert candidates[0]["occurrences"] == 2

    def test_evidence_hash_changes_with_new_data(self):
        base = [
            txn("2026-09-01", "BINGE.COM", 16.00),
            txn("2026-10-01", "BINGE.COM", 16.00),
        ]
        first = build_candidates(base)[0]
        second = build_candidates(base + [
            txn("2026-11-01", "BINGE.COM", 16.00),
        ])[0]
        assert first["evidence_hash"] != second["evidence_hash"]


class TestValidateReviews:
    def candidates(self):
        return build_candidates([
            txn("2026-09-01", "BINGE.COM", 16.00),
            txn("2026-10-01", "BINGE.COM", 16.00),
            txn("2026-09-05", "AMAZON MARKETPLACE", 45.00),
            txn("2026-10-05", "AMAZON MARKETPLACE", 45.00),
        ])

    def test_validates_category_frequency_and_keys(self):
        candidates = self.candidates()
        reviews = _validate_reviews({"reviews": [
            {"merchant_key": "binge", "bill": True, "name": "Binge",
             "category": "nonsense", "frequency": "hourly", "reason": "ok"},
            {"merchant_key": "ghost", "bill": True, "name": "Ghost",
             "category": "other", "frequency": None, "reason": "unknown"},
            {"merchant_key": "amazon", "bill": "no", "name": "Amazon",
             "category": "other", "frequency": None, "reason": "not bool"},
        ]}, candidates)
        assert len(reviews) == 1
        assert reviews[0]["merchant_key"] == "binge"
        assert reviews[0]["category"] == "other"
        assert reviews[0]["frequency"] is None

    def test_rejects_missing_reviews_list(self):
        import pytest
        from services.ai_review import AIReviewError
        with pytest.raises(AIReviewError):
            _validate_reviews({}, self.candidates())


class TestMergeSuggestions:
    def test_ai_noise_drops_a_rule_suggestion(self):
        candidates = build_candidates([
            txn("2026-08-01", "BINGE.COM", 16.00),
            txn("2026-09-01", "BINGE.COM", 16.00),
            txn("2026-10-01", "BINGE.COM", 16.00),
        ])
        detected = [{
            "merchant_key": "binge", "name": "Binge", "amount": 16.00,
            "frequency": "monthly", "category": "entertainment",
            "kind": "subscription", "confidence": "high",
            "monthly_cost": 16.00, "occurrences": 3,
        }]
        reviews = {"binge": {"verdict": "noise"}}
        assert merge_suggestions(detected, candidates, reviews) == []

    def test_ai_rename_keeps_rule_amount_and_schedule(self):
        candidates = build_candidates([
            txn("2026-08-15", "TELSTRA CORP", 65.00),
            txn("2026-09-15", "TELSTRA CORP", 65.00),
            txn("2026-10-15", "TELSTRA CORP", 65.00),
        ])
        detected = [{
            "merchant_key": "telstra", "name": "Telstra", "amount": 65.00,
            "frequency": "monthly", "category": "bills", "kind": "subscription",
            "confidence": "high", "monthly_cost": 65.00, "occurrences": 3,
        }]
        reviews = {"telstra": {
            "verdict": "bill", "display_name": "Telstra Mobile",
            "category": "bills", "frequency": "monthly", "reason": "Phone bill",
        }}
        merged = merge_suggestions(detected, candidates, reviews)
        assert len(merged) == 1
        assert merged[0]["name"] == "Telstra Mobile"
        assert merged[0]["amount"] == 65.00
        assert merged[0]["frequency"] == "monthly"

    def test_ai_can_promote_a_missed_bill_with_a_cadence(self):
        candidates = build_candidates([
            txn("2026-08-01", "CITY COUNCIL RATES", 300.00),
            txn("2026-11-01", "CITY COUNCIL RATES", 300.00),
        ])
        reviews = {"city": {
            "verdict": "bill", "display_name": "City Council Rates",
            "category": "bills", "frequency": "quarterly", "reason": "Rates notice",
        }}
        merged = merge_suggestions([], candidates, reviews)
        assert len(merged) == 1
        assert merged[0]["frequency"] == "quarterly"
        assert merged[0]["amount"] == 300.00
        assert merged[0]["source"] == "ai"
        assert merged[0]["next_due"] == "2027-02-01"

    def test_promotion_without_a_cadence_is_skipped(self):
        candidates = build_candidates([
            txn("2026-08-01", "SOMETHING ODD", 20.00),
            txn("2026-09-20", "SOMETHING ODD", 20.00),
        ])
        reviews = {"something": {
            "verdict": "bill", "display_name": "Something Odd",
            "category": "other", "frequency": None, "reason": "Unclear",
        }}
        assert merge_suggestions([], candidates, reviews) == []


class TestReviewCache:
    def test_round_trip_and_evidence_invalidation(self, db):
        candidates = build_candidates([
            txn("2026-09-01", "BINGE.COM", 16.00),
            txn("2026-10-01", "BINGE.COM", 16.00),
        ])
        reviews = [{
            "merchant_key": "binge", "evidence_hash": candidates[0]["evidence_hash"],
            "bill": True, "name": "Binge", "category": "entertainment",
            "frequency": "monthly", "reason": "Streaming",
        }]
        assert save_reviews(db, reviews) == 1
        cached = cached_reviews(db, candidates)
        assert cached["binge"]["verdict"] == "bill"

        newer = build_candidates([
            txn("2026-09-01", "BINGE.COM", 16.00),
            txn("2026-10-01", "BINGE.COM", 16.00),
            txn("2026-11-01", "BINGE.COM", 16.00),
        ])
        assert cached_reviews(db, newer) == {}
