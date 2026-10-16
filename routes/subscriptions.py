"""Routes for subscription and bill tracking CRUD."""

from datetime import date, timedelta

from flask import Blueprint, render_template, request, redirect, url_for, flash

from auth import login_required
from database import get_db, get_setting, set_setting
from services.subscriptions import (
    fortnight_for_date,
    format_frequency,
    FREQUENCY_LABELS,
    subscription_category_label,
    SUBSCRIPTION_CATEGORY_LOOKUP,
    SUBSCRIPTION_CATEGORIES,
    suggest_from_purchases,
    suggest_from_unclassified,
)
from services.spend_analysis import load_purchases

subscriptions_bp = Blueprint("subscriptions", __name__)


@subscriptions_bp.route("/subscriptions")
@login_required
def subscription_list():
    """List all active subscriptions, grouped by category."""
    db = get_db()
    rows = db.execute(
        "SELECT * FROM subscriptions WHERE active = 1 ORDER BY category, name"
    ).fetchall()
    subscriptions = [dict(r) for r in rows]

    # Group by category.
    by_category = {}
    for s in subscriptions:
        cat = s.get("category") or "other"
        by_category.setdefault(cat, []).append(s)

    # Inactive subscriptions (paused or soft-deleted) get their own section.
    inactive = [dict(r) for r in db.execute(
        "SELECT * FROM subscriptions WHERE active = 0 ORDER BY category, name"
    ).fetchall()]

    return render_template(
        "subscriptions/list.html",
        by_category=by_category,
        categories=SUBSCRIPTION_CATEGORIES,
        category_label=subscription_category_label,
        format_frequency=format_frequency,
        inactive=inactive,
        inactive_count=len(inactive),
    )


