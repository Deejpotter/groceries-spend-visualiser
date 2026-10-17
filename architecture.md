# Architecture

## Overview

A self-hosted Flask 3 + SQLite household app for **meal planning, shopping list generation, and grocery spend tracking**. It imports Woolworths order-history CSVs, links purchased products to ingredients, and provides spend analytics. Runs in Docker with Gunicorn, deployed to Coolify (dev/staging on `dev` branch, production on `main`).

## Tech Stack

Python 3.11 · Flask 3 · Flask-WTF (CSRF) · SQLite · Bootstrap 4 · Chart.js · Gunicorn · Docker · HTMX

## Layered Architecture

The codebase follows a clean **routes → services → repository** separation:

```
app.py (factory, auth, dashboard, template filters, CLI)
├── routes/          — Blueprints: HTTP concerns only (form parsing, redirects, flashes)
│   ├── __init__.py       — central registration of all 7 blueprints
│   ├── ingredients.py    — CRUD, search/filter, JSON options endpoint
│   ├── recipes.py        — CRUD with dynamic ingredient rows, tag management
│   ├── meal_plan.py      — meal rules CRUD, plan generation, manual add/swap/remove
│   ├── shopping_list.py  — generate from plan, toggle/delete, manual items
│   ├── pantry.py         — stock management, expiry tracking, consume
│   ├── spend.py          — CSV/PDF import, import review, dashboard, product-to-ingredient linking
│   └── settings.py       — plan dates, preferences, password change
│
├── services/        — Pure Python: no Flask or SQL dependency
│   ├── plan_generator.py          — rule-based plan generation, multi-day continuation
│   ├── shopping_list_generator.py — ingredient aggregation, pantry subtraction, costs
│   ├── spend_import.py            — CSV parsing, cup-price parsing, PDF invoice parsing,
│   │                               staged import batches (review/edit/confirm), de-duplicated import
│   ├── spend_analysis.py          — pure spend statistics
│   └── repository.py              — loads DB rows into the dicts the pure services use
│
├── database.py      — SQLite connection, schema, idempotent migrations, settings KV
├── auth.py          — session-based single-user authentication
├── models.py        — constants, unit conversion (metric ↔ imperial), date helpers
│
├── templates/       — Jinja2 templates (Bootstrap 4)
│   ├── base.html         — root layout, navbar, flash messages, env banner
│   ├── _macros.html      — CSRF macro, POST-button macro (enforced on all destructive actions)
│   └── <feature>/        — per-feature templates
│
├── static/          — CSS overrides (style.css) and minimal shared JS (app.js)
│   └── js/
│       └── htmx.min.js    — HTMX library (self-hosted, for async page updates)
└── tests/           — pytest: unit tests for services, integration tests via Flask test client
```

### Data Flow

1. **Route** receives the HTTP request, parses/validates form data, loads data from the DB.
2. **Repository** (`repository.py`) converts SQLite rows into plain dicts.
3. **Service** performs pure business logic on those dicts, returns a result.
4. **Route** writes the result back to the DB and renders a template or redirects.

This keeps business logic independently testable — every service module has dedicated unit tests that don't require Flask.

## Database

Single SQLite file (default `./data/groceries.db`). Schema is created idempotently on startup; column-level migrations are applied via `ALTER TABLE ADD COLUMN` guards. A `_migrate_purchase_identity` function handles a one-time schema change to the `purchases` table's unique constraint.

### Key Tables

| Table | Purpose |
|---|---|
| `users` | Single admin account |
| `settings` | Key/value store for plan dates, unit preferences, etc. |
| `ingredients` | Name, category, unit, price, store, product URL, min stock |
| `recipes` | Servings, prep/cook time, tags, coverage days, instructions |
| `recipe_ingredients` | Links recipes to ingredients with quantity and optional unit override |
| `meal_rules` | Day-of-week + meal-type + tag-filter rules, with priority ordering |
| `meal_plan_entries` | Generated or manual plan slots (date, meal_type, recipe, servings) |
| `shopping_list_items` | Aggregated list with tick state, manual items, cost estimates |
| `pantry_items` | Stock per ingredient with quantity, unit, expiry, location |
| `purchases` | Imported order lines (Woolworths invoices/CSV), linked to ingredients |
| `import_batches` / `import_staged_lines` | Pending PDF-import review batches and their editable lines |

