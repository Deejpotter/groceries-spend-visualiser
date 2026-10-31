"""Routes for shopping list generation and management."""

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify

from auth import login_required
from database import get_db, get_plan_dates, get_setting, set_setting
from models import CATEGORY_LOOKUP, UNIT_LOOKUP, validate_date_format
from services.repository import (
    load_linked_products, load_list_rows, load_pantry_stock, load_shopping_inputs, shopping_preferences,
)
from services.shopping_list_generator import (
    generate_shopping_list, list_totals, plan_list_lines, recipes_using_spares, spares,
)
from services.spend_analysis import average_spend_per_shop
from services.cycle_list import LIST_MODES, MODE_LOOKUP, MODE_SOURCES, SOURCE_LOOKUP, totals_by_source
from models import today as local_today

shopping_bp = Blueprint("shopping_list", __name__)


def _back():
    return redirect(url_for("shopping_list.shopping_list_view"))


def build_list(start, end):
    """Run the shopping-list service over the plan with the user's preferences."""
    unit_pref, subtract = shopping_preferences()
    entries, recipes, recipe_ingredients, ingredients = load_shopping_inputs(start, end)
    return generate_shopping_list(
        entries, recipes, recipe_ingredients, ingredients,
        unit_preference=unit_pref,
        subtract_pantry=subtract,
        pantry_items=load_pantry_stock() if subtract else None,
    )


@shopping_bp.route("/shopping-list")
@login_required
def shopping_list_view():
    db = get_db()
    start_str, end_str = get_plan_dates()
    from services.suggestions import current_pay_cycle
    all_rows = plan_list_lines(load_list_rows(), load_linked_products())
    for row in all_rows:
        row["source"] = row.get("source") or ("manual" if row["is_manual"] else "meal")
    show = request.args.get("show", "all")
    if show in MODE_SOURCES:
        rows = [r for r in all_rows if r["source"] in MODE_SOURCES[show] or r["source"] == "manual"]
    else:
        show, rows = "all", all_rows
    pay_cycle = current_pay_cycle(local_today())
    grouped = {}
    for row in rows:
        grouped.setdefault(row["category"] or "other", []).append(row)
    ordered = {cat: grouped[cat] for cat in sorted(grouped, key=lambda c: CATEGORY_LOOKUP.get(c, c))}

    totals = list_totals(rows)
    spare_items = spares(rows)
    unit_pref, subtract = shopping_preferences()
    _, recipes, recipe_ingredients, ingredients = load_shopping_inputs(start_str, end_str)
    has_plan = bool(start_str and end_str and db.execute(
        "SELECT 1 FROM meal_plan_entries WHERE date BETWEEN ? AND ? LIMIT 1", (start_str, end_str)
    ).fetchone())

    return render_template(
        "shopping_lists/view.html",
        grouped=ordered,
        item_count=len(rows),
        checked_count=sum(1 for r in rows if r["checked"]),
        has_generated=any(not r["is_manual"] for r in rows),
        has_plan=has_plan,
        plan_start=start_str,
        plan_end=end_str,
        totals=totals,
        spare_items=spare_items,
        spare_recipes=recipes_using_spares(spare_items, recipes, recipe_ingredients, ingredients),
        avg_shop_spend=average_spend_per_shop(db),
        unit_preference=unit_pref,
        subtract_pantry=subtract,
        pay_cycle=pay_cycle,
        list_modes=LIST_MODES,
        source_labels=SOURCE_LOOKUP,
        source_totals=totals_by_source(all_rows, (pay_cycle or {}).get("budget")),
        sources_present=sorted({r["source"] for r in all_rows}),
        show=show,
        last_mode=get_setting("last_list_mode", ""),
    )


