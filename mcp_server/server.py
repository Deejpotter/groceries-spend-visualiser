#!/usr/bin/env python3
"""Groceries MCP server (stdio) — the agent-friendly face of the app.

Every tool reads through the same pure services the web UI uses, so agents
and humans always see the same numbers. Writes arrive in a later slice;
this one is read-only on purpose.

Environment:
  DATABASE_PATH      SQLite file (default ./data/groceries.db).
  GROCERIES_API_KEY  Must be set and match the stored key (see set-api-key
                     in app.py). First run adopts the provided key.

Run:  python mcp_server/server.py   (stdio; see mcp_server/README.md)
"""

import os
import secrets
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp.server.mcpserver import MCPServer  # noqa: E402

mcp = MCPServer("groceries")

_app = None


def _app_ctx():
    """Flask app context, so database.get_db() works outside requests."""
    global _app
    if _app is None:
        from app import create_app
        _app = create_app()
    return _app.app_context()


def _rows(sql, params=()):
    from database import get_db
    return [dict(r) for r in get_db().execute(sql, params)]


# ---------------------------------------------------------------------------
# Plain query functions (ambient Flask context; directly unit-testable)
# ---------------------------------------------------------------------------

def week_plan() -> dict:
    """This plan period's days with each slot's meal (or null when empty)."""
    from database import get_plan_dates
    from models import DAY_LABELS, date_range, parse_date
    start_str, end_str = get_plan_dates()
    if not (start_str and end_str):
        return {"plan_start": None, "plan_end": None, "days": []}
    entries = {(e["date"], e["meal_type"]): e for e in _rows(
        """SELECT mpe.date, mpe.meal_type, mpe.servings, mpe.is_auto_generated,
                  mpe.is_continuation, r.name AS recipe_name
           FROM meal_plan_entries mpe JOIN recipes r ON r.id = mpe.recipe_id
           WHERE mpe.date BETWEEN ? AND ?""",
        (start_str, end_str),
    )}
    days = []
    for day in date_range(start_str, end_str):
        key = day.strftime("%Y-%m-%d")
        day_meals = {}
        for (entry_date, meal_type), entry in entries.items():
            if entry_date != key:
                continue
            day_meals[meal_type] = {
                "recipe": entry["recipe_name"],
                "servings": entry["servings"],
                "manual": not entry["is_auto_generated"],
                "leftovers": bool(entry["is_continuation"]),
            }
        days.append({
            "date": key,
            "label": f"{DAY_LABELS[day.strftime('%a').lower()]} {day.strftime('%d %b')}",
            "meals": day_meals,
        })
    return {"plan_start": start_str, "plan_end": end_str, "days": days}


def shopping_list() -> dict:
    """Current shopping list grouped by category, with picks, costs and prefs."""
    from services.repository import load_linked_products, load_list_rows, shopping_preferences
    from services.shopping_list_generator import list_totals, plan_list_lines
    rows = plan_list_lines(load_list_rows(), load_linked_products())
    groups = {}
    for row in rows:
        pick = (row.get("pack_choice") or {}).get("best") or {}
        groups.setdefault(row.get("category") or "other", []).append({
            "name": row["ingredient_name"],
            "quantity": row["quantity"],
            "unit": row["unit"],
            "ticked": bool(row["checked"]),
            "manual": bool(row["is_manual"]),
            "buy": pick.get("name"),
            "line_cost": row.get("line_cost"),
        })
    unit_pref, subtract = shopping_preferences()
    return {"groups": groups, "totals": list_totals(rows),
            "unit_preference": unit_pref, "subtract_pantry": subtract}


def pantry_status() -> dict:
    """Pantry alerts (expired/expiring/low) plus the watch list."""
    from services import dashboard as dash
    items = _rows(
        """SELECT p.id, p.quantity, p.unit, p.expiry_date, i.name, i.display_name,
                  i.minimum_stock, i.category
           FROM pantry_items p JOIN ingredients i ON p.ingredient_id = i.id""")
    return dash.pantry_alerts(items)


