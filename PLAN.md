# Grocery Visualiser Enhancement Plan

## Overview
Transform the existing Flask-based Grocery Spend Visualiser into a meal planning and grocery list generation app with Grocy-like features. The app will run in Docker for deployment to Coolify or Render.

## Existing Codebase
- **Framework**: Flask
- **UI**: Bootstrap 4 templates
- **Data**: Currently MongoDB (will switch to SQLite for self-hosting simplicity)
- **Features**: PDF invoice processing (OpenAI), Woolworths order history analysis

## New Features to Add

### 1. Ingredient Database (Grocy-like "Products")
- Name, category (produce, dairy, meat, pantry, etc.)
- Unit (kg, g, L, each, etc.)
- Price per unit
- URL link (to product online)
- Store preference
- Minimum stock level (for alerts)

### 2. Recipe Management
- Name, description
- Servings, prep time, cook time
- Ingredients list (linked to ingredient DB with quantities)
- Instructions (text)
- Source URL (where recipe came from)
- Image URL

### 3. Meal Planning
- Configurable period: 1 week, 2 weeks, 3 weeks, 4 weeks, or custom date range
- For each day: Breakfast, Lunch, Dinner (and optional Snacks)
- Assign recipes to meal slots
- View calendar/list of planned meals

### 4. Shopping List Generation
- From selected meal plan period
- Aggregate all ingredients needed
- Consolidate quantities (same ingredient across multiple meals)
- Group by category (produce, dairy, meat, etc.)
- Checkbox to mark purchased
- Add manual items
- Print-friendly view

### 5. Pantry Inventory (Optional, Grocy-like)
- Track ingredients on hand with quantities
- Expiry date tracking
- Low stock alerts
- Recipe suggestions based on available ingredients

## Architecture Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Database | SQLite | Simpler for Docker/self-hosting, no separate DB service |
| File storage | Local uploads directory | Maps to Docker volume for persistence |
| Auth | None (single user, local app) | Keep it simple for personal use |
| UIFramework | Bootstrap 4 (existing) | Already in use, good enough |

## Database Schema (SQLite)

### ingredients
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | |
| name | TEXT | e.g., "Chicken Breast" |
| category | TEXT | produce, dairy, meat, pantry, frozen, bakery, household |
| unit | TEXT | kg, g, L, mL, each, pack, bunch |
| price | REAL | Price per unit |
| url | TEXT | Product URL (optional) |
| store | TEXT | Preferred store (optional) |
| minimum_stock | REAL | Alert threshold (optional) |

### recipes
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | |
| name | TEXT | |
| description | TEXT | |
| servings | INTEGER | Default servings |
| prep_time | INTEGER | Minutes |
| cook_time | INTEGER | Minutes |
| source_url | TEXT | Where recipe came from (optional) |
| image_url | TEXT | Image URL (optional) |
| instructions | TEXT | Cooking instructions |

