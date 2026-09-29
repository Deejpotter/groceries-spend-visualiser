"""Routes for ingredient management."""

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from database import get_db
from auth import login_required
from models import CATEGORY_LOOKUP, UNIT_LOOKUP, category_label, is_http_url

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


def _read_form():
    """Parse and validate the ingredient form. Returns (values, errors)."""
    values = {
        "name": request.form.get("name", "").strip(),
        "category": request.form.get("category", "").strip(),
        "unit": request.form.get("unit", "each").strip(),
        "price": request.form.get("price", type=float),
        "url": request.form.get("url", "").strip(),
        "store": request.form.get("store", "").strip(),
        "minimum_stock": request.form.get("minimum_stock", type=float, default=0) or 0,
        "pack_size": request.form.get("pack_size", type=float) or None,
    }
    errors = []
    if not values["name"]:
        errors.append("Name is required.")
    if values["category"] not in CATEGORY_LOOKUP:
        errors.append("Category is required.")
    if values["unit"] not in UNIT_LOOKUP:
        errors.append("Choose a valid unit.")
    if values["url"] and not is_http_url(values["url"]):
        errors.append("Product link must start with http:// or https://.")
    if values["pack_size"] is not None and values["pack_size"] < 0:
        errors.append("Pack size can't be negative.")
    if values["price"] is not None and values["price"] < 0:
        errors.append("Price can't be negative.")
    return values, errors


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
        item["category_label"] = category_label(ing["category"])
        item["unit_label"] = UNIT_LOOKUP.get(ing["unit"], ing["unit"])
        result.append(item)

    return render_template("ingredients/list.html", ingredients=result)


@ingredients_bp.route("/ingredients/add", methods=["GET", "POST"])
@login_required
def ingredient_add():
    if request.method == "POST":
        values, errors = _read_form()
        if errors:
            for err in errors:
                flash(err, "error")
            return render_template("ingredients/form.html", ingredient=values)

        db = get_db()
        db.execute(
            """INSERT INTO ingredients (name, category, unit, price, url, store, minimum_stock, pack_size)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (values["name"], values["category"], values["unit"], values["price"],
             values["url"], values["store"], values["minimum_stock"], values["pack_size"])
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
        values, errors = _read_form()
        if errors:
            for err in errors:
                flash(err, "error")
            return render_template("ingredients/form.html", ingredient={**dict(ingredient), **values})

        db.execute(
            """UPDATE ingredients SET name=?, category=?, unit=?, price=?, url=?, store=?, minimum_stock=?,
               pack_size=? WHERE id=?""",
            (values["name"], values["category"], values["unit"], values["price"],
             values["url"], values["store"], values["minimum_stock"], values["pack_size"], ingredient_id)
        )
        db.commit()
        flash("Ingredient updated successfully.", "success")
        return redirect(url_for("ingredients.ingredient_list"))

    item = dict(ingredient)
    item["category_label"] = category_label(ingredient["category"])
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


@ingredients_bp.route("/ingredients/options.json")
@login_required
def ingredient_options():
    """Ingredient choices for client-side pickers (JSON, so names are never injected as HTML)."""
    rows = get_db().execute("SELECT id, name, unit FROM ingredients ORDER BY name").fetchall()
    return jsonify([dict(r) for r in rows])


def register_ingredient_routes(app):
    app.register_blueprint(ingredients_bp)
