"""HTTP API (/api/v1): the app's services over JSON with bearer auth.

Same pure services the web UI and (previously) the stdio MCP server use, so
agents and humans always see the same numbers. Auth is per-account API keys
(users generate them under Settings → Admin) with the old single global key
(the `mcp_api_key` setting, rotated with `flask set-api-key`) kept as a
fallback so existing integrations keep working.

Imports are auto-commit: the agent passes base64 files, the API parses,
dedupes and applies the skip rules (unavailable/free lines), then commits.
That decision logic lives in services/, not here.
"""

import base64
import secrets
from datetime import datetime

from flask import Blueprint, current_app, g, jsonify, request

from database import get_db, get_setting
from services.mutations import AgentError

api_bp = Blueprint("api", __name__, url_prefix="/api/v1")


class ApiError(Exception):
    """Structured API failure: HTTP status + {code, message} body."""

    def __init__(self, status: int, code: str, message):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _unauthorized():
    return jsonify({"error": {"code": "unauthorized",
                              "message": "Missing or invalid API key. Send 'Authorization: Bearer <key>'."}}), 401


def require_api_key(f):
    """Decorator: the request must carry a valid per-user key or the global key."""
    from functools import wraps

    @wraps(f)
    def wrapper(*args, **kwargs):
        header = request.headers.get("Authorization", "")
        key = header[7:].strip() if header.startswith("Bearer ") else ""
        if key:
            from services.api_keys import verify_key
            user_id = verify_key(get_db(), key)
            if user_id is not None:
                g.api_user_id = user_id
                return f(*args, **kwargs)
            stored = get_setting("mcp_api_key") or ""
            if stored and secrets.compare_digest(stored, key):
                g.api_user_id = None  # legacy global key: no owning user
                return f(*args, **kwargs)
        return _unauthorized()

    return wrapper


def _agent_error(exc: AgentError):
    """Map a service AgentError to the right HTTP status."""
    status = 404 if exc.code == "not_found" else 400
    return jsonify({"error": {"code": exc.code, "message": exc.messages}}), status


def _handle(f):
    """Run a view, translating AgentError/ApiError into JSON error bodies."""
    from functools import wraps

    @wraps(f)
    def wrapper(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except AgentError as exc:
            return _agent_error(exc)
        except ApiError as exc:
            return jsonify({"error": {"code": exc.code, "message": exc.message}}), exc.status

    return wrapper


# ---------------------------------------------------------------------------
# Health (unauthenticated — CI and deploy checks use it)
# ---------------------------------------------------------------------------

@api_bp.route("/health")
def health():
    return jsonify({"status": "ok"}), 200


# ---------------------------------------------------------------------------
# Read endpoints — thin wrappers over the same services the UI uses
# ---------------------------------------------------------------------------

def _plan_payload():
    from database import get_plan_dates
    from models import DAY_LABELS, date_range
    start_str, end_str = get_plan_dates()
    if not (start_str and end_str):
        return {"plan_start": None, "plan_end": None, "days": []}
    entries = {(e["date"], e["meal_type"]): e for e in [dict(r) for r in get_db().execute(
        """SELECT mpe.date, mpe.meal_type, mpe.servings, mpe.is_auto_generated,
                  mpe.is_continuation, mpe.origin, r.name AS recipe_name
           FROM meal_plan_entries mpe JOIN recipes r ON r.id = mpe.recipe_id
           WHERE mpe.date BETWEEN ? AND ?""", (start_str, end_str))]}
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
                "origin": entry.get("origin") or "ui",
            }
        days.append({
            "date": key,
            "label": f"{DAY_LABELS[day.strftime('%a').lower()]} {day.strftime('%d %b')}",
            "meals": day_meals,
        })
    return {"plan_start": start_str, "plan_end": end_str, "days": days}


def _shopping_payload():
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
            "origin": row.get("origin") or "ui",
        })
    unit_pref, subtract = shopping_preferences()
    return {"groups": groups, "totals": list_totals(rows),
            "unit_preference": unit_pref, "subtract_pantry": subtract}


def _spend_summary_payload():
    from services.spend_analysis import analyze_purchases, load_purchases
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


