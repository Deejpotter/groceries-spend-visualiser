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
    CATEGORY_LOOKUP, DAY_LABELS, UNIT_LOOKUP, MEAL_TYPE_LABELS,
    category_label, is_http_url, parse_date, label_of,
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

    @application.url_defaults
    def static_cache_bust(endpoint, values):
        """Append ?v=<mtime> to static URLs so deploys aren't masked by cached CSS/JS."""
        if endpoint == "static" and "filename" in values:
            path = os.path.join(application.static_folder, values["filename"])
            if os.path.isfile(path):
                values["v"] = int(os.stat(path).st_mtime)

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

    @application.template_filter("http_url")
    def filter_http_url(value):
        """Only http(s) URLs are rendered as links."""
        return value if is_http_url(value) else None

    @application.template_filter("label")
    def filter_label(value):
        """Short display name for an ingredient-like dict."""
        return label_of(value)

    @application.template_filter("nice_date")
    def format_nice_date(value):
        """'2026-09-30' -> 'Wed 30 Sep 2026'; unparseable values pass through."""
        parsed = parse_date(value) if value else None
        return parsed.strftime("%a %d %b %Y") if parsed else (value or "")

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
        from datetime import date, timedelta

        from models import today as local_today
        from services import dashboard as dash
        from services.repository import load_linked_products, load_list_rows, load_shopping_inputs
        from services.shopping_list_generator import plan_list_lines, recipes_using_spares, spares
        from services.spend_analysis import analyze_purchases, load_purchases

        db = get_db()

        def count(sql):
            return db.execute(sql).fetchone()[0]

        plan_start, plan_end = get_plan_dates()
        today = local_today()

        plan_entries = []
        if plan_start and plan_end:
            plan_entries = [dict(r) for r in db.execute(
                "SELECT date, is_continuation FROM meal_plan_entries WHERE date BETWEEN ? AND ?",
                (plan_start, plan_end))]
        coverage = dash.plan_coverage(plan_entries, plan_start, plan_end) if plan_start and plan_end else None

        shopping = dash.shopping_summary([dict(r) for r in db.execute(
            "SELECT estimated_cost, checked, is_manual FROM shopping_list_items")])
        to_buy = [dict(r) for r in db.execute(
            "SELECT ingredient_name, quantity, unit FROM shopping_list_items"
            " WHERE checked = 0 ORDER BY category, ingredient_name LIMIT 8")]

        pantry = dash.pantry_alerts([dict(r) for r in db.execute(
            "SELECT p.id, p.quantity, p.unit, p.expiry_date, i.name, i.display_name,"
            " i.minimum_stock, i.category"
            " FROM pantry_items p JOIN ingredients i ON p.ingredient_id = i.id")])

        latest = db.execute("SELECT MAX(order_date) FROM purchases").fetchone()[0]
        spend = None
        spend_delta = None
        monthly = []
        spend_spark = ""
        if latest:
            latest_day = date.fromisoformat(str(latest)[:10])
            recent_start = (latest_day - timedelta(days=90)).isoformat()
            prior_start = (latest_day - timedelta(days=180)).isoformat()
            spend = dash.spend_window(load_purchases(db, start=recent_start))
            prior = dash.spend_window(load_purchases(db, start=prior_start, end=recent_start))
            spend_delta = dash.trend(spend["avg_per_shop"], prior["avg_per_shop"])
            stats = analyze_purchases(load_purchases(db), top_n=5)
            monthly = stats["monthly_spend"] if stats else []
            spend_spark = dash.sparkline([v for _, v in monthly])

        week_end = (today + timedelta(days=6)).isoformat()
        upcoming = [dict(r) for r in db.execute(
            "SELECT e.date, e.meal_type, e.is_continuation, r.name AS recipe_name"
            " FROM meal_plan_entries e LEFT JOIN recipes r ON r.id = e.recipe_id"
            " WHERE e.date BETWEEN ? AND ? ORDER BY e.date, e.meal_type",
            (today.isoformat(), week_end))]

        spare_items = spares(plan_list_lines(load_list_rows(), load_linked_products()))
        _, recipes, recipe_ingredients, ingredients = load_shopping_inputs(
            plan_start or today.isoformat(), plan_end or week_end)
        spare_recipes = recipes_using_spares(spare_items, recipes, recipe_ingredients, ingredients)

        return render_template(
            "dashboard.html",
            ingredient_count=count("SELECT COUNT(*) FROM ingredients"),
            recipe_count=count("SELECT COUNT(*) FROM recipes"),
            rule_count=count("SELECT COUNT(*) FROM meal_rules WHERE is_active = 1"),
            entry_count=count("SELECT COUNT(*) FROM meal_plan_entries"),
            plan_start=plan_start,
            plan_end=plan_end,
            today=today.isoformat(),
            coverage=coverage,
            shopping=shopping,
            to_buy=to_buy,
            pantry=pantry,
            spend=spend,
            spend_delta=spend_delta,
            spend_spark=spend_spark,
            monthly=monthly,
            upcoming=upcoming,
            spare_items=spare_items,
            spare_recipes=spare_recipes,
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
