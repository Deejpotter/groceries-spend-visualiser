"""Routes for app settings."""

from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from database import get_db, get_setting, set_setting, get_plan_dates
from models import MAX_PLAN_DAYS, parse_date
from auth import login_required, hash_password, verify_password

settings_bp = Blueprint("settings", __name__)


@settings_bp.route("/settings")
@login_required
def settings():
    return render_template("settings.html", **_settings_values())


def _settings_values():
    """Current settings as template values (overridable with submitted input)."""
    from services.bank_import import DEFAULT_MERCHANT_PATTERNS
    plan_start, plan_end = get_plan_dates()
    default_patterns = "\n".join(f"{k}={n}" for k, n in DEFAULT_MERCHANT_PATTERNS)
    return {
        "plan_start": plan_start,
        "plan_end": plan_end,
        "unit_preference": get_setting("unit_preference", "metric"),
        "default_servings": get_setting("default_servings", "2"),
        "subtract_pantry": get_setting("subtract_pantry_from_list", "0") == "1",
        "bank_merchant_patterns": get_setting("bank_merchant_patterns") or default_patterns,
        "monthly_budget": get_setting("monthly_budget", ""),
        "fortnightly_income": get_setting("fortnightly_income", ""),
        "errors": None,
        "error_form": None,
    }


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
        errors = []
        if not start or not end:
            errors.append("Both dates are required.")
        elif end < start:
            errors.append("The end date must be on or after the start date.")
        elif (end - start).days > MAX_PLAN_DAYS:
            errors.append("Plans are limited to about three months.")
        if errors:
            values = _settings_values()
            values.update({"plan_start": plan_start, "plan_end": plan_end,
                           "errors": errors, "error_form": "dates"})
            return render_template("settings.html", **values)
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
            values = _settings_values()
            values.update({
                "unit_preference": unit_preference,
                "default_servings": request.form.get("default_servings", ""),
                "subtract_pantry": bool(request.form.get("subtract_pantry")),
                "errors": ["Default servings must be at least 1."],
                "error_form": "prefs",
            })
            return render_template("settings.html", **values)
        set_setting("unit_preference", unit_preference)
        set_setting("default_servings", default_servings)
        set_setting("subtract_pantry_from_list", 1 if request.form.get("subtract_pantry") else 0)
        db.commit()
        flash("Preferences saved.", "success")

    elif action == "save_bank":
        patterns = request.form.get("bank_merchant_patterns", "")
        errors = []
        for line in patterns.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                keyword, name = line.split("=", 1)
                if not keyword.strip() or not name.strip():
                    errors.append("Every pattern needs both a keyword and a store name.")
                    break
        if errors:
            values = _settings_values()
            values.update({"bank_merchant_patterns": patterns,
                           "errors": errors, "error_form": "bank"})
            return render_template("settings.html", **values)
        api_key = request.form.get("banksync_api_key", "").strip()
        if api_key:
            set_setting("banksync_api_key", api_key)
        set_setting("bank_merchant_patterns", patterns.strip())
        db.commit()
        flash("Bank import settings saved.", "success")

    elif action == "save_budget":
        raw = request.form.get("monthly_budget", "").strip()
        if not raw:
            set_setting("monthly_budget", "")
        else:
            try:
                target = float(raw.replace("$", "").replace(",", ""))
                if target > 0:
                    set_setting("monthly_budget", f"{target:.2f}")
            except ValueError:
                values = _settings_values()
                values.update({"monthly_budget": raw,
                               "errors": ["Enter a dollar amount above zero."],
                               "error_form": "budget"})
                return render_template("settings.html", **values)

        income_raw = request.form.get("fortnightly_income", "").strip()
        if not income_raw:
            set_setting("fortnightly_income", "")
        else:
            try:
                income_val = float(income_raw.replace("$", "").replace(",", ""))
                if income_val > 0:
                    set_setting("fortnightly_income", f"{income_val:.2f}")
            except ValueError:
                values = _settings_values()
                values.update({"fortnightly_income": income_raw,
                               "errors": ["Fortnightly income must be a dollar amount above zero."],
                               "error_form": "budget"})
                return render_template("settings.html", **values)

        db.commit()
        flash("Budget target saved.", "success")

    elif action == "test_banksync":
        from services.banksync_client import BankSyncClient, BankSyncError
        api_key = request.form.get("banksync_api_key", "").strip()
        if not api_key:
            flash("Enter a BankSync API key first.", "error")
            return redirect(url_for("settings.settings"))
        try:
            who = BankSyncClient(api_key).whoami()
            workspace = who.get("workspace") or {}
            name = workspace.get("name") or who.get("name") or "workspace"
            flash(f"Connected to BankSync ({name}).", "success")
        except BankSyncError as exc:
            flash(f"BankSync connection failed: {exc}", "error")
        return redirect(url_for("settings.settings"))

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
