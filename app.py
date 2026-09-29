"""Flask application entry point for Grocery Visualiser."""

import logging
import os
import secrets
from urllib.parse import urlparse

from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, jsonify, flash
from flask_wtf.csrf import CSRFProtect

from database import init_db, get_db, close_db, get_plan_dates, get_database_path, backup_database, database_stats
from auth import login_user, logout_user, get_current_user, is_logged_in, login_required, hash_password, verify_password
from models import (
    CATEGORIES, UNITS, MEAL_TYPES, DAYS_OF_WEEK,
    CATEGORY_LOOKUP, DAY_LABELS, UNIT_LOOKUP, MEAL_TYPE_LABELS, category_label,
)
from routes import register_all_routes

log = logging.getLogger(__name__)
csrf = CSRFProtect()


def create_app(testing=False):
    """Application factory (supports testing with a temp DB)."""
    load_dotenv()

    application = Flask(__name__)
    secret_key = os.getenv("SECRET_KEY")
    if not secret_key:
        # A random key keeps sessions safe but logs everyone out on restart.
        secret_key = secrets.token_hex(32)
        if not testing:
            log.warning("SECRET_KEY is not set; using a random key (sessions reset on restart).")
    application.secret_key = secret_key
    application.config["TESTING"] = testing
    application.config["WTF_CSRF_ENABLED"] = not testing
    application.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024

    csrf.init_app(application)
    if not init_db():
        log.warning(
            "Created a NEW empty database at %s. If you expected existing data, "
            "/app/data is probably not a persistent volume mount — see the README.",
            get_database_path(),
        )
    register_all_routes(application)
    application.teardown_appcontext(close_db)

    @application.template_filter("qty")
    def format_quantity(value):
        """Show 500 not 500.0, and at most 2 decimal places."""
        if value is None:
            return ""
        value = round(float(value), 2)
        return str(int(value)) if value == int(value) else f"{value:g}"

    @application.template_filter("amount")
    def format_amount(quantity, unit):
        """'1500', 'g' -> '1.5 kg'; hides the unit for plain counts ('each')."""
        big = {"g": "kg", "mL": "L"}
        if unit in big and quantity and quantity >= 1000:
            quantity, unit = quantity / 1000, big[unit]
        text = format_quantity(quantity)
        return text if unit == "each" else f"{text} {unit.replace('_', ' ')}"

    @application.template_filter("unit_price")
    def format_unit_price(value):
        """Prices per gram/mL are tiny: show up to 4 decimals, but always at least 2."""
        if value is None:
            return "—"
        text = f"{value:,.4f}".rstrip("0")
        if len(text.split(".")[1]) < 2:
            text = f"{value:,.2f}"
        return f"${text}"

    @application.template_filter("money")
    def format_money(value):
        return f"${value:,.2f}" if value is not None else "—"

    @application.context_processor
    def inject_common():
        return {
            "current_user": get_current_user(),
            "is_logged_in": is_logged_in(),
            "categories": CATEGORIES,
            "units": UNITS,
            "meal_types": MEAL_TYPES,
            "days_of_week": DAYS_OF_WEEK,
            "category_lookup": CATEGORY_LOOKUP,
            "day_labels": DAY_LABELS,
            "unit_lookup": UNIT_LOOKUP,
            "meal_type_labels": MEAL_TYPE_LABELS,
            "category_label": category_label,
            "unit_label": lambda u: UNIT_LOOKUP.get(u, u),
            "meal_type_label": lambda mt: MEAL_TYPE_LABELS.get(mt, mt),
            "app_env": os.getenv("APP_ENV", "").strip().lower(),
        }

    @application.route("/health")
    def health():
        return jsonify({"status": "ok"}), 200

    @application.before_request
    def ensure_admin():
        """Create the env-configured admin on first request, or send users to /setup."""
        if request.endpoint in (None, "static", "health", "setup"):
            return None
        if admin_exists():
            return None
        if auto_create_admin():
            return None
        return redirect(url_for("setup"))

    @application.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            if not username or not password:
                flash("Please enter both username and password.", "error")
                return redirect(url_for("login"))
            row = get_db().execute(
                "SELECT password_hash FROM users WHERE username = ?", (username,),
            ).fetchone()
            if row and verify_password(password, row["password_hash"]):
                login_user(username)
                flash("Logged in successfully.", "success")
                return redirect(safe_next_url(request.args.get("next")))
            flash("Invalid username or password.", "error")
            return redirect(url_for("login"))
        return render_template("login.html")

    @application.route("/logout", methods=["GET", "POST"])
    @login_required
    def logout():
        logout_user()
        flash("You have been logged out.", "info")
        return redirect(url_for("login"))

    @application.route("/setup", methods=["GET", "POST"])
    def setup():
        # Setup only creates the first account. Password changes happen in Settings.
        # Create the env-configured admin first so /setup can't claim the app before it exists.
        auto_create_admin()
        if admin_exists():
            flash("An admin account already exists. Log in, then change your password in Settings.", "info")
            return redirect(url_for("login"))
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            confirm = request.form.get("confirm_password", "")
            error = None
            if not username or not password:
                error = "Username and password are required."
            elif password != confirm:
                error = "Passwords do not match."
            elif len(password) < 8:
                error = "Password must be at least 8 characters."
            if error:
                flash(error, "error")
                return redirect(url_for("setup"))
            db = get_db()
            db.execute(
                "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                (username, hash_password(password)),
            )
            db.commit()
            flash("Admin account created. Please log in.", "success")
            return redirect(url_for("login"))
        return render_template("setup.html")

    @application.route("/")
    @login_required
    def dashboard():
        from services.spend_analysis import summarise_recent_spend
        db = get_db()

        def count(sql):
            return db.execute(sql).fetchone()[0]

        plan_start, plan_end = get_plan_dates()
        return render_template(
            "dashboard.html",
            ingredient_count=count("SELECT COUNT(*) FROM ingredients"),
            recipe_count=count("SELECT COUNT(*) FROM recipes"),
            rule_count=count("SELECT COUNT(*) FROM meal_rules WHERE is_active = 1"),
            entry_count=count("SELECT COUNT(*) FROM meal_plan_entries"),
            plan_start=plan_start,
            plan_end=plan_end,
            spend=summarise_recent_spend(db),
        )

    @application.cli.command("init-db")
    def init_db_command():
        """Create or upgrade the database schema."""
        init_db()
        print("Database initialised.")

    @application.cli.command("backup-db")
    def backup_db_command():
        """Copy the database to a timestamped backup file (safe while the app runs)."""
        dest = backup_database()
        print(f"Backup written to {dest}")

    @application.cli.command("db-status")
    def db_status_command():
        """Show where the database lives and how much is in it."""
        stats = database_stats()
        print(f"Path: {stats['path']}")
        if not stats["exists"]:
            print("Status: MISSING (no database file)")
            return
        print(f"Size: {stats['size_bytes']:,} bytes")
        print(f"{'table':24} rows")
        for table, count in stats["counts"].items():
            print(f"{table:24} {count}")

    return application


def safe_next_url(target):
    """Only allow redirects to relative paths on this site (no open redirect)."""
    if target:
        parts = urlparse(target)
        if not parts.scheme and not parts.netloc and target.startswith("/") and not target.startswith("//"):
            return target
    return url_for("dashboard")


def admin_exists():
    return get_db().execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None


def auto_create_admin():
    """Create the admin from ADMIN_USERNAME/ADMIN_PASSWORD if no user exists yet."""
    username = os.getenv("ADMIN_USERNAME")
    password = os.getenv("ADMIN_PASSWORD")
    if not username or not password or admin_exists():
        return False
    db = get_db()
    db.execute(
        "INSERT INTO users (username, password_hash) VALUES (?, ?)",
        (username, hash_password(password)),
    )
    db.commit()
    return True


app = create_app(testing=False)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=os.getenv("FLASK_DEBUG") == "1")
