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

    def test_revive_after_limits_a_dismissed_merchant_to_new_rows(self):
        rows = [
            txn("2026-01-01", "BINGE.COM", 16.00),
            txn("2026-02-01", "BINGE.COM", 16.00),
            txn("2026-05-01", "BINGE.COM", 16.00),
            txn("2026-06-01", "BINGE.COM", 16.00),
        ]
        candidates = build_candidates(
            rows, excluded_keys={"binge"},
            revive_after={"binge": "2026-03-01"})
        assert len(candidates) == 1
        assert candidates[0]["occurrences"] == 2
        assert candidates[0]["dates"] == ["2026-05-01", "2026-06-01"]
        assert candidates[0]["revived"] is True

    def test_reviews_each_amount_stream_separately(self):
        rows = []
        for month in range(1, 4):
            rows.append(txn(f"2026-{month:02d}-01", "PAYPAL *BELONG", 90.00))
            rows.append(txn(f"2026-{month:02d}-15", "PAYPAL *BELONG", 25.00))
        candidates = build_candidates(rows)
        assert sorted(c["merchant_key"] for c in candidates) == [
            "belong#1", "belong#2"]
        assert {c["mean_amount"] for c in candidates} == {25.00, 90.00}

    def test_streams_come_before_loose_merchants(self):
        rows = [txn(f"2026-{m:02d}-03", "NETFLIX.COM", 18.99) for m in range(5, 10)]
        rows += [txn("2026-08-01", "CITY COUNCIL RATES", 300.00),
                 txn("2026-11-01", "CITY COUNCIL RATES", 300.00)]
        candidates = build_candidates(rows)
        assert [c["signal"] for c in candidates] == ["merchant", "loose"]
        assert candidates[0]["score"] > 0.75

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


class TestPossibleStreams:
    def rows(self):
        # Gaps of 30, 45 and 30 days: a weak monthly pattern the rules hide.
        return [
            txn("2026-03-01", "ACME STORAGE", 40.00),
            txn("2026-03-31", "ACME STORAGE", 40.00),
            txn("2026-05-15", "ACME STORAGE", 40.00),
            txn("2026-06-14", "ACME STORAGE", 40.00),
        ]

    def test_possible_stream_is_hidden_without_ai(self):
        from services.recurring import detect_recurring
        assert detect_recurring(self.rows()) == []
        candidates = build_candidates(self.rows())
        assert candidates[0]["confidence"] == "possible"

    def test_ai_keeps_a_possible_stream_with_rule_amounts(self):
        candidates = build_candidates(self.rows())
        reviews = {"acme": {"verdict": "bill", "display_name": "Acme Storage",
                            "category": "bills", "frequency": None,
                            "priority": "high", "reason": "Storage unit"}}
        merged = merge_suggestions([], candidates, reviews)
        assert len(merged) == 1
        assert merged[0]["name"] == "Acme Storage"
        assert merged[0]["frequency"] == "monthly"
        assert merged[0]["source"] == "ai"
        assert merged[0]["merchant_key"] == "acme"

    def test_ai_priority_orders_the_list(self):
        detected = [
            {"merchant_key": "a", "stream_key": "a", "name": "Streaming",
             "confidence": "high", "score": 0.9, "monthly_cost": 30.0},
            {"merchant_key": "b", "stream_key": "b", "name": "Insurance",
             "confidence": "medium", "score": 0.6, "monthly_cost": 10.0},
        ]
        reviews = {"a": {"verdict": "bill", "priority": "low"},
                   "b": {"verdict": "bill", "priority": "high"}}
        merged = merge_suggestions(detected, [], reviews)
        assert [m["name"] for m in merged] == ["Insurance", "Streaming"]

    def test_invalid_priority_falls_back_to_normal(self):
        candidates = build_candidates(self.rows())
        reviews = _validate_reviews({"reviews": [
            {"merchant_key": "acme", "bill": True, "name": "Acme",
             "category": "bills", "frequency": None, "priority": "urgent",
             "reason": "x"}]}, candidates)
        assert reviews[0]["priority"] == "normal"


