# Grocery Visualiser

A self-hosted meal planner, shopping-list generator and grocery spend tracker.
Plan meals from simple rules, turn the plan into a shopping list, and see what you
actually spend from your supermarket order history — all in one small Flask app backed
by a single SQLite file.

---

## Features

**Ingredients** — name, category, unit, price per unit, store, product link, minimum stock.

**Recipes** — servings, prep/cook time, source and image URLs, instructions, tags
(`standard`, `quick`, `friday-special`, …), a *Covers N days* setting for batch meals, and a list of ingredients with quantities (optionally in a different unit to the ingredient's own).

**Meal rules and plan generation** — rules say which recipes can fill which slots,
e.g. *"weekday dinner → tagged `standard`"* or *"weekend breakfast → tagged `breakfast`"*.
Rules are tried in priority order; the first matching rule with a matching recipe wins.
Generation picks randomly among matching recipes, prefers ones not already in the plan,
and places batch-meal continuations in the next free slots of the same meal type. Meals you pick or change by hand are
marked *manual* and are kept when you regenerate.

**Shopping list** — aggregates every ingredient across the plan, scaled by servings,
grouped by category, with an estimated cost per line. Optional pantry subtraction
(with unit conversion) and metric/imperial display. Tick items off as you shop; manual
items and ticks survive a regenerate. Print-friendly.

**Pantry** — stock with quantity, location and expiry date; expiry highlighting;
"use" an amount after cooking.

**Spend tracking** — import a purchase-history CSV (the Woolworths order-history export
works as-is). The spend dashboard shows total, monthly and per-shop spend, days between
shops, a monthly chart, and your most frequently bought and highest-spend products, over
3/6/12 months or all time. Link purchased products to your ingredients to:
- see spend by category, and
- update ingredient prices from your latest purchase (using the shelf "cup price", e.g.
  `$1.51 / 100G` → `$15.10 / kg`), so shopping-list estimates match what you really pay.

The shopping list shows your average shop spend next to the list's estimated cost.

---

## Quick start

### Docker (recommended)

```bash
cp .env.example .env     # set SECRET_KEY, and optionally ADMIN_USERNAME / ADMIN_PASSWORD
docker compose up --build
# open http://localhost:5000
```

### Local Python

```bash
python -m venv myenv
myenv\Scripts\activate            # macOS/Linux: source myenv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
flask --app app run --debug       # http://127.0.0.1:5000
```

The database is created automatically (default `./data/groceries.db`).

### First run

1. On first visit you're sent to **/setup** to create the admin account (or it's created
   automatically from `ADMIN_USERNAME` / `ADMIN_PASSWORD`). `/setup` is disabled once an
   account exists — change the password later under **Settings**.
2. Add ingredients (or import purchases and create ingredients from them under
   **Spend → Link products**).
3. Add recipes and link their ingredients.
4. Set plan dates in **Settings**, add **Meal Rules**, then **Generate** on the Meal Plan page.
5. Generate the **Shopping List**.

### Importing purchases

Upload a CSV under **Spend → Import purchases**, or from the command line:

```bash
flask --app app import-purchases data/woolworths_order_history.csv
```

Columns: `date`, `product_name`, `quantity`, and `unit_price` or `line_total`; optional
`basket_id`, `channel`, `cup_price`, `stockcode`. Re-importing the same file skips lines that
are already there.

---

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `SECRET_KEY` | random per start | Session signing key. **Set this**, or everyone is logged out on each restart. |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | — | Create the admin automatically if no account exists yet. |
| `DATABASE_PATH` | `./data/groceries.db` (`/app/data/groceries.db` in Docker) | SQLite file location. |
| `PORT` | `5000` | Listen port. |
| `GUNICORN_WORKERS` | `2` | Worker processes (keep low: SQLite allows one writer at a time). |
| `APP_ENV` | — | Set to `staging` to show the staging banner. Leave unset in production. |

---

## Data persistence (important)