def _server_info_payload():
    from database import get_plan_dates
    db = get_db()
    counts = {}
    for table in ("ingredients", "recipes", "meal_rules", "meal_plan_entries",
                  "shopping_list_items", "pantry_items", "purchases"):
        counts[table] = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    start, end = get_plan_dates()
    return {"server": "groceries", "version": "1.0.0",
            "plan_start": start or None, "plan_end": end or None, "counts": counts}


def _resolve_recipe_id(db, ref: str) -> int:
    ref = (ref or "").strip()
    if ref.isdigit():
        row = db.execute("SELECT id FROM recipes WHERE id = ?", (int(ref),)).fetchone()
        if row:
            return row["id"]
    else:
        row = db.execute("SELECT id FROM recipes WHERE name = ?", (ref,)).fetchone()
        if row:
            return row["id"]
    raise AgentError("not_found", f"No recipe found for {ref!r}.")


def _resolve_ingredient_id(db, ref: str) -> int:
    ref = (ref or "").strip()
    if ref.isdigit():
        row = db.execute("SELECT id FROM ingredients WHERE id = ?", (int(ref),)).fetchone()
        if row:
            return row["id"]
    else:
        row = db.execute("SELECT id FROM ingredients WHERE name = ?", (ref,)).fetchone()
        if row:
            return row["id"]
    raise AgentError("not_found", f"No ingredient found for {ref!r}.")


@api_bp.route("/server-info")
@require_api_key
@_handle
def server_info():
    return jsonify(_server_info_payload())


@api_bp.route("/plan")
@require_api_key
@_handle
def plan():
    return jsonify(_plan_payload())


@api_bp.route("/shopping-list")
@require_api_key
@_handle
def shopping_list():
    return jsonify(_shopping_payload())


@api_bp.route("/pantry")
@require_api_key
@_handle
def pantry():
    from services import dashboard as dash
    items = [dict(r) for r in get_db().execute(
        """SELECT p.id, p.quantity, p.unit, p.expiry_date, p.origin, i.name, i.display_name,
                  i.minimum_stock, i.category
           FROM pantry_items p JOIN ingredients i ON p.ingredient_id = i.id""")]
    return jsonify(dash.pantry_alerts(items))


@api_bp.route("/spend/summary")
@require_api_key
@_handle
def spend_summary():
    return jsonify(_spend_summary_payload())


@api_bp.route("/purchases")
@require_api_key
@_handle
def purchases():
    """Individual grocery purchases in a date window, newest first.

    Filters: from, to (YYYY-MM-DD), store (exact), search (product name),
    limit (default 100, max 500). Totals cover the whole filtered window.
    """
    from datetime import date as _date

    db = get_db()
    conditions, params = [], []

    def _day(name):
        raw = (request.args.get(name) or "").strip()
        if not raw:
            return None
        try:
            return _date.fromisoformat(raw).isoformat()
        except ValueError:
            raise ApiError(400, "bad_date", f"{name} must be YYYY-MM-DD.")

    start, end = _day("from"), _day("to")
    if start:
        conditions.append("p.order_date >= ?")
        params.append(start)
    if end:
        conditions.append("p.order_date <= ?")
        params.append(end)
    store = (request.args.get("store") or "").strip()
    if store:
        conditions.append("p.store = ?")
        params.append(store)
    search = (request.args.get("search") or "").strip()
    if search:
        conditions.append("p.product_name LIKE ?")
        params.append(f"%{search}%")

    try:
        limit = min(max(int((request.args.get("limit") or "100").strip()), 1), 500)
    except ValueError:
        raise ApiError(400, "bad_limit", "limit must be a whole number (1-500).")

    where = " AND ".join(conditions) or "1=1"
    rows = [dict(r) for r in db.execute(
        f"""SELECT p.id, p.order_date, p.store, p.product_name, p.quantity,
                   p.unit_price, p.line_total, p.cup_price, p.ingredient_id,
                   i.name AS ingredient_name, p.origin, p.basket_id
              FROM purchases p
              LEFT JOIN ingredients i ON i.id = p.ingredient_id
             WHERE {where}
             ORDER BY p.order_date DESC, p.id ASC
             LIMIT ?""",
        params + [limit])]
    totals = db.execute(
        f"""SELECT COUNT(*) AS rows, COUNT(DISTINCT p.basket_id) AS baskets,
                   COUNT(DISTINCT p.store) AS stores,
                   ROUND(SUM(p.line_total), 2) AS spend,
                   MIN(p.order_date) AS first_date, MAX(p.order_date) AS last_date
              FROM purchases p WHERE {where}""",
        params).fetchone()
    return jsonify({
        "count": len(rows),
        "total_rows": totals["rows"],
        "baskets": totals["baskets"],
        "stores": totals["stores"],
        "spend": totals["spend"] or 0,
        "first_date": totals["first_date"],
        "last_date": totals["last_date"],
        "limit": limit,
        "purchases": rows,
    })


