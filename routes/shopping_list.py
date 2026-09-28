"""Routes for shopping list generation and management."""

from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from database import get_db
from auth import login_required
from models import CATEGORY_LOOKUP, UNIT_LOOKUP, convert_unit, TO_BASE

shopping_bp = Blueprint("shopping_list", __name__)


def generate_shopping_list(start_date_str, end_date_str):
    """Generate shopping list from meal plan entries in date range."""
    db = get_db()

    # Get all meal plan entries in range
    entries = db.execute(
        "SELECT * FROM meal_plan_entries WHERE date >= ? AND date <= ?",
        (start_date_str, end_date_str)
    ).fetchall()

    if not entries:
        return {}

    # Aggregate ingredients
    aggregated = {}  # key: (ingredient_name, unit) -> {quantity, category, recipe_refs}

    for entry in entries:
        recipe = db.execute(
            "SELECT * FROM recipes WHERE id = ?", (entry["recipe_id"],)
        ).fetchone()
        if not recipe:
            continue

        recipe_servings = recipe["servings"] or 1
        scale_factor = entry["servings"] / recipe_servings

        recipe_ingredients = db.execute(
            """SELECT ri.quantity, ri.unit_override, i.name, i.unit, i.category
               FROM recipe_ingredients ri
               JOIN ingredients i ON ri.ingredient_id = i.id
               WHERE ri.recipe_id = ?""",
            (recipe["id"],)
        ).fetchall()

        for ri in recipe_ingredients:
            qty = ri["quantity"] * scale_factor
            name = ri["name"]
            unit = ri["unit_override"] if ri["unit_override"] else ri["unit"]
            category = ri["category"] or "other"
            key = (name, unit)

            if key in aggregated:
                aggregated[key]["quantity"] += qty
                if recipe["name"] not in aggregated[key]["recipe_refs"]:
                    aggregated[key]["recipe_refs"].append(recipe["name"])
            else:
                aggregated[key] = {
                    "ingredient_name": name,
                    "quantity": qty,
                    "unit": unit,
                    "category": category,
                    "recipe_refs": [recipe["name"]],
                    "meal_date": entry["date"],
                }

    # Convert units if imperial preference
    unit_pref = db.execute("SELECT value FROM settings WHERE key = 'unit_preference'").fetchone()
    if unit_pref and unit_pref["value"] == "imperial":
        for key, item in aggregated.items():
            # Convert to imperial equivalents
            metric_unit = item["unit"]
            if metric_unit == "kg":
                item["quantity"] = round(item["quantity"] * 2.20462, 2)
                item["unit"] = "lb"
            elif metric_unit == "g":
                item["quantity"] = round(item["quantity"] * 0.035274, 2)
                item["unit"] = "oz"
            elif metric_unit == "L":
                item["quantity"] = round(item["quantity"] * 0.264172, 2)
                item["unit"] = "gal"
            elif metric_unit == "mL":
                item["quantity"] = round(item["quantity"] * 0.033814, 2)
                item["unit"] = "fl_oz"

    # Round quantities for display
    for key, item in aggregated.items():
        item["quantity"] = round(item["quantity"], 2)
        if item["quantity"] == int(item["quantity"]):
            item["quantity"] = int(item["quantity"])
        # Add synthetic id for template and toggle/delete compatibility
        item["id"] = abs(hash(f"{item['ingredient_name']}|{item['unit']}|{item.get('meal_date','')}")) % 1000000 + 100

    # Group by category
    grouped = {}
    for item in aggregated.values():
        cat = item["category"] or "other"
        if cat not in grouped:
            grouped[cat] = []
        grouped[cat].append(item)

    # Sort categories and items
    sorted_categories = sorted(grouped.keys())
    for cat in sorted_categories:
        grouped[cat] = sorted(grouped[cat], key=lambda x: x["ingredient_name"])

    return grouped


def get_shopping_list_persistent():
    """Get the persistent shopping list items."""
    db = get_db()
    items = db.execute(
        "SELECT * FROM shopping_list_items ORDER BY category, ingredient_name"
    ).fetchall()
    return items


