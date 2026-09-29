# Refactor & Spend-Integration Plan

Branch: `refactor/meal-planner-integration` — baseline commit `7d4200f` (104 tests passing).

## Review findings (what drives the plan)

**Security**
- `/setup` is unauthenticated and resets the password of any existing user → anyone can take over the admin account.
- Login `next` parameter is an open redirect.
- No CSRF protection on any POST form.
- `/ingredients/select-ajax` builds HTML from ingredient names without escaping (stored XSS).
- `SECRET_KEY` silently falls back to a hard-coded value.

**Data-loss / correctness bugs**
- Meal-rule create/edit never calls `db.commit()` → rules are silently discarded.
- Pantry add/edit never commits.
- Route-level plan generation (`routes/meal_plan.py`) duplicates the tested service with a `LEFT JOIN recipes ON 1=1` cartesian join and substring tag matching (`tag LIKE %x%`); the tested `services/*` code is not used by any route.
- Service tag matching doesn't strip whitespace (`"a, b"` never matches `b`) and always picks the first recipe (no variety).
- Shopping-list view ignores the "subtract pantry" setting; cost estimate breaks after imperial conversion.
- Shopping-list page renders a recomputed list with fake hash ids, so check/delete act on nothing.
- Inactive meal rules are hidden from the list; the "active" checkbox is always checked.
- DB schema is only created when running `python app.py`, not under gunicorn; default DB path `/app/data` breaks local dev.

**Broken UI**
- Every delete/remove/consume/toggle and "Generate Plan" button is an `<a href>` (GET) to a POST-only route → 405.
- Recipe form "Add Ingredient" rows have no `name` attributes (and app.js shows an alert) → ingredients can't be added to recipes in the browser.
- Meal plan "Swap" submits an empty recipe id.
- Font Awesome icons referenced but never loaded; duplicate `templates/ingredient_form.html`.

**Spend analysis**
- Legacy spend code (Woolworths CSV analysis, invoice-PDF + OpenAI + MongoDB pipeline) is no longer wired into the app. The invoice pipeline uses the removed `openai.ChatCompletion` API and MongoDB.
- Personal invoices/order CSV are tracked in git.

## Steps

### 1. Foundation (config, DB, security)
- [x] 1.1 Rebuild venv, run tests (104 pass), commit baseline on a branch, untrack `myenv/`.
- [x] 1.2 `database.py`: local default DB path (`./data/groceries.db`), create parent dir, `init_db()` on app start (idempotent), lightweight migrations for new columns/tables.
- [x] 1.3 Settings helpers `get_setting/set_setting/get_plan_dates` — replace ~10 duplicated queries.
- [x] 1.4 `/setup` only usable while no user exists; password reset stays in Settings (logged in).
- [x] 1.5 Safe `next` redirect (relative paths only); warn when `SECRET_KEY` unset.
- [x] 1.6 CSRF via Flask-WTF `CSRFProtect`; token in every POST form (disabled in test config) + test it's on by default.

### 2. Wire the services into the routes (single source of truth)
- [x] 2.1 Plan generator: whitespace-safe multi-tag matching, pluggable chooser (random in app, deterministic in tests), prefer recipes not yet used in the plan.
- [x] 2.2 `routes/meal_plan.py` loads rules/recipes/manual entries → calls service → inserts. Delete the duplicated SQL generator.
- [x] 2.3 Shopping-list service: key by ingredient id, pantry subtraction with unit conversion, cost estimate per line.
- [x] 2.4 `routes/shopping_list.py` uses the service; page shows persisted items (generated + manual) so check/delete work; regenerate keeps manual items.

### 3. Route bug fixes
- [x] 3.1 Commit in meal-rule and pantry forms; list shows inactive rules; correct active checkbox; validate servings.
- [x] 3.2 Recipe form: robust parsing of ingredient rows (bad numbers don't 500); JSON endpoint for ingredient options (escaped).
- [x] 3.3 Shared `category_label` helper instead of copy-pasted list-index lookups; remove unused imports.

### 4. Templates / UI
- [x] 4.1 Convert all destructive links to POST forms with CSRF token.
- [x] 4.2 Recipe form ingredient rows rebuilt (real named inputs, client-side template).
- [x] 4.3 Meal plan: Generate button as form, swap via recipe dropdown, servings edit, loop over meal types instead of 4 copy-pasted blocks.
- [x] 4.4 Load Font Awesome; add Spend nav item; delete `ingredient_form.html`, `index.html`, `woolworths.html`.

### 5. Spend integration
- [x] 5.1 `purchases` table (order date, basket, product, qty, unit price, line total, stockcode, store, optional `ingredient_id`), unique per basket line.
- [x] 5.2 `services/spend_import.py`: parse Woolworths order-history CSV (upload or CLI `flask import-purchases`), dedupe on re-import.
- [x] 5.3 `services/spend_analysis.py`: pure-Python port of the Woolworths stats (no pandas): totals, monthly spend, shop frequency, top products, per-category spend.
- [x] 5.4 `routes/spend.py`: `/spend` dashboard (stat tiles, monthly chart, top tables, date filter), `/spend/import`, `/spend/products` to link products to ingredients and update ingredient prices from the latest purchase.
- [x] 5.5 Cross-feature: dashboard spend summary; shopping list shows estimated cost vs average spend per shop.

### 6. Cleanup & docs
- [x] 6.1 Remove legacy invoice pipeline modules, `woolworths_analysis.py`, `database_handler.py`, Procfiles; untrack `invoices/` + `data/` (files kept on disk) and gitignore them.
- [x] 6.2 Merge `PLAN.md` + `IMPLEMENTATION_PLAN.md` into README (architecture + spend section); `.env.example` update.
- [x] 6.3 Gunicorn workers default capped (SQLite + many workers = lock contention).

### 7. Verification
- [x] 7.1 New tests: security (setup lockout, open redirect, CSRF), rule/pantry persistence, service changes, spend import/analysis/routes.
- [x] 7.2 Full test run green.
- [x] 7.3 Browser smoke test of the real app: setup → ingredient → recipe with ingredients → rule → generate → shopping list → spend import.
- [x] 7.4 Commit per step group.

## Outcome

- 153 tests passing (was 104): security, spend, and regression suites added.
- Browser smoke test (real app, real order history on a scratch DB) passed:
  setup → import → create ingredient from product → add ingredient → recipe with ingredient rows →
  plan dates → rule → generate plan → shopping list with costs → tick item.
- Smoke test caught two further bugs, both fixed with tests:
  - Woolworths rows with a `null` price (unsupplied items) were reported as errors → now summarised as "no price" and skipped.
  - Price inputs only allowed whole cents (`step=0.01`), so per-gram prices couldn't be entered → `step="any"`; small prices display to 4 dp.

### Deliberately not done
- Cloudflare R2 image uploads: documented but never implemented; docs corrected instead (recipes take an image URL).
- The OpenAI invoice-PDF pipeline was removed rather than ported: it used a removed OpenAI API and MongoDB, and the
  Woolworths CSV export gives cleaner data. Invoice PDFs remain on disk in `invoices/`.