class TestBatchedReview:
    def candidates(self, count):
        return [{"merchant_key": f"m{i}", "evidence_hash": f"h{i}"}
                for i in range(count)]

    def test_splits_into_batches_and_keeps_order_of_results(self, monkeypatch):
        import services.ai_review as ai
        sizes = []

        def fake_batch(batch, api_key, model, timeout, deadline=None):
            sizes.append(len(batch))
            return [{"merchant_key": c["merchant_key"]} for c in batch]

        monkeypatch.setattr(ai, "_review_batch", fake_batch)
        reviews = ai.review_candidates(self.candidates(25), "key")
        assert sorted(sizes) == [5, 10, 10]
        assert [r["merchant_key"] for r in reviews] == [f"m{i}" for i in range(25)]

    def test_keeps_good_batches_when_one_fails(self, monkeypatch):
        import services.ai_review as ai

        def fake_batch(batch, api_key, model, timeout, deadline=None):
            if batch[0]["merchant_key"] == "m10":
                raise ai.AIReviewError("bad JSON")
            return [{"merchant_key": c["merchant_key"]} for c in batch]

        monkeypatch.setattr(ai, "_review_batch", fake_batch)
        reviews = ai.review_candidates(self.candidates(20), "key")
        assert len(reviews) == 10

    def test_raises_when_every_batch_fails(self, monkeypatch):
        import pytest
        import services.ai_review as ai

        def fake_batch(batch, api_key, model, timeout, deadline=None):
            raise ai.AIReviewError("bad JSON")

        monkeypatch.setattr(ai, "_review_batch", fake_batch)
        with pytest.raises(ai.AIReviewError):
            ai.review_candidates(self.candidates(12), "key")


class TestLoosePromotionRecency:
    def test_ai_cannot_promote_a_merchant_that_stopped(self):
        candidates = build_candidates([
            txn("2026-01-16", "WESFARMERSP", 4.00),
            txn("2026-02-16", "WESFARMERSP", 4.00),
            txn("2026-10-01", "BINGE.COM", 16.00),
            txn("2026-10-20", "BINGE.COM", 16.00),
        ])
        reviews = {key: {"verdict": "bill", "display_name": key,
                         "category": "other", "frequency": "monthly",
                         "reason": "x"}
                   for key in ("wesfarmersp", "binge")}
        merged = merge_suggestions([], candidates, reviews)
        assert [m["stream_key"] for m in merged] == ["binge"]


class TestReviewDeadline:
    def test_stops_retrying_when_the_budget_is_spent(self, monkeypatch):
        import time
        import pytest
        import services.ai_review as ai
        calls = []
        monkeypatch.setattr(ai.urllib.request, "urlopen",
                            lambda *a, **k: calls.append(1))
        with pytest.raises(ai.AIReviewError, match="ran out of time"):
            ai._review_batch([{"merchant_key": "x", "descriptions": [],
                               "amounts": [], "dates": [],
                               "median_interval_days": None,
                               "occurrences": 2}],
                             "key", "model", 60, deadline=time.monotonic() + 1)
        assert calls == []


class TestTruncatedReply:
    def test_keeps_complete_reviews_from_a_cut_off_reply(self):
        from services.ai_review import _salvage_reviews
        content = ('\n\n{"reviews":[{"merchant_key":"a","bill":false,"name":"A",'
                   '"category":"other","frequency":null,"reason":"x"},'
                   '{"merchant_key":"b","bill":true,"name":"B","categ')
        assert [r["merchant_key"] for r in _salvage_reviews(content)["reviews"]] == ["a"]

    def test_nothing_salvageable_raises(self):
        import pytest
        from services.ai_review import AIReviewError, _salvage_reviews
        with pytest.raises(AIReviewError):
            _salvage_reviews('{"reviews":[{"merchant_key":"a","bi')


class TestHardDeadline:
    def test_returns_finished_batches_when_one_hangs(self, monkeypatch):
        import threading
        import services.ai_review as ai
        release = threading.Event()

        def fake_batch(batch, api_key, model, timeout, deadline=None):
            if batch[0]["merchant_key"] == "m10":
                release.wait(5)
            return [{"merchant_key": c["merchant_key"]} for c in batch]

        monkeypatch.setattr(ai, "_review_batch", fake_batch)
        monkeypatch.setattr(ai, "REVIEW_BUDGET_SECONDS", 0.5)
        candidates = [{"merchant_key": f"m{i}"} for i in range(20)]
        try:
            reviews = ai.review_candidates(candidates, "key")
        finally:
            release.set()
        assert len(reviews) == 10


