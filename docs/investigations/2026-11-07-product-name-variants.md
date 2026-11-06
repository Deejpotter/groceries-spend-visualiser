# One product, two spellings ("pdm")

- **Date:** 2026-11-07
- **Investigator:** Claude Code (for Deej)
- **Status:** Shipped to production (3ead49b)

## 1. What was asked

"Find the next improvement and plan then implement it."

## 2. What was checked

- Day Planner's open groceries tasks are mostly manual checks (Sunday count, linking products, BankSync
  schedule), not code gaps.
- The photo stocktake write-up (2026-11-06) noted two "peach in strawberry jelly" rows, one with "pdm".
  The 2026-11-04 invoice write-up had already listed "normalise product names on import (case, `pdm`)" as open.
- Production products page, all 11 pages (logged-in Chrome, read only): 1,087 product names; 137 contain
  "pdm"; 953 keys after the case fix (#133); 899 once "pdm" is also ignored.

## 3. What the evidence showed

- Woolworths invoices add a "pdm" word to some product names that the order CSV leaves out, e.g.
  "Woolworths peach in strawberry jelly pdm 4 pack" vs "Woolworths Peach In Strawberry Jelly 4 pack".
- 54 products were split in two, including eggs, bananas, apples, raisin toast, tissues, stevia, mash,
  potatoes, party pies and sausages. Each half got part of the usage rate. Some fell under the six-buy
  "steady" threshold, so they weren't predicted from usage at all.
- Links were by exact name too: linking the CSV spelling left the invoice spelling unlinked, and an import
  only inherited a link when the exact name matched.

## 4. What was changed (PRs #139, #140)

- `services/purchase_patterns.py`: `product_name_key()` (lower case, single spaces, drops the word "pdm");
  `normalise_key()` uses it for `prod:` keys.
- `services/mutations.py`: `name_variants()`; `link_product()` links, relinks and unlinks every spelling together.
- `services/spend_import.py`: `_linked_ingredients()` keyed by name key, skipping keys whose spellings are
  linked to different ingredients; `link_name_variants()` links unlinked twins of linked products; both
  imports (CSV and invoice) run it after inserting.
- `services/repository.py`, `services/usage.py`: saved limits and counts are read back under the normalised key.
- `app.py`: `flask --app app link-variants`.
- Tests: `tests/test_name_variants.py` (6). Full suite 1044 passed. CI and staging check passed.

## 5. What was deliberately not changed

- **Stored product names** stay as imported. De-duplication (`UNIQUE(store, basket_id, product_name)`) and
  price refresh rely on them.
- **The products page** still lists each spelling as its own row. Linking either one now links both.
- **Conflicting links** (spellings already linked to different ingredients) are left alone, not guessed.
- **Product decisions** (ignored, role) are still per exact name.
- **No backfill was run in production.** Old unlinked twins of linked products are linked by the next import
  or by the CLI. Grouping for unlinked products changed straight away.

## 6. Verification

- The CLI on a scratch database linked 1 row, then 0 on a second run; the database was deleted afterwards.
- Production Stock count after deploy: the two peach jelly rows (0.7 and 1.1 a week) became one row at
  1.4 a week. That's lower than the sum because a shop imported as both CSV and invoice is now counted once.
  The tracked list went from 56 to 59 items as combined products reached the six-buy threshold.

## 7. Open items

- The next import backfills old twins. Check the products page "Only unlinked" count drops afterwards.
- If other invoice-only words turn up, add them to `NAME_NOISE`.

## 8. Commands to reproduce

```bash
python -m pytest tests/test_name_variants.py -q
flask --app app link-variants
curl -s https://groceries.deejpotter.com/health
```
