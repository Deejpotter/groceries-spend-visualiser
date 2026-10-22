"""Short-lived undo for destructive deletes.

A delete writes a JSON snapshot of every row it removes into ``deleted_items``.
Restoring re-inserts those rows with their original ids, so links (recipe
ingredients, pantry stock) come back with the parent record. Snapshots expire
after ``UNDO_WINDOW_SECONDS``; expired rows are pruned on each delete/restore.
"""

import json
import sqlite3
from datetime import datetime, timedelta, timezone

UNDO_WINDOW_SECONDS = 300
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"

# Tables whose rows are stored inside a snapshot, in restore order.
SNAPSHOT_TABLES = ("recipe_ingredients", "pantry_items")


def _cutoff():
    now = datetime.now(timezone.utc)
    return (now - timedelta(seconds=UNDO_WINDOW_SECONDS)).strftime(TIME_FORMAT)


def prune_expired(db):
    """Drop snapshots older than the undo window."""
    db.execute("DELETE FROM deleted_items WHERE deleted_at < ?", (_cutoff(),))


def _fetch_all(db, sql, params):
    return [dict(row) for row in db.execute(sql, params)]


def _insert_rows(db, table, rows):
    """Re-insert rows with their original primary keys."""
    for row in rows:
        columns = list(row.keys())
        placeholders = ", ".join("?" for _ in columns)
        db.execute(
            "INSERT INTO {} ({}) VALUES ({})".format(table, ", ".join(columns), placeholders),
            [row[column] for column in columns],
        )


def _snapshot(db, kind, label, payload):
    prune_expired(db)
    cursor = db.execute(
        "INSERT INTO deleted_items (kind, label, payload) VALUES (?, ?, ?)",
        (kind, label, json.dumps(payload)),
    )
    return cursor.lastrowid


def delete_ingredient(db, ingredient_id):
    """Delete an ingredient with its recipe links and pantry stock. Returns an undo token."""
    row = db.execute("SELECT * FROM ingredients WHERE id = ?", (ingredient_id,)).fetchone()
    if not row:
        return None
    payload = {
        "ingredient": dict(row),
        "recipe_ingredients": _fetch_all(
            db, "SELECT * FROM recipe_ingredients WHERE ingredient_id = ?", (ingredient_id,)
        ),
        "pantry_items": _fetch_all(
            db, "SELECT * FROM pantry_items WHERE ingredient_id = ?", (ingredient_id,)
        ),
    }
    token = _snapshot(db, "ingredient", row["name"], payload)
    db.execute("DELETE FROM recipe_ingredients WHERE ingredient_id = ?", (ingredient_id,))
    db.execute("DELETE FROM pantry_items WHERE ingredient_id = ?", (ingredient_id,))
    db.execute("DELETE FROM ingredients WHERE id = ?", (ingredient_id,))
    return token


def delete_recipe(db, recipe_id):
    """Delete a recipe with its ingredient links. Returns an undo token."""
    row = db.execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
    if not row:
        return None
    payload = {
        "recipe": dict(row),
        "recipe_ingredients": _fetch_all(
            db, "SELECT * FROM recipe_ingredients WHERE recipe_id = ?", (recipe_id,)
        ),
    }
    token = _snapshot(db, "recipe", row["name"], payload)
    db.execute("DELETE FROM recipe_ingredients WHERE recipe_id = ?", (recipe_id,))
    db.execute("DELETE FROM recipes WHERE id = ?", (recipe_id,))
    return token


def delete_pantry_item(db, item_id):
    """Delete one pantry item. Returns an undo token."""
    row = db.execute("SELECT * FROM pantry_items WHERE id = ?", (item_id,)).fetchone()
    if not row:
        return None
    token = _snapshot(db, "pantry", "pantry item", {"pantry_items": [dict(row)]})
    db.execute("DELETE FROM pantry_items WHERE id = ?", (item_id,))
    return token


def restore(db, token):
    """Undo a delete. Returns (kind, label), or None when it can't be restored."""
    prune_expired(db)
    row = db.execute("SELECT * FROM deleted_items WHERE id = ?", (token,)).fetchone()
    if not row:
        return None
    payload = json.loads(row["payload"])
    try:
        if "ingredient" in payload:
            _insert_rows(db, "ingredients", [payload["ingredient"]])
        if "recipe" in payload:
            _insert_rows(db, "recipes", [payload["recipe"]])
        for table in SNAPSHOT_TABLES:
            _insert_rows(db, table, payload.get(table, []))
    except sqlite3.IntegrityError:
        return None
    db.execute("DELETE FROM deleted_items WHERE id = ?", (token,))
    return row["kind"], row["label"]
