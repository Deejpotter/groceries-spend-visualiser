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
