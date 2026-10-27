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
from services.suggestions import current_pay_cycle, meal_scorer

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
    toggles = [d for d in request.form.getlist("day_toggle") if d in DAYS_OF_WEEK]
    if toggles:
        # The day checkboxes post natively, so the form works without JavaScript.
        day_of_week = ",".join(toggles)
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
        submitted = {
            "id": rule_id,
            "day_of_week": day_of_week,
            "meal_type": meal_type,
            "tag_filter": tag_filter,
            "servings_override": servings_override,
            "is_active": is_active,
            "sort_order": sort_order,
        }
        ctx = _plan_context(editing_rule=submitted)
        ctx["rule_errors"] = errors
        return render_template("meal_plan/view.html", **ctx)

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


@meal_bp.route("/meal-rules/move/<int:rule_id>/<direction>", methods=["POST"])
@login_required
def meal_rule_move(rule_id, direction):
    """Move a rule up or down in evaluation order (renumbers afterwards)."""
    if direction not in ("up", "down"):
        flash("Unknown direction.", "error")
        return _back_to_rules()
    db = get_db()
    ordered = [dict(r) for r in db.execute("SELECT * FROM meal_rules ORDER BY sort_order, id")]
    ids = [r["id"] for r in ordered]
    if rule_id not in ids:
        flash("Rule not found.", "error")
        return _back_to_rules()
    i = ids.index(rule_id)
    j = i - 1 if direction == "up" else i + 1
    if j < 0 or j >= len(ids):
        return _back_to_rules()
    ids.insert(j, ids.pop(i))
    for position, rid in enumerate(ids):
        db.execute("UPDATE meal_rules SET sort_order = ? WHERE id = ?", (position, rid))
    db.commit()
    flash("Rule order updated.", "success")
    return _back_to_rules()


QUICK_START_TEMPLATES = {
    "dinners": {
        "label": "Dinners every night",
        "rules": [("all", "dinner")],
    },
    "weeknights": {
        "label": "Weeknight dinners",
        "rules": [("weekday", "dinner")],
    },
    "three_meals": {
        "label": "Three meals a day",
        "rules": [("all", "breakfast"), ("all", "lunch"), ("all", "dinner")],
    },
}


@meal_bp.route("/meal-plan/rules/quick-start", methods=["POST"])
@login_required
def meal_rule_quick_start():
    """Create a starter rule set so a plan can be generated without learning the rules system."""
    template = QUICK_START_TEMPLATES.get(request.form.get("template", "dinners"))
    if not template:
        flash("Unknown template.", "error")
        return _back_to_rules()

    db = get_db()
    if db.execute("SELECT COUNT(*) FROM meal_rules").fetchone()[0]:
        flash("You already have meal rules. Edit or delete those instead.", "warning")
        return _back_to_rules()
    if not db.execute("SELECT COUNT(*) FROM recipes").fetchone()[0]:
        flash("Add a recipe first — rules need recipes to choose from.", "error")
        return _back_to_rules()

    for position, (days, meal_type) in enumerate(template["rules"], start=1):
        db.execute(
            """INSERT INTO meal_rules
               (day_of_week, meal_type, tag_filter, servings_override, is_active, sort_order)
               VALUES (?, ?, '', NULL, 1, ?)""",
            (days, meal_type, position),
        )
    db.commit()
    flash(
        "Added {} rule(s): {}. Press Generate to fill the plan.".format(
            len(template["rules"]), template["label"]
        ),
        "success",
    )
    return _back_to_plan()


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

