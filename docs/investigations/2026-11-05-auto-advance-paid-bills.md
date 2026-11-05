# Bills marked paid when their bank payment arrives

- **Date:** 2026-11-05
- **Investigator:** Claude Code (for Deej)
- **Status:** Done (shipped dev → main: #120, #121)

## 1. What was asked

Follow-up to [bills-until-payday](2026-11-05-bills-until-payday.md): 15 of the 20 bills
due before payday showed as overdue because `next_due` never advanced after an
auto-debit. Deej: "Yes, build it". Goal: move a bill forward automatically once its bank
payment is seen.

## 2. What was checked

- Bills already link to bank data: `subscriptions.merchant_key` plus `stream_amount`.
  The Bills route filtered a merchant's payments to the bill's amount (±35%, min $5)
  inline in `_decorate_subscription()`.
- Bank rows enter through `store_bank_transactions()`, called by the CSV import route and
  by `sync_accounts()` in `services/banksync_client.py`. That covers the manual sync, the
  daily auto-sync on the spend pages, the backfill and `/api/v1/banksync/sync`.
- `mark_paid()` catches up missed cycles from the stored date. It knows nothing about
  when the payment actually happened.

## 3. What the evidence showed

- Production before (commit `066d539`): 20 due before Mon 9 Nov, $790.50, 15 overdue.
- After the sync on `40e0f87`: 15 due, $568.12, 11 overdue. Five bills moved:
  - Canview Medical: 27 Oct → 12 Nov
  - Amazon Web Services: 2 Nov → 4 Dec
  - Render.com: 6 Nov → 4 Dec
  - Bermuda Coffee: 5 Feb → 26 Nov
  - ClickUp: 29 Oct → 29 Nov
- Bills that did not move had no payment covering their current due date:
  - Belong Mobile: last payment 21 Sep, due 21 Oct.
  - Spotify, Microsoft and Dollar Shave Club: no linked bank payments at all, so only
    **Mark paid** can move them.
- The early prediction in the bills post-mortem was wrong: it assumed Belong, Spotify and
  Microsoft had probably been paid. The bank data doesn't show that.

## 4. What was changed

- `services/subscriptions.py`: `due_after_payment(sub, payments)`.
  - A payment on `next_due`, or up to `PAID_EARLY_DAYS` (3) before it, counts as paid.
  - The new date is one cycle after the latest payment, which re-anchors auto-debits to
    their real day.
  - It never moves a date back. It skips recurring spending, paused bills, a missing date
    and unknown frequencies.
- `services/price_history.py`: `stream_points()`, the amount filter moved out of the route.
- `services/bank_import.py`: `advance_paid_bills(db)` updates the bills and returns
  `{name, old, new}` for each one it moved.
- The step now runs after bank CSV import (`routes/spend.py`) and at the end of
  `sync_accounts()`.
- The bank import result page lists the bills it moved. `/api/v1/banksync/sync` returns
  `bills_advanced`.
- Tests: `tests/test_bill_auto_advance.py` (13). AGENTS.md has a feature note.

## 5. What was deliberately not changed

- Bills without a `merchant_key` (added by hand) are not matched by name. That would be a
  guess and could mark a bill paid wrongly.
- Nothing runs on a page load (GET). Updates happen only when bank data is imported.
- Overdue bills with no payment stay overdue, because the bank says they're unpaid.

## 6. Verification

- Full suite: 995 passed. CI and the staging check passed on `79ac0a6` (dev) and on the
  release PR.
- Staging has no BankSync connection or bills, so the feature couldn't be exercised there.
- Production: `/health` reports `40e0f87`. A BankSync sync was run from the Import page in
  Deej's signed-in Chrome; the numbers before and after are in section 3.

## 7. Open items

- Spotify, Microsoft and Dollar Shave Club have no bank link. Link them, or use
  **Mark paid**.
- Belong Internet and Mobile, Humble Bundle, Canview Operations, Delivery Unlimited and
  Spriggy have no payment yet for their current cycle. They'll move when one arrives.

## 8. Commands to reproduce

```bash
python -m pytest tests/test_bill_auto_advance.py
python -m pytest -q
```
