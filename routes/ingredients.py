"""Routes for ingredient management."""

import os
import uuid
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, current_app
from database import get_db
from auth import login_required
from models import CATEGORIES, UNIT_LOOKUP, convert_unit

ingredients_bp = Blueprint("ingredients", __name__)


def get_ingredients(search=None, category=None):
    """Get ingredients with optional search and category filter."""
    db = get_db()
    query = "SELECT * FROM ingredients WHERE 1=1"
    params = []

    if search:
        query += " AND name LIKE ?"
        params.append(f"%{search}%")
    if category:
        query += " AND category = ?"
        params.append(category)

    query += " ORDER BY name"
    return db.execute(query, params).fetchall()


@ingredients_bp.route("/ingredients")
@login_required
def ingredient_list():
    search = request.args.get("search", "").strip()
    category = request.args.get("category", "").strip()
    ingredients = get_ingredients(search, category)

    # Convert rows to dicts with display helpers
    result = []
    for ing in ingredients:
        item = dict(ing)
        item["category_label"] = CATEGORIES[[c[0] for c in CATEGORIES].index(ing["category"])][1] if ing["category"] in [c[0] for c in CATEGORIES] else ing["category"]
        item["unit_label"] = UNIT_LOOKUP.get(ing["unit"], ing["unit"])
        result.append(item)

    return render_template("ingredients/list.html", ingredients=result)


@ingredients_bp.route("/ingredients/add", methods=["GET", "POST"])
@login_required
def ingredient_add():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        category = request.form.get("category", "").strip()
        unit = request.form.get("unit", "each").strip()
        price = request.form.get("price", type=float)
        url = request.form.get("url", "").strip()
        store = request.form.get("store", "").strip()
        minimum_stock = request.form.get("minimum_stock", type=float, default=0)

        errors = []
        if not name:
            errors.append("Name is required.")
        if not category:
            errors.append("Category is required.")

        if errors:
            for err in errors:
                flash(err, "error")
            return render_template("ingredients/form.html")

        db = get_db()
        db.execute(
            """INSERT INTO ingredients (name, category, unit, price, url, store, minimum_stock)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (name, category, unit, price, url, store, minimum_stock)
        )
        db.commit()
        flash("Ingredient added successfully.", "success")
        return redirect(url_for("ingredients.ingredient_list"))

    return render_template("ingredients/form.html")


@ingredients_bp.route("/ingredients/edit/<int:ingredient_id>", methods=["GET", "POST"])
@login_required
def ingredient_edit(ingredient_id):
    db = get_db()
    ingredient = db.execute(
        "SELECT * FROM ingredients WHERE id = ?", (ingredient_id,)
    ).fetchone()

    if not ingredient:
        flash("Ingredient not found.", "error")
        return redirect(url_for("ingredients.ingredient_list"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        category = request.form.get("category", "").strip()
        unit = request.form.get("unit", "each").strip()
        price = request.form.get("price", type=float)
        url = request.form.get("url", "").strip()
        store = request.form.get("store", "").strip()
        minimum_stock = request.form.get("minimum_stock", type=float, default=0)

        errors = []
        if not name:
            errors.append("Name is required.")
        if not category:
            errors.append("Category is required.")

        if errors:
            for err in errors:
                flash(err, "error")
            return render_template("ingredients/form.html", ingredient=dict(ingredient))

        db.execute(
            """UPDATE ingredients SET name=?, category=?, unit=?, price=?, url=?, store=?, minimum_stock=?
               WHERE id=?""",
            (name, category, unit, price, url, store, minimum_stock, ingredient_id)
        )
        db.commit()
        flash("Ingredient updated successfully.", "success")
        return redirect(url_for("ingredients.ingredient_list"))

    item = dict(ingredient)
    item["category_label"] = CATEGORIES[[c[0] for c in CATEGORIES].index(ingredient["category"])][1] if ingredient["category"] in [c[0] for c in CATEGORIES] else ingredient["category"]
    item["unit_label"] = UNIT_LOOKUP.get(ingredient["unit"], ingredient["unit"])
    return render_template("ingredients/form.html", ingredient=item)


@ingredients_bp.route("/ingredients/delete/<int:ingredient_id>", methods=["POST"])
@login_required
def ingredient_delete(ingredient_id):
    db = get_db()
    db.execute("DELETE FROM ingredients WHERE id = ?", (ingredient_id,))
    db.commit()
    flash("Ingredient deleted.", "success")
    return redirect(url_for("ingredients.ingredient_list"))


@ingredients_bp.route("/ingredients/select-ajax")
@login_required
def ingredient_select_ajax():
    """Return HTML options for ingredient select (used by recipe form)."""
    db = get_db()
    rows = db.execute("SELECT id, name, unit FROM ingredients ORDER BY name").fetchall()
    options = []
    for row in rows:
        options.append(f'<option value="{row["id"]}">{row["name"]} ({row["unit"]})</option>')
    return "\n".join(options)


def register_ingredient_routes(app):
    app.register_blueprint(ingredients_bp)