@shopping_bp.route("/shopping-list/generate", methods=["POST"])
@login_required
def shopping_list_generate():
    stored_start, stored_end = get_plan_dates()
    plan_start = request.form.get("plan_start", "").strip() or stored_start
    plan_end = request.form.get("plan_end", "").strip() or stored_end
    if request.form.get("range") == "payday" or request.form.get("mode"):
        return _generate_cycle_list(plan_start, plan_end)
    if not (validate_date_format(plan_start) and validate_date_format(plan_end)):
        flash("Please set plan dates in Settings.", "error")
        return _back()

    db = get_db()
    # Remember what was already ticked so a regenerate doesn't lose progress.
    # Keyed by (ingredient, unit) so two lines for one ingredient in
    # incompatible units keep independent ticks. A unit-preference flip
    # resets ticks; that is rarer and safer than silently sharing them.
    previously_checked = {
        (r["ingredient_id"], r["unit"])
        for r in db.execute("SELECT ingredient_id, unit FROM shopping_list_items WHERE checked = 1 AND is_manual = 0")
    }
    db.execute("DELETE FROM shopping_list_items WHERE is_manual = 0")

    list_data = build_list(plan_start, plan_end)
    for cat, items in list_data.items():
        for item in items:
            db.execute(
                """INSERT INTO shopping_list_items
                   (ingredient_id, ingredient_name, quantity, unit, category, checked, is_manual, meal_date,
                    recipe_ref, estimated_cost)
                   VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?)""",
                (item["ingredient_id"], item["ingredient_name"], item["quantity"], item["unit"], cat,
                 1 if (item["ingredient_id"], item["unit"]) in previously_checked else 0,
                 item["meal_date"], ", ".join(item["recipe_refs"]), item["estimated_cost"]),
            )
    db.commit()

    item_count = sum(len(items) for items in list_data.values())
    flash(f"Shopping list generated with {item_count} items.", "success")
    return _back()


def _generate_cycle_list(plan_start, plan_end):
    """Build a list in one of the LIST_MODES, over the pay cycle or the plan dates."""
    from services.suggestions import build_cycle_list, current_pay_cycle, save_cycle_list
    today = local_today()
    mode = request.form.get("mode", "full")
    if mode not in MODE_LOOKUP:
        mode = "full"
    if request.form.get("range") == "payday":
        cycle = current_pay_cycle(today)
        if not cycle:
            flash("Set your payday in Settings → Budget to build a list until payday.", "error")
            return _back()
        plan_start, plan_end = cycle["until_payday_start"], cycle["cycle_end"]
    if not (validate_date_format(plan_start) and validate_date_format(plan_end)) or plan_end < plan_start:
        flash("Please set plan dates in Settings.", "error")
        return _back()
    result = build_cycle_list(plan_start, plan_end, mode, today)
    count = save_cycle_list(result["lines"])
    set_setting("last_list_mode", mode)
    get_db().commit()
    from models import parse_ymd, short_date
    span = " to ".join(short_date(parse_ymd(d)) for d in (plan_start, plan_end))
    message = f"{MODE_LOOKUP[mode]} list for {span}: {count} item{'' if count == 1 else 's'}."
    trim = result.get("trim")
    if trim and trim["over_by"] > 0:
        message += f" About ${trim['over_by']:.2f} over budget"
        if trim["drop"]:
            message += "; dropping " + ", ".join(trim["drop"][:4]) + " would help"
        message += "."
    flash(message, "warning" if trim and trim["over_by"] > 0 else "success")
    return _back()


@shopping_bp.route("/shopping-list/staple/<int:item_id>", methods=["POST"])
@login_required
def shopping_list_staple(item_id):
    """Never suggest (or always suggest) the regular buy behind a list row."""
    from services.repository import record_feedback
    from services.suggestions import staple_key_for_row
    action = request.form.get("action")
    row = get_db().execute("SELECT * FROM shopping_list_items WHERE id = ?", (item_id,)).fetchone()
    key = staple_key_for_row(dict(row)) if row else None
    if action not in ("never", "always") or not key:
        flash("That item isn't a suggested regular buy.", "error")
        return _back()
    record_feedback("staple", key, action)
    if action == "never":
        get_db().execute("DELETE FROM shopping_list_items WHERE id = ?", (item_id,))
    get_db().commit()
    flash(f"{row['ingredient_name']} won't be suggested again." if action == "never"
          else f"{row['ingredient_name']} will be on every list.", "success")
    return _back()


