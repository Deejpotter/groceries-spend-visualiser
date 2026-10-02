"""Database connection, schema, and settings helpers for Grocery Visualiser."""

import os
import sqlite3
from datetime import datetime

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
    display_name TEXT,
    category TEXT NOT NULL DEFAULT 'other',
    unit TEXT NOT NULL DEFAULT 'each',
    price REAL,
    url TEXT,
    store TEXT,
    minimum_stock REAL DEFAULT 0,
    pack_size REAL,
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
    covers_days INTEGER NOT NULL DEFAULT 1,
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
    continuation_of INTEGER,
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
    ("meal_plan_entries", "continuation_of", "INTEGER"),
    ("recipes", "covers_days", "INTEGER NOT NULL DEFAULT 1"),
    ("ingredients", "pack_size", "REAL"),
    ("ingredients", "display_name", "TEXT"),
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
            if table == "recipes" and column == "covers_days":
                db.execute("ALTER TABLE recipes ADD COLUMN covers_days INTEGER NOT NULL DEFAULT 1")
                if "is_two_night" in existing:
                    db.execute(
                        "UPDATE recipes SET covers_days = 2 "
                        "WHERE is_two_night = 1 AND covers_days = 1"
                    )
            else:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
                if table == "meal_plan_entries" and column == "continuation_of":
                    db.execute(
                        """UPDATE meal_plan_entries AS continuation SET continuation_of = (
                               SELECT original.id FROM meal_plan_entries AS original
                               WHERE original.date = date(continuation.date, '-1 day')
                                 AND original.meal_type = continuation.meal_type
                                 AND original.recipe_id = continuation.recipe_id
                                 AND original.is_continuation = 0
                           ) WHERE continuation.is_continuation = 1"""
                    )
    _migrate_purchase_identity(db)


def init_db() -> bool:
    """Create the schema (idempotent) and apply column migrations.

    Returns True if the database file already existed, False when a brand-new
    file was created (usually a missing volume mount in Docker).
    """
    path = get_database_path()
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    existed = os.path.exists(path)
    db = _connect()
    db.executescript(SCHEMA)
    _apply_migrations(db)
    db.commit()
    db.close()
    return existed


# Tables reported by database_stats(), in a stable order.
COUNTED_TABLES = (
    "users", "ingredients", "recipes", "recipe_ingredients", "meal_rules",
    "meal_plan_entries", "shopping_list_items", "pantry_items", "purchases",
)


def database_stats() -> dict:
    """Where the database lives and how much is in it, for status checks."""
    path = get_database_path()
    stats = {"path": path, "exists": os.path.exists(path), "size_bytes": 0, "counts": {}}
    if not stats["exists"]:
        return stats
    stats["size_bytes"] = os.path.getsize(path)
    db = _connect()
    try:
        for table in COUNTED_TABLES:
            stats["counts"][table] = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        db.close()
    return stats


def backup_database(dest_dir: str = None) -> str:
    """Copy the database to a timestamped file next to it (safe while in use).

    Uses SQLite's online backup API so a running app can be backed up safely.
    Returns the path of the new backup file.
    """
    path = get_database_path()
    dest_dir = dest_dir or os.path.dirname(path) or "."
    os.makedirs(dest_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(dest_dir, f"groceries-backup-{stamp}.db")
    src = sqlite3.connect(path)
    dst = sqlite3.connect(dest)
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()
    return dest


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
