"""Routes for recipe management."""

import os
import uuid

from flask import Blueprint, render_template, request, redirect, url_for, flash, send_from_directory
from database import get_db, get_setting, get_database_path
from auth import login_required
from models import UNIT_LOOKUP, is_http_url
from services.plan_generator import parse_tags

recipes_bp = Blueprint("recipes", __name__)

IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024


def _images_dir():
    """Uploaded recipe images live beside the database, on the persistent volume."""
    return os.path.join(os.path.dirname(get_database_path()), "images")


def _looks_like_image(data):
    """Cheap signature check — no image library needed."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return True
    if data.startswith(b"\xff\xd8\xff"):
        return True
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return True
    return data[:4] == b"RIFF" and data[8:12] == b"WEBP"


def _save_uploaded_image(file_storage):
    """Store an uploaded recipe image. Returns (url, error); both empty when no file."""
    if not file_storage or not file_storage.filename:
        return None, None
    filename = file_storage.filename
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if extension not in IMAGE_EXTENSIONS:
        return None, "Recipe images must be PNG, JPG, WEBP or GIF."
    data = file_storage.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        return None, "Recipe images must be 5 MB or smaller."
    if not _looks_like_image(data):
        return None, "That file is not a recognised image."
    folder = _images_dir()
    os.makedirs(folder, exist_ok=True)
    stored_name = "{}.{}".format(uuid.uuid4().hex, extension)
    with open(os.path.join(folder, stored_name), "wb") as handle:
        handle.write(data)
    return url_for("recipes.recipe_image", filename=stored_name), None


@recipes_bp.route("/recipe-images/<path:filename>")
@login_required
def recipe_image(filename):
    return send_from_directory(_images_dir(), filename)


def get_recipe_with_ingredients(recipe_id):
    """Get a recipe with its ingredients joined."""
    db = get_db()
    recipe = db.execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
    if not recipe:
        return None, []

    ingredients = db.execute(
        """SELECT ri.id as ri_id, ri.quantity, ri.unit_override,
                  i.id as ingredient_id, i.name, i.display_name, i.unit, i.category
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

    Returns ([(ingredient_id, quantity, unit_override|None)], errors, skipped).
    Blank rows are skipped; rows with an ingredient but no quantity count as
    skipped so the user is told instead of silently losing them.
    """
    ids = request.form.getlist("ingredient_ids")
    quantities = request.form.getlist("quantities")
    units = request.form.getlist("unit_overrides")
    db = get_db()
    rows, errors, seen, skipped = [], [], set(), 0
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
            skipped += 1
            continue
        ing = db.execute("SELECT name FROM ingredients WHERE id = ?", (ing_id,)).fetchone()
        if not ing:
            errors.append("One of the selected ingredients no longer exists.")
            continue
        if ing_id in seen:
            errors.append(f"\u201c{ing['name']}\u201d is listed twice \u2014 remove one row.")
            continue
        seen.add(ing_id)
        unit = units[i].strip() if i < len(units) else ""
        if unit and unit not in UNIT_LOOKUP:
            errors.append(f"Unknown unit '{unit}'.")
            continue
        rows.append((ing_id, qty, unit or None))
    return rows, errors, skipped


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
    only_favourites = request.args.get("favourites") == "1"

    query = "SELECT * FROM recipes WHERE 1=1"
    params = []

    if search:
        query += " AND (name LIKE ? OR description LIKE ?)"
        params.extend([f"%{search}%", f"%{search}%"])
    if only_favourites:
        query += " AND COALESCE(is_favourite, 0) = 1"

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

    return render_template("recipes/list.html", recipes=result,
                           only_favourites=only_favourites,
                           favourite_count=db.execute(
                               "SELECT COUNT(*) FROM recipes WHERE COALESCE(is_favourite, 0) = 1"
                           ).fetchone()[0])


@recipes_bp.route("/recipes/add", methods=["GET", "POST"])
@recipes_bp.route("/recipes/edit/<int:recipe_id>", methods=["GET", "POST"])
@login_required
def recipe_form(recipe_id=None):
    db = get_db()
    recipe = None
    recipe_ingredients = []
    form_ingredients = db.execute(
        "SELECT id, name, display_name, unit FROM ingredients ORDER BY name").fetchall()

    if recipe_id:
        recipe, recipe_ingredients = get_recipe_with_ingredients(recipe_id)
        if not recipe:
            flash("Recipe not found.", "error")
            return redirect(url_for("recipes.recipe_list"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        servings = request.form.get("servings", type=int) or 0
        covers_days = request.form.get("covers_days", default=1, type=int)
        prep_time = request.form.get("prep_time", type=int)
        cook_time = request.form.get("cook_time", type=int)
        source_url = request.form.get("source_url", "").strip()
        image_url = request.form.get("image_url", "").strip()
        instructions = request.form.get("instructions", "").strip()
        tags = ", ".join(dict.fromkeys(parse_tags(request.form.get("tags", ""))))

        uploaded_url, image_error = _save_uploaded_image(request.files.get("image_file"))
        if uploaded_url:
            image_url = uploaded_url

        rows, row_errors, skipped_rows = _read_ingredient_rows()
        errors = []
        if image_error:
            errors.append(image_error)
        if not name:
            errors.append("Recipe name is required.")
        if servings < 1:
            errors.append("Servings must be at least 1.")
        if covers_days is None or covers_days < 1:
            errors.append("Coverage must be at least 1 day.")
        if prep_time is not None and prep_time < 0:
            errors.append("Prep time can't be negative.")
        if cook_time is not None and cook_time < 0:
            errors.append("Cook time can't be negative.")
        if source_url and not is_http_url(source_url):
            errors.append("Source link must start with http:// or https://.")
        if image_url and not is_http_url(image_url) and not image_url.startswith("/recipe-images/"):
            errors.append("Image link must start with http:// or https://.")
        errors.extend(row_errors)

        if errors:
            return render_template("recipes/form.html", recipe={**(recipe or {}), **request.form.to_dict()},
                                   recipe_ingredients=_submitted_rows(), errors=errors,
                                   form_ingredients=form_ingredients)

        if recipe_id:
            db.execute(
                """UPDATE recipes SET name=?, description=?, servings=?, prep_time=?,
                   cook_time=?, source_url=?, image_url=?, instructions=?, tags=?, covers_days=?
                   WHERE id=?""",
                (name, description, servings, prep_time, cook_time,
                 source_url, image_url, instructions, tags, covers_days, recipe_id)
            )
            flash("Recipe updated successfully.", "success")
        else:
            db.execute(
                """INSERT INTO recipes (name, description, servings, prep_time, cook_time,
                   source_url, image_url, instructions, tags, covers_days)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (name, description, servings, prep_time, cook_time,
                 source_url, image_url, instructions, tags, covers_days)
            )
            recipe_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            flash("Recipe created successfully.", "success")

        db.execute("DELETE FROM recipe_ingredients WHERE recipe_id = ?", (recipe_id,))
        db.executemany(
            "INSERT INTO recipe_ingredients (recipe_id, ingredient_id, quantity, unit_override) VALUES (?, ?, ?, ?)",
            [(recipe_id, ing_id, qty, unit) for ing_id, qty, unit in rows],
        )
        db.commit()
        if skipped_rows:
            flash(f"{skipped_rows} ingredient row{'s' if skipped_rows != 1 else ''} with no quantity"
                  f" {'were' if skipped_rows != 1 else 'was'} skipped.", "warning")
        if not rows and not row_errors:
            flash("Recipe saved without ingredients — it won't add anything to the shopping list until you add some.", "warning")
        return redirect(url_for("recipes.recipe_detail", recipe_id=recipe_id))

    return render_template("recipes/form.html", recipe=recipe, recipe_ingredients=recipe_ingredients,
                           form_ingredients=form_ingredients,
                           default_servings=get_setting("default_servings", "2"))