@subscriptions_bp.route("/subscriptions/add", methods=["GET", "POST"])
@login_required
def subscription_add():
    """Add a new subscription."""
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        amount_raw = request.form.get("amount", "").strip()
        frequency = request.form.get("frequency", "monthly").strip().lower()
        category = request.form.get("category", "").strip()
        next_due = request.form.get("next_due", "").strip()
        notes = request.form.get("notes", "").strip()

        errors = []
        if not name:
            errors.append("Name is required.")
        try:
            amount = float(amount_raw.replace("$", "").replace(",", ""))
            if amount <= 0:
                errors.append("Amount must be greater than zero.")
        except (ValueError, AttributeError):
            errors.append("Amount must be a valid number.")
            amount = None
        if frequency not in FREQUENCY_LABELS:
            errors.append("Invalid frequency.")
        if not next_due:
            errors.append("Next due date is required.")
        else:
            try:
                date.fromisoformat(next_due)
            except ValueError:
                errors.append("Next due date must be a valid date.")

        if errors:
            return render_template(
                "subscriptions/form.html",
                errors=errors,
                categories=SUBSCRIPTION_CATEGORIES,
                form={
                    "name": name,
                    "amount": amount_raw,
                    "frequency": frequency,
                    "category": category,
                    "next_due": next_due,
                    "notes": notes,
                },
            )

        db = get_db()
        db.execute(
            """INSERT INTO subscriptions (name, amount, frequency, category, next_due, notes)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (name, amount, frequency, category or "other", next_due, notes or None),
        )
        db.commit()
        flash(f"Subscription '{name}' added.", "success")
        return redirect(url_for("subscriptions.subscription_list"))

    return render_template(
        "subscriptions/form.html",
        errors=None,
        categories=SUBSCRIPTION_CATEGORIES,
        form=None,
    )


@subscriptions_bp.route("/subscriptions/<int:sub_id>/edit", methods=["GET", "POST"])
@login_required
def subscription_edit(sub_id):
    """Edit an existing subscription."""
    db = get_db()
    sub = db.execute("SELECT * FROM subscriptions WHERE id = ?", (sub_id,)).fetchone()
    if not sub:
        flash("Subscription not found.", "error")
        return redirect(url_for("subscriptions.subscription_list"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        amount_raw = request.form.get("amount", "").strip()
        frequency = request.form.get("frequency", "monthly").strip().lower()
        category = request.form.get("category", "").strip()
        next_due = request.form.get("next_due", "").strip()
        notes = request.form.get("notes", "").strip()

        errors = []
        if not name:
            errors.append("Name is required.")
        try:
            amount = float(amount_raw.replace("$", "").replace(",", ""))
            if amount <= 0:
                errors.append("Amount must be greater than zero.")
        except (ValueError, AttributeError):
            errors.append("Amount must be a valid number.")
            amount = None
        if frequency not in FREQUENCY_LABELS:
            errors.append("Invalid frequency.")
        if not next_due:
            errors.append("Next due date is required.")
        else:
            try:
                date.fromisoformat(next_due)
            except ValueError:
                errors.append("Next due date must be a valid date.")

        if errors:
            return render_template(
                "subscriptions/form.html",
                errors=errors,
                categories=SUBSCRIPTION_CATEGORIES,
                form={
                    "id": sub_id,
                    "name": name,
                    "amount": amount_raw,
                    "frequency": frequency,
                    "category": category,
                    "next_due": next_due,
                    "notes": notes,
                },
            )

        db.execute(
            """UPDATE subscriptions SET name = ?, amount = ?, frequency = ?,
               category = ?, next_due = ?, notes = ? WHERE id = ?""",
            (name, amount, frequency, category or "other", next_due, notes or None, sub_id),
        )
        db.commit()
        flash(f"Subscription '{name}' updated.", "success")
        return redirect(url_for("subscriptions.subscription_list"))

    return render_template(
        "subscriptions/form.html",
        errors=None,
        categories=SUBSCRIPTION_CATEGORIES,
        form=dict(sub),
    )


@subscriptions_bp.route("/subscriptions/<int:sub_id>/toggle", methods=["POST"])
@login_required
def subscription_toggle(sub_id):
    """Activate or deactivate a subscription."""
    db = get_db()
    sub = db.execute("SELECT * FROM subscriptions WHERE id = ?", (sub_id,)).fetchone()
    if not sub:
        flash("Subscription not found.", "error")
        return redirect(url_for("subscriptions.subscription_list"))

    new_active = 0 if sub["active"] else 1
    db.execute("UPDATE subscriptions SET active = ? WHERE id = ?", (new_active, sub_id))
    db.commit()
    status = "activated" if new_active else "deactivated"
    flash(f"Subscription '{sub['name']}' {status}.", "success")
    return redirect(url_for("subscriptions.subscription_list"))


@subscriptions_bp.route("/subscriptions/<int:sub_id>/resume", methods=["POST"])
@login_required
def subscription_resume(sub_id):
    """Reactivate a paused subscription."""
    db = get_db()
    sub = db.execute("SELECT * FROM subscriptions WHERE id = ?", (sub_id,)).fetchone()
    if not sub:
        flash("Subscription not found.", "error")
        return redirect(url_for("subscriptions.subscription_list"))

    db.execute("UPDATE subscriptions SET active = 1 WHERE id = ?", (sub_id,))
    db.commit()
    flash(f"Subscription '{sub['name']}' resumed.", "success")
    return redirect(url_for("subscriptions.subscription_list"))


@subscriptions_bp.route("/subscriptions/<int:sub_id>/purge", methods=["POST"])
@login_required
def subscription_purge(sub_id):
    """Permanently delete a paused subscription (it is already hidden)."""
    db = get_db()
    sub = db.execute("SELECT * FROM subscriptions WHERE id = ?", (sub_id,)).fetchone()
    if not sub:
        flash("Subscription not found.", "error")
        return redirect(url_for("subscriptions.subscription_list"))

    db.execute("DELETE FROM subscriptions WHERE id = ?", (sub_id,))
    db.commit()
    flash(f"Subscription '{sub['name']}' permanently deleted.", "success")
    return redirect(url_for("subscriptions.subscription_list"))


@subscriptions_bp.route("/subscriptions/<int:sub_id>/delete", methods=["POST"])
@login_required
def subscription_delete(sub_id):
    """Soft-delete a subscription (set active = 0)."""
    db = get_db()
    sub = db.execute("SELECT * FROM subscriptions WHERE id = ?", (sub_id,)).fetchone()
    if not sub:
        flash("Subscription not found.", "error")
        return redirect(url_for("subscriptions.subscription_list"))

    db.execute("UPDATE subscriptions SET active = 0 WHERE id = ?", (sub_id,))
    db.commit()
    flash(f"Subscription '{sub['name']}' deleted.", "success")
    return redirect(url_for("subscriptions.subscription_list"))


@subscriptions_bp.route("/subscriptions/<int:sub_id>/mark-paid", methods=["POST"])
@login_required
def subscription_mark_paid(sub_id):
    """Mark a payment as paid: advance one cycle, catching up missed ones."""
    from services.subscriptions import mark_paid
    db = get_db()
    sub = db.execute("SELECT * FROM subscriptions WHERE id = ?", (sub_id,)).fetchone()
    if not sub:
        flash("Subscription not found.", "error")
        return redirect(url_for("subscriptions.subscription_list"))

    next_due, skipped = mark_paid(dict(sub))
    if next_due:
        db.execute("UPDATE subscriptions SET next_due = ? WHERE id = ?", (next_due, sub_id))
        db.commit()
        note = f"Marked '{sub['name']}' as paid. Next due: {next_due}."
        if skipped:
            note += f" Caught up {skipped} missed cycle{'s' if skipped != 1 else ''}."
        flash(note, "success")
    else:
        flash("Couldn't compute next due date.", "error")
    return redirect(url_for("subscriptions.subscription_list"))


@subscriptions_bp.route("/subscriptions/fortnight")
@login_required
def subscription_fortnight():
    """Fortnightly budget summary: income - groceries - subscriptions = spare."""
    from services.budget import fortnightly_summary
    from services.repository import load_shopping_inputs, shopping_preferences
    from services.shopping_list_generator import generate_shopping_list, total_cost

    db = get_db()
    today = date.today()
    start, end = fortnight_for_date(today)

    # Subscriptions due this fortnight.
    subs = [dict(r) for r in db.execute(
        "SELECT * FROM subscriptions WHERE active = 1 ORDER BY category, name"
    ).fetchall()]

    # Grocery estimate for the fortnight (current plan, prorated to the overlap).
    from services.budget import prorate_estimate
    plan_start = get_setting("plan_start_date", "")
    plan_end = get_setting("plan_end_date", "")
    grocery_estimate = 0.0
    grocery_note = None
    if plan_start and plan_end:
        entries, recipes, recipe_ingredients, ingredients = load_shopping_inputs(plan_start, plan_end)
        unit_pref, subtract_pantry = shopping_preferences()
        grouped = generate_shopping_list(
            entries, recipes, recipe_ingredients, ingredients,
            unit_preference=unit_pref,
            subtract_pantry=subtract_pantry,
        )
        grocery_estimate, plan_days, overlap_days = prorate_estimate(
            total_cost(grouped), plan_start, plan_end, start, end)
        if plan_days and overlap_days != plan_days:
            grocery_note = f"{plan_days}-day plan, prorated to {overlap_days} days"

    # Fortnightly income from settings.
    raw_income = (get_setting("fortnightly_income") or "").strip()
    try:
        income = float(raw_income.replace("$", "").replace(",", "")) if raw_income else None
    except ValueError:
        income = None

    summary = fortnightly_summary(subs, grocery_estimate, income, start, end)
    return render_template(
        "subscriptions/fortnight.html",
        summary=summary,
        start=start,
        end=end,
        today=today,
        grocery_note=grocery_note,
    )


@subscriptions_bp.route("/subscriptions/suggest")
@login_required
def subscription_suggest():
    """Show subscription suggestions from bank data and purchase history."""
    db = get_db()

    # Suggestions from purchase history.
    purchases = load_purchases(db, start="2024-01-01")
    purchase_suggestions = suggest_from_purchases(purchases)

    # Suggestions from BankSync unclassified (stored in settings as JSON).
    import json
    unclassified_raw = (get_setting("banksync_unclassified") or "[]").strip()
    try:
        unclassified = json.loads(unclassified_raw) if unclassified_raw else []
    except (ValueError, TypeError):
        unclassified = []
    banksync_suggestions = suggest_from_unclassified(unclassified)

    # Merge and deduplicate by name.
    seen = set()
    all_suggestions = []
    for s in purchase_suggestions + banksync_suggestions:
        key = s["name"].lower()
        if key not in seen:
            seen.add(key)
            all_suggestions.append(s)

    return render_template(
        "subscriptions/suggest.html",
        suggestions=all_suggestions,
    )


@subscriptions_bp.route("/subscriptions/suggest/accept", methods=["POST"])
@login_required
def subscription_accept_suggestion():
    """Accept a suggested subscription and create it."""
    name = request.form.get("name", "").strip()
    amount_raw = request.form.get("amount", "").strip()
    frequency = request.form.get("frequency", "monthly").strip().lower()
    category = request.form.get("category", "other").strip()

    if not name:
        flash("Suggestion is missing a name.", "error")
        return redirect(url_for("subscriptions.subscription_suggest"))

    try:
        amount = float(amount_raw)
    except (ValueError, TypeError):
        flash("Suggestion has an invalid amount.", "error")
        return redirect(url_for("subscriptions.subscription_suggest"))

    if frequency not in FREQUENCY_LABELS:
        frequency = "monthly"

    # Next due defaults to the detected cadence, else today.
    next_due_raw = (request.form.get("next_due") or "").strip()
    try:
        next_due = date.fromisoformat(next_due_raw).isoformat()
    except ValueError:
        next_due = date.today().isoformat()

    db = get_db()
    db.execute(
        """INSERT INTO subscriptions (name, amount, frequency, category, next_due, notes)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (name, amount, frequency, category, next_due, "Added from suggestion"),
    )
    db.commit()
    flash(f"Added subscription '{name}'.", "success")
    return redirect(url_for("subscriptions.subscription_list"))


def register_subscriptions_routes(app):
    app.register_blueprint(subscriptions_bp)
