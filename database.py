"""Database connection and initialization for Grocery Visualiser."""

import sqlite3
import os
from flask import g, current_app

DATABASE_PATH = os.getenv("DATABASE_PATH", "/app/data/groceries.db")


def get_db():
    """Get a database connection for the current request."""
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(e=None):
    """Close the database connection at end of request."""
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    """Initialize database schema if it does not exist."""
    db = sqlite3.connect(DATABASE_PATH)
    db.execute("PRAGMA foreign_keys = ON")

    schema = """
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
    """

    db.executescript(schema)
    db.commit()
    db.close()
