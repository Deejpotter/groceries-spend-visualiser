# Multi-stream bills and in-page actions

- **Date:** 2026-10-25
- **Investigator:** OpenCode (DeepSeek V4.1 Flash)
- **Status:** Implemented on `dev`, full suite green (650 passed)

## 1. What was asked

- Add and dismiss suggestions asynchronously, without leaving the page.
- Treat `$90 on the 1st` and `$25 mid-month` from the same provider as two bills, not one averaged payment.
- A dismissed merchant should return if a new regular pattern starts (three payments after the dismissal).

## 2. What was checked

- The production suggestion: `Paypal Belong ...`, 24 payments, about 16 days apart, average $60, $130/month.
- The real pattern: $90 on the 1st for internet and $25 around the 15th for phone, both monthly.
- The Add/Dismiss forms: normal POST plus redirect, so every action reloaded and lost the triage position.
- `merchant_decisions`: a dismissal excluded the merchant forever, regardless of later payments.
- Price history: one subscription per merchant key, so two streams could not have separate histories.

## 3. What the evidence showed

1. The merchant grouping was right, but the **detection assumed one amount per merchant**. Averaging 90 and 25 created a false fortnightly $60 bill and a meaningless 280% price change.
2. **Dismissal was permanent.** A resumed service could never return automatically.
3. **Every add/dismiss was a page navigation**, which is painful with 30 suggestions.
4. Price history keyed only by merchant would mix both Belong streams.

## 4. What was changed

### Amount streams

- `split_amount_clusters()` sorts a merchant's payments by amount and splits when the gap exceeds 25% or $5 (whichever is larger). A normal price rise stays in one stream.
- Each stream gets a stable `stream_key` (`belong#1`, `belong#2`, ordered by amount) and `stream_amount`.
- If every split stream fails detection, the merchant falls back to one variable bill only when the amounts are close (max/min under 1.6). `$90` vs `$25` cannot fall back into one bill.
- Names for a split merchant include the amount: `Belong (90.00)` and `Belong (25.00)`.
- Display names now strip `PAYPAL *`, card numbers, "Value date", and currency/location noise.
- `Belong` added to the known Australian merchants.

### Persistent stream identity

- `subscriptions` gained `stream_key` and `stream_amount`; existing rows backfill `stream_key = merchant_key`.
- `merchant_decisions` stores the stream key in its existing `merchant_key` column.
- Accepting a stream stores the base merchant key for price history, plus the stream key and amount for identity.
- Price history filters a stream by `stream_amount` (±35%, minimum $5), so the $90 and $25 charts stay separate.
- AI candidates skip merchants with multiple streams; the rule split handles them, and sending the combined merchant to the model would average them again.

### Dismissal revival

- `detect_recurring()` and `build_candidates()` accept `revive_after`, a map of stream key to dismissal date.
- A dismissed stream only considers payments **after** the dismissal. Three payments in a valid pattern (two for yearly) make it a suggestion again, with a `new pattern since` chip.
- Dismissing the same stream again resets its `created_at`, so the next revival starts from the new dismissal.

### Async actions

- The suggestions content is wrapped in `#suggest-content`.
- Add, Dismiss and Restore use HTMX `hx-post` against `#suggest-content`, with `hx-select` and `outerHTML`. The server re-renders the panel and returns it; the URL and scroll stay put.
- The panel shows an inline notice ("Added ... to Bills", "Dismissed ...", "Suggestion restored") or an inline error.
- Non-JS users still get the original POST + redirect.
- The backfill and AI review forms remain normal submissions because they are long-running.

## 5. What was deliberately not changed

- **AI review does not split streams.** It skips multi-stream merchants and lets the rule detector do it deterministically. Naming each stream with AI is a future improvement.
- **No automatic recency cutoff.** A bill that stopped months ago still appears until dismissed; the user's scenario is handled by dismissal + revival.
- **Stream keys are ordinal, not amount-hashed.** A large price change that reorders streams could change `#1`/`#2`; amounts are stable enough for normal price rises.
- **No automatic price updates**; still a deliberate action.

## 6. Verification

- Full suite: **650 passed** (9 new tests).
- Belong case: 12 × $90 on the 1st and 12 × $25 on the 15th produce two monthly bills, not one fortnightly bill.
- Adding the $90 stream leaves the $25 suggestion in place; dismissing one stream leaves the other; price history shows $90 only.
- Dismissal revival: three payments after the cutoff bring the merchant back; two do not.
- Browser check: Add, Dismiss and Restore each updated the panel in place, with the URL unchanged, the notice shown and the summary count/total updated.
- Temporary database, seed, screenshot and browser sessions removed.

## 7. Open items

- Run the backfill and AI review on production data (the separate task).
- Consider AI-assisted naming for split streams ("Belong Internet" vs "Belong Mobile").
- Consider a recency window for bills that have clearly ended.
- Stream-key ordinal stability after a large price change.

## 8. Commands to reproduce

```bash
myenv/Scripts/python.exe -m pytest tests/test_recurring.py tests/test_integration.py::TestSubscriptions -q
myenv/Scripts/python.exe -m pytest -q
```

Seed a merchant with two monthly amounts, open `/subscriptions/suggest`, and add one stream.