class TestSiblingContext:
    def test_streams_carry_other_amounts_at_the_merchant(self):
        rows = []
        for month in range(5, 11):
            rows.append(txn(f"2026-{month:02d}-01", "PAYPAL *BELONG", 95.00))
            rows.append(txn(f"2026-{month:02d}-21", "PAYPAL *BELONG", 25.00))
        by_key = {c["merchant_key"]: c for c in build_candidates(rows)}
        assert by_key["belong#1"]["other_amounts"] == [95.00]
        assert by_key["belong#2"]["other_amounts"] == [25.00]

    def test_prompt_version_is_part_of_the_evidence_hash(self, monkeypatch):
        import services.ai_review as ai
        rows = [txn(f"2026-{m:02d}-03", "NETFLIX.COM", 18.99) for m in range(6, 11)]
        before = build_candidates(rows)[0]["evidence_hash"]
        monkeypatch.setattr(ai, "PROMPT_VERSION", ai.PROMPT_VERSION + 1)
        assert build_candidates(rows)[0]["evidence_hash"] != before


class TestLateBatches:
    def test_late_batch_is_delivered_when_it_finishes(self, monkeypatch):
        import threading
        import services.ai_review as ai
        release = threading.Event()
        delivered = []
        got_late = threading.Event()

        def fake_batch(batch, api_key, model, timeout, deadline=None):
            if batch[0]["merchant_key"] == "m10":
                release.wait(5)
            return [{"merchant_key": c["merchant_key"]} for c in batch]

        def on_late(reviews):
            delivered.extend(reviews)
            got_late.set()

        monkeypatch.setattr(ai, "_review_batch", fake_batch)
        monkeypatch.setattr(ai, "REVIEW_BUDGET_SECONDS", 0.5)
        candidates = [{"merchant_key": f"m{i}"} for i in range(20)]
        reviews = ai.review_candidates(candidates, "key", on_late=on_late)
        assert len(reviews) == 10 and delivered == []
        release.set()
        assert got_late.wait(5)
        assert [r["merchant_key"] for r in delivered] == [f"m{i}" for i in range(10, 20)]


# ---------------------------------------------------------------------------
# Recurring spending (no fixed date or amount) and user overrides
# ---------------------------------------------------------------------------

from services.ai_review import _SYSTEM_PROMPT, override_review, recurring_spend  # noqa: E402

COFFEE = [txn(f"2026-10-{d:02d}", "BERMUDA COFFEE STUD", a)
          for d, a in ((1, 5.5), (3, 6.0), (6, 5.5), (8, 11.0), (10, 5.5), (13, 6.0), (15, 5.5))]


class TestRecurringType:
    def test_prompt_treats_fuel_cafes_and_usage_as_recurring(self):
        recurring = _SYSTEM_PROMPT.split('- "recurring"')[1].split('- "noise"')[0]
        for word in ("Fuel", "cafe visits", "OpenAI", "parking"):
            assert word in recurring
        assert "Reclaim.ai" in _SYSTEM_PROMPT.split('- "subscription"')[1].split('- "bill"')[0]

    def test_validate_reads_type_and_falls_back_to_bill_flag(self):
        candidates = build_candidates(COFFEE)
        key = candidates[0]["merchant_key"]
        cleaned = _validate_reviews({"reviews": [{"merchant_key": key, "type": "recurring", "name": "Coffee"}]},
                                    candidates)
        assert cleaned[0]["type"] == "recurring" and cleaned[0]["bill"] is True
        legacy = _validate_reviews({"reviews": [{"merchant_key": key, "bill": False}]}, candidates)
        assert legacy[0]["type"] == "noise"
        assert _validate_reviews({"reviews": [{"merchant_key": key, "type": "maybe"}]}, candidates) == []

    def test_recurring_saved_and_promoted_with_spend_per_period(self, db):
        candidates = build_candidates(COFFEE)
        key = candidates[0]["merchant_key"]
        save_reviews(db, [{"merchant_key": key, "evidence_hash": candidates[0]["evidence_hash"], "bill": True,
                           "type": "recurring", "name": "Bermuda Coffee", "category": "other",
                           "frequency": None, "reason": "Regular cafe spending"}])
        reviews = cached_reviews(db, candidates)
        assert reviews[key]["verdict"] == "recurring"
        merged = merge_suggestions([], candidates, reviews)
        assert len(merged) == 1
        coffee = merged[0]
        assert coffee["kind"] == "recurring" and coffee["frequency"] == "weekly"
        # $45 over 16 days (14 days + one usual 2-day gap) is about $19.69 a week.
        assert coffee["amount"] == 19.69
        assert coffee["per_visit"] == candidates[0]["mean_amount"]

    def test_recurring_spend_that_stopped_is_dropped(self):
        candidate = build_candidates(COFFEE)[0]
        from datetime import date
        assert recurring_spend(candidate, date(2026, 12, 1)) is None
        assert recurring_spend(candidate, date(2026, 10, 16)) is not None