@api_bp.route("/rules")
@require_api_key
@_handle
def rules():
    from services.repository import load_rules
    return jsonify({"rules": [{
        "id": r["id"], "days": r["day_of_week"], "meal": r["meal_type"],
        "tags": r["tag_filter"], "servings": r["servings_override"],
        "active": bool(r["is_active"]), "order": r["sort_order"],
    } for r in load_rules()]})


@api_bp.route("/ingredients")
@require_api_key
@_handle
def ingredients():
    search = (request.args.get("search") or "").strip()
    if search:
        rows = [dict(r) for r in get_db().execute(
            "SELECT id, name, display_name, category, unit, price FROM ingredients"
            " WHERE name LIKE ? ORDER BY name", (f"%{search}%",))]
    else:
        rows = [dict(r) for r in get_db().execute(
            "SELECT id, name, display_name, category, unit, price FROM ingredients ORDER BY name")]
    return jsonify({"count": len(rows), "ingredients": rows})


@api_bp.route("/ingredients", methods=["POST"])
@require_api_key
@_handle
def ingredients_create():
    """Create a new ingredient. Body: {name, display_name?, category?, unit?, price?, store?, minimum_stock?, pack_size?}"""
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        raise ApiError(400, "validation", "name is required.")
    db = get_db()
    # Avoid duplicates by exact name.
    existing = db.execute("SELECT id FROM ingredients WHERE name = ?", (name,)).fetchone()
    if existing:
        return jsonify({"id": existing["id"], "name": name, "note": "already exists."})
    cur = db.execute(
        """INSERT INTO ingredients (name, display_name, category, unit, price, store,
                   minimum_stock, pack_size, created_at, origin)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'api')""",
        (name,
         (body.get("display_name") or "").strip() or None,
         (body.get("category") or "other").strip() or None,
         (body.get("unit") or "each").strip() or None,
         float(body.get("price") or 0) if body.get("price") else None,
         (body.get("store") or "").strip() or None,
         float(body.get("minimum_stock")) if body.get("minimum_stock") not in (None, "") else None,
         float(body.get("pack_size")) if body.get("pack_size") not in (None, "") else None,
         datetime.now().isoformat())
    )
    db.commit()
    return jsonify({"id": cur.lastrowid, "name": name}), 201