def spend_summary() -> dict:
    """All-time spend totals, averages and top products."""
    from services.spend_analysis import analyze_purchases, load_purchases
    from database import get_db
    stats = analyze_purchases(load_purchases(get_db()), top_n=5)
    if not stats:
        return {"purchases": 0, "note": "No purchases yet."}
    return {
        "total_spend": stats["total_spend"],
        "total_shops": stats["total_shops"],
        "avg_per_shop": stats["avg_spend_per_shop"],
        "avg_days_between_shops": stats["avg_days_between_shops"],
        "monthly_spend": stats["monthly_spend"],
        "top_by_spend": stats["top_by_spend"],
        "top_by_frequency": stats["top_by_frequency"],
    }


def list_rules() -> dict:
    """Meal rules in evaluation order."""
    from services.repository import load_rules
    return {"rules": [{
        "id": r["id"], "days": r["day_of_week"], "meal": r["meal_type"],
        "tags": r["tag_filter"], "servings": r["servings_override"],
        "active": bool(r["is_active"]), "order": r["sort_order"],
    } for r in load_rules()]}


def list_ingredients(search: str = "") -> dict:
    """Ingredients, optionally filtered by name."""
    if search:
        items = _rows("SELECT id, name, display_name, category, unit, price FROM ingredients"
                      " WHERE name LIKE ? ORDER BY name", (f"%{search}%",))
    else:
        items = _rows("SELECT id, name, display_name, category, unit, price FROM ingredients ORDER BY name")
    return {"count": len(items), "ingredients": items}


def get_recipe(ref: str) -> dict:
    """One recipe with its ingredients, by id or exact name."""
    from routes.recipes import get_recipe_with_ingredients
    recipe = None
    if ref.isdigit():
        recipe, _ = get_recipe_with_ingredients(int(ref))
    if recipe is None:
        row = _rows("SELECT id FROM recipes WHERE name = ?", (ref,))
        if row:
            recipe, _ = get_recipe_with_ingredients(row[0]["id"])
    if recipe is None:
        return {"error": f"No recipe found for {ref!r}."}
    return recipe


def _resolve_recipe_id(db, ref: str) -> int:
    """Recipe id from an id string or exact name; raises ValueError when unknown."""
    ref = (ref or "").strip()
    if ref.isdigit():
        row = db.execute("SELECT id FROM recipes WHERE id = ?", (int(ref),)).fetchone()
        if row:
            return row["id"]
    else:
        row = db.execute("SELECT id FROM recipes WHERE name = ?", (ref,)).fetchone()
        if row:
            return row["id"]
    raise ValueError(f"No recipe found for {ref!r}.")


def _resolve_ingredient_id(db, ref: str) -> int:
    """Ingredient id from an id string or exact name; raises ValueError when unknown."""
    ref = (ref or "").strip()
    if ref.isdigit():
        row = db.execute("SELECT id FROM ingredients WHERE id = ?", (int(ref),)).fetchone()
        if row:
            return row["id"]
    else:
        row = db.execute("SELECT id FROM ingredients WHERE name = ?", (ref,)).fetchone()
        if row:
            return row["id"]
    raise ValueError(f"No ingredient found for {ref!r}.")


SERVER_VERSION = "0.3.0"


def server_info() -> dict:
    """Server version, plan dates and table counts — orient in one call."""
    from database import get_db, get_plan_dates
    db = get_db()
    counts = {}
    for table in ("ingredients", "recipes", "meal_rules", "meal_plan_entries",
                  "shopping_list_items", "pantry_items", "purchases"):
        counts[table] = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    start, end = get_plan_dates()
    return {"server": "groceries", "version": SERVER_VERSION,
            "plan_start": start or None, "plan_end": end or None, "counts": counts}


