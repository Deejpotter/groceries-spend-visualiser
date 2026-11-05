# Recipes rebuilt from Deej's Woolworths meal lists, with alternatives

- **Date:** 2026-11-05
- **Investigator:** Claude Code (for Deej)
- **Status:** Step 1 of 4 done (code: #123, #124; production data loaded)

## 1. What was asked

"Finish the shopping list features" and "finish the recipes". Ask how ingredients
from the shopping orders are used, so the app can predict when items are needed again.

## 2. What was checked

- Repo docs, UX audits and Day Planner: no unfinished shopping list work was recorded.
- Production purchase patterns:
  - 32 regular buys, 87 occasional, 72 stopped, 237 bought once.
  - Lactose-free milk was bought 31 times about every 6 days, but the gaps vary so much
    that it counts as "occasional", so the app never predicts it.
- Production meal plan: no plan dates, no rules and no planned meals. Recipe-based
  prediction had nothing to work from.
- Production recipes: 14, built on 2026-11-04 with estimated quantities.
- Deej's Woolworths account has 49 saved lists:
  - 29 meal lists ("Dinner …", "Lunch …") naming the exact products.
  - "Main shop" (81 items), Deej's favourite regular items.
  - Category lists.
  - All read through the signed-in Chrome session.

## 3. What Deej said

- **Household:** 2 adults and 2 kids. Paid fortnightly on Monday.
- **Shopping rhythm:** plans the weekend before payday. The Monday main shop covers the
  fortnight's meals and most staples; top-up shops happen in between.
- **Batch meals:** made from standard packs and split into 8 serves, which covers 2 nights.
- **Friday fun meals:** one night, 4 serves. Schnitzel and chips, parma, tacos, nachos,
  burgers, chicken burgers, hotdogs and mini pizzas. Some Fridays are takeaway instead.
- Sushi is a batch meal; kyiv is one night.
- Curry and chorizo pasta are batch work lunches.
- **Interrupted meals:** sometimes they eat out or get takeaway, and the leftover meals
  should roll into the next fortnight automatically.
- **Lists aren't shopping lists:** they hold backups and alternatives. Pies rotate by
  price, curry is chicken thigh or tofu, laksa is tofu or chicken breast. Deej wants the
  cheapest option chosen, and later live price sources.
- **Rice:** bulk jasmine. Sausage rolls come with a coleslaw (savoy). Lasagne is made
  like spaghetti.
- **Storage limits:** long-life milk 8–10 at most; fruit, sandwich meat and bread (they
  go off); toilet paper and tissues; cordial and soft drink; frozen goods.
- **Stock count:** Deej already counts on Sunday when ordering, and is open to a hybrid
  of counting and estimating, a long-term usage log, and a photo stocktake if it's
  reliable.
- Snacks are the same all year, with no school-term pattern.

## 4. What was changed

**Code (#123 to dev, #124 to main, production `49db11f`):**
- `recipe_ingredients.option_group`. Rows in a recipe with the same group are
  alternatives.
- `choose_options()` picks an option the pantry already covers, else the cheapest priced
  one, else the first listed. It's used by the shopping list and cost per serving.
- Recipe form gets an **Option** field; the detail page shows an "or · group" badge.
- The API accepts `option_group`.
- Tests: `tests/test_recipe_options.py`. Full suite: 1003 passed.

**Production data (through the app's forms in the signed-in session):**
- Added 63 ingredients from the meal-list products, with Woolworths list prices read on
  2026-11-05. Ingredients went from 53 to 116.
- Added 30 recipes built from the lists:
  - batch dinners: 8 serves, 2 nights, tagged `dinner, batch`
  - Friday meals: 4 serves, 1 night, tagged `dinner, friday`
  - Chicken Kyiv: 4 serves, 1 night
  - lunches: 8 serves, tagged `lunch, batch`
- Alternatives are option groups: pies, protein, sides, sauces, shells and patties.
- **Mistake:** the script didn't recognise the 13 existing recipes (it matched the link
  text "View"), so it added copies instead of editing them. With Deej's OK, the old
  copies (IDs 1, 2 and 4–14) were deleted; they're in Recently deleted. Production now
  has 31 recipes: the 30 new ones plus Pancakes.

## 5. What was deliberately not changed

- No prices were invented. These ingredients have no price, because the list page showed
  a discount label instead of a price or the product wasn't on a list:
  - Caesar dressing, French dressing, garden-vegetable sauce, vermicelli
  - deli kyivs, mushrooms, jasmine rice, lasagne sheets
- The new ingredients have no linked Woolworths products yet, so the price refresh on
  import won't update them until products are linked.
- Lunch batches cover 1 slot until the fortnight planner (step 2) decides how lunches
  are scheduled.

## 6. Verification

- Staging: created a recipe with two "protein" options through the API and read it back
  with `option_group` intact. The test records were deleted and the cleanup checked.
- Production: Recipes lists 31 recipes. Pies and Veg, Curry and Tacos show their
  alternatives.

## 7. Open items (agreed build order)

1. **Done:** recipes from the lists.
2. **Fortnight plan:**
   - About 6 batch meals plus Friday meals per pay cycle.
   - Marking a meal skipped (ate out or takeaway) rolls it, and the ingredients already
     bought, into the next fortnight.
3. **Sunday stock count:**
   - Max-on-hand limits and perishable limits.
   - Learned usage rates per staple (milk is daily use plus cooking), replacing the
     purchase-gap rhythm for steady-use items.
4. **Photo stocktake** as an experiment.

Also: link the Woolworths products to the new ingredients, and fill the missing prices.

## 8. Commands to reproduce

```bash
python -m pytest tests/test_recipe_options.py
python -m pytest -q
```
