"""Routes for pantry inventory."""

from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash
from database import get_db, get_setting
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


@pantry_bp.route("/pantry/stock-count", methods=["GET", "POST"])
@login_required
def stock_count():
    """Sunday count of the steady items, plus how many to keep and how long they keep."""
    from services.suggestions import stock_count_rows
    today = local_today()
    if request.method == "POST":
        db = get_db()
        keys = request.form.getlist("item_key")
        counted = request.form.getlist("count")
        maxes = request.form.getlist("max_on_hand")
        keeps = request.form.getlist("keep_days")
        saved, problems = 0, []

        def number(values, i, cast):
            raw = values[i].strip() if i < len(values) else ""
            if not raw:
                return None
            value = cast(raw)
            if value < 0:
                raise ValueError
            return value

        for i, key in enumerate(keys):
            try:
                count = number(counted, i, float)
                max_on_hand = number(maxes, i, float)
                keep_days = number(keeps, i, int)
            except ValueError:
                problems.append(key.split(":", 1)[-1])
                continue
            db.execute(
                """INSERT INTO item_usage (item_key, max_on_hand, keep_days) VALUES (?, ?, ?)
                   ON CONFLICT(item_key) DO UPDATE SET max_on_hand = excluded.max_on_hand,
                   keep_days = excluded.keep_days, updated_at = CURRENT_TIMESTAMP""",
                (key, max_on_hand, keep_days))
            if count is not None:
                db.execute(
                    """INSERT INTO stock_counts (item_key, counted_on, quantity) VALUES (?, ?, ?)
                       ON CONFLICT(item_key, counted_on) DO UPDATE SET quantity = excluded.quantity""",
                    (key, today.isoformat(), count))
                saved += 1
        db.commit()
        if problems:
            flash("Counts and limits must be whole positive numbers. Not saved for: " + ", ".join(problems), "error")
        flash(f"Saved {saved} count{'s' if saved != 1 else ''}. The next shopping list buys what runs short.", "success")
        return redirect(url_for("pantry.stock_count"))
    from services.ai_review import resolve_config
    from services.photo_count import accuracy
    db = get_db()
    photos = {r["item_key"]: dict(r) for r in db.execute(
        "SELECT item_key, quantity, confidence, seen_as FROM photo_counts WHERE counted_on = ?",
        (today.isoformat(),))}
    pairs = [dict(r) for r in db.execute(
        """SELECT p.item_key AS label, p.counted_on, p.quantity AS photo, s.quantity AS counted
           FROM photo_counts p JOIN stock_counts s
             ON s.item_key = p.item_key AND s.counted_on = p.counted_on""")]
    rows = stock_count_rows(today)
    labels = {r["key"]: r["label"] for r in rows}
    for pair in pairs:
        pair["label"] = labels.get(pair["label"], pair["label"].split(":", 1)[-1])
    return render_template("pantry/stock_count.html", rows=rows, today=today, photos=photos,
                           photo_accuracy=accuracy(pairs),
                           ai_configured=resolve_config(get_setting)["configured"])


@pantry_bp.route("/pantry/stock-count/photo", methods=["POST"])
@login_required
def stock_count_photo():
    """Experimental: guess today's counts from photos. Guesses are shown, never saved as counts."""
    from services.ai_review import AIReviewError, resolve_config
    from services.photo_count import (
        MAX_PHOTO_BYTES, MAX_PHOTOS, count_from_photos, image_type, resolve_model,
    )
    from services.suggestions import stock_count_rows
    config = resolve_config(get_setting)
    if not config["configured"]:
        flash("Photo counts need an OpenRouter key in Settings.", "error")
        return redirect(url_for("pantry.stock_count"))
    photos = []
    for upload in request.files.getlist("photos"):
        if not upload or not upload.filename:
            continue
        data = upload.read(MAX_PHOTO_BYTES + 1)
        media_type = image_type(data)
        if len(data) > MAX_PHOTO_BYTES or not media_type:
            flash(f"{upload.filename} was skipped: photos must be JPG, PNG or WEBP and 5 MB or smaller.", "error")
            continue
        photos.append((media_type, data))
    if len(photos) > MAX_PHOTOS:
        flash(f"Only the first {MAX_PHOTOS} photos were used.", "warning")
        photos = photos[:MAX_PHOTOS]
    today = local_today()
    model = resolve_model(get_setting)
    try:
        guesses = count_from_photos(stock_count_rows(today), photos, config["key"], model)
    except AIReviewError as exc:
        flash(f"Photo count failed: {exc}", "error")
        return redirect(url_for("pantry.stock_count"))
    db = get_db()
    for key, guess in guesses.items():
        db.execute(
            """INSERT INTO photo_counts (item_key, counted_on, quantity, confidence, seen_as, model)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(item_key, counted_on) DO UPDATE SET quantity = excluded.quantity,
               confidence = excluded.confidence, seen_as = excluded.seen_as, model = excluded.model,
               created_at = CURRENT_TIMESTAMP""",
            (key, today.isoformat(), guess["count"], guess["confidence"], guess["seen_as"], model))
    db.commit()
    flash(f"The photos show {len(guesses)} tracked item{'s' if len(guesses) != 1 else ''}. "
          "Their guesses are beside the count boxes: count by hand and save as usual, "
          "and the page keeps score of how close the photos were.", "success")
    return redirect(url_for("pantry.stock_count"))


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
    from services import undo as undo_service

    db = get_db()
    token = undo_service.delete_pantry_item(db, item_id)
    db.commit()
    if request.headers.get("HX-Request"):
        return "", 204
    if token:
        flash("Pantry item removed.", "success")
        return redirect(url_for("pantry.pantry_list", undo=token))
    flash("Pantry item not found.", "error")
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
