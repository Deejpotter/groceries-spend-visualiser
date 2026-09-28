"""Flask application entry point for Grocery Visualiser."""

import os
from flask import Flask, render_template, request, session, redirect, url_for, jsonify, flash
from dotenv import load_dotenv
from database import init_db, get_db, close_db, DATABASE_PATH
from auth import login_user, logout_user, get_current_user, is_logged_in, login_required, hash_password, verify_password
from models import CATEGORIES, UNITS, MEAL_TYPES, DAYS_OF_WEEK
from routes import register_all_routes


def create_app(testing=False):
    """Application factory for Flask (supports testing with temp DB)."""
    load_dotenv()

    application = Flask(__name__)
    application.secret_key = os.getenv("SECRET_KEY", "dev-secret-change-in-production")
    application.config["TESTING"] = testing

    register_all_routes(application)

    @application.teardown_appcontext
    def teardown_db(exception):
        close_db(exception)

    @application.context_processor
    def inject_common():
        from models import CATEGORY_LOOKUP, DAY_LABELS, UNIT_LOOKUP, MEAL_TYPE_LABELS
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
            "category_label": lambda cat: CATEGORY_LOOKUP.get(cat, cat),
            "unit_label": lambda u: UNIT_LOOKUP.get(u, u),
            "day_label": lambda d: DAY_LABELS.get(
                d.strftime("%a").lower() if hasattr(d, "strftime") else str(d), str(d)
            ),
            "meal_type_label": lambda mt: MEAL_TYPE_LABELS.get(mt, mt),
        }

    @application.route("/health")
    def health():
        return jsonify({"status": "ok"}), 200

    @application.before_request
    def before_request():
        """Skip DB init in testing mode."""
        if application.config.get("TESTING"):
            return None
        if request.path.startswith("/static") or request.path == "/health":
            return None
        if admin_exists():
            return None
        username = os.getenv("ADMIN_USERNAME")
        password = os.getenv("ADMIN_PASSWORD")
        if username and password:
            auto_create_admin()

    @application.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            if not username or not password:
                flash("Please enter both username and password.", "error")
                return redirect(url_for("login"))
            db = get_db()
            row = db.execute(
                "SELECT id, username, password_hash FROM users WHERE username = ?",
                (username,),
            ).fetchone()
            if row and verify_password(password, row["password_hash"]):
                login_user(username)
                flash("Logged in successfully.", "success")
                next_url = request.args.get("next") or url_for("dashboard")
                return redirect(next_url)
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
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            confirm = request.form.get("confirm_password", "")
            if not username or not password:
                flash("Username and password are required.", "error")
                return redirect(url_for("setup"))
            if password != confirm:
                flash("Passwords do not match.", "error")
                return redirect(url_for("setup"))
            if len(password) < 8:
                flash("Password must be at least 8 characters.", "error")
                return redirect(url_for("setup"))
            db = get_db()
            existing = db.execute(
                "SELECT id FROM users WHERE username = ?", (username,),
            ).fetchone()
            if existing:
                db.execute(
                    "UPDATE users SET password_hash = ? WHERE username = ?",
                    (hash_password(password), username),
                )
                db.commit()
                flash("Password updated. Please log in.", "success")
                return redirect(url_for("login"))
            else:
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
        db = get_db()
        ingredient_count = db.execute("SELECT COUNT(*) as count FROM ingredients").fetchone()["count"]
        recipe_count = db.execute("SELECT COUNT(*) as count FROM recipes").fetchone()["count"]
        rule_count = db.execute("SELECT COUNT(*) as count FROM meal_rules WHERE is_active = 1").fetchone()["count"]
        entry_count = db.execute("SELECT COUNT(*) as count FROM meal_plan_entries").fetchone()["count"]
        plan_start = db.execute("SELECT value FROM settings WHERE key = 'plan_start_date'").fetchone()
        plan_end = db.execute("SELECT value FROM settings WHERE key = 'plan_end_date'").fetchone()
        return render_template(
            "dashboard.html",
            ingredient_count=ingredient_count,
            recipe_count=recipe_count,
            rule_count=rule_count,
            entry_count=entry_count,
            plan_start=plan_start["value"] if plan_start else "",
            plan_end=plan_end["value"] if plan_end else "",
        )

    return application


def admin_exists():
    try:
        db = get_db()
        row = db.execute("SELECT id FROM users LIMIT 1").fetchone()
        return row is not None
    except Exception:
        return False


def auto_create_admin():
    username = os.getenv("ADMIN_USERNAME")
    password = os.getenv("ADMIN_PASSWORD")
    if not username or not password:
        return False
    if admin_exists():
        return False
    try:
        db = get_db()
        db.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            (username, hash_password(password)),
        )
        db.commit()
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Create the app instance for production (module-level)
# ---------------------------------------------------------------------------

app = create_app(testing=False)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)
