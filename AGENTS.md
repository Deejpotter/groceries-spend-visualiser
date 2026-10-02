# AGENTS.md

Guidance for AI coding agents working in this repository. Read this before making changes.

## What this project is

A self-hosted household web app for **meal planning, shopping list generation, and grocery spend tracking**. Single-user (one admin account), single SQLite database, deployed via Docker.

- Plan meals by defining simple rules (*"weekday dinner → recipes tagged `standard`"*) and generating a plan for a date range.
- Turn the plan into a shopping list with per-line cost estimates, pantry subtraction, and unit conversion.
- Import a Woolworths order-history CSV to see real spend, then link purchased products to ingredients so price estimates match what you actually pay.

Not a multi-tenant SaaS. There is no API layer, no queue, no external database. Keep that scope in mind — don't add infrastructure the app doesn't need.

## Tech stack

Python 3.11 · Flask 3 · Flask-WTF (CSRF) · SQLite · Jinja2 · Bootstrap 4 · Chart.js · Gunicorn · Docker

## Set up and run

```bash
python -m venv myenv
myenv\Scripts\activate            # macOS/Linux: source myenv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
flask --app app run --debug       # http://127.0.0.1:5000
```

Or with Docker: `cp .env.example .env` then `docker compose up --build`.

The database is created automatically at `./data/groceries.db` (gitignored).

## Run tests

```bash
python -m pytest            # 181 tests
python -m pytest -q         # quiet
python -m pytest tests/test_plan_generator.py   # one file
```

Tests use a throwaway SQLite database per test (see `tests/conftest.py`). **Run the full suite before finishing any change.** There is no lint or typecheck step configured — pytest is the verification gate.

## Architecture

```
app.py            App factory: auth routes, dashboard, CSRF, template filters, CLI
auth.py           Session login helpers
database.py       SQLite connection, schema + migrations, settings helpers
models.py         Constants, unit conversion (metric/imperial), date helpers
routes/           Blueprints: ingredients, recipes, meal_plan, shopping_list,
                  pantry, settings, spend
services/         PURE business logic — no Flask, no SQL, fully unit-testable
  plan_generator.py          Rule-based meal plan generation
  shopping_list_generator.py Ingredient aggregation, pantry subtraction, costs
  spend_import.py            CSV parsing, cup-price parsing, de-duplicated import
  spend_analysis.py          Spend statistics
  repository.py              Loads DB rows into the dicts the pure services use
templates/        Jinja2 (Bootstrap 4). _macros.html has csrf() and post_button()
static/           css/style.css, js/app.js
tests/            Unit tests for services + integration tests via Flask test client
```

### The one rule to follow

**Business logic belongs in `services/`, not in routes or templates.** Routes load data, call pure functions, write results back, and render. If you find yourself writing a formula, a filter, a calculation, or a decision inside a route or a template — move it to `services/`.

## Key conventions

### Python
- **2-space indentation** (not PEP 8's 4). Match the surrounding file.
- Module and function docstrings are used; keep them short and factual.
- Don't add comments unless the *why* is non-obvious. The codebase is deliberately light on comments.
- SQL is always parameterised (`?` placeholders). Never string-format SQL.
- `database.py` handles schema changes via `MIGRATIONS` (list of `(table, column, definition)`) applied with `ALTER TABLE ADD COLUMN`. Add to that list — don't hand-edit existing DBs.

### Templates
- Every state-changing action is a **POST form with a CSRF token**. Use `{% from "_macros.html" import csrf, post_button %}`.
  - `{{ csrf() }}` in a plain form, `{{ post_button(url, 'Label', confirm='...') }}` for one-click destructive actions.
  - **Never** make a delete/remove/toggle a GET link.
- Templates extend `base.html` and fill `title`, `content`, optionally `scripts`.
- Jinja2 auto-escaping is on. Never use `|safe` on user input. Embed JSON with `|tojson`.
- External links opening in a new tab need `rel="noopener noreferrer"`.
- CDN resources carry `integrity` (SRI) and `crossorigin="anonymous"`.

### Security invariants — do not break these
- `safe_next_url()` restricts post-login redirects to relative paths (no open redirect).
- `/setup` is disabled once a user exists; the env-configured admin is created before the setup form renders.
- Every state-changing route is `POST` only, protected by `@login_required` and CSRF.
- Passwords are hashed with `werkzeug.security`; never log or store plaintext.

## Common tasks

**Add a route** → add it to the right blueprint in `routes/`, use `@login_required`, delegate to `services/`, redirect with `flash()` for feedback.

**Add a service function** → put it in `services/<area>.py` as a pure function over plain dicts. Write tests for it in `tests/test_<area>.py` without Flask. `services/*` files have `make_*` helper factories at the bottom for building test data — reuse them.

**Change the DB schema** → add the column/table to `SCHEMA` in `database.py`, and add a `MIGRATIONS` entry if existing installs need `ALTER TABLE ADD COLUMN`.

**Change the UI** → templates are server-rendered. Prefer `<details>/<summary>` for progressive disclosure over JavaScript. Keep JS minimal.

## Feature notes worth knowing

- **Meal plan generation** (`services/plan_generator.py`): rules are tried in `sort_order`, first match wins. `day_of_week` is comma-separated (`mon,wed,fri`) and also supports `weekday`, `weekend`, `all`. Manual entries are preserved across regeneration. Recipes can cover N days; continuations use the next free slot of the same meal type and link to the original entry so ingredients aren't bought twice. Removing or replacing an original meal promotes its continuations to standalone meals.
- **Shopping list** (`services/shopping_list_generator.py`): aggregates ingredients across the plan, scales by servings, merges compatible units (g + kg → one line), optionally subtracts pantry stock (keyed by **ingredient id**), and estimates cost per line.
- **Spend import** (`services/spend_import.py`): de-duplicated by `UNIQUE(store, basket_id, product_name)`. Cup prices like `$1.51 / 100G` are parsed and converted to per-unit prices. Unpriced rows are skipped with a summary, not an error.

## Git and deployment

- **Branches:** `dev` (staging on Coolify, auto-deploys) → `main` (production, via PR from `dev`).
- **Commit style:** Conventional Commits (`feat:`, `fix:`, `refactor:`, `docs:`, `chore:`, `test:`) with a short imperative subject and a body bullet-listing the logical changes.
- **CI** (`.github/workflows/ci.yml`) runs pytest and a Docker build + `/health` check on pushes and PRs to `dev` and `main`.
- `GET /health` returns `{"status": "ok"}` — used by CI and the deploy health check. Keep it unauthenticated and cheap.

### Data persistence — don't break this

All data is one SQLite file at `/app/data/groceries.db`. It survives deploys **only** if that
directory is a persistent volume; otherwise each deploy starts empty and the loss is silent.
`docker-compose.yml` mounts `grocery-data:/app/data`, but Coolify only honours that when the
service uses the *Docker Compose* buildpack — the *Dockerfile* buildpack needs a Storage
mount on `/app/data` configured in the Coolify UI.

- `init_db()` returns `False` when it creates a brand-new file; `app.py` logs a loud warning
  in that case so missing volume mounts are obvious in the logs. Keep that warning.
- `flask --app app db-status` — path, size, row counts. Fastest way to confirm data survived.
- `flask --app app backup-db` — timestamped copy via SQLite's online backup API (safe live).
- `SECRET_KEY` must be set in each environment, or sessions reset every deploy.

## Reference docs

- `README.md` — user-facing features, quick start, configuration.
- `architecture.md` — detailed architecture, data model, security, deployment.
- `REFACTOR_PLAN.md` — historical record of the major refactor (all steps complete).
