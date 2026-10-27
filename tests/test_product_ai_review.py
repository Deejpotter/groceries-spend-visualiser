"""Tests for services/product_ai_review.py with the model call mocked."""

import services.product_ai_review as par

INGREDIENTS = [{"id": 1, "name": "Milk"}, {"id": 2, "name": "Rice"}]


def test_candidates_skip_linked_confident_nonfood_and_cached(db):
    rows = [{"product_name": "Woolworths Milk 2L"}, {"product_name": "SunRice Jasmine 5kg"},
            {"product_name": "Finish Dishwasher Tablets"}, {"product_name": "Mystery Thing", "ingredient_id": 2},
            {"product_name": "Kewpie Mayo"}]
    db.execute("INSERT INTO product_ai_reviews (product_name, evidence_hash) VALUES (?, ?)",
               ("Kewpie Mayo", par._hash("Kewpie Mayo", INGREDIENTS)))
    found = [c["product_name"] for c in par.product_candidates(db, rows, INGREDIENTS, {})]
    assert found == ["SunRice Jasmine 5kg"]


def test_review_validates_ids_and_roles(monkeypatch):
    reply = {"products": [
        {"product_name": "SunRice Jasmine 5kg", "ingredient_id": 2, "role": "food", "reason": "rice"},
        {"product_name": "Odd", "ingredient_id": 99, "role": "rocket", "reason": "?"},
        {"product_name": "Not asked", "ingredient_id": 1},
    ]}
    monkeypatch.setattr(par, "request_chat_json", lambda *a, **k: reply)
    candidates = [{"product_name": "SunRice Jasmine 5kg", "evidence_hash": "h1"},
                  {"product_name": "Odd", "evidence_hash": "h2"}]
    reviews = par.review_products(candidates, INGREDIENTS, "key", "model")
    assert reviews[0]["ingredient_id"] == 2
    assert reviews[1]["ingredient_id"] is None and reviews[1]["role"] is None
    assert len(reviews) == 2


def test_failed_batches_raise_only_when_nothing_worked(monkeypatch):
    from services.ai_review import AIReviewError

    def boom(*a, **k):
        raise AIReviewError("down")
    monkeypatch.setattr(par, "request_chat_json", boom)
    try:
        par.review_products([{"product_name": "X", "evidence_hash": "h"}], INGREDIENTS, "k", "m")
        assert False, "expected an error"
    except AIReviewError:
        pass


def test_saved_reviews_become_suggestions(app, client, db, monkeypatch):
    from conftest import login
    login(client)
    db.execute("INSERT INTO ingredients (id, name, unit) VALUES (2, 'Rice', 'kg')")
    db.execute("INSERT INTO purchases (order_date, basket_id, product_name, line_total) VALUES"
               " ('2026-10-01', 'b1', 'SunRice Jasmine 5kg', 12)")
    db.execute("INSERT INTO settings (key, value) VALUES ('openrouter_api_key', 'k')")
    db.commit()
    monkeypatch.setattr(par, "request_chat_json", lambda *a, **k: {"products": [
        {"product_name": "SunRice Jasmine 5kg", "ingredient_id": 2, "role": "food", "reason": "it is rice"}]})
    page = client.post("/spend/products/ai-review", follow_redirects=True).get_data(as_text=True)
    assert "AI suggestion" in page and "it is rice" in page
