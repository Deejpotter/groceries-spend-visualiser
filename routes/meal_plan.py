"""Routes for meal rules and meal plan generation."""

import random

from flask import Blueprint, render_template, request, redirect, url_for, flash

from auth import login_required
from database import get_db, get_plan_dates, get_setting, set_setting
from models import MAX_PLAN_DAYS, today as local_today, DAYS_OF_WEEK, DAY_LABELS, MEAL_TYPES, date_range, parse_date, validate_date_format
from services.plan_generator import generate_plan_entries, plan_summary
from services.repository import load_linked_products, load_list_rows, load_shopping_inputs
from services.shopping_list_generator import plan_list_lines, recipes_using_spares, spares
from services.repository import load_rules, load_recipes, load_manual_entries

meal_bp = Blueprint("meal_plan", __name__)

RULE_DAYS = DAYS_OF_WEEK + ["weekday", "weekend", "all"]


def _back_to_plan():
    return redirect(url_for("meal_plan.meal_plan_view"))


def _back_to_rules(edit=None):
    return redirect(url_for("meal_plan.meal_plan_view", tab="rules", edit=edit))


# ---------------------------------------------------------------------------
# Meal Rules
# ---------------------------------------------------------------------------

@meal_bp.route("/meal-rules")
@login_required
def meal_rule_list():
    return _back_to_rules()


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
            return _back_to_rules()

    if request.method == "GET":
        if rule_id:
            return _back_to_rules(edit=rule_id)
        return _back_to_rules()

    day_of_week = request.form.get("day_of_week", "").strip()
    meal_type = request.form.get("meal_type", "").strip()
    tag_filter = request.form.get("tag_filter", "").strip()
    servings_override = request.form.get("servings_override", type=int) or None
    is_active = 1 if request.form.get("is_active") else 0
    sort_order = request.form.get("sort_order", type=int)

    errors = []
    day_parts = [d.strip() for d in day_of_week.split(",") if d.strip()]
    if not day_parts or not all(d in RULE_DAYS for d in day_parts):
        errors.append("Choose which day(s) the rule applies to.")
    else:
        day_of_week = ",".join(day_parts)
    if meal_type not in MEAL_TYPES:
        errors.append("Choose a meal type.")
    if servings_override is not None and servings_override < 1:
        errors.append("Servings override must be at least 1.")
    if errors:
        for err in errors:
            flash(err, "error")
        return _back_to_rules(edit=rule_id)

    if sort_order is None:
        if rule:
            sort_order = rule["sort_order"]
        else:
            sort_order = (db.execute("SELECT MAX(sort_order) FROM meal_rules").fetchone()[0] or 0) + 1

    if rule_id:
        db.execute(
            """UPDATE meal_rules SET day_of_week=?, meal_type=?, tag_filter=?,
               servings_override=?, is_active=?, sort_order=? WHERE id=?""",
            (day_of_week, meal_type, tag_filter, servings_override, is_active, sort_order, rule_id),
        )
        flash("Rule updated.", "success")
    else:
        db.execute(
            """INSERT INTO meal_rules (day_of_week, meal_type, tag_filter, servings_override, is_active, sort_order)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (day_of_week, meal_type, tag_filter, servings_override, is_active, sort_order),
        )
        flash("Rule created.", "success")
    db.commit()
    return _back_to_rules()


@meal_bp.route("/meal-rules/delete/<int:rule_id>", methods=["POST"])
@login_required
def meal_rule_delete(rule_id):
    db = get_db()
    db.execute("DELETE FROM meal_rules WHERE id = ?", (rule_id,))
    db.commit()
    flash("Rule deleted.", "success")
    return _back_to_rules()


# ---------------------------------------------------------------------------
# Plan generation
# ---------------------------------------------------------------------------

def generate_plan(start_date_str, end_date_str, chooser=random.choice):
    """Regenerate auto entries in the range, keeping manual entries. Returns (count, error)."""
    manual = load_manual_entries(start_date_str, end_date_str)
    entries, error = generate_plan_entries(
        start_date_str, end_date_str, load_rules(), load_recipes(), manual, chooser=chooser,
    )
    if error:
        return None, error

    db = get_db()
    db.execute(
        "DELETE FROM meal_plan_entries WHERE date BETWEEN ? AND ? AND is_auto_generated = 1",
        (start_date_str, end_date_str),
    )
    entry_ids = {}
    for entry in entries:
        continuation_of = entry_ids.get(entry.get("continuation_of"))
        cursor = db.execute(
            """INSERT INTO meal_plan_entries
               (date, meal_type, recipe_id, servings, is_auto_generated, is_continuation,
                continuation_of, source_rule_id)
               VALUES (?, ?, ?, ?, 1, ?, ?, ?)""",
            (entry["date"], entry["meal_type"], entry["recipe_id"], entry["servings"],
             entry["is_continuation"], continuation_of, entry["source_rule_id"]),
        )
        if not entry["is_continuation"]:
            entry_ids[(entry["date"], entry["meal_type"])] = cursor.lastrowid
    db.commit()
    return len(entries), None


def _build_days(start, end, entries):
    """One row per date in the plan with a slot for each meal type."""
    by_slot = {(e["date"], e["meal_type"]): e for e in entries}
    days = []
    today = local_today()
    for day in date_range(start, end):
        key = day.strftime("%Y-%m-%d")
        days.append({
            "date": key,
            "label": f"{DAY_LABELS[day.strftime('%a').lower()]} {day.strftime('%d %b')}",
            "weekday": day.strftime("%a"),
            "day_month": day.strftime("%d %b").lstrip("0"),
            "is_today": day.date() == today,
            "meals": {mt: by_slot.get((key, mt)) for mt in MEAL_TYPES},
        })
    return days


def _spare_uses(start, end):
    """{recipe_id: [spare ingredient names]} for recipes that would use up the shopping list's spares."""
    spare_items = spares(plan_list_lines(load_list_rows(), load_linked_products()))
    if not spare_items:
        return {}
    _, recipes, recipe_ingredients, ingredients = load_shopping_inputs(start, end)
    return {r["recipe_id"]: r["uses"] for r in recipes_using_spares(spare_items, recipes, recipe_ingredients, ingredients)}


@meal_bp.route("/meal-plan")
@login_required
def meal_plan_view():
    db = get_db()
    start_str, end_str = get_plan_dates()
    days = []
    if start_str and end_str:
        entries = [dict(r) for r in db.execute(
            """SELECT mpe.*, r.name AS recipe_name, r.covers_days
               FROM meal_plan_entries mpe JOIN recipes r ON mpe.recipe_id = r.id
               WHERE mpe.date BETWEEN ? AND ? ORDER BY mpe.date""",
            (start_str, end_str),
        )]
        days = _build_days(start_str, end_str, entries)
    used_types = {r["meal_type"] for r in db.execute("SELECT meal_type FROM meal_rules WHERE is_active = 1")}
    used_types |= {mt for d in days for mt, e in d["meals"].items() if e}
    shown_types = [mt for mt in MEAL_TYPES if mt in used_types] or ["dinner"]
    recipes = db.execute("SELECT id, name FROM recipes ORDER BY name").fetchall()
    rules = [dict(r) for r in db.execute("SELECT * FROM meal_rules ORDER BY is_active DESC, sort_order, id")]
    editing_rule = None
    edit_id = request.args.get("edit", type=int)
    if edit_id:
        editing_rule = db.execute("SELECT * FROM meal_rules WHERE id = ?", (edit_id,)).fetchone()
    tab = "rules" if editing_rule or request.args.get("tab") == "rules" else "week"
    return render_template(
        "meal_plan/view.html",
        days=days,
        shown_types=shown_types,
        recipes=recipes,
        plan_start=start_str,
        plan_end=end_str,
        has_entries=any(e for d in days for e in d["meals"].values()),
        rules=rules,
        editing_rule=editing_rule,
        tab=tab,
        summary=plan_summary(days, shown_types),
        spare_uses=_spare_uses(start_str, end_str),
    )


@meal_bp.route("/meal-plan/generate", methods=["POST"])
@login_required
def meal_plan_generate():
    stored_start, stored_end = get_plan_dates()
    plan_start = request.form.get("plan_start", "").strip() or stored_start
    plan_end = request.form.get("plan_end", "").strip() or stored_end

    if not (validate_date_format(plan_start) and validate_date_format(plan_end)):
        flash("Please set valid plan start and end dates first.", "error")
        return _back_to_plan()
    start, end = parse_date(plan_start), parse_date(plan_end)
    if end < start:
        flash("The end date must be on or after the start date.", "error")
        return _back_to_plan()
    if (end - start).days > MAX_PLAN_DAYS:
        flash("Plans are limited to about three months.", "error")
        return _back_to_plan()

    set_setting("plan_start_date", plan_start)
    set_setting("plan_end_date", plan_end)
    get_db().commit()

    count, error = generate_plan(plan_start, plan_end)
    if error:
        flash(error, "error")
    elif count == 0:
        flash("No meals were generated. Check that you have active meal rules and matching recipes.", "warning")
    else:
        flash(f"Meal plan generated with {count} entries.", "success")
    return _back_to_plan()


@meal_bp.route("/meal-plan/add", methods=["POST"])
@login_required
def meal_plan_add():
    """Manually place a recipe in an empty slot."""
    date = request.form.get("date", "").strip()
    meal_type = request.form.get("meal_type", "").strip()
    recipe_id = request.form.get("recipe_id", type=int)
    db = get_db()
    recipe = db.execute("SELECT servings FROM recipes WHERE id = ?", (recipe_id,)).fetchone() if recipe_id else None
    if not validate_date_format(date) or meal_type not in MEAL_TYPES or not recipe:
        flash("Choose a recipe to add.", "error")
        return _back_to_plan()
    servings = request.form.get("servings", type=int)
    if servings is None:
        servings = recipe["servings"] or int(get_setting("default_servings", "2"))
    if servings < 1:
        flash("Servings must be at least 1.", "error")
        return _back_to_plan()
    existing = db.execute(
        "SELECT id, is_continuation FROM meal_plan_entries WHERE date = ? AND meal_type = ?",
        (date, meal_type),
    ).fetchone()
    if existing and not existing["is_continuation"]:
        db.execute(
            "UPDATE meal_plan_entries SET is_continuation = 0, continuation_of = NULL "
            "WHERE continuation_of = ?",
            (existing["id"],),
        )
    elif existing:
        flash("This slot is a continuation of an earlier meal. Replacing it.", "info")
    db.execute("DELETE FROM meal_plan_entries WHERE date = ? AND meal_type = ?", (date, meal_type))
    db.execute(
        "INSERT INTO meal_plan_entries (date, meal_type, recipe_id, servings, is_auto_generated) VALUES (?, ?, ?, ?, 0)",
        (date, meal_type, recipe_id, servings),
    )
    db.commit()
    flash("Meal added.", "success")
    return _back_to_plan()


@meal_bp.route("/meal-plan/swap/<int:entry_id>", methods=["POST"])
@login_required
def meal_plan_swap(entry_id):
    """Replace a meal plan entry with a different recipe (marks it manual)."""
    db = get_db()
    entry = db.execute("SELECT * FROM meal_plan_entries WHERE id = ?", (entry_id,)).fetchone()
    if not entry:
        flash("Entry not found.", "error")
        return _back_to_plan()

    new_recipe_id = request.form.get("recipe_id", type=int) or request.form.get("new_recipe_id", type=int)
    if not new_recipe_id or not db.execute("SELECT 1 FROM recipes WHERE id = ?", (new_recipe_id,)).fetchone():
        flash("No recipe selected.", "error")
        return _back_to_plan()

    servings = request.form.get("servings", type=int)
    if servings is None:
        servings = entry["servings"]
    if servings < 1:
        flash("Servings must be at least 1.", "error")
        return _back_to_plan()
    if not entry["is_continuation"]:
        db.execute(
            "UPDATE meal_plan_entries SET is_continuation = 0, continuation_of = NULL "
            "WHERE continuation_of = ?",
            (entry_id,),
        )
    db.execute(
        "UPDATE meal_plan_entries SET recipe_id = ?, servings = ?, is_auto_generated = 0, "
        "is_continuation = 0, continuation_of = NULL WHERE id = ?",
        (new_recipe_id, servings, entry_id),
    )
    db.commit()
    flash("Meal swapped.", "success")
    return _back_to_plan()


@meal_bp.route("/meal-plan/remove/<int:entry_id>", methods=["POST"])
@login_required
def meal_plan_remove(entry_id):
    db = get_db()
    entry = db.execute("SELECT * FROM meal_plan_entries WHERE id = ?", (entry_id,)).fetchone()
    if entry and not entry["is_continuation"]:
        db.execute(
            "UPDATE meal_plan_entries SET is_continuation = 0, continuation_of = NULL "
            "WHERE continuation_of = ?",
            (entry_id,),
        )
    db.execute("DELETE FROM meal_plan_entries WHERE id = ?", (entry_id,))
    db.commit()
    flash("Meal removed.", "success")
    return _back_to_plan()


@meal_bp.route("/meal-plan/edit-servings/<int:entry_id>", methods=["POST"])
@login_required
def meal_plan_edit_servings(entry_id):
    servings = request.form.get("servings", type=int)
    if not servings or servings < 1:
        flash("Invalid servings value.", "error")
        return _back_to_plan()
    db = get_db()
    db.execute(
        "UPDATE meal_plan_entries SET servings = ?, is_auto_generated = 0 WHERE id = ?",
        (servings, entry_id),
    )
    db.commit()
    flash("Servings updated.", "success")
    return _back_to_plan()


def register_meal_routes(app):
    app.register_blueprint(meal_bp)