@recipes_bp.route("/recipes/<int:recipe_id>")
@login_required
def recipe_detail(recipe_id):
    from services.scaling import normalise_servings, scale_ingredients

    recipe, _ = get_recipe_with_ingredients(recipe_id)
    if not recipe:
        flash("Recipe not found.", "error")
        return redirect(url_for("recipes.recipe_list"))

    base_servings = recipe["servings"] or 1
    target_servings = normalise_servings(request.args.get("servings"), base_servings)
    recipe["recipe_ingredients"] = scale_ingredients(
        recipe["recipe_ingredients"], base_servings, target_servings
    )
    return render_template(
        "recipes/detail.html",
        recipe=recipe,
        base_servings=base_servings,
        target_servings=target_servings,
        is_scaled=target_servings != base_servings,
    )


@recipes_bp.route("/recipes/<int:recipe_id>/favourite", methods=["POST"])
@login_required
def recipe_toggle_favourite(recipe_id):
    """Star or unstar a recipe."""
    db = get_db()
    row = db.execute("SELECT is_favourite FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
    if not row:
        flash("Recipe not found.", "error")
        return redirect(url_for("recipes.recipe_list"))
    new_value = 0 if row["is_favourite"] else 1
    db.execute("UPDATE recipes SET is_favourite = ? WHERE id = ?", (new_value, recipe_id))
    db.commit()
    if request.headers.get("HX-Request"):
        return render_template("recipes/_favourite_button.html", recipe={"id": recipe_id, "is_favourite": new_value})
    flash("Added to favourites." if new_value else "Removed from favourites.", "success")
    return redirect(request.referrer or url_for("recipes.recipe_list"))


@recipes_bp.route("/recipes/delete/<int:recipe_id>", methods=["POST"])
@login_required
def recipe_delete(recipe_id):
    from services import undo as undo_service

    db = get_db()
    token = undo_service.delete_recipe(db, recipe_id)
    db.commit()
    if token:
        flash("Recipe deleted.", "success")
        return redirect(url_for("recipes.recipe_list", undo=token))
    flash("Recipe not found.", "error")
    return redirect(url_for("recipes.recipe_list"))


def register_recipe_routes(app):
    app.register_blueprint(recipes_bp)
