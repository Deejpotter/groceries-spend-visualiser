# Bill candidate coverage: backfill and fuzzy grouping

- **Date:** 2026-10-25
- **Investigator:** OpenCode (DeepSeek V4.1 Flash)
- **Status:** Implemented on `dev`, deployed to staging (`c7a9e57`); PR pending

## 1. What was asked

The user has far more regular payments than the app was finding. The AI review was
supposed to help, but it only had one merchant to review. Add fuzzy grouping of
similar descriptions and filter by likelihood.

## 2. What was checked

- The production Suggestions page after the AI review: **"AI reviewed 1 merchant:
  0 kept, 1 dismissed"** and no suggestions.
- `bank_transactions` ingestion: it started with the transaction-storage release, so
  it only held rows from the deploy date onward.
- BankSync sync: it resumes from a cursor, so older transactions are never fetched.
- Merchant grouping: the first meaningful token, with processor prefixes stripped.
- The legacy `banksync_unclassified` cache: 16 descriptions but no dates, so it
  cannot establish cadence.

## 3. What the evidence showed

1. **The AI was not the bottleneck.** It only received one candidate because the
   transaction table had almost no history. The user's older regular payments were
   never stored.
2. **A normal sync could not fix it.** The cursor only moves forward; old rows stay
   invisible.
3. **Grouping was already aggressive but blind to suffixes.** `AMAZON` and
   `AMAZONAU` would become separate weak groups, each below the detection threshold.
4. **The AI's dismissals were invisible.** With 1 merchant and 0 kept, the user had
   no way to see what was rejected or why.

## 4. What was changed

### Backfill

- `sync_accounts()` now accepts `from_date_override` and `update_cursor=False`.
- `run_backfill(db, months=12)` pulls a fixed window without moving the sync cursor,
  so the next normal sync still starts where it left off.
- New `POST /spend/banksync/backfill` route with CSRF, a clear flash summary and a
  **Backfill 12 months** button on the import page.
- `transactions_added` is reported so the user sees the coverage change, not just
  new grocery purchases.
- Re-running is safe: bank rows are idempotent by external id.

### Backfill prompt where it matters

- The Suggestions page now shows how many transactions and repeat merchants are
  stored, with the date range.
- When coverage is thin (under 40 transactions or fewer than 3 candidates) it offers
  **Backfill 12 months of bank history** directly on that page, returning there after
  the run.

### Fuzzy grouping

- `merge_similar_groups()` folds merchant keys where one is a prefix of another and
  the shorter key is at least four characters:
  `amazon` + `amazonau`, `telstra` + `telstracorp`.
- Short codes stay distinct (`agl` vs `aglenergy`).
- Applied to both rule detection and AI candidates, so variants accumulate enough
  sightings to qualify.
- The shorter key wins as the group key, keeping the grouping deterministic between
  runs and the AI cache stable.

### Visibility

- A **What the AI dismissed (N)** disclosure lists the rejected merchants and the
  model's reasons, so a wrongly dropped real bill is visible instead of silent.

## 5. What was deliberately not changed

- **No change to the AI prompt for now.** The main failure was missing data, not the
  model's judgement. The dismissed list will show if that changes.
- **The legacy cache is still not used.** It has no dates and produced the original
  card/fee noise.
- **No second cursor or synthetic history.** Backfilled rows are real transactions.
- **No automatic backfill on deploy.** It is a user action because it calls BankSync
  and can take a while.

## 6. Verification

- Full suite: **641 passed** (8 new tests).
- New tests cover backfill date override, cursor preservation, transaction counts,
  the backfill route, the missing-key path, prefix merging, short-code separation,
  the thin-coverage prompt and the AI-dismissed list.
- The live AI review was rechecked locally on messy merchants; it correctly kept
  Netflix, Telstra and Council Rates and dropped Amazon/DeepInfra.
- Live BankSync backfill against production data has not been run yet; it needs the
  user's key and account.
- Staging `c7a9e57` is healthy and shows the coverage warning. Staging has no
  BankSync key, so it displays the import path; production has the key and will show
  **Backfill 12 months of bank history**.

## 7. Open items

- Run **Backfill 12 months** on production and re-review. CDR providers may only
  expose about six months, so the result may be shorter than the label.
- Check whether the AI dismissed a real bill; if so, adjust the prompt or model.
- Consider batching AI review if more than 30 candidates appear after backfill
  (`MAX_CANDIDATES` currently caps the request).
- Consider a manual "keep as bill" override on the AI-dismissed list.

## 8. Commands to reproduce

```bash
myenv/Scripts/python.exe -m pytest tests/test_banksync.py tests/test_recurring.py tests/test_ai_review.py -q
myenv/Scripts/python.exe -m pytest -q
```

Open `/subscriptions/suggest` with a thin history, press **Backfill 12 months**, then
review again.