def generate_plan(start_date_str, end_date_str, chooser=random.choice, suggest=False):
    """Regenerate auto entries in the range, keeping manual entries. Returns (count, error).

    With ``suggest``, recipes are ranked from meal history (see MealScorer) and
    slots the household usually fills are planned even without a rule.
    """
    manual = load_manual_entries(start_date_str, end_date_str)
    scorer, slots = meal_scorer(local_today()) if suggest else (None, None)
    entries, error = generate_plan_entries(
        start_date_str, end_date_str, load_rules(), load_recipes(), manual, chooser=chooser,
        scorer=scorer, history_slots=slots,
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
                continuation_of, source_rule_id, origin)
               VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?)""",
            (entry["date"], entry["meal_type"], entry["recipe_id"], entry["servings"],
             entry["is_continuation"], continuation_of, entry["source_rule_id"], entry.get("origin")),
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
        day_entries = {}
        for mt in MEAL_TYPES:
            e = by_slot.get((key, mt))
            if e:
                e = dict(e)
                e["servings_label"] = f'Servings for {mt}'
            day_entries[mt] = e
        days.append({
            "date": key,
            "label": f"{DAY_LABELS[day.strftime('%a').lower()]} {day.strftime('%d %b')}",
            "weekday": day.strftime("%a"),
            "day_month": day.strftime("%d %b").lstrip("0"),
            "is_today": day.date() == today,
            "meals": day_entries,
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
    edit_id = request.args.get("edit", type=int)
    editing_rule = None
    if edit_id:
        editing_rule = get_db().execute("SELECT * FROM meal_rules WHERE id = ?", (edit_id,)).fetchone()
    return render_template("meal_plan/view.html", **_plan_context(editing_rule))


def _plan_context(editing_rule=None):
    """Shared template values for the plan page (also used to re-show rule errors)."""
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
    hidden_types = [mt for mt in MEAL_TYPES if mt not in shown_types]
    recipes = db.execute("SELECT id, name FROM recipes ORDER BY name").fetchall()
    rules = [dict(r) for r in db.execute("SELECT * FROM meal_rules ORDER BY sort_order, id")]
    tab = "rules" if editing_rule or request.args.get("tab") == "rules" else "week"
    slot_entries = [e for d in days for e in d["meals"].values() if e]
    _attach_alternatives(slot_entries)
    return {
        "days": days,
        "shown_types": shown_types,
        "hidden_types": hidden_types,
        "recipes": recipes,
        "plan_start": start_str,
        "plan_end": end_str,
        "has_entries": bool(slot_entries),
        "regen": {
            "manual": sum(1 for e in slot_entries if not e["is_auto_generated"]),
            "auto": sum(1 for e in slot_entries if e["is_auto_generated"]),
        },
        "rules": rules,
        "editing_rule": editing_rule,
        "tab": tab,
        "summary": plan_summary(days, shown_types),
        "spare_uses": _spare_uses(start_str, end_str),
        "rule_errors": None,
        "pay_cycle": current_pay_cycle(local_today()),
    }


def _attach_alternatives(slot_entries):
    """Give each planned meal the three best other recipes for its slot, with reasons."""
    if not slot_entries:
        return
    recipes = load_recipes()
    by_id = {r["id"]: r for r in recipes}
    scorer, _ = meal_scorer(local_today())
    for e in slot_entries:
        if e["recipe_id"] in by_id:
            scorer.record(by_id[e["recipe_id"]])
    for e in slot_entries:
        if e.get("is_continuation"):
            continue
        day = parse_date(e["date"])
        own = scorer.score(by_id[e["recipe_id"]], day, e["meal_type"]) if e["recipe_id"] in by_id else (0, [])
        e["reasons"] = [r for r in own[1] if not r.startswith("Shares")][:2]
        e["alternatives"] = scorer.alternatives(
            [r for r in recipes if r["id"] not in scorer.placed], day, e["meal_type"], exclude={e["recipe_id"]})


@meal_bp.route("/meal-plan/generate", methods=["POST"])
@login_required
def meal_plan_generate():
    stored_start, stored_end = get_plan_dates()
    plan_start = request.form.get("plan_start", "").strip() or stored_start
    plan_end = request.form.get("plan_end", "").strip() or stored_end
    if request.form.get("range") == "payday":
        cycle = current_pay_cycle(local_today())
        if not cycle:
            flash("Set your payday in Settings → Budget first.", "error")
            return _back_to_plan()
        plan_start, plan_end = cycle["until_payday_start"], cycle["cycle_end"]

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

    count, error = generate_plan(plan_start, plan_end, suggest=request.form.get("suggest") == "1")
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
    from services.mutations import place_meal
    db = get_db()
    try:
        result = place_meal(
            db,
            request.form.get("date", "").strip(),
            request.form.get("meal_type", "").strip(),
            request.form.get("recipe_id", type=int),
            servings=request.form.get("servings", type=int),
            default_servings=int(get_setting("default_servings", "2")),
        )
    except ValueError as exc:
        if request.headers.get("HX-Request"):
            return f'<div class="text-danger small">{exc}</div>', 400
        flash(str(exc), "error")
        return _back_to_plan()
    if request.headers.get("HX-Request"):
        entry = db.execute("SELECT * FROM meal_plan_entries WHERE id = ?", (result["entry_id"],)).fetchone()
        if entry:
            entry = dict(entry)
            entry["servings_label"] = f'Servings for {entry["meal_type"]}'
            recipes = db.execute("SELECT id, name FROM recipes ORDER BY name").fetchall()
            return render_template("meal_plan/_slot_filled.html", entry=entry, recipes=recipes)
        return "", 204
    if result["replaced_continuation"]:
        flash("This slot is a continuation of an earlier meal. Replacing it.", "info")
    flash("Meal added.", "success")
    return _back_to_plan()


@meal_bp.route("/meal-plan/swap/<int:entry_id>", methods=["POST"])
@login_required
def meal_plan_swap(entry_id):
    """Replace a meal plan entry with a different recipe (marks it manual)."""
    from services.mutations import swap_meal
    try:
        swap_meal(
            get_db(),
            entry_id,
            request.form.get("recipe_id", type=int) or request.form.get("new_recipe_id", type=int),
            servings=request.form.get("servings", type=int),
        )
    except ValueError as exc:
        if request.headers.get("HX-Request"):
            return f'<div class="text-danger small">{exc}</div>', 400
        flash(str(exc), "error")
        return _back_to_plan()
    if request.headers.get("HX-Request"):
        db = get_db()
        entry = db.execute("SELECT * FROM meal_plan_entries WHERE id = ?", (entry_id,)).fetchone()
        if entry:
            entry = dict(entry)
            entry["servings_label"] = f'Servings for {entry["meal_type"]}'
            recipes = db.execute("SELECT id, name FROM recipes ORDER BY name").fetchall()
            return render_template("meal_plan/_slot_filled.html", entry=entry, recipes=recipes)
        return "", 204
    flash("Meal swapped.", "success")
    return _back_to_plan()


@meal_bp.route("/meal-plan/lock/<int:entry_id>", methods=["POST"])
@login_required
def meal_plan_lock(entry_id):
    """Keep a suggested meal when regenerating (or release it again)."""
    from services.mutations import lock_meal
    locked = request.form.get("locked", "1") == "1"
    try:
        lock_meal(get_db(), entry_id, locked)
    except ValueError as exc:
        flash(str(exc), "error")
        return _back_to_plan()
    flash("Meal kept for regeneration." if locked else "Meal handed back to the generator.", "success")
    return _back_to_plan()


@meal_bp.route("/meal-plan/remove/<int:entry_id>", methods=["POST"])
@login_required
def meal_plan_remove(entry_id):
    from services.mutations import remove_meal
    remove_meal(get_db(), entry_id)
    if request.headers.get("HX-Request"):
        return "", 204
    flash("Meal removed.", "success")
    return _back_to_plan()


@meal_bp.route("/meal-plan/edit-servings/<int:entry_id>", methods=["POST"])
@login_required
def meal_plan_edit_servings(entry_id):
    servings = request.form.get("servings", type=int)
    if not servings or servings < 1:
        if request.headers.get("HX-Request"):
            return '<div class="text-danger small">Invalid servings value.</div>', 400
        flash("Invalid servings value.", "error")
        return _back_to_plan()
    db = get_db()
    db.execute(
        "UPDATE meal_plan_entries SET servings = ?, is_auto_generated = 0 WHERE id = ?",
        (servings, entry_id),
    )
    db.commit()
    if request.headers.get("HX-Request"):
        entry = db.execute("SELECT * FROM meal_plan_entries WHERE id = ?", (entry_id,)).fetchone()
        if entry:
            entry = dict(entry)
            entry["servings_label"] = f'Servings for {entry["meal_type"]}'
            recipes = db.execute("SELECT id, name FROM recipes ORDER BY name").fetchall()
            return render_template("meal_plan/_slot_filled.html", entry=entry, recipes=recipes)
        return "", 204
    flash("Servings updated.", "success")
    return _back_to_plan()


def register_meal_routes(app):
    app.register_blueprint(meal_bp)
