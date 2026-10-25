# AI bill review and visual pass

- **Date:** 2026-10-25
- **Investigator:** OpenCode (DeepSeek V4.1 Flash)
- **Status:** Merged to `main` (`2d7f437`), deployed to production and staging; production healthy

## 1. What was asked

Improve the visuals, and stop the suggestions page being flooded with things that are
not bills. Use the OpenRouter free endpoint for AI help, with the key configurable
through a Coolify environment variable or the settings page.

## 2. What was checked

- The production suggestions page: 16 suggestions, most from the old
  `banksync_unclassified` JSON cache, including `AMAZON AU MARKETPLACE ... Card xx2287`,
  `Auto Smart Savings debit`, `DEEPINFRA.COM`, and repeated
  `International Transaction Fee, Value date: ...` rows.
- The legacy cache contents and `suggest_from_unclassified()`.
- OpenRouter's public model list: `openrouter/free` is the Free Models Router, price 0.
- A real call to `openrouter/free` with the messy merchants.
- The current Spend, Bills and Suggested Bills pages at desktop and 390 px.

## 3. What the evidence showed

1. **The noise came from the legacy cache.** It stores only `{description, amount}`,
   has no dates, and called any description seen twice "monthly". It cannot tell a
   card authorisation or a fee from a bill.
2. **The rule detector was working.** With the same data it rejected groceries,
   credits, savings and fees, and it only suggests merchants with a real cadence.
3. **The free router can filter well with a strict prompt.** A live call on five
   candidates kept Netflix, Telstra and City Council Rates (promoting the quarterly
   rates pair the rules could not take), and dropped Amazon and DeepInfra. It took
   17.5 seconds.
4. **The charts and suggestion rows were information-dense but flat.** Charts had no
   values, donuts had empty centres, the two product tables duplicated the chart, and
   the inbox was a wall of badges and small text.

## 4. What was changed

### Visual pass

- **Spend:** a plain-language insight strip ("You spent $123 a month on average..."),
  value labels on the monthly and product bars, a total in the doughnut centre,
  budget bars that turn red when a month is over target, and the two exact product
  tables moved into a single disclosure.
- **Bills:** value labels on the weekly bars, a monthly total in the doughnut centre,
  category colour dots beside section headings.
- **Suggested Bills:** a summary strip with the count and monthly total, calmer rows
  with a category dot, confidence as a small indicator, cadence/next-due/category
  chips, and the amount as the focal point. AI-reviewed merchants show the AI reason.
- **Charts:** respect `prefers-reduced-motion`, hide value labels when bars get too
  narrow, and follow the light/dark toggle (already added, extended to the new
  plugins).

### AI review (`services/ai_review.py`)

- Builds candidates from every non-grocery merchant with two or more transactions.
- Excludes income, internal transfers, savings and fees deterministically before any
  AI call.
- Redacts card and account numbers, then sends only descriptions, amounts, dates and
  interval details to OpenRouter.
- The model returns a keep/drop verdict, a clean name, a category and an optional
  frequency. **It never supplies amounts or dates.**
- Cached in the new `ai_reviews` table keyed by a hash of the evidence; new
  transactions invalidate the cache.
- Rule-detected bills keep their rule amount and schedule. AI can rename, recategorise
  or drop them, and can promote a missed bill only when it returns a valid frequency.
- The legacy JSON cache is no longer read by the suggestions page.

### Configuration

- Settings page: optional **AI review** section with an OpenRouter key, model
  (default `openrouter/free`) and a remove-key checkbox.
- Environment fallbacks: `OPENROUTER_API_KEY`, `OPENROUTER_MODEL`.
- The review is manual: a **Review N merchants with AI** button, with a loading state.
  No page load or sync makes an AI call.
- Gunicorn timeout raised from 30s to 90s (`GUNICORN_TIMEOUT`) because a free-model
  review can take longer than the default.

## 5. What was deliberately not changed

- **No automatic AI calls.** Every call is user-triggered, so a slow or unavailable
  free model never delays a page load or a sync.
- **The model is not trusted with money.** Amounts, dates and next-due dates always
  come from the bank data.
- **The legacy cache is still written** for the import-result API, but it no longer
  feeds suggestions. Removing the writer is a separate cleanup.
- **No auto-promotion without a cadence.** A merchant can only become a bill if a
  valid frequency is supplied.
- **Purchase-history suggestions remain** in their separate "not bills" section.

## 6. Verification

- Full suite: **623 passed** (15 new tests).
- New unit tests cover redaction, candidate building, evidence hashing, verdict
  validation, noise filtering, renaming, promotion and cache invalidation.
- New integration tests cover the settings form and the review route with a mocked
  model, including a promotion and a dismissal.
- The tests explicitly clear `OPENROUTER_API_KEY` from the environment so no test can
  call the real API.
- Live free-router check with five realistic candidates: correct keep/drop, 17.5s.
- Visual review with a seeded database at 1280 px and 390 px, with chart canvases read
  directly because headless screenshots catch them mid-animation.
- Staging `9a20462` and production `2d7f437` deployed healthy, `/health` 200 and no
  startup errors. The Settings page on staging confirmed the environment key and
  `openrouter/free`.
- Temporary database, seed script, screenshots and browser sessions removed.

## 7. Open items

- Run the review on real production data and check the model's judgement; free-router
  quality can vary between requests.
- Consider a "Review automatically after BankSync" option once the quality is proven.
- Decide whether to stop writing the legacy `banksync_unclassified` cache and remove
  `suggest_from_unclassified`.
- Production admin login still uses a password that is not the Coolify env value, so
  live verification is done through staging and the user's own session.

## 8. Commands to reproduce

```bash
myenv/Scripts/python.exe -m pytest tests/test_ai_review.py -q
myenv/Scripts/python.exe -m pytest -q
```

Set `OPENROUTER_API_KEY` in the environment or on the settings page, open
`/subscriptions/suggest`, and press **Review with AI**.
