# Grocery Visualiser — Implementation Plan

## Overview

Transform the existing Flask-based Grocery Spend Visualiser into a self-contained, Docker-deployable meal planning and grocery list generation app with Grocy-like features. The app is designed for single-user self-hosting on Coolify, Render, or any Docker-compatible platform.

---

## Architecture Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Database | SQLite | Single file, no external service, portable |
| Image storage | Local `static/uploads/` default, Cloudflare R2 via env vars if configured | Self-contained by default, optional cloud storage |
| Admin setup | Option C: env vars `ADMIN_USERNAME` + `ADMIN_PASSWORD` for auto-setup on first boot, plus `/setup` web UI for manual creation/reset | Flexible for both Docker and manual installs |
| Starting data | Empty — first-run wizard creates admin, then user populates ingredients/recipes/rules | Clean slate, no unwanted seed data |
| Unit system | Metric default, imperial toggle in settings | Australian context (metric) with flexibility |
| Recipe servings | Default servings on recipe, override per meal plan entry | Flexibility for different household sizes |
| Recipe duration | `is_two_night` boolean on recipe; generation handles cascading | Supports your cooking patterns |
| Meal plan model | Single active plan, rule-driven generation, manual override allowed | Simple, automated, with escape hatches |
| Auth | Session-based, single admin user, password hashed with werkzeug | Simple, secure enough for personal use |

---

## Data Model

### Users
```
users (id, username, password_hash, created_at)
```

### Settings (key-value store)
```
settings (key, value)
```
Keys: `plan_start_date`, `plan_end_date`, `unit_preference` (metric/imperial), `default_servings`, `subtract_pantry_from_list` (0/1)

### Ingredients
```
ingredients (id, name, category, unit, price, url, store, minimum_stock, created_at)
```
Categories: produce, meat, dairy, bakery, pantry, frozen, household, other
Units: kg, g, L, mL, each, pack, bunch, gram, ounce, pound, etc.

### Recipes
```
recipes (id, name, description, servings, prep_time, cook_time, source_url, image_url, instructions, is_two_night, tags, created_at)
```
Tags stored as comma-separated TEXT (e.g., "standard, dinner, italian")

### Recipe-Ingredient Link
```
recipe_ingredients (id, recipe_id, ingredient_id, quantity, unit_override)
```
Links recipes to ingredients with quantity. `unit_override` allows using a different unit than the ingredient's default.

### Meal Rules
```
meal_rules (id, day_of_week, meal_type, tag_filter, servings_override, is_active, sort_order)
```
- `day_of_week`: mon, tue, wed, thu, fri, sat, sun, weekday, weekend, all
- `meal_type`: breakfast, lunch, dinner, snack
- `tag_filter`: comma-separated tags or NULL for "any recipe"
- Rules are evaluated in `sort_order` — user controls priority

### Meal Plan Entries
```
meal_plan_entries (id, date, meal_type, recipe_id, servings, is_auto_generated, source_rule_id)
```
Single active plan concept — entries exist within the configured date range.

### Shopping List Items
```
shopping_list_items (id, ingredient_name, quantity, unit, category, checked, is_manual, meal_date, recipe_ref, created_at)
```
Generated fresh each time from the current plan. Persisted so user can check off items over multiple shopping trips.

### Pantry Items
```
pantry_items (id, ingredient_id, quantity, unit, expiry_date, location, updated_at)
```
Tracks what you have at home. Optional integration with shopping list.

---

## Generation Algorithm

```
function generate_plan(start_date, end_date):
    delete_entries_in_range(start_date, end_date)
    
    for each date in range(start_date, end_date):
        day_key = date.strftime('%a').lower()
        is_weekday = date.weekday() < 5
        is_weekend = not is_weekday
        
        for each meal_type in ['breakfast', 'lunch', 'dinner', 'snack']:
            rules = get_active_rules_for_day_meal(day_key, is_weekday, is_weekend, meal_type)
            
            for rule in rules:
                existing = get_entry(date, meal_type)
                if existing and existing.is_auto_generated == 0:
                    continue  # Respect manual overrides
                
                recipe = pick_recipe(rule.tag_filter)
                if not recipe:
                    continue
                
                servings = rule.servings_override or recipe.servings
                
                create_entry(date, meal_type, recipe.id, servings, rule.id)
                
                if recipe.is_two_night:
                    next_date = date + 1 day
                    if next_date <= end_date:
                        next_existing = get_entry(next_date, meal_type)
                        if not next_existing:
                            create_entry(next_date, meal_type, recipe.id, servings, rule.id)
```

