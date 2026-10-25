# Subscription price history

- **Date:** 2026-10-25
- **Investigator:** OpenCode (DeepSeek V4.1 Flash)
- **Status:** Implemented on `dev`, full suite green (633 passed)

## 1. What was asked

Show the price of a subscription over time, not just its current amount.

## 2. What was checked

- `bank_transactions` already stores every dated payment with a merchant key.
- The Bills list had no trend information; the detail page did not exist.
- Detected suggestions carried only the mean amount, so a price rise was invisible
  before accepting.

## 3. What the evidence showed

- The data needed for price history was already present; no new ingestion was
  required.
- A bill linked to a merchant (`subscriptions.merchant_key`) can be joined to its
  payments. Manually added bills have no link and therefore no history.
- The existing transaction amount is the current price, not necessarily the latest
  payment. Showing both is useful, and the latest payment should be adoptable in
  one click.

## 4. What was changed

### Service

- New `services/price_history.py`:
  - `group_price_history()` builds dated points per merchant from spend rows only.
  - `summarise()` gives first/latest, change, percentage, direction and first date.
  - `payment_rows()` annotates each payment with its change from the previous one.
- `detect_recurring()` now returns `first_amount`, `latest_amount`, `price_change`,
  `price_pct` and `price_direction`.
- AI-promoted suggestions carry the same fields from the candidate evidence.

### Bills list

- Each amount cell shows a small sparkline and a percentage change since the first
  payment (`▲ 10.5%` in red, `▼` in green, or "steady").
- Bill names link to the new detail page.

### Bill detail page (`/subscriptions/<id>`)

- Current price, price change since the first payment, payments tracked, and the
  latest payment when it differs from the stored amount.
- A **Price over time** line chart with linear segments, tooltips, theme support and
  reduced-motion handling.
- **Use this price** updates the stored amount to the latest observed payment.
- A payment history table with the change on each row.
- A clear empty state when the bill has no linked merchant or no matched payments.

### Suggestions

- Each detected or AI-promoted bill shows a price chip: up, down, or "price steady",
  including the first-seen date.

## 5. What was deliberately not changed

- **No automatic price updates.** Adopting the latest price is a deliberate click;
  a one-off anomaly should not silently rewrite a budget.
- **No synthetic history.** Only real matched payments appear; manual bills without a
  merchant link show an empty state.
- **No smoothing.** The detail chart uses straight segments so a step is a real price
  change, not an interpolation artifact.
- **Frequency stays as set.** A price change does not alter the billing cadence.

## 6. Verification

- Full suite: **633 passed** (10 new tests).
- Unit tests cover grouping, credits exclusion, up/down/flat summaries, payment
  annotations, and detection price fields.
- Integration tests cover the detail page, the list sparkline, the empty state, and
  **Use this price** updating the amount to the latest payment.
- Visual check with a seeded database: a flat Netflix line, then a second run with a
  $20.99 final payment showing +$2.00 (10.5%), the latest-payment tile and the use
  button.
- Temporary database and screenshots removed.

## 7. Open items

- Backfill more bank history to make the charts meaningful; a single recent import
  produces one or two points.
- Consider a monthly cost history (amount × frequency) alongside the raw price.
- The production admin password still differs from the Coolify env, so live checks
  are done on staging and by the user.

## 8. Commands to reproduce

```bash
myenv/Scripts/python.exe -m pytest tests/test_price_history.py tests/test_integration.py::TestSubscriptions -q
myenv/Scripts/python.exe -m pytest -q
```

Link a subscription to a merchant, open `/subscriptions/<id>`, and inspect **Price
over time**.
