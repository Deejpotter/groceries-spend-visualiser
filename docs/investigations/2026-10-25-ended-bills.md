# Suggest removing bills that have stopped

- **Date:** 2026-10-25
- **Investigator:** OpenCode (DeepSeek V4.1 Flash)
- **Status:** Implemented on `dev`, full suite green (659 passed)

## 1. What was asked

If a subscription or bill has not happened for three or more expected repeats, suggest removing it.

## 2. What was checked

- Every subscription has a frequency, a next-due date and, when it came from a suggestion, a linked merchant stream.
- Bank transactions carry the actual payment dates, so missed cycles can be counted from the last real payment.
- The Bills page currently counts every active subscription in the monthly total, donut and upcoming-weeks chart, even if it appears to have stopped.

## 3. What the evidence showed

- A linked bill can be checked without guessing: filter its transactions to its amount stream, take the last payment, and advance by its frequency.
- Three missed cycles is a reasonable threshold; two is often just a late payment.
- A bill that has stopped should not inflate the current budget while the user decides.
- The user needs an escape hatch for bills paid by cash or another account: "Keep tracking" must suppress the prompt without deleting the bill.

## 4. What was changed

### Health check

- New `services/subscription_health.py`:
  - `missed_cycles(last_seen, frequency, today)` advances by calendar cycles (monthly keeps the same day of month).
  - `ended_subscriptions(subscriptions, threshold=3)` flags active bills with a linked price stream and at least three missed payments.
  - Inactive bills and bills with no bank history are skipped.
  - Sorted by most missed first.
- `subscriptions.ended_dismissed_at` added by migration.

### Bills page

- New **Possibly ended** card between the charts and the category lists.
- Each row shows the bill, last payment date and how many payments were missed, with **Remove** and **Keep tracking**.
- **Keep tracking** sets `ended_dismissed_at`. The prompt stays hidden until a newer payment appears and three more cycles are missed.
- **Remove** uses the existing soft delete; the bill moves to Paused and can be restored.
- Ended bills are excluded from the monthly total, category donut, biggest-bill tile and next-eight-weeks chart until kept or removed.

## 5. What was deliberately not changed

- **No automatic removal or pausing.** The user decides.
- **No prompt for manual bills without a merchant link.** There is no payment stream to judge.
- **No email or push notification.**
- **No recency filter on suggestions.** This is only about bills already being tracked.
- **Yearly bills use the same three-cycle rule**, which means three years. That is intentionally conservative.

## 6. Verification

- Full suite: **659 passed** (10 new tests).
- Unit tests cover monthly, weekly, quarterly and yearly missed counts, inactive and unlinked bills, suppression and sorting.
- Integration test: a linked Netflix bill last paid in March is flagged; **Keep tracking** hides it and stores the dismissal.
- Browser check with Old Gym last paid in March:
  - Bills-per-month excluded it ($18.99 instead of $68.99) while the card was shown.
  - **Keep tracking** removed the card and returned it to the totals.
- Temporary database, seed and screenshots removed.

## 7. Open items

- Decide whether a "possibly ended" bill should also appear on the dashboard.
- Consider a separate threshold for yearly bills.
- Consider detecting a stopped bill even when the last payment is older than the retained history.

## 8. Commands to reproduce

```bash
myenv/Scripts/python.exe -m pytest tests/test_subscription_health.py -q
myenv/Scripts/python.exe -m pytest -q
```

Link a bill to a merchant, leave its last payment three cycles behind, and open `/subscriptions`.