def add_stock(ingredient: str, quantity: float, unit: str = "",
                expiry_date: str = "", location: str = "pantry") -> dict:
    """Add pantry stock for an ingredient (id or exact name)."""
    from database import get_db
    from services.mutations import add_stock as _insert
    db = get_db()
    return _insert(db, _resolve_ingredient_id(db, ingredient), quantity,
                   unit=unit, expiry_date=expiry_date, location=location)


def tick_many(item_ids: list, checked: bool = True) -> dict:
    """Tick or untick many shopping-list rows at once."""
    from database import get_db
    from services.mutations import set_ticks
    return set_ticks(get_db(), [int(i) for i in (item_ids or [])], checked)


def link_product(product: str, ingredient: str = "", update_price: bool = True,
                 new_category: str = "other") -> dict:
    """Link a product to an ingredient (id/name, "new", or "" to unlink)."""
    from database import get_db
    from services.mutations import link_product as _link
    db = get_db()
    choice = (ingredient or "").strip()
    if choice not in ("", "new"):
        choice = str(_resolve_ingredient_id(db, choice))
    level, message, price_updated = _link(db, (product or "").strip(), choice,
                                          update_price, (new_category or "").strip() or "other")
    return {"level": level, "message": message, "price_updated": price_updated}


def stage_invoice_pdf(pdf_base64: str) -> dict:
    """Stage a base64 PDF invoice for review (nothing is imported yet)."""
    import base64
    from database import get_db
    from services.spend_import import (
        create_import_batch, get_import_batch, parse_woolworths_invoice_pdf,
        staged_read_total,
    )
    try:
        raw = base64.b64decode(pdf_base64, validate=True)
    except Exception:
        return {"error": "That isn't valid base64 PDF data."}
    meta, rows, warnings = parse_woolworths_invoice_pdf(raw)
    if not meta:
        return {"error": warnings[0] if warnings else "That PDF couldn't be read as a Woolworths invoice."}
    db = get_db()
    token = create_import_batch(db, meta, rows)
    _, lines = get_import_batch(db, token)
    return {"token": token, "invoice_number": meta.get("invoice_number"),
            "order_date": meta.get("order_date"), "subtotal": meta.get("subtotal"),
            "staged_lines": len(lines), "read_total": staged_read_total(lines),
            "warnings": warnings,
            "note": "Nothing is imported until the review is confirmed in the UI."}


def import_status(token: str) -> dict:
    """A staged batch's lines and read total, for review."""
    from database import get_db
    from services.spend_import import get_import_batch, staged_read_total
    batch, lines = get_import_batch(get_db(), (token or "").strip())
    if not batch:
        return {"error": "Unknown or finished import batch."}
    return {"invoice_number": batch["invoice_number"], "order_date": batch["order_date"],
            "subtotal": batch["subtotal"], "status": batch["status"],
            "read_total": staged_read_total(lines),
            "lines": [{"id": l["id"], "product_name": l["product_name"], "quantity": l["quantity"],
                       "unit_price": l["unit_price"], "line_total": l["line_total"],
                       "excluded": bool(l["excluded"]), "already": bool(l["already"]),
                       "note": l.get("note")} for l in lines]}


def add_meal(date: str, meal_type: str, recipe: str, servings: int = 0) -> dict:
    """Place a recipe in a plan slot (replaces whatever is there)."""
    from database import get_db, get_setting
    from services.mutations import place_meal
    db = get_db()
    return place_meal(db, date.strip(), meal_type.strip(), _resolve_recipe_id(db, recipe),
                      servings=servings or None,
                      default_servings=int(get_setting("default_servings", "2")))


def swap_meal(entry_id: int, recipe: str, servings: int = 0) -> dict:
    """Replace a plan entry's recipe (marks it manual)."""
    from database import get_db
    from services.mutations import swap_meal as _swap
    db = get_db()
    return _swap(db, entry_id, _resolve_recipe_id(db, recipe), servings=servings or None)