Indexes: `idx_purchases_date` on `purchases(order_date)`, `idx_plan_date` on `meal_plan_entries(date, meal_type)`.

## Authentication

Single-user session-based auth via Flask `session` cookies. Passwords hashed with Werkzeug's `generate_password_hash`. On first visit, the app redirects to `/setup` to create the admin account. `ADMIN_USERNAME` / `ADMIN_PASSWORD` env vars can auto-create the admin on first request.

### HTTP API auth (`/api/v1`)

`routes/api.py` exposes the same services over JSON with **bearer-token auth**: the
`Authorization: Bearer <key>` header, where the key is the `mcp_api_key` setting
(rotated with `flask --app app set-api-key`). Because the app is single-admin, there
is one shared key rather than per-user tokens. `/api/v1/health` is unauthenticated
(CI and deploy checks use it); every other endpoint returns 401 with
`{error: {code: "unauthorized"}}` on a missing or wrong key. The `before_request`
setup-redirect is skipped for the `api` blueprint — it authenticates itself.

This is how the MCP server (`mcp_server/server.py`) reaches remote deployments: it
is a thin stdio client that calls `/api/v1`, so `GROCERIES_URL` alone selects local
dev, staging or production. The API key is therefore a real remote credential —
HTTPS only, stored in the secrets store like any other.

## Security Measures

- CSRF protection on every form via Flask-WTF; enforced by a `csrf()` macro in `_macros.html`.
- All destructive actions use POST (never GET links), enforced by the `post_button` macro.
- `safe_next_url()` validates login-redirect targets as relative paths, blocking open redirects.
- `/setup` is disabled once any user exists; env-configured admin is created before the setup form is shown.
- JSON endpoints (ingredient options) avoid rendering user input as HTML (XSS prevention).
- `SECRET_KEY` warning logged when unset.
- Docker runs as non-root user (`appuser`).
- Parameterised SQL queries throughout — no injection risk.

## Unit Conversion

`models.py` defines a conversion system for metric and imperial weight/volume units. Base units are kg and L. Recipes can specify ingredient quantities in a different unit than the ingredient's own; the shopping list merges compatible units and converts for display. Count units (each, pack, can, etc.) are not converted.

## Meal Plan Generation

Rules are tried in priority order per slot (date × meal_type). The first matching rule with a matching recipe wins. Among candidates, the generator prefers recipes not already in the plan. Recipes can cover multiple days; continuations fill the next free slot of the same meal type, preserve servings, and link to the original entry so ingredients aren't counted twice. Manual entries (added/changed by hand) are preserved across regenerations.

## Spend Tracking

