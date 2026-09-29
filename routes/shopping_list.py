"""Routes for shopping list generation and management."""

from flask import Blueprint, render_template, request, redirect, url_for, flash

from auth import login_required
from database import get_db, get_plan_dates
from models import CATEGORY_LOOKUP, UNIT_LOOKUP, validate_date_format
from services.repository import load_shopping_inputs, load_pantry_stock, shopping_preferences
from services.shopping_list_generator import generate_shopping_list
from services.spend_analysis import average_spend_per_shop

shopping_bp = Blueprint("shopping_list", __name__)


def _back():
    return redirect(url_for("shopping_list.shopping_list_view"))


def build_list(start, end):
    """Run the shopping-list service over the plan with the user's preferences."""
    unit_pref, subtract = shopping_preferences()
    entries, recipes, recipe_ingredients, ingredients = load_shopping_inputs(start, end)
    return generate_shopping_list(
        entries, recipes, recipe_ingredients, ingredients,
        unit_preference=unit_pref,
        subtract_pantry=subtract,
        pantry_items=load_pantry_stock() if subtract else None,
    )


@shopping_bp.route("/shopping-list")
@login_required
def shopping_list_view():
    db = get_db()
    start_str, end_str = get_plan_dates()
    rows = db.execute(
        "SELECT * FROM shopping_list_items ORDER BY checked, category, ingredient_name"
    ).fetchall()

    grouped = {}
    for row in rows:
        grouped.setdefault(row["category"] or "other", []).append(row)
    ordered = {cat: grouped[cat] for cat in sorted(grouped, key=lambda c: CATEGORY_LOOKUP.get(c, c))}

    cost_estimate = round(sum(r["estimated_cost"] or 0 for r in rows if not r["is_manual"]), 2)
    has_plan = bool(start_str and end_str and db.execute(
        "SELECT 1 FROM meal_plan_entries WHERE date BETWEEN ? AND ? LIMIT 1", (start_str, end_str)
    ).fetchone())

    return render_template(
        "shopping_lists/view.html",
        grouped=ordered,
        item_count=len(rows),
        checked_count=sum(1 for r in rows if r["checked"]),
        has_generated=any(not r["is_manual"] for r in rows),
        has_plan=has_plan,
        plan_start=start_str,
        plan_end=end_str,
        cost_estimate=cost_estimate,
        avg_shop_spend=average_spend_per_shop(db),
    )


@shopping_bp.route("/shopping-list/generate", methods=["POST"])
@login_required
def shopping_list_generate():
    stored_start, stored_end = get_plan_dates()
    plan_start = request.form.get("plan_start", "").strip() or stored_start
    plan_end = request.form.get("plan_end", "").strip() or stored_end
    if not (validate_date_format(plan_start) and validate_date_format(plan_end)):
        flash("Please set plan dates in Settings.", "error")
        return _back()

    db = get_db()
    # Remember what was already ticked so a regenerate doesn't lose progress.
    # Keyed by ingredient id so a unit-preference change doesn't drop ticks.
    previously_checked = {
        r["ingredient_id"]
        for r in db.execute("SELECT ingredient_id FROM shopping_list_items WHERE checked = 1 AND is_manual = 0")
    }
    db.execute("DELETE FROM shopping_list_items WHERE is_manual = 0")

    list_data = build_list(plan_start, plan_end)
    for cat, items in list_data.items():
        for item in items:
            db.execute(
                """INSERT INTO shopping_list_items
                   (ingredient_id, ingredient_name, quantity, unit, category, checked, is_manual, meal_date,
                    recipe_ref, estimated_cost)
                   VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?)""",
                (item["ingredient_id"], item["ingredient_name"], item["quantity"], item["unit"], cat,
                 1 if item["ingredient_id"] in previously_checked else 0,
                 item["meal_date"], ", ".join(item["recipe_refs"]), item["estimated_cost"]),
            )
    db.commit()

    item_count = sum(len(items) for items in list_data.values())
    flash(f"Shopping list generated with {item_count} items.", "success")
    return _back()


@shopping_bp.route("/shopping-list/toggle/<int:item_id>", methods=["POST"])
@login_required
def shopping_list_toggle(item_id):
    db = get_db()
    db.execute("UPDATE shopping_list_items SET checked = 1 - checked WHERE id = ?", (item_id,))
    db.commit()
    return _back()


@shopping_bp.route("/shopping-list/add-manual", methods=["POST"])
@login_required
def shopping_list_add_manual():
    name = request.form.get("name", "").strip() or request.form.get("ingredient_name", "").strip()
    quantity = request.form.get("quantity", type=float) or 1
    unit = request.form.get("unit", "each").strip()
    category = request.form.get("category", "other").strip()

    if not name:
        flash("Item name is required.", "error")
        return _back()
    if unit not in UNIT_LOOKUP:
        unit = "each"
    if category not in CATEGORY_LOOKUP:
        category = "other"

    db = get_db()
    db.execute(
        """INSERT INTO shopping_list_items (ingredient_name, quantity, unit, category, checked, is_manual, recipe_ref)
           VALUES (?, ?, ?, ?, 0, 1, '')""",
        (name, quantity, unit, category),
    )
    db.commit()
    flash("Item added to shopping list.", "success")
    return _back()


@shopping_bp.route("/shopping-list/delete/<int:item_id>", methods=["POST"])
@login_required
def shopping_list_delete(item_id):
    db = get_db()
    db.execute("DELETE FROM shopping_list_items WHERE id = ?", (item_id,))
    db.commit()
    flash("Item removed.", "success")
    return _back()


@shopping_bp.route("/shopping-list/clear-checked", methods=["POST"])
@login_required
def shopping_list_clear_checked():
    db = get_db()
    db.execute("DELETE FROM shopping_list_items WHERE checked = 1")
    db.commit()
    flash("Checked items cleared.", "success")
    return _back()


def register_shopping_routes(app):
    app.register_blueprint(shopping_bp)