### recipe_ingredients
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | |
| recipe_id | INTEGER FK | |
| ingredient_id | INTEGER FK | |
| quantity | REAL | Amount needed |
| unit | TEXT | Unit for this recipe (may differ from ingredient's base unit) |

### meal_plans
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | |
| name | TEXT | e.g., "Week 12" |
| start_date | DATE | |
| end_date | DATE | |
| created_at | TIMESTAMP | |

### meal_plan_entries
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | |
| meal_plan_id | INTEGER FK | |
| date | DATE | |
| meal_type | TEXT | breakfast, lunch, dinner, snack |
| recipe_id | INTEGER FK | |
| notes | TEXT | Optional notes |

### shopping_lists
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | |
| name | TEXT | |
| meal_plan_id | INTEGER FK | (optional, if generated from plan) |
| created_at | TIMESTAMP | |

### shopping_list_items
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | |
| shopping_list_id | INTEGER FK | |
| ingredient_name | TEXT | |
| quantity | REAL | |
| unit | TEXT | |
| category | TEXT | |
| checked | BOOLEAN | |
| recipe_source | TEXT | Which recipe needed this (for reference) |

### pantry_items (optional)
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | |
| ingredient_id | INTEGER FK | |
| quantity | REAL | |
| unit | TEXT | |
| expiry_date | DATE | |
| purchased_date | DATE | |
| notes | TEXT | |

## Docker Setup

### Dockerfile
```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create uploads directory
RUN mkdir -p /app/uploads

CMD ["python", "app.py"]
```

### docker-compose.yml
```yaml
version: '3.8'

services:
  grocery-app:
    build: .
    ports:
      - "5000:5000"
    volumes:
      - ./data:/app/data
      - ./uploads:/app/uploads
    environment:
      - FLASK_ENV=production
    restart: unless-stopped
```

### .dockerignore
```
__pycache__/
*.pyc
*.pyo
*.pyd
.git
.gitignore
myenv/
invoices/
*.pdf
.DS_Store
```

## Implementation Phases

### Phase 1: Foundation (Database + Docker)
1. Update requirements.txt (add Flask-SQLAlchemy or use sqlite3 directly)
2. Create database.py with SQLite setup and schema creation
3. Create Dockerfile
4. Create docker-compose.yml
5. Create .dockerignore
6. Test: docker build and run, verify app starts

### Phase 2: Ingredient Management
1. Create Ingredient model
2. Create routes: /ingredients (list), /ingredients/add (form+POST), /ingredients/edit/<id>, /ingredients/delete/<id>
3. Create templates: ingredient_list.html, ingredient_form.html
4. Update base template with navigation link

### Phase 3: Recipe Management
1. Create Recipe and RecipeIngredient models
2. Create routes: /recipes (list), /recipes/add (form+POST), /recipes/view/<id>, /recipes/edit/<id>, /recipes/delete/<id>
3. Create templates: recipe_list.html, recipe_form.html, recipe_view.html
4. Recipe form should allow adding ingredients with quantities

### Phase 4: Meal Planning
1. Create MealPlan and MealPlanEntry models
2. Create routes: /meal-plans (list), /meal-plans/create (form), /meal-plans/view/<id>, /meal-plans/edit/<id>
3. Create templates: meal_plan_list.html, meal_plan_form.html, meal_plan_view.html
4. Meal plan form: date range picker, day-by-day meal slot assignment with recipe dropdowns

### Phase 5: Shopping List Generation
1. Create ShoppingList and ShoppingListItem models
2. Create route: /shopping-lists/generate (POST - from meal plan), /shopping-lists/view/<id>
3. Create template: shopping_list_view.html
4. Implement generation algorithm:
   - For each meal in plan, get recipe ingredients
   - Sum quantities by ingredient
   - Group by category
   - Return list

### Phase 6: Pantry (Optional)
1. Create PantryItem model
2. Create routes: /pantry (list), /pantry/add, /pantry/edit/<id>, /pantry/delete/<id>
3. Create templates: pantry_list.html, pantry_form.html
4. Add low-stock alert feature

### Phase 7: Polish & Integration
1. Update navigation to include all sections
2. Add print CSS for shopping lists
3. Consider integrating with existing Woolworths analysis (optional)
4. Documentation update

## File Structure (Final)
```
groceries-spend-visualiser/
├── app.py                    # Main Flask app
├── database.py              # SQLite database setup and helpers
├── models.py                # SQLAlchemy models (or dict-based)
├── requirements.txt         # Python dependencies
├── Dockerfile              # Docker build
├── docker-compose.yml      # Docker compose
├── .dockerignore           # Docker ignore
├── uploads/                # Image uploads (gitignored)
├── data/                   # SQLite database (gitignored)
├── invoices/              # Existing PDF invoices (kept)
├── data/                  # Existing Woolworths data (kept)
├── templates/
│   ├── base.html          # Base template with nav
│   ├── index.html         # Home/dashboard
│   ├── ingredients/
│   │   ├── list.html
│   │   └── form.html
│   ├── recipes/
│   │   ├── list.html
│   │   ├── form.html
│   │   └── view.html
│   ├── meal_plans/
│   │   ├── list.html
│   │   ├── create.html
│   │   └── view.html
│   ├── shopping_lists/
│   │   └── view.html
│   └── pantry/
│       ├── list.html
│       └── form.html
└── static/
    └── css/
        └── custom.css    # Additional styles
```

## Success Criteria
- [ ] App runs in Docker and serves on port 5000
- [ ] Can add ingredients with name, category, unit, price, URL
- [ ] Can add recipes with ingredients and quantities
- [ ] Can create a meal plan for 1-4 weeks or custom range
- [ ] Can assign recipes to breakfast/lunch/dinner slots per day
- [ ] Can generate shopping list from meal plan
- [ ] Shopping list groups by category and consolidates quantities
- [ ] Shopping list has checkboxes and print view
- [ ] Data persists across Docker restarts (volume mounts)