def remove_meal(entry_id: int) -> dict:
    """Delete a plan entry (continuations become standalone meals)."""
    from database import get_db
    from services.mutations import remove_meal as _remove
    return _remove(get_db(), entry_id)


def generate_plan(confirm: bool = False) -> dict:
    """Without confirm: preview counts. With confirm: regenerate auto entries."""
    from database import get_db, get_plan_dates
    from services.plan_generator import generate_plan_entries
    from services.repository import load_manual_entries, load_recipes, load_rules
    start, end = get_plan_dates()
    if not (start and end):
        return {"error": "No plan dates set."}
    if not confirm:
        manual = load_manual_entries(start, end)
        entries, error = generate_plan_entries(
            start, end, load_rules(), load_recipes(), manual)
        if error:
            return {"error": error}
        return {"plan_start": start, "plan_end": end, "auto_entries": len(entries),
                "manual_kept": len(manual), "confirmed": False,
                "note": "Preview uses first-match picks; call again with confirm=true to regenerate."}
    from routes.meal_plan import generate_plan as _generate
    count, error = _generate(start, end)
    if error:
        return {"error": error}
    return {"plan_start": start, "plan_end": end, "entries": count or 0, "confirmed": True}


def set_item_ticked(item_id: int, checked: bool = True) -> dict:
    """Tick or untick a shopping-list row."""
    from database import get_db
    from services.mutations import set_tick
    return set_tick(get_db(), item_id, checked)


def add_list_item(name: str, quantity: float, unit: str = "each", category: str = "other") -> dict:
    """Add a free-text row to the shopping list."""
    from database import get_db
    from services.mutations import add_manual_item
    return add_manual_item(get_db(), name, quantity, unit=unit, category=category)


def use_pantry_stock(item_id: int, amount: float) -> dict:
    """Use up pantry stock; reports what remains."""
    from database import get_db
    from services.mutations import consume_stock
    return consume_stock(get_db(), item_id, amount)


# ---------------------------------------------------------------------------
# MCP tool wrappers (each call runs inside its own app context)
# ---------------------------------------------------------------------------

def _safe(fn, **kwargs):
    try:
        return fn(**kwargs)
    except Exception as exc:  # structured error, like the day-planner server
        return {"error": f"{type(exc).__name__}: {exc}"}


@mcp.tool()
def get_week_plan() -> dict:
    """This plan period's days with each slot's meal (or null when empty)."""
    with _app_ctx():
        return _safe(week_plan)


@mcp.tool()
def get_shopping_list() -> dict:
    """Current shopping list grouped by category, with picks, costs and prefs."""
    with _app_ctx():
        return _safe(shopping_list)


@mcp.tool()
def get_pantry_status() -> dict:
    """Pantry alerts (expired/expiring/low) plus the watch list."""
    with _app_ctx():
        return _safe(pantry_status)


@mcp.tool()
def get_spend_summary() -> dict:
    """All-time spend totals, averages and top products."""
    with _app_ctx():
        return _safe(spend_summary)


@mcp.tool()
def get_rules() -> dict:
    """Meal rules in evaluation order."""
    with _app_ctx():
        return _safe(list_rules)


@mcp.tool()
def search_ingredients(search: str = "") -> dict:
    """Ingredients, optionally filtered by name."""
    with _app_ctx():
        return _safe(list_ingredients, search=search)


@mcp.tool()
def read_recipe(ref: str) -> dict:
    """One recipe with its ingredients, by id or exact name."""
    with _app_ctx():
        return _safe(get_recipe, ref=ref)


@mcp.tool()
def plan_add_meal(date: str, meal_type: str, recipe: str, servings: int = 0) -> dict:
    """Place a recipe in a plan slot (replaces whatever is there)."""
    with _app_ctx():
        return _safe(add_meal, date=date, meal_type=meal_type, recipe=recipe, servings=servings)


