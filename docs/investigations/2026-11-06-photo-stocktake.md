# Photo stocktake experiment

- **Date:** 2026-11-06
- **Investigator:** Claude Code (for Deej)
- **Status:** Shipped to production (44ee13c). Real-world accuracy not yet measured.

## 1. What was asked

Step 4 of the meal and grocery plan: try a stock count from photos ("photos if reliable"). It has to show
whether photo counts are reliable before anything relies on them.

## 2. What was checked

- The app already calls OpenRouter for bill and product reviews (`services/ai_review.py`,
  `request_chat_json`), with the key in Settings or `OPENROUTER_API_KEY`. Production has a key set.
- No Anthropic key exists in `krasus/.env`; an OpenRouter key does.
- OpenRouter's model list (`/api/v1/models`, 2026-11-06): `anthropic/claude-opus-5.5` accepts images at
  USD $4 / $20 per million tokens.
- Recipe image upload already checks file signatures and a 5 MB cap.

## 3. What the evidence showed

- Reusing OpenRouter needs no new secret or provider, and matches the existing code.
- Opus 5.5 rejects sampling settings, and `request_chat_json` always sent `temperature: 0.1`.
- Phone photos are often over 5 MB, and the app has no image library to shrink them on the server.
- Live smoke test with a generated shelf image (3 long-life milks, 2 Tim Tams; bananas listed but not
  shown): the model returned milk 3 (high), Tim Tams 2 (high), no bananas, in 5.3 s. This proves the request
  and parsing work. It says nothing about real cupboards.

## 4. What was changed (PRs #136, #137)

- `services/photo_count.py` (new): numbered item list prompt, images as data URLs, `parse_counts`
  (drops unknown items and negative or non-numeric counts; adds an item listed twice), `accuracy`
  (exact, within one, mean error, biggest misses), `image_type` signature check. Default model
  `anthropic/claude-opus-5.5`, overridable by the `photo_count_model` setting or `PHOTO_COUNT_MODEL`.
- `services/ai_review.py`: `request_chat_json` accepts a list of content parts and `temperature=None`.
- `database.py`: `photo_counts(item_key, counted_on, quantity, confidence, seen_as, model)`, one row per
  item per day (a re-run replaces it).
- `routes/pantry.py`: `POST /pantry/stock-count/photo` (up to 4 photos, 5 MB each, JPG/PNG/WEBP). The
  stock count page shows each guess beside its count box and a score of photo versus hand counts.
- `templates/pantry/stock_count.html`: collapsible "Try counting from photos" panel; a small script shrinks
  photos to 2000 px JPEG in the browser before upload.
- Tests: `tests/test_photo_count.py` (8). Full suite 1038 passed. CI and the staging check passed.

## 5. What was deliberately not changed

- **Guesses never fill the count boxes.** If they did, a hand count would just confirm the guess and
  the score would be meaningless. Prefilling can come once the score shows photos are reliable.
- **Photos are not stored**, only the guesses.
- **No Anthropic SDK or new provider.** The project already uses OpenRouter over `urllib`.
- **No server-side resizing** (no new dependency). Browsers without canvas support must send photos under 5 MB.

## 6. Verification

- Unit and route tests pass with the model stubbed, including: a guess is never written to `stock_counts`; a
  failed request saves nothing; the request omits `temperature`.
- Live OpenRouter call with the generated image: exact.
- Production page (logged-in Chrome): the photo form renders, the key is detected, 56 items are listed.
  No photo was uploaded in production.

## 7. Open items

- Deej: on the Sunday count, upload photos first, then count by hand and save. After two or three Sundays
  the panel's score shows whether photos can be trusted (aim: 80%+ exact on high-confidence guesses).
- If reliable: offer "fill in the photo counts" for high-confidence guesses.
- The list still has look-alike products, e.g. two "Woolworths peach in strawberry jelly 4 pack" rows (one
  with "pdm"), which also split usage. Worth linking both to one ingredient.

## 8. Commands to reproduce

```bash
python -m pytest tests/test_photo_count.py -q
python -m pytest -q
curl -s https://groceries.deejpotter.com/health
```
