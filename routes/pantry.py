"""Routes for pantry inventory."""

from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash
from database import get_db
from auth import login_required
from models import category_label, today as local_today
from services.mutations import AgentError

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
        from services.mutations import add_stock, update_stock
        ingredient_id = request.form.get("ingredient_id", type=int)
        raw_quantity = request.form.get("quantity", "").strip()
        try:
            quantity = float(raw_quantity) if raw_quantity else 0
        except ValueError:
            quantity = None
        unit = request.form.get("unit", "").strip()
        expiry_date = request.form.get("expiry_date", "").strip() or None
        location = request.form.get("location", "pantry").strip()

        submitted = {
            "id": item_id,
            "ingredient_id": ingredient_id,
            "quantity": raw_quantity,
            "unit": unit,
            "expiry_date": expiry_date or "",
            "location": location or "pantry",
        }
        if item_id and existing:
            submitted = {**dict(existing), **submitted}
        try:
            if item_id:
                update_stock(db, item_id, ingredient_id, quantity, unit=unit,
                             expiry_date=expiry_date, location=location)
                flash("Pantry item updated.", "success")
            else:
                add_stock(db, ingredient_id, quantity, unit=unit,
                          expiry_date=expiry_date, location=location)
                flash("Pantry item added.", "success")
        except AgentError as exc:
            return render_template("pantry/form.html", item=submitted,
                                   ingredients=ingredients, errors=exc.messages)
        except ValueError as exc:
            if request.headers.get("HX-Request"):
                return render_template("pantry/form.html", item=submitted,
                                       ingredients=ingredients, errors=[str(exc)]), 400
            return render_template("pantry/form.html", item=submitted,
                                   ingredients=ingredients, errors=[str(exc)])
        db.commit()
        if request.headers.get("HX-Request"):
            return "", 204
        return redirect(url_for("pantry.pantry_list"))

    return render_template("pantry/form.html", item=existing, ingredients=ingredients)


@pantry_bp.route("/pantry/delete/<int:item_id>", methods=["POST"])
@login_required
def pantry_delete(item_id):
    db = get_db()
    db.execute("DELETE FROM pantry_items WHERE id = ?", (item_id,))
    db.commit()
    if request.headers.get("HX-Request"):
        return "", 204
    flash("Pantry item removed.", "success")
    return redirect(url_for("pantry.pantry_list"))


@pantry_bp.route("/pantry/consume/<int:item_id>", methods=["POST"])
@login_required
def pantry_consume(item_id):
    """Reduce pantry item quantity (e.g., after using in a recipe)."""
    from services.mutations import consume_stock
    amount_str = request.form.get("amount", "").strip() or request.form.get("quantity", "0").strip()
    try:
        amount = float(amount_str)
    except ValueError:
        if request.headers.get("HX-Request"):
            return "Invalid amount.", 400
        flash("Invalid amount.", "error")
        return redirect(url_for("pantry.pantry_list"))
    try:
        result = consume_stock(get_db(), item_id, amount)
    except ValueError as exc:
        if request.headers.get("HX-Request"):
            return str(exc), 400
        flash(str(exc), "error")
        return redirect(url_for("pantry.pantry_list"))
    if request.headers.get("HX-Request"):
        if result["depleted"]:
            return "", 204
        db = get_db()
        item = db.execute("""
            SELECT p.*, i.name as ingredient_name, i.display_name, i.category, i.unit as ingredient_unit
            FROM pantry_items p JOIN ingredients i ON p.ingredient_id = i.id WHERE p.id = ?
        """, (item_id,)).fetchone()
        if item:
            entry = dict(item)
            from models import today as local_today
            from datetime import datetime
            today = local_today()
            if item["expiry_date"]:
                try:
                    exp_date = datetime.strptime(item["expiry_date"], "%Y-%m-%d").date()
                    days_left = (exp_date - today).days
                    entry["days_until_expiry"] = days_left
                except ValueError:
                    entry["days_until_expiry"] = None
            entry["expiry_status"] = "ok"
            return render_template("pantry/_item_row.html", item=entry)
        return "", 204
    if result["depleted"]:
        flash(f"Used {result['used']} {result['unit']}. Item depleted.", "info")
    else:
        flash(f"Used {result['used']} {result['unit']}. {result['remaining']} {result['unit']} remaining.", "success")
    return redirect(url_for("pantry.pantry_list"))


def register_pantry_routes(app):
    app.register_blueprint(pantry_bp)