**Shopping list generation**:
```
function generate_shopping_list(start_date, end_date):
    items = {}
    for each entry in meal_plan_entries where date in range:
        recipe = get_recipe(entry.recipe_id)
        for each ri in recipe_ingredients:
            ingredient = get_ingredient(ri.ingredient_id)
            scaled_qty = ri.quantity * (entry.servings / recipe.servings)
            key = (ingredient.name, convert_unit(ri.unit_override or ingredient.unit, settings.unit_preference))
            if key in items:
                items[key].quantity += scaled_qty
            else:
                items[key] = {name, quantity: scaled_qty, unit, category: ingredient.category, recipe_ref: recipe.name}
    
    if settings.subtract_pantry_from_list:
        for each item in items:
            pantry = get_pantry_item_by_name(item.name)
            if pantry:
                item.quantity = max(0, item.quantity - pantry.quantity)
                if item.quantity == 0:
                    remove item
    
    return sorted(items, by category)
```

---

## File Inventory

### Configuration & Deployment
| File | Status | Purpose |
|------|--------|---------|
| `requirements.txt` | Done | Flask, gunicorn, python-dotenv, boto3 (optional R2) |
| `.env.example` | Done | All env vars documented |
| `Dockerfile` | Done | Container build |
| `docker-compose.yml` | Done | Local dev orchestration |
| `.dockerignore` | Done | Exclude files from image |
| `gunicorn.conf.py` | Done | Gunicorn production config |
| `DOCKER.md` | Not created | Docker deployment guide (optional) |
| `README.md` | Done | Full feature + setup documentation |
| `IMPLEMENTATION_PLAN.md` | Done | This file |

### Core Application
| File | Status | Purpose |
|------|--------|---------|
| `app.py` | Done | Flask app factory, route registration, DB init, auth, health endpoint, dashboard |
| `database.py` | Done | SQLite connection, init, query helpers |
| `init_db.py` | Done | CLI DB initialization script |
| `models.py` | Done | Constants, validation, unit conversion, date helpers |
| `auth.py` | Done | Login/logout, session, decorator, password hashing |

### Route Modules
| File | Status | Purpose |
|------|--------|---------|
| `routes/__init__.py` | Done | `register_all_routes(app)` — imports and registers all blueprints |
| `routes/ingredients.py` | Done | Ingredient CRUD |
| `routes/recipes.py` | Done | Recipe CRUD |
| `routes/meal_rules.py` | Done | Rule CRUD |
| `routes/meal_plan.py` | Done | Plan view, generation trigger, swap/remove/edit, manual add |
| `routes/shopping_list.py` | Done | List view, generation, toggle/delete/clear, manual add |
| `routes/pantry.py` | Done | Pantry CRUD |
| `routes/settings.py` | Done | App settings (dates, units, servings, pantry toggle, password) |

### Service Modules
| File | Status | Purpose |
|------|--------|---------|
| `services/plan_generator.py` | Done | Rule-based plan generation algorithm |
| `services/shopping_list_generator.py` | Done | Aggregation + unit conversion logic |
| `services/image_upload.py` | Not created | R2/local upload abstraction was planned but not implemented; image uploads are handled inline in `routes/recipes.py` using boto3 directly |

### Templates
| File | Status | Purpose |
|------|--------|---------|
| `templates/base.html` | Done | Layout wrapper with navbar |
| `templates/dashboard.html` | Done | Homepage with stats |
| `templates/index.html` | Done | Legacy index (redirects to dashboard) |
| `templates/setup.html` | Done | Admin setup / password reset |
| `templates/login.html` | Done | Login form |
| `templates/change_password.html` | Done | Change password form |
| `templates/ingredients/list.html` | Done | Ingredient table |
| `templates/ingredients/form.html` | Done | Ingredient form |
| `templates/recipes/list.html` | Done | Recipe cards |
| `templates/recipes/form.html` | Done | Recipe form (add/edit) |
| `templates/recipes/detail.html` | Done | Recipe detail |
| `templates/meal_rules/list.html` | Done | Rules table |
| `templates/meal_rules/form.html` | Done | Rule form |
| `templates/meal_plan/view.html` | Done | Plan display |
| `templates/shopping_lists/view.html` | Done | Shopping list |
| `templates/pantry/list.html` | Done | Pantry table |
| `templates/pantry/form.html` | Done | Pantry form |
| `templates/settings.html` | Done | Settings form |

