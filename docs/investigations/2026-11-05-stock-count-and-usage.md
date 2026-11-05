# Sunday stock count, cupboard limits and usage-based predictions

- **Date:** 2026-11-05
- **Investigator:** Claude Code (for Deej)
- **Status:** Done (feature #129/#130; fixes #131/#132 and #133/#134; production `9d7324b`)

## 1. What was asked

Step 3 of the plan in [recipes-from-woolworths-lists](2026-11-05-recipes-from-woolworths-lists.md):
predict when items are actually needed again.
- Use a quick Sunday count.
- Apply cupboard limits: long-life milk 8–10, toilet paper, drinks, freezer.
- Treat fruit, sandwich meat and bread as perishables.
- Learn how fast things get used.

## 2. What was checked

- **Why milk was missed:** pay-cycle lists predicted from purchase gaps (`predict_needs`).
  Milk was bought 31 times in six months, but the gaps were uneven, so it was rated
  "occasional" and rarely predicted.
- **Units:** purchase rows record packs (`quantity`). Counting in packs matches how Deej
  thinks ("8 milks") and needs no unit conversion.
- **Double imports:** a shop can be imported twice, as order-history CSV and as invoice.
  Summing both would double the usage.

## 3. What was changed

- **`services/usage.py`** (pure):
  - `daily_packs`: packs per item per day, keeping the largest basket per day so a
    double-imported shop counts once. Bank payments are skipped (fix #131).
  - `usage_rate`: from stock-count spans when there are any (used = previous count +
    bought since − this count); otherwise everything bought since the first buy, over at
    least 28 days.
  - `on_hand`: the last count plus buys since, minus usage since.
  - `packs_needed`: cover the cycle. **Keeps for** limits perishables to that many days.
    **Keep at most** is a hard cap that rounds down.
  - `usage_needs` and `merge_needs` replace the rhythm guess for tracked items. A tracked
    item with enough stock is dropped from the list.
- **Which items are tracked:** six or more buys in six months, or anything counted or
  given limits.
- **New tables:** `stock_counts` and `item_usage`, keyed by pattern key.
- **Pantry → Stock count** page: shows uses per week, probably left and the last count,
  with inputs for today's count, keep at most and keeps for.
- **List reasons** read like "Uses about 9.3 a week (from purchases); keep at most 8".
- **Fix #133:** unlinked product keys now ignore case and spacing. The CSV and invoice
  spellings had split Oreos, Tasti bars, fruit cups, Pepsi Max and others into two rows
  with half the usage each. Old feedback keys still match.
- **Tests:** `tests/test_usage.py` (15) and a product-key test. Full suite: 1030 passed.

**Production settings saved through the stock count form:**

| Item | Limit |
|---|---|
| Milk, lactose-free full cream | keep at most 8 |
| Milk, lactose-free lite | keep at most 2 |
| Bananas | keeps 5 days |
| Apples | keeps 14 days |
| Wholemeal bread | keeps 7 days |
| Raisin toast | keeps 7 days |
| Salami | keeps 7 days |
| Champagne leg ham (deli) | keeps 5 days |
| Honey ham (deli) | keeps 5 days |

- The milk split follows usage (about 9.3 and 2.4 a week) inside Deej's "8 or 10 at
  once".
- The keep-for days are typical shelf lives, not Deej's numbers.

## 4. What was deliberately not changed

- No limits were set for toilet paper, tissues, cordial, soft drink or freezer items.
  Deej named them but gave no numbers, so they're on the stock count page to fill in.
- No stock counts were entered. Only Deej knows what's in the cupboard.
- The shopping list wasn't regenerated in production. The web route saves the list,
  which would replace Deej's current one, and there's no production API key to preview
  it. The first real check is Deej's next list.
- Long-life milk's 8–10 cap is total across two items; the app caps each item
  separately, so it's split 8 + 2.

## 5. Verification

- CI and the staging check passed for every release.
- Production `/health` reports `9d7324b`.
- The stock count page lists 56 tracked items. The bank-payment row is gone, there's one
  row per product regardless of spelling, and the limits read back as saved.

## 6. Open items

- Deej: the first Sunday count. Rates switch from "from buys" to "counted" after two
  counts.
- Deej: fill in limits for toilet paper, tissues, drinks and frozen goods.
- Step 4: photo stocktake experiment.
- Possible: a preview of the pay-cycle list that doesn't save it.

## 7. Commands to reproduce

```bash
python -m pytest tests/test_usage.py tests/test_purchase_patterns.py
python -m pytest -q
```