Import a Woolworths PDF tax invoice (or a CSV). A PDF upload is first staged as a pending
import batch (`import_batches` / `import_staged_lines`) and shown on a review page with every
parsed line editable and the read total cross-checked against the printed subtotal; confirming
writes the approved lines to the `purchases` table (de-duplicated by `UNIQUE(store, basket_id, product_name)`,
where a PDF invoice's order number is the basket id, so re-imports are recognised and flagged per line).
The spend dashboard computes total/monthly/per-shop spend, days between shops, and top products by frequency and spend over 3/6/12 months or all time. Purchased products can be linked to ingredients; the cup price (e.g. `$1.51 / 100G`) is parsed and converted to a per-unit price so shopping-list estimates match real spend.

## Deployment

### Branches

| Branch | Deploys to | Purpose |
|---|---|---|
| `dev` | Staging (Coolify, `APP_ENV=staging`) | Day-to-day work; auto-deploys on push |
| `main` | Production (Coolify) | Updated by PR from `dev` once staging is verified |

### CI

`.github/workflows/ci.yml` runs on every push and PR to `dev` and `main`:
1. **test** — `pip install` + `pytest -q`
2. **docker** — build image + run container + curl `/health` smoke test

### Docker

Dockerfile uses `python:3.11-slim`, installs deps with `--no-cache-dir`, creates a non-root `appuser`, sets up data directories, and runs Gunicorn. `docker-compose.yml` uses a named volume (`grocery-data`) for persistent data so the non-root user can write the DB.

### Data persistence

The entire application state is one SQLite file at `/app/data/groceries.db`. It only survives a deploy if that directory is a **persistent volume** — otherwise each deployment ships a fresh container, `init_db()` creates an empty schema, and all data is silently lost.

`docker-compose.yml` mounts `grocery-data:/app/data`. Coolify reads that file only when the service uses the *Docker Compose* buildpack; the *Dockerfile* buildpack requires a Storage mount on `/app/data` configured per service in the Coolify UI. Staging and production must use separate volumes and separate `SECRET_KEY` values.

Because silent loss is the failure mode, `init_db()` reports whether the database file pre-existed and `app.py` logs a warning when it creates a new one. Two CLI commands support operations:

| Command | Purpose |
|---|---|
| `flask --app app db-status` | Path, file size, row counts per table — verify data survived a deploy. |
| `flask --app app backup-db` | Timestamped copy via SQLite's online backup API; safe to run while serving. |
| `flask --app app init-db` | Idempotent schema create/upgrade (also runs on app start). |

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `SECRET_KEY` | random per start | Session signing key. **Set this** in production. |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | — | Auto-create admin if no user exists yet. |
| `DATABASE_PATH` | `./data/groceries.db` | SQLite file location. |
| `PORT` | `5000` | Listen port. |
| `GUNICORN_WORKERS` | `2` | Worker processes (keep low: SQLite allows one writer at a time). |
| `APP_ENV` | — | Set to `staging` to show the staging banner. |

## Testing

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest
```

181 tests across 8 files. Each test gets an isolated temp database. Service modules are unit-tested with dict factories; routes are tested through Flask's test client.

| Test File | Coverage |
|---|---|
| `test_integration.py` | Full user workflows: auth, CRUD, meal plan, shopping list, pantry, settings |
| `test_plan_generator.py` | Plan generation algorithm, multi-day continuations, manual overrides |
| `test_shopping_list_generator.py` | Unit conversion, aggregation, pantry subtraction, cost estimation |
| `test_spend.py` | CSV parsing, cup-price parsing, import idempotency, analysis |
| `test_spend_pdf.py` | PDF invoice parsing (incl. NET weights, subs, totals labels), staged review/edit/confirm flow |
| `test_security.py` | Setup lockout, open redirect, CSRF, POST-only enforcement |
| `test_review_fixes.py` | Regression tests for PR #1 review findings |
| `test_regressions.py` | Specific bugs from the refactor |
| `test_persistence.py` | Fresh-database detection, online backups, database stats |

## Async Page Updates (HTMX)

Most state-changing actions use **HTMX** for partial page updates instead of full page reloads. HTMX is self-hosted at `static/js/htmx.min.js` and loaded in `base.html`. A CSRF meta tag (`<meta name="csrf-token">`) plus a global `htmx:configRequest` listener attach the CSRF token to every HTMX request automatically, so existing Flask-WTF CSRF protection works unchanged.

### Pattern

Every HTMX-enabled action follows the same pattern:

1. **Template** adds `hx-post`, `hx-target`, and `hx-swap` attributes to a form or button.
2. **Route** checks for the `HX-Request` header. On HTMX requests, it renders a small partial template (in `templates/<feature>/_*.html`) instead of redirecting.
3. **Without JavaScript**, the same route falls back to a normal POST + redirect (progressive enhancement).

### Partials

| Partial | Purpose |
|---|---|
| `templates/shopping_lists/_item_row.html` | Shopping list row (tick toggles in place) |
| `templates/pantry/_item_row.html` | Pantry row (consume/delete without reload) |
| `templates/subscriptions/_sub_row.html` | Active subscription row (mark paid / pause) |
| `templates/subscriptions/_inactive_row.html` | Paused subscription row (resume / purge) |
| `templates/settings/_save_status.html` | Settings save confirmation ("Saved.") |
| `templates/meal_plan/_slot_filled.html` | Meal plan slot with recipe (swap/add/remove) |
| `templates/meal_plan/_slot_empty.html` | Empty meal plan slot (add meal) |

### CSRF

HTMX requests include `X-CSRFToken` header via the global config listener. Non-HTMX POST forms include `{{ csrf() }}` hidden input as before. Both paths are protected by Flask-WTF CSRF validation.