### Static Assets
| File | Status | Purpose |
|------|--------|---------|
| `static/css/style.css` | Done | Custom styles |
| `static/js/app.js` | Done | Dynamic form behaviors |
| `static/uploads/` | Created dir | Local image storage (.gitignore'd) |

### Test Infrastructure
| File | Status | Purpose |
|------|--------|---------|
| `tests/conftest.py` | Done | Pytest fixtures: `app`, `client`, `db` |
| `tests/test_integration.py` | Done | 47 integration tests covering full workflows |
| `tests/test_plan_generator.py` | Done | Unit tests for plan generation logic |
| `tests/test_shopping_list_generator.py` | Done | Unit tests for shopping list generation |

---

## What Was Built

The implementation followed the plan above with these phases completed:

1. **Phase 0** — Docker files, requirements, test build ✓
2. **Phase 1** — Database layer, auth, app skeleton, first-run detection ✓
3. **Phase 2** — Ingredient CRUD + UI ✓
4. **Phase 3** — Recipe CRUD + UI (with ingredient linking) ✓
4. **Base templates** — Dashboard, login, setup, base layout ✓
5. **Phase 4** — Meal rules + plan generation engine + UI ✓
6. **Phase 5** — Shopping list generation + UI ✓
7. **Phase 6** — Pantry + UI ✓
8. **Phase 7** — Settings page, change password, final polish ✓
9. **Testing** — 47 integration tests + 57 unit tests ✓

The `services/image_upload.py` module was planned but not created. Image uploads
are handled inline in `routes/recipes.py` using boto3 directly when R2 credentials
are configured, with local filesystem fallback.

---

## Environment Variables

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `FLASK_ENV` | No | production | Flask environment |
| `PORT` | No | 5000 | Port to listen on |
| `SECRET_KEY` | No | (random) | Session secret — set for production |
| `ADMIN_USERNAME` | No | — | Auto-create admin on first boot |
| `ADMIN_PASSWORD` | No | — | Admin password (used with ADMIN_USERNAME) |
| `DATABASE_PATH` | No | /app/data/groceries.db | SQLite database path |
| `UPLOAD_DIR` | No | /app/static/uploads | Local upload directory |
| `R2_BUCKET` | No | — | Cloudflare R2 bucket name |
| `R2_ACCESS_KEY` | No | — | R2 access key |
| `R2_SECRET_KEY` | No | — | R2 secret key |
| `R2_ACCOUNT_ID` | No | — | Cloudflare account ID |
| `R2_DOMAIN` | No | `<bucket>.r2.dev` | Custom domain or default R2 domain |

When R2 vars are present → uploads go to R2. Otherwise → local storage.

### Auto-Created Admin (Option C)

If `ADMIN_USERNAME` and `ADMIN_PASSWORD` are both set AND no users exist in the database, an admin account is created automatically on first boot. If a user already exists (e.g., you created one via the `/setup` UI), the env vars are ignored. You can always reset the password later via `/setup`.

---

## Open Questions (Minor — Non-Blocking)

| # | Question | Recommendation |
|---|----------|---------------|
| A | Should recipe tags have suggested defaults shown as clickable chips? | Yes — common tags as chips, free text still allowed |
| B | Should there be a "Clear plan" button to reset all entries? | Yes — with confirmation dialog |
| C | Should shopping list persist or regenerate fresh each time? | Fresh each time — simpler, no stale data |
| D | Should pantry auto-decrement when recipe cooked? | No — manual "consume" action is enough for v1 |
| E | Should shopping list export to CSV? | No — print (window.print()) covers this for v1 |

---

## Deployment Targets

- **Coolify**: Single Docker service, connect R2 via env vars if desired
- **Render**: Docker web service with env vars in dashboard
- **Local Docker**: `docker compose up` for development
- **Manual**: `python init_db.py && flask run` for development without Docker

### Test Suite

The app has 104 tests: 47 integration tests (`test_integration.py`) covering full user workflows via the Flask test client, and 57 unit tests (`test_plan_generator.py` + `test_shopping_list_generator.py`) covering the generation algorithms in isolation. Run with `pytest`.