@mcp.tool()
def plan_swap_meal(entry_id: int, recipe: str, servings: int = 0) -> dict:
    """Replace a plan entry's recipe (marks it manual)."""
    with _app_ctx():
        return _safe(swap_meal, entry_id=entry_id, recipe=recipe, servings=servings)


@mcp.tool()
def plan_remove_meal(entry_id: int) -> dict:
    """Delete a plan entry (continuations become standalone meals)."""
    with _app_ctx():
        return _safe(remove_meal, entry_id=entry_id)


@mcp.tool()
def plan_generate(confirm: bool = False) -> dict:
    """Preview regeneration counts; with confirm=true, regenerate auto entries."""
    with _app_ctx():
        return _safe(generate_plan, confirm=confirm)


@mcp.tool()
def tick_item(item_id: int, checked: bool = True) -> dict:
    """Tick or untick a shopping-list row."""
    with _app_ctx():
        return _safe(set_item_ticked, item_id=item_id, checked=checked)


@mcp.tool()
def add_shopping_item(name: str, quantity: float, unit: str = "each", category: str = "other") -> dict:
    """Add a free-text row to the shopping list."""
    with _app_ctx():
        return _safe(add_list_item, name=name, quantity=quantity, unit=unit, category=category)


@mcp.tool()
def use_pantry(item_id: int, amount: float) -> dict:
    """Use up pantry stock; reports what remains."""
    with _app_ctx():
        return _safe(use_pantry_stock, item_id=item_id, amount=amount)


@mcp.tool()
def get_server_info() -> dict:
    """Server version, plan dates and table counts — orient in one call."""
    with _app_ctx():
        return _safe(server_info)


@mcp.tool()
def add_pantry_stock(ingredient: str, quantity: float, unit: str = "",
                     expiry_date: str = "", location: str = "pantry") -> dict:
    """Add pantry stock for an ingredient (id or exact name)."""
    with _app_ctx():
        return _safe(add_stock, ingredient=ingredient, quantity=quantity, unit=unit,
                     expiry_date=expiry_date, location=location)


@mcp.tool()
def tick_items(item_ids: list, checked: bool = True) -> dict:
    """Tick or untick many shopping-list rows at once."""
    with _app_ctx():
        return _safe(tick_many, item_ids=item_ids, checked=checked)


@mcp.tool()
def link_spend_product(product: str, ingredient: str = "", update_price: bool = True,
                       new_category: str = "other") -> dict:
    """Link a product to an ingredient (id/name, "new", or "" to unlink)."""
    with _app_ctx():
        return _safe(link_product, product=product, ingredient=ingredient,
                     update_price=update_price, new_category=new_category)


@mcp.tool()
def stage_invoice(pdf_base64: str) -> dict:
    """Stage a base64 PDF invoice for review (nothing is imported yet)."""
    with _app_ctx():
        return _safe(stage_invoice_pdf, pdf_base64=pdf_base64)


@mcp.tool()
def get_import_status(token: str) -> dict:
    """A staged batch's lines and read total, for review."""
    with _app_ctx():
        return _safe(import_status, token=token)


# ---------------------------------------------------------------------------
# Startup gate + CLI for the API key
# ---------------------------------------------------------------------------

def require_api_key() -> None:
    """Refuse to start unless GROCERIES_API_KEY matches the stored key."""
    key = os.environ.get("GROCERIES_API_KEY", "")
    if not key:
        sys.exit("GROCERIES_API_KEY is not set — see mcp_server/README.md.")
    with _app_ctx():
        from database import get_db, get_setting, set_setting
        stored = get_setting("mcp_api_key")
        if not stored:
            set_setting("mcp_api_key", key)
            get_db().commit()
            return
        if not secrets.compare_digest(stored, key):
            sys.exit("GROCERIES_API_KEY does not match the stored key.")


def main() -> None:
    require_api_key()
    mcp.run()


if __name__ == "__main__":
    main()