@api_bp.route("/recipes", methods=["POST"])
@require_api_key
@_handle
def recipes_create():
    """Create a new recipe. Body: {name, description?, servings?, prep_time?, cook_time?,
    source_url?, image_url?, instructions?, covers_days?, tags?}"""
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        raise ApiError(400, "validation", "name is required.")
    db = get_db()
    existing = db.execute("SELECT id FROM recipes WHERE name = ?", (name,)).fetchone()
    if existing:
        return jsonify({"id": existing["id"], "name": name, "note": "already exists."})
    cur = db.execute(
        """INSERT INTO recipes (name, description, servings, prep_time, cook_time,
                   source_url, image_url, instructions, covers_days, tags, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (name,
         (body.get("description") or "").strip() or None,
         int(body.get("servings")) if body.get("servings") not in (None, "") else None,
         int(body.get("prep_time")) if body.get("prep_time") not in (None, "") else None,
         int(body.get("cook_time")) if body.get("cook_time") not in (None, "") else None,
         (body.get("source_url") or "").strip() or None,
         (body.get("image_url") or "").strip() or None,
         (body.get("instructions") or "").strip() or None,
         int(body.get("covers_days")) if body.get("covers_days") not in (None, "") else 1,
         (body.get("tags") or "").strip() or None,
         datetime.now().isoformat())
    )
    db.commit()
    return jsonify({"id": cur.lastrowid, "name": name}), 201


@api_bp.route("/recipes/<recipe_ref>/ingredients", methods=["POST"])
@require_api_key
@_handle
def recipe_add_ingredient(recipe_ref):
    """Link an ingredient to a recipe. Body: {ingredient, quantity, unit_override?}

    Both the recipe and the ingredient accept an id or an exact name.
    """
    body = request.get_json(silent=True) or {}
    db = get_db()
    recipe_id = _resolve_recipe_id(db, recipe_ref)
    ingredient_ref = (body.get("ingredient") or "").strip()
    if not ingredient_ref:
        raise ApiError(400, "validation", "ingredient is required.")
    ingredient_id = _resolve_ingredient_id(db, ingredient_ref)
    quantity = body.get("quantity")
    try:
        quantity = float(quantity) if quantity not in (None, "") else None
    except (TypeError, ValueError):
        raise ApiError(400, "validation", "quantity must be a number.")
    unit_override = (body.get("unit_override") or "").strip() or None
    cur = db.execute(
        """INSERT INTO recipe_ingredients (recipe_id, ingredient_id, quantity, unit_override)
           VALUES (?, ?, ?, ?)""",
        (recipe_id, ingredient_id, quantity, unit_override))
    db.commit()
    return jsonify({"recipe_id": recipe_id, "ingredient_id": ingredient_id,
                    "quantity": quantity, "unit_override": unit_override}), 201


@api_bp.route("/recipes/<ref>")
@require_api_key
@_handle
def recipe(ref):
    from routes.recipes import get_recipe_with_ingredients
    db = get_db()
    if ref.isdigit():
        recipe_row, _ = get_recipe_with_ingredients(int(ref))
        if recipe_row:
            return jsonify(recipe_row)
    row = db.execute("SELECT id FROM recipes WHERE name = ?", (ref,)).fetchone()
    if row:
        recipe_row, _ = get_recipe_with_ingredients(row["id"])
        if recipe_row:
            return jsonify(recipe_row)
    raise AgentError("not_found", f"No recipe found for {ref!r}.")


@api_bp.route("/ingredients/<ref>", methods=["DELETE"])
@require_api_key
@_handle
def ingredients_delete(ref):
    """Delete an ingredient by id or exact name (also drops its recipe links)."""
    db = get_db()
    ingredient_id = _resolve_ingredient_id(db, ref)
    db.execute("DELETE FROM recipe_ingredients WHERE ingredient_id = ?", (ingredient_id,))
    db.execute("DELETE FROM pantry_items WHERE ingredient_id = ?", (ingredient_id,))
    db.execute("DELETE FROM ingredients WHERE id = ?", (ingredient_id,))
    db.commit()
    return jsonify({"deleted": ingredient_id})


@api_bp.route("/recipes/<ref>", methods=["DELETE"])
@require_api_key
@_handle
def recipes_delete(ref):
    """Delete a recipe by id or exact name (also drops its ingredient links)."""
    db = get_db()
    recipe_id = _resolve_recipe_id(db, ref)
    db.execute("DELETE FROM recipe_ingredients WHERE recipe_id = ?", (recipe_id,))
    db.execute("DELETE FROM recipes WHERE id = ?", (recipe_id,))
    db.commit()
    return jsonify({"deleted": recipe_id})


# ---------------------------------------------------------------------------
# Import — base64 files in, committed purchases out
# ---------------------------------------------------------------------------

def _decode_file(entry) -> bytes:
    if not isinstance(entry, dict):
        raise ApiError(400, "validation", "Each file must be an object with filename and content_base64.")
    raw_b64 = entry.get("content_base64") or ""
    try:
        raw = base64.b64decode(raw_b64, validate=True)
    except Exception:
        raise ApiError(400, "validation",
                       f"{entry.get('filename') or 'file'}: that isn't valid base64 data.")
    if not raw:
        raise ApiError(400, "validation",
                       f"{entry.get('filename') or 'file'}: the file is empty.")
    return raw


def _import_one(db, filename: str, raw: bytes, store: str) -> dict:
    """Parse + commit one file. Never raises: errors ride in the result dict."""
    from services.spend_import import (
        import_purchases, parse_purchase_csv, parse_woolworths_invoice_pdf,
    )
    name = (filename or "").lower()
    result = {"filename": filename}
    if name.endswith(".pdf") or raw[:5] == b"%PDF-":
        meta, rows, warnings = parse_woolworths_invoice_pdf(raw, store="Woolworths")
        if not meta:
            result["error"] = warnings[0] if warnings else "That PDF couldn't be read as a Woolworths invoice."
            result["added"] = result["skipped"] = 0
            return result
        result["invoice_number"] = meta.get("invoice_number")
        result["order_date"] = meta.get("order_date")
        result["warnings"] = warnings
    else:
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            result["error"] = "That file isn't a UTF-8 CSV."
            result["added"] = result["skipped"] = 0
            return result
        rows, warnings = parse_purchase_csv(text, store)
        result["warnings"] = warnings
    if not rows:
        result.setdefault("error", "No priced lines could be imported from that file.")
        result["added"] = result["skipped"] = 0
        return result
    added, skipped = import_purchases(db, rows)
    result["rows"] = len(rows)
    result["added"] = added
    result["skipped"] = skipped
    return result


@api_bp.route("/import", methods=["POST"])
@require_api_key
@_handle
def import_files():
    body = request.get_json(silent=True) or {}
    files = body.get("files")
    if not isinstance(files, list) or not files:
        raise ApiError(400, "validation", "Send {\"files\": [{\"filename\", \"content_base64\"}]}.")
    store = (body.get("store") or "").strip() or "Woolworths"
    db = get_db()
    results = [_import_one(db, entry.get("filename") if isinstance(entry, dict) else "",
                           _decode_file(entry), store) for entry in files]
    return jsonify({
        "added": sum(r.get("added", 0) for r in results),
        "skipped": sum(r.get("skipped", 0) for r in results),
        "files": results,
    })


# ---------------------------------------------------------------------------
# Write endpoints — services/mutations, stamped origin="mcp" like the old server
# ---------------------------------------------------------------------------

@api_bp.route("/plan/meals", methods=["POST"])
@require_api_key
@_handle
def plan_add_meal():
    from services.mutations import place_meal
    body = request.get_json(silent=True) or {}
    db = get_db()
    default_servings = int(get_setting("default_servings", "2"))
    result = place_meal(
        db, (body.get("date") or "").strip(), (body.get("meal_type") or "").strip(),
        _resolve_recipe_id(db, body.get("recipe") or ""),
        servings=body.get("servings") or None, default_servings=default_servings,
        origin="mcp")
    return jsonify(result)


@api_bp.route("/plan/entries/<int:entry_id>", methods=["DELETE"])
@require_api_key
@_handle
def plan_remove_meal(entry_id):
    from services.mutations import remove_meal
    return jsonify(remove_meal(get_db(), entry_id))


@api_bp.route("/plan/entries/<int:entry_id>/swap", methods=["POST"])
@require_api_key
@_handle
def plan_swap_meal(entry_id):
    from services.mutations import swap_meal
    body = request.get_json(silent=True) or {}
    db = get_db()
    result = swap_meal(db, entry_id, _resolve_recipe_id(db, body.get("recipe") or ""),
                       servings=body.get("servings") or None, origin="mcp")
    return jsonify(result)


@api_bp.route("/plan/generate", methods=["POST"])
@require_api_key
@_handle
def plan_generate():
    from database import get_plan_dates
    from routes.meal_plan import generate_plan as _generate
    from services.plan_generator import generate_plan_entries
    from services.repository import load_manual_entries, load_recipes, load_rules
    body = request.get_json(silent=True) or {}
    confirm = bool(body.get("confirm"))
    start, end = get_plan_dates()
    if not (start and end):
        raise AgentError("no_plan_dates", "No plan dates set.")
    if not confirm:
        manual = load_manual_entries(start, end)
        entries, error = generate_plan_entries(start, end, load_rules(), load_recipes(), manual)
        if error:
            return jsonify({"error": error})
        return jsonify({"plan_start": start, "plan_end": end, "auto_entries": len(entries),
                        "manual_kept": len(manual), "confirmed": False,
                        "note": "Preview; call again with confirm=true to regenerate."})
    count, error = _generate(start, end)
    if error:
        return jsonify({"error": error})
    return jsonify({"plan_start": start, "plan_end": end, "entries": count or 0, "confirmed": True})


@api_bp.route("/shopping-list/items", methods=["POST"])
@require_api_key
@_handle
def shopping_add_item():
    from services.mutations import add_manual_item
    body = request.get_json(silent=True) or {}
    result = add_manual_item(get_db(), body.get("name") or "", body.get("quantity"),
                             unit=body.get("unit") or "each",
                             category=body.get("category") or "other", origin="mcp")
    return jsonify(result)


@api_bp.route("/shopping-list/tick", methods=["POST"])
@require_api_key
@_handle
def shopping_tick():
    from services.mutations import set_ticks
    body = request.get_json(silent=True) or {}
    ids = body.get("item_ids") or []
    if not isinstance(ids, list):
        raise ApiError(400, "validation", "item_ids must be a list.")
    result = set_ticks(get_db(), [int(i) for i in ids], bool(body.get("checked", True)),
                       origin="mcp")
    return jsonify(result)


@api_bp.route("/pantry/stock", methods=["POST"])
@require_api_key
@_handle
def pantry_add_stock():
    from services.mutations import add_stock
    body = request.get_json(silent=True) or {}
    db = get_db()
    result = add_stock(db, _resolve_ingredient_id(db, body.get("ingredient") or ""),
                       body.get("quantity"), unit=body.get("unit"),
                       expiry_date=body.get("expiry_date"),
                       location=body.get("location") or "pantry", origin="mcp")
    return jsonify(result)


@api_bp.route("/pantry/items/<int:item_id>/use", methods=["POST"])
@require_api_key
@_handle
def pantry_use(item_id):
    from services.mutations import consume_stock
    body = request.get_json(silent=True) or {}
    result = consume_stock(get_db(), item_id, body.get("amount"), origin="mcp")
    return jsonify(result)


@api_bp.route("/spend/links", methods=["POST"])
@require_api_key
@_handle
def spend_link():
    from services.mutations import link_product
    body = request.get_json(silent=True) or {}
    db = get_db()
    choice = (body.get("ingredient") or "").strip()
    if choice not in ("", "new"):
        choice = str(_resolve_ingredient_id(db, choice))
    level, message, price_updated = link_product(
        db, (body.get("product") or "").strip(), choice,
        bool(body.get("update_price", True)),
        (body.get("new_category") or "other").strip() or "other", origin="mcp")
    return jsonify({"level": level, "message": message, "price_updated": price_updated})


# ---------------------------------------------------------------------------
# BankSync — status and a manual pull, so an agent can finish or repair setup
# ---------------------------------------------------------------------------

def _banksync_status_payload():
    import json as _json

    from services.bank_import import load_merchant_patterns

    db = get_db()
    try:
        cursors = _json.loads(get_setting("banksync_cursors") or "{}")
    except ValueError:
        cursors = {}
    try:
        unclassified = _json.loads(get_setting("banksync_unclassified") or "[]")
    except ValueError:
        unclassified = []
    last_purchase = db.execute(
        "SELECT MAX(order_date) FROM purchases WHERE origin = 'banksync'"
    ).fetchone()[0]
    return {
        "configured": bool(get_setting("banksync_api_key")),
        "last_sync": get_setting("banksync_last_sync") or None,
        "accounts_tracked": len(cursors),
        "account_cursors": cursors,
        "merchant_patterns": len(load_merchant_patterns(db)),
        "unclassified_count": len(unclassified),
        "purchases_imported": db.execute(
            "SELECT COUNT(*) FROM purchases WHERE origin = 'banksync'"
        ).fetchone()[0],
        "latest_purchase_date": last_purchase,
    }


@api_bp.route("/banksync")
@require_api_key
@_handle
def banksync_status():
    """Whether BankSync is configured, how far each account has synced, what's pending."""
    return jsonify(_banksync_status_payload())


@api_bp.route("/banksync/sync", methods=["POST"])
@require_api_key
@_handle
def banksync_sync():
    """Pull new BankSync transactions now and report what happened."""
    from services.banksync_client import BankSyncError, run_sync

    if not get_setting("banksync_api_key"):
        raise ApiError(400, "banksync_not_configured",
                       "No BankSync API key saved. Add one under Settings → Bank imports.")
    try:
        result = run_sync(get_db())
    except BankSyncError as exc:
        # The message explains what to do (reconnect the bank, fix scopes, wait).
        raise ApiError(502, "banksync_failed", str(exc))
    return jsonify({
        "added": result["added"],
        "duplicates": len(result["duplicates"]),
        "unclassified": len(result["unclassified"]),
        "errors": result["errors"],
        "status": _banksync_status_payload(),
    })
