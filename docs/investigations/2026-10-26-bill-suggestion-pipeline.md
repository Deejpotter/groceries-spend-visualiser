# Bill suggestion pipeline: one grouping, scored signals, AI on top

- **Date:** 2026-10-26
- **Investigator:** Claude Code (Opus 5.5)
- **Status:** In progress on `dev`

## 1. What was asked

Suggestions on `/subscriptions/suggest` should be much better and should catch
bills Deej doesn't notice. Deej suggested a fuzzy algorithm plus an AI pass that
filters and sorts. Follow-up: the same amount on the same day each month is a
signal even when the description changes. Deej asked for the pipeline to be
planned properly rather than patched signal by signal.

## 2. What was checked

- `services/recurring.py` (`detect_recurring`, `_suggestion_for_rows`)
- `services/ai_review.py` (`build_candidates`, `review_candidates`, `merge_suggestions`)
- `routes/subscriptions.py` (`_suggest_context`, `subscription_ai_review`)
- Production screenshot of the suggestions page (3 suggestions, wrong names)

## 3. What the evidence showed

1. **Grouping key was too coarse.** `PAYPAL BELONG…` and `PAYPAL CLOUDFLARE…` both
   keyed to `paypal`, so they were averaged into a fake $60 fortnightly bill with
   a "280% rise". Fixed in `618ce72` (boilerplate stripping, fresh keys).
2. **Detection is a chain of hard gates.** Count ≥3, median on a cadence, ≥50% of
   intervals in tolerance, variance under a limit. A weak but real pattern fails
   one gate and disappears; a noisy one that scrapes past every gate looks the
   same as a perfect one. Adding a signal (missed months) loosened a gate and let
   `CAFE ONE/TWO/THREE` (gaps 46, 23 days) through.
3. **Only one signal is used: same merchant key.** Same amount on the same day of
   the month under different descriptions is never considered.
4. **The AI step builds its own grouping.** `build_candidates` re-filters and
   re-groups transactions, skips any merchant with two amount streams, and caches
   verdicts by `merchant_key`, while `merge_suggestions` looks them up by
   `stream_key`. The model never sees the rule detector's evidence or confidence.
5. **The AI filters but doesn't sort.** Order is confidence then monthly cost.

## 4. Plan

### Stage 1: clean (one place)
`spend_rows(transactions)` drops credits, income, fees, transfers and grocery
rows, and returns `{date, amount, description, key}`. Both modules use it.

### Stage 2: group into streams (several signals, one list)
- **Merchant streams:** fuzzy-merged merchant key, split by amount cluster (as now).
- **Amount streams:** the same exact amount (≥$5) in ≥3 different months, on days
  of the month within 3 days of each other, under ≥2 merchant keys. Dropped when
  most of its rows already belong to a scored merchant stream.

Each stream carries `signal` (`merchant` / `amount`) and its rows.

### Stage 3: score, don't gate
`score_stream(rows)` computes features:
- `timing_error`: mean distance of each interval from the nearest whole multiple
  of the cadence (a skipped month is k=2), divided by the cadence.
  Extra cycles skipped carry a small penalty.
- `amount_cv`: spread of amounts (utilities get a looser scale).
- `cycles`: number of sightings.
- `day_spread`: day-of-month spread for monthly and longer cadences.
- `known`: matches the known-merchant list.

The score (0–1) is a weighted sum. ≥0.75 is high, ≥0.5 medium, ≥0.35 "possible".
Hard excludes stay hard (income, fees, transfers, groceries, fewer than 2 rows,
no cadence).

### Stage 4: AI reviews the streams
- The candidates *are* the scored streams (high, medium and possible), sorted by
  score and capped at 40. The model sees the evidence: descriptions (redacted),
  amounts, dates, cadence, score and signal.
- The model returns `bill`, `name`, `category`, `priority` (`high` / `normal` /
  `low`) and `reason`. Amounts, dates and cadence always come from the rules.
- Verdicts are cached by `stream_key` (the same key `merge_suggestions` uses).
- "Possible" streams are shown only when the AI keeps them. That is how weak
  patterns Deej hasn't noticed get through without flooding the page.

### Stage 5: present
Sort by AI priority, then score, then monthly cost. The reason line names the
signal, e.g. "same $49.95 around the 3rd under 3 descriptions".

## 5. Deliberately not changed
- Schema: `ai_reviews.merchant_key` stores the stream key. The column name is
  historical, and a migration isn't worth it.
- The accept and dismiss flows and `merchant_decisions`.
- The model choice (`openrouter/free`).

## 6. Verification
- Unit tests for each signal, the scoring edge cases (cafe gaps, skipped month,
  month-end wrap) and AI merge by stream key.
- Full pytest suite.
- Staging check of `/subscriptions/suggest` after deploy.

## 7. Open items
- Calibrate the score weights against real bank history on staging.
