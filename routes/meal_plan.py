"""Routes for meal rules and meal plan generation."""

from datetime import datetime, timedelta
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from database import get_db
from auth import login_required
from models import DAYS_OF_WEEK, DAY_LABELS, MEAL_TYPES, MEAL_TYPE_LABELS, parse_date, date_range, get_day_key, is_weekday

meal_bp = Blueprint("meal_plan", __name__)


# ---------------------------------------------------------------------------
# Meal Rules
# ---------------------------------------------------------------------------

@meal_bp.route("/meal-rules")
@login_required
def meal_rule_list():
    db = get_db()
    rules = db.execute("SELECT * FROM meal_rules WHERE is_active = 1 ORDER BY sort_order").fetchall()
    return render_template("meal_rules/list.html", rules=rules)


@meal_bp.route("/meal-rules/add", methods=["GET", "POST"])
@meal_bp.route("/meal-rules/edit/<int:rule_id>", methods=["GET", "POST"])
@login_required
def meal_rule_form(rule_id=None):
    db = get_db()
    rule = None
    if rule_id:
        rule = db.execute("SELECT * FROM meal_rules WHERE id = ?", (rule_id,)).fetchone()
        if not rule:
            flash("Rule not found.", "error")
            return redirect(url_for("meal_plan.meal_rule_list"))

    if request.method == "POST":
        day_of_week = request.form.get("day_of_week", "all").strip()
        meal_type = request.form.get("meal_type", "dinner").strip()
        tag_filter = request.form.get("tag_filter", "").strip()
        servings_override = request.form.get("servings_override", type=int, default=0)
        is_active = 1 if request.form.get("is_active") else 0

        errors = []
        if not day_of_week:
            errors.append("Day of week is required.")
        if not meal_type:
            errors.append("Meal type is required.")

        if errors:
            for err in errors:
                flash(err, "error")
            return render_template("meal_rules/form.html", rule=rule)

        if rule_id:
            db.execute(
                """UPDATE meal_rules SET day_of_week=?, meal_type=?, tag_filter=?,
                   servings_override=?, is_active=?, sort_order=?
                   WHERE id=?""",
                (day_of_week, meal_type, tag_filter, servings_override, is_active, rule["sort_order"], rule_id)
            )
            flash("Rule updated.", "success")
        else:
            max_order = db.execute("SELECT MAX(sort_order) as max FROM meal_rules").fetchone()["max"] or 0
            submitted_order = request.form.get("sort_order", "").strip()
            sort_order = int(submitted_order) if submitted_order else max_order + 1
            db.execute(
                """INSERT INTO meal_rules (day_of_week, meal_type, tag_filter, servings_override, is_active, sort_order)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (day_of_week, meal_type, tag_filter, servings_override, is_active, sort_order)
            )
            flash("Rule created.", "success")
        return redirect(url_for("meal_plan.meal_rule_list"))

    return render_template("meal_rules/form.html", rule=rule)


@meal_bp.route("/meal-rules/delete/<int:rule_id>", methods=["POST"])
@login_required
def meal_rule_delete(rule_id):
    db = get_db()
    db.execute("DELETE FROM meal_rules WHERE id = ?", (rule_id,))
    db.commit()
    flash("Rule deleted.", "success")
    return redirect(url_for("meal_plan.meal_rule_list"))


# ---------------------------------------------------------------------------
# Plan Generation
# ---------------------------------------------------------------------------

def generate_plan(start_date_str, end_date_str):
    """Generate meal plan entries for the given date range."""
    start = parse_date(start_date_str)
    end = parse_date(end_date_str)
    if not start or not end or end < start:
        return None, "Invalid date range."

    db = get_db()

    # Clear existing AUTO-GENERATED entries in range (preserve manual overrides)
    db.execute(
        "DELETE FROM meal_plan_entries WHERE date >= ? AND date <= ? AND is_auto_generated = 1",
        (start_date_str, end_date_str)
    )

    entries_created = 0

    for date in date_range(start_date_str, end_date_str):
        day_key = get_day_key(date)
        for meal_type in MEAL_TYPES:
            # Find matching rules in order
            rules = db.execute(
                """SELECT mr.*, r.id as recipe_id, r.name as recipe_name
                   FROM meal_rules mr
                   LEFT JOIN recipes r ON 1=1
                   WHERE mr.is_active = 1 AND mr.meal_type = ?
                   AND (mr.day_of_week = ? OR mr.day_of_week = 'weekday' AND ?
                        OR mr.day_of_week = 'weekend' AND ? OR mr.day_of_week = 'all')
                   ORDER BY mr.sort_order""",
                (meal_type, day_key, is_weekday(date), not is_weekday(date))
            ).fetchall()

            for rule in rules:
                # Check if entry already exists (manual override protection)
                existing = db.execute(
                    "SELECT id FROM meal_plan_entries WHERE date = ? AND meal_type = ?",
                    (date.strftime("%Y-%m-%d"), meal_type)
                ).fetchone()
                if existing:
                    continue

                # Pick a recipe
                if rule["tag_filter"]:
                    tag_clause = " AND tags LIKE ?"
                    tag_params = [f"%{rule['tag_filter']}%"]
                    recipe_query = db.execute(
                        f"SELECT id, name, servings, is_two_night FROM recipes WHERE 1=1{tag_clause} ORDER BY RANDOM() LIMIT 1",
                        tag_params
                    ).fetchone()
                else:
                    recipe_query = db.execute(
                        "SELECT id, name, servings, is_two_night FROM recipes ORDER BY RANDOM() LIMIT 1"
                    ).fetchone()

                if not recipe_query:
                    continue

                recipe_id = recipe_query["id"]
                recipe_servings = recipe_query["servings"] or 1
                servings = rule["servings_override"] if rule["servings_override"] else recipe_servings

                # Create entry
                db.execute(
                    """INSERT INTO meal_plan_entries (date, meal_type, recipe_id, servings, is_auto_generated, source_rule_id)
                       VALUES (?, ?, ?, ?, 1, ?)""",
                    (date.strftime("%Y-%m-%d"), meal_type, recipe_id, servings, rule["id"])
                )
                entries_created += 1

                # Handle two-night recipes
                if recipe_query["is_two_night"]:
                    next_date = date + timedelta(days=1)
                    if next_date <= end:
                        next_existing = db.execute(
                            "SELECT id FROM meal_plan_entries WHERE date = ? AND meal_type = ?",
                            (next_date.strftime("%Y-%m-%d"), meal_type)
                        ).fetchone()
                        if not next_existing:
                            db.execute(
                                """INSERT INTO meal_plan_entries (date, meal_type, recipe_id, servings, is_auto_generated, source_rule_id)
                                   VALUES (?, ?, ?, ?, 1, ?)""",
                                (next_date.strftime("%Y-%m-%d"), meal_type, recipe_id, servings, rule["id"])
                            )
                            entries_created += 1

    db.commit()
    return entries_created, None


@meal_bp.route("/meal-plan")
@login_required
def meal_plan_view():
    db = get_db()

    # Get plan dates from settings
    plan_start = db.execute("SELECT value FROM settings WHERE key = 'plan_start_date'").fetchone()
    plan_end = db.execute("SELECT value FROM settings WHERE key = 'plan_end_date'").fetchone()

    start_str = plan_start["value"] if plan_start else ""
    end_str = plan_end["value"] if plan_end else ""

    entries = []
    error = None

    if start_str and end_str:
        entries_raw = db.execute(
            """SELECT mpe.*, r.name as recipe_name, r.tags, r.is_two_night,
                      r.servings as recipe_servings
               FROM meal_plan_entries mpe
               JOIN recipes r ON mpe.recipe_id = r.id
               WHERE mpe.date >= ? AND mpe.date <= ?
               ORDER BY mpe.date, mpe.meal_type""",
            (start_str, end_str)
        ).fetchall()

        # Convert sqlite3.Row to dicts and date strings to datetime.date
        from datetime import datetime
        entries = []
        for row in entries_raw:
            entry = dict(row)
            entry["date"] = datetime.strptime(entry["date"], "%Y-%m-%d").date()
            entries.append(entry)

    return render_template(
        "meal_plan/view.html",
        entries=entries,
        plan_start=start_str,
        plan_end=end_str,
        error=error,
        meal_types=MEAL_TYPES,
        meal_type_labels=MEAL_TYPE_LABELS,
    )


@meal_bp.route("/meal-plan/generate", methods=["POST"])
@login_required
def meal_plan_generate():
    plan_start = request.form.get("plan_start", "").strip()
    plan_end = request.form.get("plan_end", "").strip()

    if not plan_start or not plan_end:
        flash("Please set plan start and end dates in Settings first.", "error")
        return redirect(url_for("meal_plan.meal_plan_view"))

    # Save dates to settings
    db = get_db()
    db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('plan_start_date', ?)", (plan_start,))
    db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('plan_end_date', ?)", (plan_end,))
    db.commit()

    count, error = generate_plan(plan_start, plan_end)

    if error:
        flash(error, "error")
    else:
        flash(f"Meal plan generated with {count} entries.", "success")

    return redirect(url_for("meal_plan.meal_plan_view"))


@meal_bp.route("/meal-plan/swap/<int:entry_id>", methods=["POST"])
@login_required
def meal_plan_swap(entry_id):
    """Replace a meal plan entry with a different recipe."""
    db = get_db()
    entry = db.execute(
        "SELECT * FROM meal_plan_entries WHERE id = ?", (entry_id,)
    ).fetchone()

    if not entry:
        flash("Entry not found.", "error")
        return redirect(url_for("meal_plan.meal_plan_view"))

    new_recipe_id = request.form.get("recipe_id", type=int) or request.form.get("new_recipe_id", type=int)
    if not new_recipe_id:
        flash("No recipe selected.", "error")
        return redirect(url_for("meal_plan.meal_plan_view"))

    servings = request.form.get("servings", type=int, default=entry["servings"])

    db.execute(
        "UPDATE meal_plan_entries SET recipe_id = ?, servings = ?, is_auto_generated = 0 WHERE id = ?",
        (new_recipe_id, servings, entry_id)
    )
    db.commit()
    flash("Meal swapped.", "success")
    return redirect(url_for("meal_plan.meal_plan_view"))


@meal_bp.route("/meal-plan/remove/<int:entry_id>", methods=["POST"])
@login_required
def meal_plan_remove(entry_id):
    """Remove a meal plan entry."""
    db = get_db()
    db.execute("DELETE FROM meal_plan_entries WHERE id = ?", (entry_id,))
    db.commit()
    flash("Meal removed.", "success")
    return redirect(url_for("meal_plan.meal_plan_view"))


@meal_bp.route("/meal-plan/edit-servings/<int:entry_id>", methods=["POST"])
@login_required
def meal_plan_edit_servings(entry_id):
    """Update servings for a meal plan entry."""
    servings = request.form.get("servings", type=int)
    if not servings or servings < 1:
        flash("Invalid servings value.", "error")
        return redirect(url_for("meal_plan.meal_plan_view"))

    db = get_db()
    db.execute(
        "UPDATE meal_plan_entries SET servings = ?, is_auto_generated = 0 WHERE id = ?",
        (servings, entry_id)
    )
    db.commit()
    flash("Servings updated.", "success")
    return redirect(url_for("meal_plan.meal_plan_view"))


def register_meal_routes(app):
    app.register_blueprint(meal_bp)
