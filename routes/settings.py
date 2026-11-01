"""Routes for app settings."""

import os

from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from database import get_db, get_setting, set_setting, get_plan_dates
from models import MAX_PLAN_DAYS, parse_date, validate_date_format
from auth import login_required, hash_password, verify_password

settings_bp = Blueprint("settings", __name__)


@settings_bp.route("/settings")
@login_required
def settings():
    return render_template("settings.html", **_settings_values())


def _current_user_id(db):
    """The logged-in user's id, or None (e.g. a stale session)."""
    row = db.execute("SELECT id FROM users WHERE username = ?",
                     (session.get("username"),)).fetchone()
    return row["id"] if row else None


def _detected_payday():
    """A payday guessed from bank credits, offered when none is set yet."""
    if get_setting("payday_date"):
        return None
    from services.bank_import import resolve_directions
    from services.pay_cycle import detect_payday
    rows = [dict(r) for r in get_db().execute(
        "SELECT source, txn_date, description, merchant_key, amount, direction"
        " FROM bank_transactions ORDER BY txn_date")]
    return detect_payday(resolve_directions(rows))


def _bank_accounts():
    from services.suggestions import bank_accounts
    return bank_accounts()["accounts"]


def _settings_values():
    """Current settings as template values (overridable with submitted input)."""
    from services.bank_import import DEFAULT_MERCHANT_PATTERNS
    plan_start, plan_end = get_plan_dates()
    default_patterns = "\n".join(f"{k}={n}" for k, n in DEFAULT_MERCHANT_PATTERNS)
    user_id = _current_user_id(get_db())
    from services.api_keys import list_keys
    banksync_key_set = bool(get_setting("banksync_api_key"))
    banksync_last_sync = get_setting("banksync_last_sync") or ""
    stored_ai_key = bool(get_setting("openrouter_api_key"))
    env_ai_key = bool(os.getenv("OPENROUTER_API_KEY"))
    return {
        "plan_start": plan_start,
        "plan_end": plan_end,
        "unit_preference": get_setting("unit_preference", "metric"),
        "default_servings": get_setting("default_servings", "2"),
        "subtract_pantry": get_setting("subtract_pantry_from_list", "0") == "1",
        "bank_merchant_patterns": get_setting("bank_merchant_patterns") or default_patterns,
        "monthly_budget": get_setting("monthly_budget", ""),
        "fortnightly_income": get_setting("fortnightly_income", ""),
        "payday_date": get_setting("payday_date", ""),
        "pay_frequency": get_setting("pay_frequency", "fortnightly"),
        "cycle_grocery_budget": get_setting("cycle_grocery_budget", ""),
        "savings_per_pay": get_setting("savings_per_pay", ""),
        "long_term_per_pay": get_setting("long_term_per_pay", ""),
        "savings_accounts": get_setting("savings_accounts", ""),
        "bank_accounts": _bank_accounts(),
        "detected_payday": _detected_payday(),
        "api_keys": list_keys(get_db(), user_id) if user_id else [],
        "new_api_key": None,
        "banksync_key_set": banksync_key_set,
        "banksync_last_sync": banksync_last_sync,
        "openrouter_key_set": stored_ai_key or env_ai_key,
        "openrouter_from_env": env_ai_key and not stored_ai_key,
        "openrouter_model": (get_setting("openrouter_model")
                             or os.getenv("OPENROUTER_MODEL")
                             or "openrouter/free"),
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
            if request.headers.get("HX-Request"):
                return render_template("settings.html", **values)
            return render_template("settings.html", **values)
        set_setting("plan_start_date", plan_start)
        set_setting("plan_end_date", plan_end)
        db.commit()
        if request.headers.get("HX-Request"):
            return render_template("settings/_save_status.html", action="dates")
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
            if request.headers.get("HX-Request"):
                return render_template("settings.html", **values)
            return render_template("settings.html", **values)
        set_setting("unit_preference", unit_preference)
        set_setting("default_servings", default_servings)
        set_setting("subtract_pantry_from_list", 1 if request.form.get("subtract_pantry") else 0)
        db.commit()
        if request.headers.get("HX-Request"):
            return render_template("settings/_save_status.html", action="prefs")
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
            if request.headers.get("HX-Request"):
                return render_template("settings.html", **values)
            return render_template("settings.html", **values)
        api_key = request.form.get("banksync_api_key", "").strip()
        if api_key:
            set_setting("banksync_api_key", api_key)
        set_setting("bank_merchant_patterns", patterns.strip())
        db.commit()
        if request.headers.get("HX-Request"):
            return render_template("settings/_save_status.html", action="bank")
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
                if request.headers.get("HX-Request"):
                    return render_template("settings.html", **values)
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
                if request.headers.get("HX-Request"):
                    return render_template("settings.html", **values)
                return render_template("settings.html", **values)

        from services.pay_cycle import PAY_FREQUENCIES
        payday = request.form.get("payday_date", "").strip()
        frequency = request.form.get("pay_frequency", "fortnightly").strip()
        cycle_raw = request.form.get("cycle_grocery_budget", "").strip()
        problems = []
        if payday and not validate_date_format(payday):
            problems.append("Enter payday as a date.")
        if frequency not in dict(PAY_FREQUENCIES):
            problems.append("Choose how often you are paid.")
        cycle_budget = ""
        if cycle_raw:
            try:
                value = float(cycle_raw.replace("$", "").replace(",", ""))
                if value <= 0:
                    raise ValueError
                cycle_budget = f"{value:.2f}"
            except ValueError:
                problems.append("The grocery budget per pay cycle must be a dollar amount above zero.")
        pay_amounts = {}
        for key, label in (("savings_per_pay", "Savings"), ("long_term_per_pay", "Long term savings")):
            raw_amount = request.form.get(key, "").strip()
            pay_amounts[key] = ""
            if raw_amount:
                try:
                    value = float(raw_amount.replace("$", "").replace(",", ""))
                    if value < 0:
                        raise ValueError
                    pay_amounts[key] = f"{value:.2f}"
                except ValueError:
                    problems.append(f"{label} each payday must be a dollar amount.")
        if problems:
            values = _settings_values()
            values.update({"payday_date": payday, "pay_frequency": frequency,
                           "cycle_grocery_budget": cycle_raw,
                           "savings_per_pay": request.form.get("savings_per_pay", ""),
                           "long_term_per_pay": request.form.get("long_term_per_pay", ""),
                           "savings_accounts": request.form.get("savings_accounts", ""),
                           "errors": problems, "error_form": "budget"})
            return render_template("settings.html", **values)
        set_setting("payday_date", payday)
        set_setting("pay_frequency", frequency)
        set_setting("cycle_grocery_budget", cycle_budget)
        for key, value in pay_amounts.items():
            set_setting(key, value)
        from services.savings_advice import parse_accounts
        set_setting("savings_accounts", ", ".join(parse_accounts(request.form.get("savings_accounts", ""))))

        db.commit()
        if request.headers.get("HX-Request"):
            return render_template("settings/_save_status.html", action="budget")
        flash("Budget target saved.", "success")

    elif action == "save_ai":
        key = request.form.get("openrouter_api_key", "").strip()
        model = request.form.get("openrouter_model", "").strip() or "openrouter/free"
        if len(model) > 80:
            values = _settings_values()
            values.update({"openrouter_model": model,
                           "errors": ["The model name is too long."],
                           "error_form": "ai"})
            if request.headers.get("HX-Request"):
                return render_template("settings.html", **values)
            return render_template("settings.html", **values)
        if request.form.get("remove_ai_key"):
            set_setting("openrouter_api_key", "")
        elif key:
            set_setting("openrouter_api_key", key)
        set_setting("openrouter_model", model)
        db.commit()
        if request.headers.get("HX-Request"):
            return render_template("settings/_save_status.html", action="ai")
        flash("AI review settings saved.", "success")

    elif action == "test_banksync":
        from markupsafe import escape

        from services.banksync_client import (
            BankSyncClient, BankSyncError, describe_credential,
        )
        api_key = request.form.get("banksync_api_key", "").strip()
        if not api_key:
            if request.headers.get("HX-Request"):
                return '<div class="alert alert-danger mt-2">Enter a BankSync API key first.</div>', 400
            flash("Enter a BankSync API key first.", "error")
            return redirect(url_for("settings.settings"))
        try:
            client = BankSyncClient(api_key)
            credential = describe_credential(client.whoami())
            try:
                banks = len(client.list_banks())
                access = "{} bank connection{}".format(
                    banks, "" if banks == 1 else "s")
            except BankSyncError as exc:
                access = str(exc)
            message = "Connected to BankSync ({})".format(credential["name"])
            if credential["plan"]:
                message += " · plan {}".format(credential["plan"])
            message += " · {}".format(access)
            if not credential["plan_has_api"]:
                message += (" — this plan has no API access. Upgrade to "
                            "Standard or higher before syncing.")
            if request.headers.get("HX-Request"):
                css = "warning" if not credential["plan_has_api"] else "success"
                return '<div class="alert alert-{} mt-2">{}</div>'.format(
                    css, escape(message))
            flash(message, "warning" if not credential["plan_has_api"] else "success")
        except BankSyncError as exc:
            if request.headers.get("HX-Request"):
                return '<div class="alert alert-danger mt-2">BankSync connection failed: {}</div>'.format(
                    escape(str(exc))), 400
            flash(f"BankSync connection failed: {exc}", "error")
        return redirect(url_for("settings.settings"))

    else:
        flash("No settings to save.", "error")

    return redirect(url_for("settings.settings"))


@settings_bp.route("/settings/api-keys", methods=["POST"])
@login_required
def api_key_create():
    """Generate a per-account API key; the raw key is shown once."""
    from services.api_keys import create_key
    db = get_db()
    user_id = _current_user_id(db)
    if not user_id:
        flash("Log in again to manage API keys.", "error")
        return redirect(url_for("settings.settings"))
    name = request.form.get("key_name", "").strip() or "default"
    key = create_key(db, user_id, name)
    values = _settings_values()
    values["new_api_key"] = key
    flash(f"API key “{key['name']}” created — copy it now, it won't be shown again.",
          "success")
    return render_template("settings.html", **values)


@settings_bp.route("/settings/api-keys/<int:key_id>/revoke", methods=["POST"])
@login_required
def api_key_revoke(key_id):
    """Revoke one of the account's API keys."""
    from services.api_keys import revoke_key
    from services.mutations import AgentError
    db = get_db()
    user_id = _current_user_id(db)
    if not user_id:
        flash("Log in again to manage API keys.", "error")
        return redirect(url_for("settings.settings"))
    try:
        revoke_key(db, user_id, key_id)
    except AgentError:
        flash("That API key doesn't exist.", "error")
        return redirect(url_for("settings.settings"))
    flash("API key revoked.", "success")
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
