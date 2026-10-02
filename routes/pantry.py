"""Routes for pantry inventory."""

from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash
from database import get_db
from auth import login_required
from models import UNIT_LOOKUP, category_label, today as local_today, validate_date_format

pantry_bp = Blueprint("pantry", __name__)


@pantry_bp.route("/pantry")
@login_required
def pantry_list():
    db = get_db()
    location = request.args.get("location", "").strip()
    sql = """SELECT p.*, i.name as ingredient_name, i.display_name, i.category, i.unit as ingredient_unit
             FROM pantry_items p JOIN ingredients i ON p.ingredient_id = i.id"""
    params = []
    if location:
        sql += " WHERE p.location = ?"
        params.append(location)
    items = db.execute(sql + " ORDER BY p.expiry_date IS NULL, p.expiry_date, i.name", params).fetchall()

    # Add computed fields
    result = []
    today = local_today()
    for item in items:
        entry = dict(item)
        entry["ingredient_label"] = f"{item['ingredient_name']} ({item['ingredient_unit']})"
        entry["category_label"] = category_label(item["category"])

        if item["expiry_date"]:
            try:
                exp_date = datetime.strptime(item["expiry_date"], "%Y-%m-%d").date()
                days_left = (exp_date - today).days
                entry["days_until_expiry"] = days_left
                if days_left < 0:
                    entry["expiry_status"] = "expired"
                elif days_left <= 3:
                    entry["expiry_status"] = "soon"
                else:
                    entry["expiry_status"] = "ok"
            except ValueError:
                entry["days_until_expiry"] = None
                entry["expiry_status"] = "unknown"
        else:
            entry["days_until_expiry"] = None
            entry["expiry_status"] = "no_date"

        result.append(entry)

    return render_template("pantry/list.html", items=result)


@pantry_bp.route("/pantry/add", methods=["GET", "POST"])
@pantry_bp.route("/pantry/edit/<int:item_id>", methods=["GET", "POST"])
@login_required
def pantry_form(item_id=None):
    db = get_db()

    existing = None
    if item_id:
        existing = db.execute("SELECT * FROM pantry_items WHERE id = ?", (item_id,)).fetchone()
        if not existing:
            flash("Item not found.", "error")
            return redirect(url_for("pantry.pantry_list"))

    ingredients = db.execute("SELECT id, name, display_name, unit, category FROM ingredients ORDER BY name").fetchall()

    if request.method == "POST":
        ingredient_id = request.form.get("ingredient_id", type=int)
        quantity = request.form.get("quantity", type=float, default=0)
        unit = request.form.get("unit", "").strip()
        expiry_date = request.form.get("expiry_date", "").strip() or None
        location = request.form.get("location", "pantry").strip()

        ingredient = db.execute("SELECT unit FROM ingredients WHERE id = ?", (ingredient_id,)).fetchone() if ingredient_id else None
        errors = []
        if not ingredient:
            errors.append("Ingredient is required.")
        if quantity is None or quantity < 0:
            errors.append("Quantity must be zero or more.")
        if unit and unit not in UNIT_LOOKUP:
            errors.append("Choose a valid unit.")
        if expiry_date and not validate_date_format(expiry_date):
            errors.append("Expiry date must be a valid date.")
        if not location:
            errors.append("Location is required.")

        if errors:
            for err in errors:
                flash(err, "error")
            return render_template("pantry/form.html", item=existing, ingredients=ingredients)

        unit = unit or ingredient["unit"]

        if item_id:
            db.execute(
                """UPDATE pantry_items SET ingredient_id=?, quantity=?, unit=?,
                   expiry_date=?, location=?, updated_at=CURRENT_TIMESTAMP
                   WHERE id=?""",
                (ingredient_id, quantity, unit, expiry_date, location, item_id)
            )
            flash("Pantry item updated.", "success")
        else:
            db.execute(
                """INSERT INTO pantry_items (ingredient_id, quantity, unit, expiry_date, location)
                   VALUES (?, ?, ?, ?, ?)""",
                (ingredient_id, quantity, unit, expiry_date, location)
            )
            flash("Pantry item added.", "success")
        db.commit()
        return redirect(url_for("pantry.pantry_list"))

    return render_template("pantry/form.html", item=existing, ingredients=ingredients)


@pantry_bp.route("/pantry/delete/<int:item_id>", methods=["POST"])
@login_required
def pantry_delete(item_id):
    db = get_db()
    db.execute("DELETE FROM pantry_items WHERE id = ?", (item_id,))
    db.commit()
    flash("Pantry item removed.", "success")
    return redirect(url_for("pantry.pantry_list"))


@pantry_bp.route("/pantry/consume/<int:item_id>", methods=["POST"])
@login_required
def pantry_consume(item_id):
    """Reduce pantry item quantity (e.g., after using in a recipe)."""
    db = get_db()
    entry = db.execute("SELECT * FROM pantry_items WHERE id = ?", (item_id,)).fetchone()

    if not entry:
        flash("Item not found.", "error")
        return redirect(url_for("pantry.pantry_list"))

    amount_str = request.form.get("amount", "").strip() or request.form.get("quantity", "0").strip()
    try:
        amount = float(amount_str)
    except ValueError:
        flash("Invalid amount.", "error")
        return redirect(url_for("pantry.pantry_list"))

    if amount <= 0:
        flash("Amount must be positive.", "error")
        return redirect(url_for("pantry.pantry_list"))

    new_qty = max(0, entry["quantity"] - amount)
    db.execute(
        """UPDATE pantry_items SET quantity = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
        (new_qty, item_id)
    )
    db.commit()

    if new_qty == 0:
        flash(f"Used {amount} {entry['unit']}. Item depleted.", "info")
    else:
        flash(f"Used {amount} {entry['unit']}. {new_qty} {entry['unit']} remaining.", "success")

    return redirect(url_for("pantry.pantry_list"))


def register_pantry_routes(app):
    app.register_blueprint(pantry_bp)
