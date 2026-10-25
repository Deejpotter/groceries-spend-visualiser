# Automatic bills and subscriptions from bank transactions

- **Date:** 2026-10-25
- **Investigator:** OpenCode (DeepSeek V4.1 Flash)
- **Status:** Implemented on `dev`, awaiting full-suite sign-off and review

## 1. What was asked

Identify bills and subscriptions automatically from transaction data, as the next
feature after the existing app work was verified complete.

## 2. What was checked

- `services/subscriptions.py` — existing suggestion helpers
- `services/bank_import.py` — NetBank CSV parsing, merchant classification, dedupe
- `services/banksync_client.py` — BankSync fetch, mapping, cursor handling
- `routes/subscriptions.py` — suggestion page and accept flow
- `routes/spend.py` — CSV and BankSync import entry points
- `database.py` — schema, migrations, counted tables
- `templates/subscriptions/suggest.html` — what the user actually sees
- `tests/test_subscriptions.py`, `tests/test_bank_import.py`, `tests/test_banksync.py`,
  `tests/test_integration.py` — existing expectations
- Local database at `data/groceries.db`

## 3. What the evidence showed

1. **No transaction history was stored.** `remember_unclassified()` kept only
   `{description, amount}` pairs in the `banksync_unclassified` settings JSON,
   deduped and capped at 200. Dates, ids and repeats were discarded, so cadence
   could not be measured at all.
2. **BankSync fetched full transactions and threw most away.** `sync_accounts()`
   imported only rows matching a grocery merchant; everything else went to the
   JSON cache. `bank_transactions` did not exist.
3. **Detection was a hardcoded merchant list.** `suggest_from_unclassified()`
   matched ~30 keyword/name pairs; anything else became "seen 2+ times → monthly".
   No interval, amount-stability or next-due logic was applied to bank data.
4. **Real interval detection existed, but for the wrong data.**
   `suggest_from_purchases()` inspects grocery line items (store + product), not
   bank transactions.
5. **Accepted suggestions came back.** The suggestion route did not filter
   existing subscriptions, and there was no dismiss action or dismissal store.
6. **Credits were treated as spend.** `parse_netbank_csv()` reduced every amount
   to its magnitude and dropped direction, so salary and refunds looked like
   ordinary positive rows.
7. **The local database was empty** (0 purchases, 0 subscriptions, no sync
   settings), so calibration against real data was not possible here.

## 4. What was changed

### Schema (`database.py`)

- New `bank_transactions` table: source, external id, date, description,
  normalised merchant key, amount, direction, status, with
  `UNIQUE(source, external_id)` and date/merchant indexes.
- New `merchant_decisions` table: accepted/dismissed merchant keys, display
  fields and the linked subscription id.
- `subscriptions.merchant_key` added through `MIGRATIONS`.
- Both new tables added to `COUNTED_TABLES` for `db-status`.

### Ingestion

- `parse_netbank_csv()` now returns `direction` (`debit`/`credit`) for both
  single-amount and debit/credit layouts.
- `to_purchases()` skips credits, so refunds and salary never become purchases.
- `store_bank_transactions()` persists every mapped row. CSV rows get a stable
  hash id (re-import is idempotent); BankSync rows use their transaction id.
  Grocery matches are stamped `status='grocery'` but not filtered out.
- CSV import and `sync_accounts()` both call it before purchase import.

### Detection (`services/recurring.py`)

- `normalise_merchant()` strips processor prefixes, punctuation, reference
  numbers and noise words (`PAYPAL *SPOTIFY`, `NETFLIX.COM 4471` → `spotify`,
  `netflix`).
- `classify_frequency()` maps the median interval to weekly, fortnightly,
  monthly, quarterly or yearly.
- `detect_recurring()` requires 3+ sightings, at least half the intervals inside
  the cadence tolerance, and bounded amount variance. Fixed amounts are
  `subscription`, variable amounts `bill`. Known AU merchants supply the display
  name and category; everything else is named from the most common description.
- Output includes occurrences, first/last seen, next due, monthly-equivalent
  cost, confidence and a human-readable reason.
- Credits are skipped. BankSync rows with an ambiguous (positive) sign are still
  considered, but income descriptions (payroll, salary, interest, refund,
  transfer from, etc.) are excluded. Grocery rows are excluded.

### UI and routes

- `/subscriptions/suggest` now reads `bank_transactions`, merges detected
  suggestions with the older purchase-history and legacy JSON suggestions, and
  hides anything already tracked by name or merchant decision.
- **Add** stores `merchant_key` on the subscription and records an accepted
  decision. **Dismiss** records an ignored decision. Dismissed merchants are
  listed with **Restore**.
- Purging a subscription removes its merchant decision, so it can be suggested
  again.
- The page shows occurrences, cadence, monthly cost, kind (bill/subscription)
  and next due.
- `KNOWN_MERCHANTS` moved to module scope in `services/subscriptions.py` so both
  detection paths share it (plus an `energy australia` alias).

## 5. What was deliberately not changed

- **Purchase-history suggestions stay.** They detect repeat grocery buys, which
  is different from bills, but removing them was out of scope and existing tests
  cover them.
- **The legacy `banksync_unclassified` JSON path stays.** It has no dates and
  cannot be backfilled, but it still serves data imported before this change.
- **No scheduler or background job.** Detection runs inside the request on the
  suggestion page; it is a pure function over a small table.
- **No API/MCP endpoint** for suggestions. The web UI is the only consumer today.
- **BankSync direction is not forced.** Providers disagree on sign, so positive
  amounts are marked `unknown` rather than guessed as credit; income keywords do
  the disambiguation. Consequence: a provider that reports all debits as
  positive and uses no income words will yield no bill suggestions. Not fixable
  without the provider's contract.

## 6. Verification

- Full suite: **588 passed** (`python -m pytest -q`), 2 minutes.
- Focused suites: `tests/test_recurring.py`, `tests/test_bank_import.py`,
  `tests/test_banksync.py`, `tests/test_subscriptions.py`,
  `tests/test_integration.py` — all passing.
- Migration check: copied the pre-change local database, ran `init_db()`, and
  confirmed `bank_transactions` (10 columns), `merchant_decisions` (8 columns)
  and `subscriptions.merchant_key` all appear without touching existing rows.
- Manual DB check: the local database is empty, so the feature was verified
  through fixtures and the Flask test client rather than live data.
- Not verified: production behaviour with real transaction history. That is the
  calibration task below.

## 7. Open items

- Calibrate thresholds against Deej's real transaction history in staging. In
  particular, how much utility-bill variance is common.
- Confirm which sign convention BankSync uses for production accounts.
- Decide whether detection should also surface an API/MCP endpoint.
- Add a real-data smoke check after the first production sync following deploy.

## 8. Commands to reproduce

```bash
myenv/Scripts/python.exe -m pytest tests/test_recurring.py tests/test_bank_import.py tests/test_banksync.py -q
myenv/Scripts/python.exe -m pytest -q
flask --app app db-status
```

To reproduce detection by hand, import a bank CSV with the same merchant three
months running, then open `/subscriptions/suggest`.
