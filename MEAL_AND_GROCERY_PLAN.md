# Meal plan and pay-cycle grocery list plan

Status: planned, not started. Created 2026-10-27.

## Goal

1. Generate meal plans from the meals we actually eat.
2. Generate a grocery list that covers everything needed until the next payday.
3. Split that list three ways: **full** (meals + everything else), **meals only**, and **non-meal only** (staples, snacks, household).

## Logic and what was missing from the brief

What exists today:
- `plan_generator.py` fills slots from rules (`weekday dinner -> tag standard`). It only knows recipes and tags, not what we have actually eaten or bought.
- `shopping_list_generator.py` turns a meal plan into lines with pantry subtraction and costs. It knows nothing about non-recipe purchases.
- `purchases` holds Woolworths history, but only some products are linked to ingredients, and the matcher misses common cases (`Full Cream Milk 2l` vs `Milk` scores 0.33, `Onions` vs `Onion` scores 0).
- Pay cycle: there is fortnight logic (`fortnight_for_date`, Monday to Sunday) but no real payday setting.

So the foundation is the data: every purchased product needs to be classified (which ingredient, meal ingredient vs staple vs household vs treat) and its purchase rhythm known. Everything else builds on that.

Things the brief did not mention but will matter:
- **Payday definition.** The date and frequency of payday, so "until I get paid" is a date range. This can be detected from bank credits, the same way recurring bills are detected.
- **Budget cap.** The list should show the estimated total against what is left in the budget, and offer what to drop or swap.
- **Consumption vs purchase.** Buying milk weekly does not mean using 2L per meal. Staples are predicted from purchase cadence, not from recipes. Meal ingredients come from recipes. Products used by both (eggs, butter) must not be counted twice.
- **Pantry stock and decay.** Estimate what is probably still in stock from the last purchase date and usual cadence, not only from manual pantry entries.
- **Leftovers and pack sizes.** Choosing meals that use up the rest of a pack (half a cabbage) lowers cost.
- **Eating out / no-cook nights**, so the plan does not overbuy.
- **Feedback loop.** Accepting, removing or swapping suggestions should teach the system (like `merchant_decisions` for bills).
- **Extra use cases for separate lists:** a top-up shop mid-cycle (only what is running out), a staples-only restock after payday, a household/non-food list, a "budget is tight" minimal list, a list for a single meal or event, and an export / Woolworths-friendly order.

Approach: rules and statistics first (pure services, fully testable), AI only as an optional reviewer on top, the same pattern as the subscriptions work. AI gets small batches and has a deadline and fallback.

## Steps

### Step 1. Product classification foundation
Logic: nothing can be suggested reliably until each purchased product is linked to an ingredient and has a role. This is the "transaction filtering" equivalent for groceries.
- 1.1 Improve `product_matching`: containment scoring (ingredient words inside product name), plural/singular folding, filler words (fresh, free range, loose, punnet, pack sizes), prefer the most specific ingredient on ties.
- 1.2 Add a product role: `meal_ingredient`, `staple`, `snack_treat`, `household`, `personal_care`, `pet`, `other`. Keyword rules in a pure service, stored per product name with a user override (new `product_decisions` table, like `merchant_decisions`).
- 1.3 Hide non-food by default on the Products page, with a "show hidden" toggle and a per-product ignore that survives re-imports.
- 1.4 Review UX: confidence labels, "why it matched", accept-all for high confidence.
- 1.5 Optional AI review of the leftover unclear products, reusing the `ai_review.py` batching, deadline and caching.
- 1.6 Tests for every rule with real product names from the order history.

### Step 2. Purchase pattern analysis
Logic: to predict "what else do we need" we need each product's rhythm: how often, how much, how regular.
- 2.1 Pure `services/purchase_patterns.py`: per product (or per linked ingredient) compute buy count, median interval, interval spread, usual quantity, last bought, last price.
- 2.2 Classify rhythm: `regular` (steady cadence), `occasional`, `one_off`, `stopped` (overdue by 3+ cycles), reusing ideas from `services/recurring.py`.
- 2.3 Predict "likely run out by" date and the quantity needed for a date range.
- 2.4 Merge products that map to the same ingredient (two brands of milk = one milk rhythm).
- 2.5 Patterns page: list regular buys with cadence, so the user can check the model is right.
- 2.6 Tests with synthetic histories (weekly, fortnightly, irregular, stopped).