class TestOverride:
    def test_user_override_wins_and_survives_new_evidence(self, db):
        candidates = build_candidates(COFFEE)
        key = candidates[0]["merchant_key"]
        save_reviews(db, [{"merchant_key": key, "evidence_hash": candidates[0]["evidence_hash"], "bill": False,
                           "type": "noise", "name": "Coffee", "category": "other", "frequency": None,
                           "reason": "Cafe"}])
        override_review(db, key, "recurring")
        assert cached_reviews(db, candidates)[key]["verdict"] == "recurring"
        newer = build_candidates(COFFEE + [txn("2026-10-17", "BERMUDA COFFEE STUD", 5.5)])
        assert cached_reviews(db, newer)[key]["verdict"] == "recurring"
        # A fresh AI verdict doesn't wipe the user's call.
        save_reviews(db, [{"merchant_key": key, "evidence_hash": newer[0]["evidence_hash"], "bill": False,
                           "type": "noise", "name": "Coffee", "category": "other", "frequency": None,
                           "reason": "Cafe"}])
        assert cached_reviews(db, newer)[key]["verdict"] == "recurring"

    def test_unknown_verdict_rejected(self, db):
        import pytest
        with pytest.raises(ValueError):
            override_review(db, "x", "maybe")


def test_override_route_and_accepting_stores_kind(app, client, db):
    from conftest import login
    login(client)
    for row in COFFEE:
        db.execute("INSERT INTO bank_transactions (external_id, txn_date, description, merchant_key, amount)"
                   " VALUES (?, ?, ?, ?, ?)", (row["txn_date"], row["txn_date"], row["description"],
                                               row["merchant_key"], row["amount"]))
    db.commit()
    with app.app_context():
        candidates = build_candidates([dict(r) for r in COFFEE])
    key = candidates[0]["merchant_key"]
    save_reviews(db, [{"merchant_key": key, "evidence_hash": candidates[0]["evidence_hash"], "bill": False,
                       "type": "noise", "name": "Bermuda Coffee", "category": "other", "frequency": None,
                       "reason": "Cafe"}])
    page = client.get("/subscriptions/suggest").get_data(as_text=True)
    assert "It&#39;s recurring" in page or "It's recurring" in page
    page = client.post("/subscriptions/suggest/override", data={"merchant_key": key, "verdict": "recurring"},
                       headers={"HX-Request": "true"}).get_data(as_text=True)
    assert "recurring, no fixed date" in page
    client.post("/subscriptions/suggest/accept", data={"name": "Bermuda Coffee", "amount": "19.69",
                                                       "frequency": "weekly", "category": "other",
                                                       "merchant_key": key, "kind": "recurring"})
    assert db.execute("SELECT kind FROM subscriptions").fetchone()["kind"] == "recurring"
    assert "recurring</span>" in client.get("/subscriptions").get_data(as_text=True)


class TestCoffeeSubscription:
    MONTHLY = [txn(d, "BERMUDA COFFEE STUD", 48.0) for d in ("2026-07-03", "2026-08-03", "2026-09-03", "2026-10-03")]

    def test_prompt_says_monthly_coffee_is_a_subscription(self):
        subscription = _SYSTEM_PROMPT.split('- "subscription"')[1].split('- "bill"')[0]
        assert "coffee bean subscription" in subscription

    def test_recurring_verdict_on_a_fixed_monthly_amount_becomes_a_subscription(self, db):
        candidates = build_candidates(self.MONTHLY)
        key = candidates[0]["merchant_key"]
        save_reviews(db, [{"merchant_key": key, "evidence_hash": candidates[0]["evidence_hash"], "bill": True,
                           "type": "recurring", "name": "Bermuda Coffee", "category": "other",
                           "frequency": None, "reason": "Cafe"}])
        merged = merge_suggestions([], candidates, cached_reviews(db, candidates))
        assert len(merged) == 1
        assert merged[0]["kind"] == "subscription"
        assert merged[0]["frequency"] == "monthly" and merged[0]["amount"] == 48.0

    def test_user_choice_of_recurring_is_respected(self, db):
        candidates = build_candidates(self.MONTHLY)
        key = candidates[0]["merchant_key"]
        save_reviews(db, [{"merchant_key": key, "evidence_hash": candidates[0]["evidence_hash"], "bill": True,
                           "type": "subscription", "name": "Bermuda Coffee", "category": "other",
                           "frequency": "monthly", "reason": "Plan"}])
        override_review(db, key, "recurring")
        merged = merge_suggestions([], candidates, cached_reviews(db, candidates))
        assert merged[0]["kind"] == "recurring"
