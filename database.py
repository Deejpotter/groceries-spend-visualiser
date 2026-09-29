"""Database connection, schema, and settings helpers for Grocery Visualiser."""

import os
import sqlite3
from flask import g

DEFAULT_DATABASE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "groceries.db")


def get_database_path():
    """Resolve the DB path at call time so tests/env changes are picked up."""
    return os.getenv("DATABASE_PATH") or DEFAULT_DATABASE_PATH


# Kept for backwards compatibility with scripts that import it.
DATABASE_PATH = get_database_path()


def _connect():
    db = sqlite3.connect(get_database_path())
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def get_db():
    """Get a database connection for the current request."""
    if "db" not in g:
        g.db = _connect()
    return g.db


def close_db(e=None):
    """Close the database connection at end of request."""
    db = g.pop("db", None)
    if db is not None:
        db.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ingredients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'other',
    unit TEXT NOT NULL DEFAULT 'each',
    price REAL,
    url TEXT,
    store TEXT,
    minimum_stock REAL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS recipes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT,
    servings INTEGER DEFAULT 1,
    prep_time INTEGER,
    cook_time INTEGER,
    source_url TEXT,
    image_url TEXT,
    instructions TEXT,
    is_two_night INTEGER DEFAULT 0,
    tags TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS recipe_ingredients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recipe_id INTEGER NOT NULL,
    ingredient_id INTEGER NOT NULL,
    quantity REAL NOT NULL,
    unit_override TEXT,
    FOREIGN KEY (recipe_id) REFERENCES recipes(id) ON DELETE CASCADE,
    FOREIGN KEY (ingredient_id) REFERENCES ingredients(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS meal_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day_of_week TEXT NOT NULL DEFAULT 'all',
    meal_type TEXT NOT NULL DEFAULT 'dinner',
    tag_filter TEXT,
    servings_override INTEGER,
    is_active INTEGER DEFAULT 1,
    sort_order INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS meal_plan_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    meal_type TEXT NOT NULL,
    recipe_id INTEGER NOT NULL,
    servings INTEGER NOT NULL,
    is_auto_generated INTEGER DEFAULT 1,
    is_continuation INTEGER DEFAULT 0,
    source_rule_id INTEGER,
    FOREIGN KEY (recipe_id) REFERENCES recipes(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS shopping_list_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ingredient_name TEXT NOT NULL,
    quantity REAL NOT NULL,
    unit TEXT NOT NULL,
    category TEXT,
    checked INTEGER DEFAULT 0,
    is_manual INTEGER DEFAULT 0,
    meal_date TEXT,
    recipe_ref TEXT,
    ingredient_id INTEGER,
    estimated_cost REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pantry_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ingredient_id INTEGER NOT NULL,
    quantity REAL NOT NULL DEFAULT 0,
    unit TEXT NOT NULL,
    expiry_date TEXT,
    location TEXT DEFAULT 'pantry',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (ingredient_id) REFERENCES ingredients(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS purchases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_date TEXT NOT NULL,
    basket_id TEXT NOT NULL,
    store TEXT NOT NULL DEFAULT 'Woolworths',
    channel TEXT,
    product_name TEXT NOT NULL,
    quantity REAL NOT NULL DEFAULT 1,
    unit_price REAL,
    line_total REAL NOT NULL DEFAULT 0,
    cup_price TEXT,
    stockcode TEXT,
    ingredient_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (store, basket_id, product_name),
    FOREIGN KEY (ingredient_id) REFERENCES ingredients(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_purchases_date ON purchases(order_date);
CREATE INDEX IF NOT EXISTS idx_plan_date ON meal_plan_entries(date, meal_type);
"""

# Columns added after the first release: (table, column, definition).
MIGRATIONS = [
    ("shopping_list_items", "estimated_cost", "REAL"),
    ("shopping_list_items", "ingredient_id", "INTEGER"),
    ("meal_plan_entries", "is_continuation", "INTEGER DEFAULT 0"),
]


def _migrate_purchase_identity(db):
    """Early databases made (basket_id, product_name) unique; the identity now includes store."""
    row = db.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'purchases'").fetchone()
    if not row or "UNIQUE (basket_id, product_name)" not in row[0]:
        return
    db.execute("ALTER TABLE purchases RENAME TO purchases_old")
    db.execute("DROP INDEX IF EXISTS idx_purchases_date")
    db.executescript(SCHEMA)  # recreates purchases with the new constraint
    columns = ", ".join(r[1] for r in db.execute("PRAGMA table_info(purchases_old)"))
    db.execute(f"INSERT INTO purchases ({columns}) SELECT {columns} FROM purchases_old")
    db.execute("DROP TABLE purchases_old")


def _apply_migrations(db):
    for table, column, definition in MIGRATIONS:
        existing = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    _migrate_purchase_identity(db)


def init_db():
    """Create the schema (idempotent) and apply column migrations."""
    parent = os.path.dirname(get_database_path())
    if parent:
        os.makedirs(parent, exist_ok=True)
    db = _connect()
    db.executescript(SCHEMA)
    _apply_migrations(db)
    db.commit()
    db.close()


# ---------------------------------------------------------------------------
# Settings (key/value)
# ---------------------------------------------------------------------------

def get_setting(key, default=None):
    row = get_db().execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key, value):
    get_db().execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value))
    )


def get_plan_dates():
    """Return (start, end) plan date strings; empty strings when unset."""
    return get_setting("plan_start_date", ""), get_setting("plan_end_date", "")
