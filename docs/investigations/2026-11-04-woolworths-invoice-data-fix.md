# Woolworths invoices: import, ingredient and recipe fixes

- **Date:** 2026-11-04
- **Investigator:** Claude Code (for Deej)
- **Status:** Done (production data fixed; parser fix shipped dev → main)

## 1. What was asked

Fix the ingredients and recipes in production based on the Woolworths invoices,
downloading and importing the invoices from the Woolworths account if needed.

## 2. What was checked

- Woolworths "My Orders": 20 past orders, 9 Jul – 26 Oct 2026. All 20 tax-invoice
  PDFs were downloaded through the logged-in Chrome session.
- All 20 PDFs parsed locally with `parse_woolworths_invoice_pdf` and reconciled to
  each invoice's printed subtotal.
- Production (`groceries.deejpotter.com`) ingredients (33), recipes (14) and the
  products page (1,168 distinct purchased product names) were read through the UI.

## 3. What the evidence showed

- **Parser bug.** Two invoices (#321978888, #331719186) were short by exactly the
  value of a variable-weight meat line ($9.83 chicken thigh, $10.39 chuck steak).
  Woolworths prints these as two lines: `13 ... 380g - 800g 1 $19.50/Kg` then
  `~ ... 1 @ 533g $19.49/Kg $10.39`. The parser didn't match either, so the meat
  was dropped and its text was glued onto the next product's name.
- **No product was linked to any ingredient.** All 1,168 product names had
  `ingredient_id` NULL, so no price ever flowed from purchases to ingredients.
  Invoice names ("Woolworths free from lactose full cream long life milk 1l") also
  differ from the older CSV names ("... Milk UHT 1L"), so one product appears under
  several names.
- **Ingredient errors.**
  - Deli items were priced per pack-weight, not per kg: bacon $158.33/kg (real
    $19/kg), salami $300/kg ($36/kg), sliced chicken $130/kg ($16/kg).
  - `pack_size` on "each" ingredients held a weight (eggs 0.35, wraps 0.4,
    yoghurt 0.56), which makes pack maths wrong.
  - "Maple Syrup" is actually Green's *Pancake Shake* (a just-add-water mix).
  - Cucumber had no price.
- **Recipe errors.** Pasta dishes had no pasta; Tuna Casserole had no tuna (it
  used bolognese sauce); Curry had no rice; Pies and Veg had no pies; Schnitzel
  had no schnitzel or chips; Beef Stew used mince; Burgers asked for 4 loaves of
  bread; Tacos asked for 8 packs of wraps; Baked Potatoes used frozen mash;
  Pancakes added milk and eggs to a just-add-water mix. Several rows used units
  the ingredient couldn't convert to (g of an "each" item, "each" of a kg item).

## 4. What was changed

**Code (PR to `dev`, then `dev` → `main`):**
`services/spend_import.py` now reads `~ ... N @ <weight> $x/Kg $amount` lines as
their own row (name from the numbered line, `cup_price` = the per-kg rate).
Regression test `test_invoice_variable_weight_meat_line`. Full suite: 952 passed.

**Production data (through the app's own forms, logged-in session):**

- Imported all 20 invoices via `/spend/import` review. The two fused names were
  corrected in review to "Icare everyday facial tissue 200 pack" and
  "Woolworths chocolate creme wafer biscuits 125g". After the parser fix deployed,
  the two invoices were re-imported so the meat lines were added (dedupe kept the
  rest).
- Edited all 33 existing ingredients: latest invoice prices, per-kg deli prices,
  per-item prices with count pack sizes for multipacks (eggs, wraps, crumpets,
  muffins, pikelets, yoghurt), weight units for jars/tubs/blocks (pasta sauce,
  sour cream, cheese slices), frozen items moved to the Frozen category,
  "Maple Syrup" renamed "Pancake Shake (Maple)", onion switched to the 1 kg bag.
- Added 20 ingredients for things the recipes need, each from a product actually
  bought: spaghetti, macaroni, spirals, tuna 425 g, cream of celery soup, hamburger
  rolls, iceberg lettuce, potato wedges, deli schnitzel, lower-carb potatoes,
  coleslaw dressing, savoy cabbage, taco kit, coconut cream, Italian tomatoes,
  chuck steak, party pies & sausage rolls, stir-fry veg, chicken thigh, rice.
- Linked 142 product names (all name variants) to their ingredient.
- Rewrote the ingredient rows and descriptions of all 14 recipes to use those
  ingredients with convertible units.

## 5. What was deliberately not changed

- Recipe names, servings, tags and coverage days were kept.
- The ~1,000 other product names (snacks, drinks, household) were left unlinked:
  they are not recipe ingredients.
- No API key was created on production; the auto-mode classifier blocked creating
  one, so all writes went through the logged-in web forms instead.
- Recipe quantities are reasonable family-of-4 estimates, not measured amounts.
  Curry and Stir Fry use the sushi rice actually bought (no other rice since 2023).

## 6. Verification

- All 20 invoices reconcile to their printed subtotals with the fixed parser.
- Every ingredient edit/add and recipe save returned the app's success flash.
- Read back the ingredients list (53 rows, prices as intended) and recipe pages.

## 7. Open items

- Party pies/sausage rolls have been bought as several products; only one is the
  priced ingredient.
- Consider normalising product names on import (case, `pdm`) so invoice and CSV
  names group together.

## 8. Commands to reproduce

```bash
python -m pytest tests/test_spend_pdf.py
python -m pytest -q
```