### Step 3. Meal pattern analysis
Logic: "meal plans based on my meals" means learning which meals we actually eat and how often, not just applying tag rules.
- 3.1 Meal history: use past `meal_plan_entries` (kept, not deleted) as the record of eaten meals; add a "we ate this / skipped" mark if needed.
- 3.2 Per recipe stats: frequency, last eaten, typical day/meal slot, favourite flag, cost per serving from linked prices.
- 3.3 Infer meals from purchases: recipes whose key ingredients appear in the same basket score as "probably cooked" (helps when the plan was not recorded).
- 3.4 Tests.

### Step 4. Suggestion-based meal plan generation
Logic: extend the existing rule engine with scoring, so rules still decide the slot and the score decides which recipe.
- 4.1 Scoring function in `plan_generator.py`: variety (not eaten recently), favourites, typical slot fit, cost per serving, pantry/leftover use (shares open packs), effort on weeknights.
- 4.2 Constraints: max repeats per range, budget ceiling, no-cook / eat-out nights.
- 4.3 "Suggest" mode that proposes a plan for review (accept, swap, lock) instead of writing straight away.
- 4.4 Swap suggestions: show the 3 next-best recipes for a slot with reasons.
- 4.5 Record accept/swap decisions to tune weights later.
- 4.6 Tests, keeping manual entries and continuations behaviour unchanged.

### Step 5. Pay cycle
Logic: the list range is "today until next payday", so payday must be known.
- 5.1 Settings: payday date and frequency (weekly, fortnightly, monthly), plus optional grocery budget per cycle.
- 5.2 Optional detection of payday from bank credits (same detector as recurring bills, on credits).
- 5.3 Pure helper `pay_cycle_range(today, settings)` returning start and end dates.
- 5.4 Tests including month-end and fortnightly edge cases.

### Step 6. Pay-cycle grocery list generation
Logic: one generator, three filters, so the lists always agree.
- 6.1 Pure `services/cycle_list.py` that combines meal plan lines (existing generator) with predicted staple lines (Step 2) for the pay-cycle range.
- 6.2 De-duplicate: an ingredient needed by meals and as a staple appears once, with both sources shown.
- 6.3 Subtract pantry and "probably still have" estimates.
- 6.4 Each line carries a `source` (meal, staple, household, manual) so the list can be filtered: full, meals only, non-meal only, household only.
- 6.5 Extra modes: top-up (only items due before the next shop), staples restock, tight-budget minimal (drop treats first, cheaper swaps).
- 6.6 Totals per source vs cycle budget.
- 6.7 Save to `shopping_list_items` using the existing `origin` column; keep manual items.
- 6.8 Tests per mode.

### Step 7. UI, API and MCP
Logic: make it usable in the web app and by agents through the same services.
- 7.1 Shopping list page: range selector defaulting to "until payday", list mode tabs, source badges, "why is this here".
- 7.2 Meal plan page: "Suggest plan" button with accept/swap/lock.
- 7.3 `/api/v1` endpoints and MCP tools for suggest plan and generate cycle list.
- 7.4 Integration tests via the Flask test client.

### Step 8. Feedback and tuning
Logic: suggestions improve only if dismissals and edits are remembered.
- 8.1 Store removed/added lines and swapped meals as decisions.
- 8.2 Use them: products removed repeatedly drop out of staples; swaps lower a recipe's score.
- 8.3 Accuracy check after each import: predicted vs actually bought, shown on the patterns page.

### Step 9. Docs and release
- 9.1 Update `README.md`, `architecture.md`, `AGENTS.md` feature notes.
- 9.2 Full test suite green, deploy to `dev` staging, check with real data, then PR to `main`.

## Suggested order

Step 1 and 2 first (data foundation), then 5 (small), then 6 (most direct value: the payday list), then 3 and 4 (meal suggestions), then 7 to 9.