@shopping_bp.route("/shopping-list/toggle/<int:item_id>", methods=["POST"])
@login_required
def shopping_list_toggle(item_id):
    from services.mutations import set_tick
    db = get_db()
    row = db.execute("SELECT checked FROM shopping_list_items WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        if request.headers.get("HX-Request"):
            from services.repository import load_list_rows
            rows = {r["id"]: r for r in load_list_rows()}
            item = rows.get(item_id)
            if item:
                from flask import render_template
                return render_template("shopping_lists/_item_row.html", item=item)
            return jsonify({"checked": False}), 404
        if request.accept_mimetypes.best == "application/json":
            return jsonify({"checked": False})
        flash("Item not found.", "error")
        return _back()
    state = set_tick(db, item_id, not row["checked"])
    if request.headers.get("HX-Request"):
        from flask import render_template
        from services.repository import load_list_rows
        rows = {r["id"]: r for r in load_list_rows()}
        item = rows.get(item_id)
        if item:
            return render_template("shopping_lists/_item_row.html", item=item)
        return jsonify({"checked": state["checked"]}), 200
    if request.accept_mimetypes.best == "application/json":
        return jsonify({"checked": state["checked"]})
    return _back()


@shopping_bp.route("/shopping-list/add-manual", methods=["POST"])
@login_required
def shopping_list_add_manual():
    from services.mutations import add_manual_item
    try:
        add_manual_item(
            get_db(),
            request.form.get("name", "").strip() or request.form.get("ingredient_name", "").strip(),
            request.form.get("quantity", type=float),
            unit=request.form.get("unit", "each").strip(),
            category=request.form.get("category", "other").strip(),
        )
    except ValueError as exc:
        flash(str(exc), "error")
        return _back()
    flash("Item added to shopping list.", "success")
    return _back()


@shopping_bp.route("/shopping-list/pack/<int:item_id>", methods=["POST"])
@login_required
def shopping_list_pack(item_id):
    """Remember which pack offer to buy for a row (blank resets to automatic)."""
    from services.shopping_list_generator import pack_candidates
    db = get_db()
    rows = {r["id"]: r for r in load_list_rows()}
    row = rows.get(item_id)
    if not row:
        flash("Item not found.", "error")
        return _back()
    name = request.form.get("product_name", "").strip()
    if name:
        ingredient = {"name": row["ing_name"], "unit": row["ing_unit"],
                      "pack_size": row.get("ing_pack_size"), "price": row.get("ing_price")}
        offered = {o["name"] for o in pack_candidates(
            ingredient, load_linked_products().get(row.get("ingredient_id"), []))}
        if name not in offered:
            flash("That pack is no longer offered for this item.", "error")
            return _back()
    db.execute("UPDATE shopping_list_items SET pack_pick = ? WHERE id = ?", (name or None, item_id))
    db.commit()
    flash(f"You'll buy {name} for this item." if name else "Back to the automatic pick.", "success")
    return _back()


@shopping_bp.route("/shopping-list/delete/<int:item_id>", methods=["POST"])
@login_required
def shopping_list_delete(item_id):
    from services.suggestions import record_removed_line
    db = get_db()
    row = db.execute("SELECT * FROM shopping_list_items WHERE id = ?", (item_id,)).fetchone()
    if row:
        record_removed_line(dict(row))
    db.execute("DELETE FROM shopping_list_items WHERE id = ?", (item_id,))
    db.commit()
    flash("Item removed.", "success")
    return _back()


@shopping_bp.route("/shopping-list/clear-checked", methods=["POST"])
@login_required
def shopping_list_clear_checked():
    db = get_db()
    db.execute("DELETE FROM shopping_list_items WHERE checked = 1")
    db.commit()
    flash("Ticked items removed.", "success")
    return _back()


def register_shopping_routes(app):
    app.register_blueprint(shopping_bp)
