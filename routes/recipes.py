"""Routes for recipe management."""

from flask import Blueprint, render_template, request, redirect, url_for, flash
from database import get_db, get_setting
from auth import login_required
from models import UNIT_LOOKUP
from services.plan_generator import parse_tags

recipes_bp = Blueprint("recipes", __name__)


def get_recipe_with_ingredients(recipe_id):
    """Get a recipe with its ingredients joined."""
    db = get_db()
    recipe = db.execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
    if not recipe:
        return None, []

    ingredients = db.execute(
        """SELECT ri.id as ri_id, ri.quantity, ri.unit_override,
                  i.id as ingredient_id, i.name, i.unit, i.category
           FROM recipe_ingredients ri
           JOIN ingredients i ON ri.ingredient_id = i.id
           WHERE ri.recipe_id = ?""",
        (recipe_id,)
    ).fetchall()

    result = dict(recipe)
    result["tags_list"] = parse_tags(recipe["tags"])
    result["recipe_ingredients"] = []
    for ing in ingredients:
        ri = dict(ing)
        ri["ingredient_name"] = ing["name"]
        ri["unit_display"] = ing["unit_override"] if ing["unit_override"] else ing["unit"]
        result["recipe_ingredients"].append(ri)

    return result, result["recipe_ingredients"]


def _read_ingredient_rows():
    """Parse the parallel ingredient_ids/quantities/unit_overrides lists.

    Returns ([(ingredient_id, quantity, unit_override|None)], errors). Blank rows are skipped.
    """
    ids = request.form.getlist("ingredient_ids")
    quantities = request.form.getlist("quantities")
    units = request.form.getlist("unit_overrides")
    db = get_db()
    rows, errors = [], []
    for i, raw_id in enumerate(ids):
        if not raw_id:
            continue
        try:
            ing_id = int(raw_id)
            qty = float(quantities[i]) if i < len(quantities) and quantities[i] else 0
        except ValueError:
            errors.append("Ingredient quantities must be numbers.")
            continue
        if qty <= 0:
            continue
        if not db.execute("SELECT 1 FROM ingredients WHERE id = ?", (ing_id,)).fetchone():
            errors.append("One of the selected ingredients no longer exists.")
            continue
        unit = units[i].strip() if i < len(units) else ""
        if unit and unit not in UNIT_LOOKUP:
            errors.append(f"Unknown unit '{unit}'.")
            continue
        rows.append((ing_id, qty, unit or None))
    return rows, errors


def _submitted_rows():
    """Rebuild the ingredient rows the user just submitted, so a validation error doesn't wipe them."""
    ids = request.form.getlist("ingredient_ids")
    quantities = request.form.getlist("quantities")
    units = request.form.getlist("unit_overrides")
    names = {r["id"]: r for r in get_db().execute("SELECT id, name, unit FROM ingredients")}
    rows = []
    for i, raw_id in enumerate(ids):
        ing = names.get(int(raw_id)) if raw_id.isdigit() else None
        if not ing:
            continue
        rows.append({
            "ingredient_id": ing["id"], "ingredient_name": ing["name"], "unit": ing["unit"],
            "quantity": quantities[i] if i < len(quantities) else "",
            "unit_override": units[i] if i < len(units) else "",
        })
    return rows


@recipes_bp.route("/recipes")
@login_required
def recipe_list():
    db = get_db()
    search = request.args.get("search", "").strip()
    tag_filter = request.args.get("tag", "").strip()

    query = "SELECT * FROM recipes WHERE 1=1"
    params = []

    if search:
        query += " AND (name LIKE ? OR description LIKE ?)"
        params.extend([f"%{search}%", f"%{search}%"])

    query += " ORDER BY name"
    recipes = db.execute(query, params).fetchall()

    result = []
    wanted = tag_filter.lower()
    for recipe in recipes:
        item = dict(recipe)
        item["tags_list"] = parse_tags(recipe["tags"])
        if wanted and wanted not in item["tags_list"]:
            continue
        result.append(item)

    return render_template("recipes/list.html", recipes=result)


@recipes_bp.route("/recipes/add", methods=["GET", "POST"])
@recipes_bp.route("/recipes/edit/<int:recipe_id>", methods=["GET", "POST"])
@login_required
def recipe_form(recipe_id=None):
    db = get_db()
    recipe = None
    recipe_ingredients = []

    if recipe_id:
        recipe, recipe_ingredients = get_recipe_with_ingredients(recipe_id)
        if not recipe:
            flash("Recipe not found.", "error")
            return redirect(url_for("recipes.recipe_list"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        servings = request.form.get("servings", type=int) or 0
        prep_time = request.form.get("prep_time", type=int)
        cook_time = request.form.get("cook_time", type=int)
        source_url = request.form.get("source_url", "").strip()
        image_url = request.form.get("image_url", "").strip()
        instructions = request.form.get("instructions", "").strip()
        tags = ", ".join(dict.fromkeys(parse_tags(request.form.get("tags", ""))))
        is_two_night = 1 if request.form.get("is_two_night") else 0

        rows, row_errors = _read_ingredient_rows()
        errors = []
        if not name:
            errors.append("Recipe name is required.")
        if servings < 1:
            errors.append("Servings must be at least 1.")
        errors.extend(row_errors)

        if errors:
            for err in errors:
                flash(err, "error")
            return render_template("recipes/form.html", recipe={**(recipe or {}), **request.form.to_dict()},
                                   recipe_ingredients=_submitted_rows())

        if recipe_id:
            db.execute(
                """UPDATE recipes SET name=?, description=?, servings=?, prep_time=?,
                   cook_time=?, source_url=?, image_url=?, instructions=?, tags=?, is_two_night=?
                   WHERE id=?""",
                (name, description, servings, prep_time, cook_time,
                 source_url, image_url, instructions, tags, is_two_night, recipe_id)
            )
            flash("Recipe updated successfully.", "success")
        else:
            db.execute(
                """INSERT INTO recipes (name, description, servings, prep_time, cook_time,
                   source_url, image_url, instructions, tags, is_two_night)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (name, description, servings, prep_time, cook_time,
                 source_url, image_url, instructions, tags, is_two_night)
            )
            recipe_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            flash("Recipe created successfully.", "success")

        db.execute("DELETE FROM recipe_ingredients WHERE recipe_id = ?", (recipe_id,))
        db.executemany(
            "INSERT INTO recipe_ingredients (recipe_id, ingredient_id, quantity, unit_override) VALUES (?, ?, ?, ?)",
            [(recipe_id, ing_id, qty, unit) for ing_id, qty, unit in rows],
        )
        db.commit()
        return redirect(url_for("recipes.recipe_detail", recipe_id=recipe_id))

    return render_template("recipes/form.html", recipe=recipe, recipe_ingredients=recipe_ingredients,
                           default_servings=get_setting("default_servings", "2"))


@recipes_bp.route("/recipes/<int:recipe_id>")
@login_required
def recipe_detail(recipe_id):
    recipe, recipe_ingredients = get_recipe_with_ingredients(recipe_id)
    if not recipe:
        flash("Recipe not found.", "error")
        return redirect(url_for("recipes.recipe_list"))
    return render_template("recipes/detail.html", recipe=recipe)


@recipes_bp.route("/recipes/delete/<int:recipe_id>", methods=["POST"])
@login_required
def recipe_delete(recipe_id):
    db = get_db()
    db.execute("DELETE FROM recipes WHERE id = ?", (recipe_id,))
    db.commit()
    flash("Recipe deleted.", "success")
    return redirect(url_for("recipes.recipe_list"))


def register_recipe_routes(app):
    app.register_blueprint(recipes_bp)
