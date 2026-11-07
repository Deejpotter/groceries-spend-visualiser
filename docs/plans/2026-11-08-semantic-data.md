# Semantic data for meals, ingredients, products and suppliers

- **Date:** 2026-11-08
- **Author:** Claude Code (for Deej)
- **Status:** Proposal. Nothing built yet.

## 1. Why

The planner and list builder only know names, tags, categories and purchase history. They guess the rest
from words and patterns, and the 8–22 Nov plan shows where the guesses go wrong.

| What happened | What the app didn't know |
|---|---|
| Five pasta dinners plus a week of pasta lunches in week one | That Chorizo Pasta, Pasta Bake, Mac and Cheese, Spaghetti and Tuna Pasta are all pasta |
| Eggs listed twice ("Eggs" and "Woolworths 12 pack extra large free range eggs") | That the unlinked product is the same thing as the ingredient |
| Lasagne sheets, winter veg mix, mushrooms, tomatoes and bacon listed twice in the same way | Same as above |
| Four lactose-free cheeses as regular buys next to the recipe cheese | That they substitute for each other, and that the household needs lactose free |
| Mini Tacos at $46, Pepsi Max at $59 for "2 packs" | What one pack is (10 shells? a 30-can carton?) |
| An Aldi card payment listed as a $58.36 item (fixed in #154) | Which rows are shops and which are products |
| Nothing priced at Aldi or Coles on the list | Which store you buy each thing at, and when you go there |

## 2. What to record

Keep it small: a few fixed-choice fields per thing, filled in by suggestion and confirmed by Deej, not typed.

### Recipes
- **Base:** pasta, rice, potato, bread/wrap, noodles, none.
- **Main protein:** beef, pork, chicken, fish, vegetarian, mixed.
- **Cuisine:** Italian, Mexican, Asian, Australian, other.
- **Good for:** lunch, dinner, both. **Leftovers:** keep N days, freezes yes/no.
- **Effort:** quick (under 30 min), normal, project (prep and cook time already exist; this is the label).

### Ingredients
- **Diet:** must be lactose free (a household rule, so it applies to every dairy ingredient).
- **Substitutes:** a group name, e.g. "shredded cheese", so any member satisfies the need.
- **Storage and shelf life:** pantry, fridge or freezer, and days it keeps once opened. `item_usage.keep_days`
  already exists for regular buys and would move here.
- **Kind:** staple (always keep some), recipe-only, or treat.

### Products (what the store sells)
- **Pack:** count and unit per pack (10 shells, 30 cans, 1 kg), so the price per pack and the quantity make sense.
- **Same as:** the ingredient it is. Unlinked regular buys get a review queue instead of becoming their own line.

### Suppliers
A `suppliers` table: Woolworths, Coles, Aldi, the butcher, and so on.
- Bank merchant keys that belong to it (this is how bank rows become "a shop at Aldi").
- How you shop there (delivery or in store), the usual days, delivery fee and minimum order.
- Per ingredient: the preferred supplier (`ingredients.store` is free text today).

## 3. How the algorithm uses it

- **Planner (`MealScorer`):** a penalty for repeating a base or protein inside a week (for example, at most two
  pasta dinners), lunches only from lunch-suitable recipes, and leftovers that respect keep days. Each one
  shows as a reason, the same way "Eaten in the last week" does now.
- **Shopping list:** substitutes and "same as" merge duplicate lines into one; dairy picks only lactose-free
  products; a staple is topped up to its minimum, not bought again on its rhythm.
- **Prices:** cost per pack from the pack count, so a 10-pack at $46 is flagged as $4.60 each or a bad price.
- **Suppliers:** split the list by store and day; Aldi bank totals stop looking like items and start counting
  as Aldi shops.

## 4. How it gets filled in

1. Keyword suggestions first (the same approach as `product_role()`), then the existing AI review
   (`product_ai_reviews`) for what keywords miss.
2. One review page per kind: suggested values shown, accept all or change one. Nothing is used until confirmed.
3. API and MCP fields, so an agent can fill it in with Deej watching.

## 5. Order

1. **Recipe attributes and the variety rule.** The biggest visible change, and about 30 recipes to label.
2. **Same-as review for unlinked regular buys.** Removes the duplicate lines.
3. **Substitutes and the lactose-free rule.**
4. **Pack sizes and price sanity.**
5. **Suppliers.**

Each phase is its own PR with tests, through `dev` to `main`.