@shopping_bp.route("/shopping-list")
@login_required
def shopping_list_view():
    db = get_db()

    # Get plan dates from settings
    plan_start = db.execute("SELECT value FROM settings WHERE key = 'plan_start_date'").fetchone()
    plan_end = db.execute("SELECT value FROM settings WHERE key = 'plan_end_date'").fetchone()

    start_str = plan_start["value"] if plan_start else ""
    end_str = plan_end["value"] if plan_end else ""

    generated_list = {}
    cost_estimate = 0

    if start_str and end_str:
        generated_list = generate_shopping_list(start_str, end_str)

        # Calculate cost estimate
        for cat, items in generated_list.items():
            for item in items:
                ing = db.execute(
                    "SELECT price FROM ingredients WHERE name = ? AND unit = ?",
                    (item["ingredient_name"], item["unit"])
                ).fetchone()
                if ing and ing["price"]:
                    cost_estimate += item["quantity"] * ing["price"]

    persistent_items = get_shopping_list_persistent()

    return render_template(
        "shopping_lists/view.html",
        generated_list=generated_list,
        persistent_items=persistent_items,
        plan_start=start_str,
        plan_end=end_str,
        cost_estimate=round(cost_estimate, 2),
        category_labels=CATEGORY_LOOKUP,
    )


@shopping_bp.route("/shopping-list/generate", methods=["POST"])
@login_required
def shopping_list_generate():
    plan_start = request.form.get("plan_start", "").strip()
    plan_end = request.form.get("plan_end", "").strip()

    if not plan_start or not plan_end:
        flash("Please set plan dates in Settings.", "error")
        return redirect(url_for("shopping_list.shopping_list_view"))

    # Clear old persistent items and refill from generated list
    db = get_db()
    db.execute("DELETE FROM shopping_list_items")
    db.commit()

    # Generate and save
    list_data = generate_shopping_list(plan_start, plan_end)
    for cat, items in list_data.items():
        for item in items:
            db.execute(
                """INSERT INTO shopping_list_items (ingredient_name, quantity, unit, category, checked, is_manual, meal_date, recipe_ref)
                   VALUES (?, ?, ?, ?, 0, 0, ?, ?)""",
                (item["ingredient_name"], item["quantity"], item["unit"], cat, item["meal_date"], ", ".join(item["recipe_refs"]))
            )
    db.commit()

    item_count = sum(len(items) for items in list_data.values())
    flash(f"Shopping list generated with {item_count} items.", "success")
    return redirect(url_for("shopping_list.shopping_list_view"))


@shopping_bp.route("/shopping-list/toggle/<int:item_id>", methods=["POST"])
@login_required
def shopping_list_toggle(item_id):
    db = get_db()
    db.execute(
        "UPDATE shopping_list_items SET checked = CASE WHEN checked = 0 THEN 1 ELSE 0 END WHERE id = ?",
        (item_id,)
    )
    db.commit()
    return redirect(url_for("shopping_list.shopping_list_view"))


@shopping_bp.route("/shopping-list/add-manual", methods=["POST"])
@login_required
def shopping_list_add_manual():
    name = request.form.get("name", "").strip() or request.form.get("ingredient_name", "").strip()
    quantity = request.form.get("quantity", type=float, default=1)
    unit = request.form.get("unit", "each").strip()
    category = request.form.get("category", "other").strip()

    if not name:
        flash("Item name is required.", "error")
        return redirect(url_for("shopping_list.shopping_list_view"))

    db = get_db()
    db.execute(
        """INSERT INTO shopping_list_items (ingredient_name, quantity, unit, category, checked, is_manual, recipe_ref)
           VALUES (?, ?, ?, ?, 0, 1, '')""",
        (name, quantity, unit, category)
    )
    db.commit()
    flash("Item added to shopping list.", "success")
    return redirect(url_for("shopping_list.shopping_list_view"))


@shopping_bp.route("/shopping-list/delete/<int:item_id>", methods=["POST"])
@login_required
def shopping_list_delete(item_id):
    db = get_db()
    db.execute("DELETE FROM shopping_list_items WHERE id = ?", (item_id,))
    db.commit()
    flash("Item removed.", "success")
    return redirect(url_for("shopping_list.shopping_list_view"))


@shopping_bp.route("/shopping-list/clear-checked", methods=["POST"])
@login_required
def shopping_list_clear_checked():
    db = get_db()
    db.execute("DELETE FROM shopping_list_items WHERE checked = 1")
    db.commit()
    flash("Checked items cleared.", "success")
    return redirect(url_for("shopping_list.shopping_list_view"))


def register_shopping_routes(app):
    app.register_blueprint(shopping_bp)
