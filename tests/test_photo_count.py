"""Experimental photo stock counts: prompt, parsing, scoring and the upload route."""

import io
import json
from datetime import date

from conftest import login
from services.photo_count import (
    accuracy, build_content, image_type, item_list, make_photo_pair, parse_counts,
)
from tests.test_usage import MILK, TODAY

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64
ROWS = [{"key": "ing:15", "label": "Milk", "products": ["Devondale Long Life Milk 1L"]},
        {"key": "prod:tim tams", "label": "Tim Tams", "products": ["Tim Tams"]}]


def test_image_type_reads_the_signature():
    assert image_type(JPEG) == "image/jpeg"
    assert image_type(b"\x89PNG\r\n\x1a\nrest") == "image/png"
    assert image_type(b"RIFF1234WEBPVP8 ") == "image/webp"
    assert image_type(b"%PDF-1.7") is None


def test_prompt_numbers_items_and_attaches_photos():
    items = item_list(ROWS)
    assert [(i["number"], i["product"]) for i in items] == [(1, "Devondale Long Life Milk 1L"), (2, None)]
    content = build_content(items, [("image/jpeg", JPEG)])
    assert "1. Milk (usually bought as: Devondale Long Life Milk 1L)" in content[0]["text"]
    assert "2. Tim Tams\n" in content[0]["text"]
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_parse_counts_keeps_valid_guesses_only():
    payload = {"counts": [
        {"item": 1, "count": 3, "confidence": "high", "seen_as": "3 cartons"},
        {"item": 1, "count": 1, "confidence": "medium"},  # second photo
        {"item": 2, "count": -1},
        {"item": 9, "count": 2},
        {"item": "two", "count": 2},
        {"item": 2, "count": "lots"},
    ]}
    assert parse_counts(payload, item_list(ROWS)) == {
        "ing:15": {"count": 4.0, "confidence": "medium", "seen_as": ""}}


def test_accuracy_scores_photo_against_hand_count():
    assert accuracy([]) is None
    score = accuracy([make_photo_pair(photo=2, counted=2), make_photo_pair("Bread", 1, 2),
                      make_photo_pair("Tim Tams", 5, 2)])
    assert (score["compared"], score["exact"], score["within_one"], score["exact_pct"]) == (3, 1, 2, 33)
    assert score["mean_error"] == 1.33
    assert [m["label"] for m in score["misses"]] == ["Tim Tams", "Bread"]


def _setup(client, db, monkeypatch):
    login(client)
    db.execute("INSERT INTO ingredients (id, name, category, unit) VALUES (15, 'Milk', 'dairy', 'L')")
    for row in MILK:
        db.execute("INSERT INTO purchases (order_date, basket_id, store, product_name, quantity, line_total, ingredient_id)"
                   " VALUES (?, ?, 'Woolworths', ?, ?, ?, 15)",
                   (row["order_date"], row["basket_id"], row["product_name"], row["quantity"], row["quantity"] * 2))
    db.commit()
    monkeypatch.setattr("routes.pantry.local_today", lambda: TODAY)


def test_photo_route_needs_a_key(client, db, monkeypatch):
    _setup(client, db, monkeypatch)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert b"Needs an OpenRouter key" in client.get("/pantry/stock-count").data
    resp = client.post("/pantry/stock-count/photo", data={"photos": (io.BytesIO(JPEG), "a.jpg")},
                       content_type="multipart/form-data", follow_redirects=True)
    assert b"need an OpenRouter key" in resp.data


def test_photo_guess_is_shown_then_scored_against_the_hand_count(client, db, monkeypatch):
    _setup(client, db, monkeypatch)
    db.execute("INSERT INTO settings (key, value) VALUES ('openrouter_api_key', 'test-key')")
    db.commit()
    sent = {}

    def fake_request(system_prompt, user_prompt, api_key, model, **kwargs):
        sent.update(prompt=user_prompt, model=model, kwargs=kwargs)
        return {"counts": [{"item": 1, "count": 3, "confidence": "high", "seen_as": "3 cartons"}]}

    monkeypatch.setattr("services.photo_count.request_chat_json", fake_request)
    resp = client.post("/pantry/stock-count/photo",
                       data={"photos": [(io.BytesIO(JPEG), "a.jpg"), (io.BytesIO(b"not an image"), "b.jpg")]},
                       content_type="multipart/form-data", follow_redirects=True)
    assert b"b.jpg was skipped" in resp.data
    assert b"The photos show 1 tracked item" in resp.data
    assert b"photo: 3.0 (high)" in resp.data
    assert len(sent["prompt"]) == 2 and sent["kwargs"]["temperature"] is None
    assert sent["model"] == "anthropic/claude-opus-5.5"
    assert not db.execute("SELECT 1 FROM stock_counts").fetchone()  # a guess is never a count

    client.post("/pantry/stock-count", data={"item_key": "ing:15", "count": "4", "max_on_hand": "", "keep_days": ""})
    page = client.get("/pantry/stock-count").data
    assert b"0 of 1 counts exact" in page and b"photo said 3.0, you counted 4.0" in page


def test_failed_request_is_reported(client, db, monkeypatch):
    from services.ai_review import AIReviewError
    _setup(client, db, monkeypatch)
    db.execute("INSERT INTO settings (key, value) VALUES ('openrouter_api_key', 'test-key')")
    db.commit()

    def boom(*args, **kwargs):
        raise AIReviewError("OpenRouter returned HTTP 400.")

    monkeypatch.setattr("services.photo_count.request_chat_json", boom)
    resp = client.post("/pantry/stock-count/photo", data={"photos": (io.BytesIO(JPEG), "a.jpg")},
                       content_type="multipart/form-data", follow_redirects=True)
    assert b"Photo count failed: OpenRouter returned HTTP 400." in resp.data
    assert not db.execute("SELECT 1 FROM photo_counts").fetchone()


def test_request_body_omits_temperature_when_asked(monkeypatch):
    from services import ai_review
    bodies = []

    class Reply(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_open(request, timeout):
        bodies.append(json.loads(request.data))
        return Reply(json.dumps({"choices": [{"message": {"content": '{"counts": []}'}}]}).encode())

    monkeypatch.setattr(ai_review.urllib.request, "urlopen", fake_open)
    ai_review.request_chat_json("sys", [{"type": "text", "text": "hi"}], "k", "m", temperature=None)
    ai_review.request_chat_json("sys", "hi", "k", "m")
    assert "temperature" not in bodies[0] and bodies[1]["temperature"] == 0.1
    assert bodies[0]["messages"][1]["content"] == [{"type": "text", "text": "hi"}]
