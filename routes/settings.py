"""Routes for app settings."""

from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from database import get_db, get_setting, set_setting, get_plan_dates
from models import MAX_PLAN_DAYS, parse_date
from auth import login_required, hash_password, verify_password

settings_bp = Blueprint("settings", __name__)


@settings_bp.route("/settings")
@login_required
def settings():
    plan_start, plan_end = get_plan_dates()
    return render_template(
        "settings.html",
        plan_start=plan_start,
        plan_end=plan_end,
        unit_preference=get_setting("unit_preference", "metric"),
        default_servings=get_setting("default_servings", "2"),
        subtract_pantry=get_setting("subtract_pantry_from_list", "0") == "1",
    )


@settings_bp.route("/settings/save", methods=["POST"])
@login_required
def save():
    """Save one of the two settings forms (identified by the submit button's action)."""
    action = request.form.get("action", "").strip()
    db = get_db()

    if action == "save_dates":
        plan_start = request.form.get("plan_start", "").strip()
        plan_end = request.form.get("plan_end", "").strip()
        start, end = parse_date(plan_start), parse_date(plan_end)
        if not start or not end:
            flash("Both dates are required.", "error")
        elif end < start:
            flash("The end date must be on or after the start date.", "error")
        elif (end - start).days > MAX_PLAN_DAYS:
            flash("Plans are limited to about three months.", "error")
        else:
            set_setting("plan_start_date", plan_start)
            set_setting("plan_end_date", plan_end)
            db.commit()
            flash("Plan dates saved.", "success")

    elif action == "save_prefs":
        unit_preference = request.form.get("unit_preference", "metric")
        default_servings = request.form.get("default_servings", type=int)
        if unit_preference not in ("metric", "imperial"):
            unit_preference = "metric"
        if not default_servings or default_servings < 1:
            flash("Default servings must be at least 1.", "error")
            return redirect(url_for("settings.settings"))
        set_setting("unit_preference", unit_preference)
        set_setting("default_servings", default_servings)
        set_setting("subtract_pantry_from_list", 1 if request.form.get("subtract_pantry") else 0)
        db.commit()
        flash("Preferences saved.", "success")

    else:
        flash("No settings to save.", "error")

    return redirect(url_for("settings.settings"))


@settings_bp.route("/settings/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not current_password or not new_password:
            flash("Current and new passwords are required.", "error")
            return redirect(url_for("settings.change_password"))

        if new_password != confirm_password:
            flash("New passwords do not match.", "error")
            return redirect(url_for("settings.change_password"))

        if len(new_password) < 8:
            flash("New password must be at least 8 characters.", "error")
            return redirect(url_for("settings.change_password"))

        db = get_db()
        username = session.get("username")
        user = db.execute(
            "SELECT id, password_hash FROM users WHERE username = ?", (username,)
        ).fetchone()

        if not user or not verify_password(current_password, user["password_hash"]):
            flash("Current password is incorrect.", "error")
            return redirect(url_for("settings.change_password"))

        db.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (hash_password(new_password), user["id"])
        )
        db.commit()
        flash("Password changed successfully.", "success")
        return redirect(url_for("dashboard"))

    return render_template("change_password.html")


def register_settings_routes(app):
    app.register_blueprint(settings_bp)
