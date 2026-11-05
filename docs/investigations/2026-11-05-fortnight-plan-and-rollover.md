# Pay-cycle meal plans, ate-out skips and rollover

- **Date:** 2026-11-05
- **Investigator:** Claude Code (for Deej)
- **Status:** Done (#126 to dev, #127 to main; production `9f16892`)

## 1. What was asked

Step 2 of the plan in [recipes-from-woolworths-lists](2026-11-05-recipes-from-woolworths-lists.md):
plan each pay fortnight with batch meals, Friday fun meals and work lunches. When a meal
is interrupted (eating out, takeaway), the remaining meals should roll into the next
fortnight automatically, without being bought again.

## 2. What was checked

- The plan generator already had rules by day and tag, multi-day meals with
  continuations ("Leftovers"), skip rules tagged "takeaway", and an **Until payday**
  range.
- **Gap 1:** a continuation always took the next night, even when another rule planned
  that night. With a Mon–Thu + Sat–Sun batch rule and a Friday rule, a Thursday batch
  meal would have taken Friday.
  - The test `test_coverage_continuation_prevents_rule_on_occupied_slot` pinned exactly
    that behaviour.
- **Gap 2:** there was no way to plan the *next* pay cycle, which is the fortnight
  Deej's Monday shop covers.
- **Gap 3:** there was no way to mark a planned meal as skipped and move the rest along.

## 3. What was changed

- `pay_cycle()` returns `next_cycle_start` and `next_cycle_end`. The plan page has a
  **Next pay cycle** button.
- `generate_plan_entries()`: a continuation skips a night another active rule for that
  meal plans. Nights no rule covers behave as before. The old test was rewritten as
  `test_continuation_does_not_take_another_rules_night`.
- New table `plan_skips` (date, meal_type). Regeneration treats a skipped slot as taken.
- `skip_and_shift()` and `rollover_dates()` (pure, in `services/plan_generator.py`).
  `skip_meal()` and `unskip_meal()` (in `services/mutations.py`).
- **Ate out / takeaway**:
  - moves the meals after the skipped slot that came from the same rule along one of
    that rule's slots, so Friday meals stay on Fridays;
  - marks the moved meals as kept;
  - sends the meal that no longer fits to the first free night on its rule's days after
    the plan ends, with `origin = 'rollover'`;
  - before the cycle starts, nothing has been bought, so the overflow is removed
    instead;
  - **Undo** reopens the slot.
- `generate_shopping_list()` skips rollover meals, and they show an "Already bought"
  badge.
- Tests: `tests/test_fortnight_plan.py` (11). Full suite: 1014 passed.

**Production setup (through the app's forms, signed-in session):**
- Rules: dinner Mon/Tue/Wed/Thu/Sat/Sun → `batch`; dinner Fri → `friday`; lunch on
  weekdays → `lunch`.
- Curry and Chorizo Pasta set to cover 5 days (one lunch batch per work week). Both
  still have all their rows and option groups.
- Generated the next pay cycle, Mon 9 – Sun 22 Nov:
  - Batch dinners: Laksa, Stir Fry, Lasagne, Pasta Bake, Mac and Cheese, Baked Potatoes,
    each over 2 nights.
  - Friday meals: Beef Burgers, Mini Pizzas.
  - Lunches: Curry in week 1, Chorizo Pasta in week 2.
  - 24 entries in total.

## 4. What was deliberately not changed

- The shopping list wasn't regenerated in production; that would replace Deej's current
  list.
- No skip was tried in production. It would rearrange Deej's real plan, and because the
  cycle hasn't started yet the overflow would be deleted. It's covered by integration
  tests for both cases (after the shop it rolls over; before the shop it's dropped).
- Skipping doesn't account for half-eaten batches (for example 4 of 8 serves frozen).
  The whole slot moves.
- Lunch servings stayed at 8 for 5 days. That's a guess at how many people take lunch;
  Deej can change it on the recipe.

## 5. Verification

- CI and the staging check passed on `6ba41cc` (dev) and on the release PR. Staging has
  no payday and Chrome isn't signed in there, so the page was checked in production
  instead.
- Production `/health` reports `9f16892`.
- The plan page shows the dates 9–22 Nov, 24 **Ate out / takeaway** actions and the
  **Next pay cycle** button.

## 6. Open items

- Step 3: Sunday stock count, max-on-hand limits, learned usage rates.
- Step 4: photo stocktake experiment.
- A partial-batch skip ("we ate half, froze the rest") could become a later option.

## 7. Commands to reproduce

```bash
python -m pytest tests/test_fortnight_plan.py tests/test_plan_generator.py
python -m pytest -q
```
