# Ingredient prices refresh on import

- **Date:** 2026-11-05
- **Investigator:** Claude Code (for Deej)
- **Status:** In progress

## 1. What was asked

Pick the most useful improvement (best of three), implement it, ship it through
`dev` (test staging) to `main` (test production).

## 2. Candidates considered

1. **Reprice linked ingredients on every import.** Chosen. After the 2026-11-04 data
   fix, 144 product names are linked, but an ingredient's price only changed when
   someone linked a product by hand with "update price" ticked. Every new invoice
   added purchases that inherited the link but left the price stale, so shopping-list
   estimates drift away from what is actually paid. This is the app's core promise.
2. **Normalise product names on import** so CSV and invoice spellings of one product
   share a link. Less value: the two spellings differ in words, not just case
   ("long life milk 1l" vs "Milk UHT 1L"), so normalising would catch few, and the
   existing links already cover both spellings.
3. **Flag stale prices** on the ingredients page. Only shows the problem; option 1
   removes it.

## 3. What the evidence showed while building it

- `commit_import_batch()` dropped `cup_price`, `stockcode` and `channel` from staged
  invoice lines. The per-kg rate the 2026-11-04 parser fix captured for weighed meat
  never reached `purchases`, so a kg ingredient could not be priced from an invoice.
- `link_product()` priced `each` ingredients at the pack price: linking "Eggs 12 Pack"
  ($6.70) to an `each` ingredient with pack size 12 set the price per egg to $6.70.

- Found while preparing the production test (before anything was written there):
  weighed meat is named with a range ("Woolworths beef chuck steak medium pdm 380g -
  800g"). The two weighed rows already in production were committed before this
  change, so they have no cup price. The refresh read "800g" from the name and would
  have repriced chuck steak at $10.39 / 0.8 = $12.99/kg instead of $19.49/kg (a 1.5x
  drop, inside the 3x guard). Fixed in a follow-up PR: a name with a weight range
  gives no price unless the cup price converts.

## 4. What was changed

- `services/spend_import.py`
  - `ingredient_unit_price()`: per-unit price from a purchase. Uses the cup price when
    it converts; otherwise the size in the product name (`parse_pack_size`), then the
    ingredient's `pack_size`. `each` prices are per item.
    Names with a weight range ("380g - 800g") never use the name or pack size.
  - `price_change()`: `update`, `hold` (more than 3x up or down) or no change.
  - `refresh_linked_prices()`: for each ingredient linked to the given products, reprice
    from its latest linked purchase. Held changes are reported, not saved.
  - `price_refresh_messages()`: flash text for the result.
  - `commit_import_batch()` now keeps `cup_price`, `stockcode` and `channel`.
- `services/mutations.py`: `link_product()` uses `ingredient_unit_price()`.
- `routes/spend.py`: CSV import and invoice confirm call the refresh and flash what
  changed. `routes/api.py`: `/api/v1/import` returns `prices_updated` / `prices_held`
  per file.
- Tests: `tests/test_price_refresh.py` (26 tests). Docs: `architecture.md`, `AGENTS.md`.

## 5. What was deliberately not changed

- No one-off reprice of existing production ingredients. The 2026-11-04 prices were set
  by hand from the invoices; the refresh only runs on the next import.
- No per-ingredient "lock price" setting. The 3x hold covers the realistic failure
  (a unit or pack-size mismatch) without new UI.
- Name normalisation and stale-price flags (candidates 2 and 3) were not built.

## 6. Verification

- Full suite: 976 passed.

## 7. Open items

- None yet.

## 8. Commands to reproduce

```bash
python -m pytest tests/test_price_refresh.py
python -m pytest -q
```