Everything — your admin account, recipes, meal plans, imported purchases — lives in one
SQLite file at **`/app/data/groceries.db`** (inside the container). If that directory is
not a **persistent volume**, every deploy creates a brand-new empty database and your data
is silently gone. The app can't prevent this from inside the container, so make sure the
volume is mounted.

`docker-compose.yml` already mounts a named volume (`grocery-data:/app/data`), so
`docker compose up` is safe out of the box. **Coolify does not read `docker-compose.yml`
unless you tell it to** — check which mode your service uses:

| Coolify buildpack | How to persist |
|---|---|
| **Docker Compose** | Nothing to do — the volume is in `docker-compose.yml`. |
| **Dockerfile** | Services → your app → **Storage**, then add a persistent mount: host path (or Coolify volume) → **`/app/data`**. Deploy again. |

Do the same on **both** the `dev` (staging) and `main` (production) services — they need
their own separate volumes and their own `SECRET_KEY`.

### Check that it worked

After any deploy, look at the container logs:

- `Created a NEW empty database at ...` → **the volume isn't mounted.** Fix it before entering any data.
- No such line → the existing database was picked up.

Or ask the app directly (inside the container, or locally with the venv active):

```bash
flask --app app db-status      # path, file size, row counts per table
flask --app app backup-db      # timestamped copy, safe to run while the app is live
```

`db-status` is the quickest way to confirm your admin account and data survived a deploy.

### Also set `SECRET_KEY`

A missing `SECRET_KEY` means a fresh random key on every restart, so everyone is logged out
on each deploy. That is not data loss — your rows are still there — but it feels like it.
Set `SECRET_KEY` in the environment to keep sessions stable.

### Backups

`flask --app app backup-db` writes `groceries-backup-<timestamp>.db` next to the live
database using SQLite's online backup API, so it is safe to run while the app is serving.
Take one before a risky migration or a major upgrade.

---

## Deploying

Build the `Dockerfile`, set the env vars above, mount a persistent volume at `/app/data`
(see [Data persistence](#data-persistence-important)), and use `GET /health` — it returns
`{"status": "ok"}` — as the health check. `docker-compose.yml` is a working reference.

---

## Branches and environments

| Branch | Deploys to | Purpose |
|--------|-----------|---------|
| `dev`  | Staging (Coolify, `APP_ENV=staging`) | Day-to-day work. Push or merge here; staging auto-deploys. |
| `main` | Production (Coolify) | Only updated by a PR from `dev` once staging looks good. |

CI (`.github/workflows/ci.yml`) runs the tests and a Docker build + `/health` check on every push
and PR to `dev` and `main`. Staging shows a yellow banner so it can't be mistaken for the live site,
and uses its own database volume and `SECRET_KEY`.

Release: open a PR `dev → main` (`gh pr create --base main --head dev`), wait for CI, merge.

## Development

```bash
pip install -r requirements-dev.txt
python -m pytest
```

### Project structure

```text
app.py                 App factory: auth routes, dashboard, CSRF, template filters, CLI
auth.py                Session login helpers
database.py            SQLite connection, schema + migrations, settings helpers
models.py              Constants, unit conversion, date helpers
routes/                Blueprints: ingredients, recipes, meal_plan (rules + plan),
                       shopping_list, pantry, settings, spend
services/
  plan_generator.py          Pure rule-based plan generation
  shopping_list_generator.py Pure ingredient aggregation, pantry subtraction, costs
  spend_import.py            CSV parsing, cup-price parsing, de-duplicated import
  spend_analysis.py          Pure spend statistics
  repository.py              Loads DB rows into the dicts the pure services use
templates/             Jinja templates (Bootstrap 4); _macros.html has CSRF/POST helpers
tests/                 Unit tests for services, integration tests via the Flask test client
```

Routes load data and call the pure functions in `services/`; keep business logic there so it
stays testable without Flask. Every state-changing action is a POST form carrying a CSRF token
(`{% from "_macros.html" import csrf, post_button %}`).

## Tech stack

Python 3.11 · Flask 3 · Flask-WTF (CSRF) · SQLite · Bootstrap 4 · Chart.js · Gunicorn · Docker

## License

See [LICENSE](LICENSE).
