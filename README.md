# Grocery Visualiser

A self-contained, Docker-deployable meal planning and grocery list generation app.
Plan your meals for any period, set up rules to automate meal selection, and generate
shopping lists — all in one place. Inspired by Grocy, built for simplicity.

![Flask](https://img.shields.io/badge/Flask-%23000.svg?style=for-the-badge&logo=Flask&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-%2307405e.svg?style=for-the-badge&logo=sqlite&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-%232496ED.svg?style=for-the-badge&logo=docker&logoColor=white)
![Bootstrap](https://img.shields.io/badge/Bootstrap-%23859BC7.svg?style=for-the-badge&logo=bootstrap&logoColor=white)

---

## Features

### Ingredient Management
Add your groceries with prices, units, store links, and minimum stock levels.
Ingredients are the building blocks — everything else references them.

- Name, category (produce, meat, dairy, bakery, pantry, frozen, household, other)
- Unit (kg, g, L, mL, each, pack, bunch, etc.)
- Price per unit, product URL, store name
- Minimum stock threshold for low-stock alerts

### Recipe Management
Store your recipes with full ingredient lists, instructions, and metadata.

- Name, description, servings, prep time, cook time
- Source URL (where you found the recipe)
- Image upload (local or Cloudflare R2)
- Instructions (free text)
- **Tags** — label recipes as `standard`, `single-night`, `friday-special`, `quick`, `dessert`, etc.
- **Two-night flag** — mark recipes that cover two nights (e.g., roast chicken on Friday and Saturday)
- Dynamic ingredient list — select from your ingredient database, set quantities per recipe

### Rule-Based Meal Planning
Instead of manually assigning meals to every day, set up **rules** that tell the app
how to fill your meal plan automatically.

**Example rules:**
- "Monday–Friday dinners → recipes tagged `standard` (two-night meals)"
- "Friday dinners → recipes tagged `friday-special` (single-night)"
- "Saturday & Sunday breakfasts → recipes tagged `breakfast`"
- "Weekday lunches → any recipe"

Rules are evaluated in priority order. You control which recipes get picked for which
timeslots. After generation, you can swap individual meals or adjust servings.

### Single Meal Plan with Auto-Generation

1. Set your plan start and end dates in settings
2. Configure your meal rules
3. Click **Generate** — the app fills every meal slot for the entire period
4. Review, swap, or adjust individual meals as needed
5. Regenerate anytime — manual overrides are preserved

Two-night recipes automatically cascade to the next day when generated.

### Shopping List Generation

Generate a consolidated shopping list from your meal plan with one click.

- Aggregates ingredients across all meals in the plan period
- Scales quantities based on servings (recipe default vs. meal plan servings)
- Groups items by category (produce, meat, dairy, etc.)
- Optional unit conversion (metric ↔ imperial)
- Optional pantry subtraction — deduct what you already have at home
- Estimated total cost based on ingredient prices
- Check off items as you shop
- Add manual items not from recipes
- Print-friendly view

### Pantry Inventory (Optional)

Track what you have at home to avoid over-buying.

- Ingredient, quantity, unit, expiry date, location (pantry / fridge / freezer)
- Expiry alerts — items expiring within 3 days highlighted, expired items flagged
- Low stock alerts based on ingredient minimum stock levels
- Consume action — reduce quantity after using ingredients
- Optional integration with shopping list generation

### Settings

- **Plan dates** — set the start and end date for your meal plan
- **Unit preference** — metric (default) or imperial
- **Default servings** — fallback when recipe or rule doesn't specify
- **Pantry integration** — toggle whether shopping list subtracts pantry stock
- **Admin password** — change your login password

---

## Screenshots

The app uses Bootstrap 5 for a clean, responsive UI. Key pages:

- **Dashboard** (`/`) — overview stats: ingredient count, recipe count, active rules, meal plan entries
- **Meal Plan** (`/meal-plan`) — calendar grid grouped by date, with swap/remove/edit actions
- **Shopping List** (`/shopping-list`) — categorized items with check-off, manual add, delete
- **Setup** (`/setup`) — admin creation or password reset

_For screenshots, run the app locally and capture the pages._

---

## Quick Start

### Option 1: Docker (Recommended)

```bash
# Clone and enter the directory
git clone <repo-url>
cd groceries-spend-visualiser

# Copy env template and edit (or use the existing .env)
cp .env.example .env
# Edit .env — at minimum set ADMIN_USERNAME and ADMIN_PASSWORD

# Build and run
docker compose up --build

# Open http://localhost:5000
```

On first visit, you'll be prompted to set up your admin account (or it's auto-created
from the `ADMIN_USERNAME` / `ADMIN_PASSWORD` env vars).

### Option 2: Manual Python

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Initialize the database
python init_db.py

# Set environment variables
export SECRET_KEY="your-secret-key-here"
export ADMIN_USERNAME=admin
export ADMIN_PASSWORD=yourpassword

# Run with gunicorn (production)
gunicorn -c gunicorn.conf.py app:app

# Or run with Flask's dev server (development only)
flask run
```

> **Note:** `FLASK_ENV` is no longer used. Set `SECRET_KEY` explicitly for any
> non-trivial deployment. The app auto-creates an admin from `ADMIN_USERNAME` /
> `ADMIN_PASSWORD` on first boot if no users exist.

### Deployment Targets

- **Coolify**: Add as a Docker project. Set environment variables in the Coolify UI.
  Optionally connect your R2 bucket for image storage.
- **Render**: Create a Docker Web Service, connect your repo.
  Add env vars in the Render dashboard.
- **Any Docker host**: `docker compose up -d` with a properly configured `.env` file.

### Health Check

The app exposes `GET /health` which returns `{"status": "ok"}` with HTTP 200.
Docker's built-in HEALTHCHECK uses this endpoint (see `Dockerfile`).

### Database Persistence

The database is stored at `DATABASE_PATH` (default: `/app/data/groceries.db`).
Persist it by mounting a volume:

```yaml
# docker-compose.yml
services:
  app:
    volumes:
      - ./data:/app/data
```

Images are stored locally at `UPLOAD_DIR` (default: `/app/static/uploads`).
Persist them similarly if you use local uploads:

```yaml
services:
  app:
    volumes:
      - ./data:/app/data
      - ./static/uploads:/app/static/uploads
```

---

## Configuration

Copy `.env.example` to `.env` and configure. The app reads these environment variables:

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `FLASK_ENV` | No | `production` | Flask environment (legacy; `SECRET_KEY` is what matters) |
| `PORT` | No | `5000` | Port to listen on |
| `SECRET_KEY` | No | `dev-secret-change-in-production` | Session secret — set a random string for production |
| `ADMIN_USERNAME` | No | — | Auto-create admin user on first boot (with `ADMIN_PASSWORD`) |
| `ADMIN_PASSWORD` | No | — | Admin password (used with `ADMIN_USERNAME`) |
| `DATABASE_PATH` | No | `/app/data/groceries.db` | Path to SQLite database file |
| `UPLOAD_DIR` | No | `/app/static/uploads` | Directory for local image uploads |
| `R2_BUCKET` | No | — | Cloudflare R2 bucket name (enables R2 uploads) |
| `R2_ACCESS_KEY` | No | — | R2 access key |
| `R2_SECRET_KEY` | No | — | R2 secret key |
| `R2_ACCOUNT_ID` | No | — | Cloudflare account ID |
| `R2_DOMAIN` | No | `<bucket>.r2.dev` | Custom R2 domain for public URLs |

### Auto-Created Admin (Option C)

If `ADMIN_USERNAME` and `ADMIN_PASSWORD` are both set **and** no users exist in the database,
an admin account is created automatically on first boot. If you already created a user via
the `/setup` UI, the env vars are ignored. You can always reset the password via `/setup`.

---

## First-Time Setup

1. Open the app in your browser
2. If admin user doesn't exist, you'll be redirected to the setup page
3. Create your admin username and password (or it's auto-created from env vars)
4. Log in
5. Go to **Ingredients** and add your grocery items with prices and units
6. Go to **Recipes** and add your recipes, tagging them appropriately
7. Go to **Meal Rules** and configure how meals are selected for each timeslot
8. Go to **Settings** and set your plan start and end dates
9. Go to **Meal Plan** and click **Generate**
10. Go to **Shopping List** and generate your list

---

## Workflow Example

1. **Add ingredients**: Milk (dairy, L, $1.20/L, Coles), Chicken breast (meat, kg, $12.99/kg, Coles), Rice (pantry, kg, $2.50/kg, Coles)
2. **Add recipes**:
   - "Chicken stir fry" — tags: `standard, dinner`, servings: 2, ingredients: chicken breast 500g, rice 200g, veggies...
   - "Roast chicken" — tags: `standard, dinner, two-night`, servings: 4, is_two_night: true
   - "Oatmeal" — tags: `breakfast`, servings: 1
3. **Set up rules**:
   - "Weekday dinner → tag `standard`, servings 2"
   - "Friday dinner → tag `friday-special`, servings 4"
   - "Every day breakfast → tag `breakfast`"
4. **Set plan dates**: Start 2026-10-01, End 2026-10-14 (2 weeks)
5. **Generate**: App fills 14 days of breakfast, lunch, dinner, and snacks
6. **Review**: Swap out a meal you don't want, adjust servings for a big family night
7. **Generate shopping list**: See everything you need for the 2 weeks, grouped by aisle
8. **Check off items** as you shop

---

## Project Structure

```text
groceries-spend-visualiser/
├── app.py                  # Flask application entry point (app factory + routes)
├── auth.py                 # Session-based authentication helpers
├── database.py             # SQLite connection and initialization
├── gunicorn.conf.py        # Gunicorn production server config
├── init_db.py              # CLI script to initialize the database
├── models.py               # Constants, unit conversion, validation, date helpers
├── requirements.txt        # Python dependencies
├── .env.example            # Environment variable template
├── .env                    # Local environment config (gitignored)
├── Dockerfile              # Container build definition
├── docker-compose.yml      # Local development orchestration
├── .dockerignore           # Files excluded from Docker image
├── DOCKER.md               # Deployment documentation (not included — create if needed)
├── README.md               # This file
├── IMPLEMENTATION_PLAN.md  # Detailed implementation plan
├── routes/
│   ├── __init__.py         # Route registration (`register_all_routes`)
│   ├── ingredients.py      # Ingredient CRUD routes
│   ├── recipes.py          # Recipe CRUD routes
│   ├── meal_plan.py        # Plan view, generation, swap/remove/edit
│   ├── meal_rules.py       # Meal rule CRUD routes
│   ├── shopping_list.py    # Shopping list generation and management
│   ├── pantry.py           # Pantry inventory routes
│   └── settings.py         # App settings routes
├── services/
│   ├── plan_generator.py   # Rule-based meal plan generation algorithm
│   └── shopping_list_generator.py  # Shopping list aggregation + unit conversion
├── templates/
│   ├── base.html           # Base layout with navigation
│   ├── dashboard.html      # Homepage (overview stats)
│   ├── index.html          # Legacy index (redirects to dashboard)
│   ├── setup.html          # Admin setup / password reset
│   ├── login.html          # Login form
│   ├── change_password.html  # Change password form
│   ├── ingredients/        # Ingredient list and form templates
│   ├── recipes/            # Recipe list, form, and detail templates
│   ├── meal_rules/         # Meal rule list and form templates
│   ├── meal_plan/          # Meal plan view template
│   ├── shopping_lists/     # Shopping list view template
│   ├── pantry/             # Pantry list and form templates
│   └── settings.html       # Settings form
├── static/
│   ├── css/style.css       # Custom styles
│   └── js/app.js           # Dynamic form behavior
├── data/                   # SQLite database (gitignored)
├── .hermes/                # Hermes planning files (gitignored)
│   └── plans/              # Plan markdown files
└── tests/
    ├── conftest.py         # Pytest fixtures (app, client, db)
    ├── test_integration.py # Full workflow integration tests (47 tests)
    ├── test_plan_generator.py  # Plan generation unit tests
    └── test_shopping_list_generator.py  # Shopping list unit tests
```

**Legacy files** (from the original groceries-spend-visualiser, harmless but unused by the meal planning app):
`data_analyzer.py`, `data_extractor.py`, `database_handler.py`, `process_invoices.py`, `text_cleaner.py`, `text_extractor.py`, `woolworths_analysis.py`, `woolworths_order_history.csv`, `templates/woolworths.html`, `templates/ingredient_form.html`.

---

## Tech Stack

- **Python 3.11** — runtime (Docker `python:3.11-slim` base)
- **Flask** — web framework
- **SQLite** — database (single file, no external service)
- **Bootstrap 5** — UI framework (via CDN)
- **Gunicorn** — production WSGI server
- **Docker** — containerization and deployment
- **Cloudflare R2** (optional) — image storage via boto3

---

## License

See the LICENSE file in the repository root.
