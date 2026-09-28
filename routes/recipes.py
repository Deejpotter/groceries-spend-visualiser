"""Routes for recipe management."""

import os
import uuid
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from database import get_db
from auth import login_required
from models import CATEGORIES, UNIT_LOOKUP, MEAL_TYPES, convert_unit

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
    result["tags_list"] = [t.strip() for t in (recipe["tags"] or "").split(",") if t.strip()]
    result["recipe_ingredients"] = []
    for ing in ingredients:
        ri = dict(ing)
        ri["ingredient_name"] = ing["name"]
        ri["unit_display"] = ing["unit_override"] if ing["unit_override"] else ing["unit"]
        result["recipe_ingredients"].append(ri)

    return result, result["recipe_ingredients"]


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
    if tag_filter:
        query += " AND tags LIKE ?"
        params.append(f"%{tag_filter}%")

    query += " ORDER BY name"
    recipes = db.execute(query, params).fetchall()

    result = []
    for recipe in recipes:
        item = dict(recipe)
        item["tags_list"] = [t.strip() for t in (recipe["tags"] or "").split(",") if t.strip()]
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
        servings = request.form.get("servings", type=int, default=1)
        prep_time = request.form.get("prep_time", type=int)
        cook_time = request.form.get("cook_time", type=int)
        source_url = request.form.get("source_url", "").strip()
        image_url = request.form.get("image_url", "").strip()
        instructions = request.form.get("instructions", "").strip()
        tags = request.form.get("tags", "").strip()
        is_two_night = 1 if request.form.get("is_two_night") else 0

        errors = []
        if not name:
            errors.append("Recipe name is required.")
        if servings < 1:
            errors.append("Servings must be at least 1.")

        if errors:
            for err in errors:
                flash(err, "error")
            return render_template("recipes/form.html", recipe=recipe)

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

        # Handle ingredients
        ingredient_ids = request.form.getlist("ingredient_ids")
        quantities = request.form.getlist("quantities") or request.form.getlist("ingredient_quantities")
        unit_overrides = request.form.getlist("unit_overrides") or request.form.getlist("ingredient_unit_overrides")

        # Delete existing ingredients if editing
        if recipe_id:
            db.execute("DELETE FROM recipe_ingredients WHERE recipe_id = ?", (recipe_id,))

        for i, ing_id in enumerate(ingredient_ids):
            if ing_id:
                qty = float(quantities[i]) if quantities[i] else 0
                if qty > 0:
                    unit_ov = unit_overrides[i].strip() if i < len(unit_overrides) else ""
                    db.execute(
                        """INSERT INTO recipe_ingredients (recipe_id, ingredient_id, quantity, unit_override)
                           VALUES (?, ?, ?, ?)""",
                        (recipe_id, int(ing_id), qty, unit_ov if unit_ov else None)
                    )

        db.commit()
        return redirect(url_for("recipes.recipe_detail", recipe_id=recipe_id))

    return render_template("recipes/form.html", recipe=recipe, recipe_ingredients=recipe_ingredients)


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
