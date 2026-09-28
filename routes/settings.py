"""Routes for app settings."""

from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from database import get_db
from auth import login_required, hash_password, verify_password

settings_bp = Blueprint("settings", __name__)


@settings_bp.route("/settings")
@login_required
def settings():
    db = get_db()

    plan_start = db.execute("SELECT value FROM settings WHERE key = 'plan_start_date'").fetchone()
    plan_end = db.execute("SELECT value FROM settings WHERE key = 'plan_end_date'").fetchone()
    unit_preference = db.execute("SELECT value FROM settings WHERE key = 'unit_preference'").fetchone()
    default_servings = db.execute("SELECT value FROM settings WHERE key = 'default_servings'").fetchone()
    subtract_pantry = db.execute("SELECT value FROM settings WHERE key = 'subtract_pantry_from_list'").fetchone()

    return render_template(
        "settings.html",
        plan_start=plan_start["value"] if plan_start else "",
        plan_end=plan_end["value"] if plan_end else "",
        unit_preference=unit_preference["value"] if unit_preference else "metric",
        default_servings=default_servings["value"] if default_servings else 1,
        subtract_pantry=bool(subtract_pantry and subtract_pantry["value"] == "1"),
    )


@settings_bp.route("/settings/save", methods=["POST"])
@login_required
def save():
    # Support both 옛날 action-based and field-based form submissions
    action = request.form.get("action", "").strip()
    has_dates = bool(request.form.get("plan_start_date") or request.form.get("plan_start"))
    has_prefs = bool(request.form.get("unit_preference") or request.form.get("default_servings") is not None)

    if not action and not has_dates and not has_prefs:
        flash("No settings to save.", "error")
        return redirect(url_for("settings.settings"))

    db = get_db()

    # Save dates if present (from either field naming convention)
    plan_start = request.form.get("plan_start", "").strip() or request.form.get("plan_start_date", "").strip()
    plan_end = request.form.get("plan_end", "").strip() or request.form.get("plan_end_date", "").strip()

    if plan_start or plan_end or action == "save_dates":
        if not plan_start or not plan_end:
            flash("Both dates are required.", "error")
            return redirect(url_for("settings.settings"))
        db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('plan_start_date', ?)", (plan_start,))
        db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('plan_end_date', ?)", (plan_end,))
        db.commit()
        flash("Plan dates saved.", "success")

    # Save preferences if present
    unit_preference = request.form.get("unit_preference", "").strip()
    default_servings = request.form.get("default_servings", type=int)
    subtract_pantry = request.form.get("subtract_pantry_from_list") or request.form.get("subtract_pantry")

    if unit_preference or default_servings is not None or subtract_pantry is not None or action == "save_prefs":
        if not unit_preference:
            unit_preference = "metric"
        if default_servings is None:
            default_servings = 1
        if default_servings < 1:
            flash("Default servings must be at least 1.", "error")
            return redirect(url_for("settings.settings"))
        db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('unit_preference', ?)", (unit_preference,))
        db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('default_servings', ?)", (str(default_servings),))
        db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('subtract_pantry_from_list', ?)", (str(1 if subtract_pantry else 0),))
        db.commit()
        flash("Preferences saved.", "success")

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
