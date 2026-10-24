#!/usr/bin/env python3

"""Groceries MCP server (stdio) — a thin client over the app's HTTP API.

Every tool calls /api/v1 with the bearer key, so the same server works
against a local dev instance, staging or production by changing
GROCERIES_URL. All business logic lives in the app's services; this file
only shapes requests and errors for the MCP protocol.

Environment:
  GROCERIES_URL      Base URL of the app (e.g. https://groceries.deejpotter.com).
  GROCERIES_API_KEY  The bearer key (from `flask --app app set-api-key`).

Run:  python mcp_server/server.py   (stdio; see mcp_server/README.md)

"""

import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

from mcp.server.mcpserver import MCPServer  # noqa: E402

SERVER_VERSION = "1.0.0"


class ApiError(Exception):
    """The API returned {error: {code, message}} or failed to connect."""

    def __init__(self, code: str, message):
        super().__init__(message)
        self.code = code
        self.message = message


class GroceriesApi:
    """Minimal HTTP client for the groceries /api/v1 surface.

    `opener` is injectable so tests can run without a network; production
    uses urllib's default opener.
    """

    def __init__(self, base_url: str, api_key: str, timeout: float = 60.0,
                 opener=None):
        if not base_url:
            raise ApiError("config", "GROCERIES_URL is not set.")
        if not api_key:
            raise ApiError("config", "GROCERIES_API_KEY is not set.")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self._opener = opener or urllib.request.urlopen

    def _request(self, method: str, path: str, body=None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            self.base_url + "/api/v1" + path, data=data, method=method,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
                # Cloudflare's browser-integrity check blocks the default
                # "Python-urllib/..." UA with a 1010 before we reach the app.
                "User-Agent": "groceries-mcp/1.0",
            },
        )
        try:
            res = self._opener(req, timeout=self.timeout)
            with res:
                return json.loads(res.read().decode() or "null")
        except urllib.error.HTTPError as exc:
            try:
                payload = json.loads(exc.read().decode() or "{}")
                err = payload.get("error") or {}
                raise ApiError(err.get("code", "http_error"),
                               err.get("message") or f"HTTP {exc.code}")
            except json.JSONDecodeError:
                raise ApiError("http_error", f"HTTP {exc.code}")
        except urllib.error.URLError as exc:
            raise ApiError("connection", f"Could not reach {self.base_url}: {exc.reason}")

    def get(self, path: str) -> dict:
        return self._request("GET", path)

    def post(self, path: str, body=None) -> dict:
        return self._request("POST", path, body or {})

    def delete(self, path: str) -> dict:
        return self._request("DELETE", path)


def _client() -> GroceriesApi:
    return GroceriesApi(os.environ.get("GROCERIES_URL", ""),
                        os.environ.get("GROCERIES_API_KEY", ""))


mcp = MCPServer("groceries")


def _safe(fn, **kwargs):
    """Run a tool, mapping ApiError to the structured {error: {...}} shape."""
    try:
        return fn(**kwargs)
    except ApiError as exc:
        return {"error": {"code": exc.code, "message": exc.message}}
    except Exception as exc:  # surface unexpected failures structurally
        return {"error": {"code": "internal", "message": f"{type(exc).__name__}: {exc}"}}


def _b64(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


# ---------------------------------------------------------------------------
# Tools — each maps 1:1 onto an /api/v1 endpoint
# ---------------------------------------------------------------------------

@mcp.tool()
def get_server_info() -> dict:
    """Server version, plan dates and table counts — orient in one call."""
    return _safe(_client().get, path="/server-info")


@mcp.tool()
def get_week_plan() -> dict:
    """This plan period's days with each slot's meal (or null when empty)."""
    return _safe(_client().get, path="/plan")


@mcp.tool()
def get_shopping_list() -> dict:
    """Current shopping list grouped by category, with picks, costs and prefs."""
    return _safe(_client().get, path="/shopping-list")


@mcp.tool()
def get_pantry_status() -> dict:
    """Pantry alerts (expired/expiring/low) plus the watch list."""
    return _safe(_client().get, path="/pantry")


@mcp.tool()
def get_spend_summary() -> dict:
    """All-time spend totals, averages and top products."""
    return _safe(_client().get, path="/spend/summary")


@mcp.tool()
def list_purchases(start: str = "", end: str = "", store: str = "",
                   search: str = "", limit: int = 50) -> dict:
    """Individual grocery purchases (date, store, product, qty, price), newest first.

    start/end are YYYY-MM-DD; search matches the product name.
    """
    query = {"limit": limit}
    if start:
        query["from"] = start
    if end:
        query["to"] = end
    if store:
        query["store"] = store
    if search:
        query["search"] = search
    return _safe(_client().get, path="/purchases?" + urllib.parse.urlencode(query))


@mcp.tool()
def get_rules() -> dict:
    """Meal rules in evaluation order."""
    return _safe(_client().get, path="/rules")


@mcp.tool()
def search_ingredients(search: str = "") -> dict:
    """Ingredients, optionally filtered by name."""
    path = "/ingredients" + (f"?search={urllib.parse.quote(search)}" if search else "")
    return _safe(_client().get, path=path)


@mcp.tool()
def read_recipe(ref: str) -> dict:
    """One recipe with its ingredients, by id or exact name."""
    return _safe(_client().get, path=f"/recipes/{urllib.parse.quote(ref)}")


@mcp.tool()
def create_ingredient(name: str, display_name: str = "", category: str = "other",
                     unit: str = "each", price: float = 0.0,
                     store: str = "", minimum_stock: float = 0.0,
                     pack_size: float = 0.0) -> dict:
    """Create a new ingredient (dedupes by exact name)."""
    body = {"name": name, "display_name": display_name, "category": category,
            "unit": unit, "price": price, "store": store,
            "minimum_stock": minimum_stock, "pack_size": pack_size}
    return _safe(_client().post, path="/ingredients", body=body)


@mcp.tool()
def create_recipe(name: str, description: str = "", servings: int = 4,
                 prep_time: int = 0, cook_time: int = 0,
                 covers_days: int = 1, tags: str = "") -> dict:
    """Create a new recipe (dedupes by exact name)."""
    body = {"name": name, "description": description, "servings": servings,
            "prep_time": prep_time, "cook_time": cook_time,
            "covers_days": covers_days, "tags": tags}
    return _safe(_client().post, path="/recipes", body=body)


@mcp.tool()
def add_recipe_ingredient(recipe: str, ingredient: str, quantity: float,
                         unit_override: str = "") -> dict:
    """Link an ingredient to a recipe (by id or exact name for each)."""
    body = {"ingredient": ingredient, "quantity": quantity,
            "unit_override": unit_override}
    return _safe(_client().post,
                 path=f"/recipes/{urllib.parse.quote(recipe)}/ingredients",
                 body=body)


@mcp.tool()
def delete_ingredient(ref: str) -> dict:
    """Delete an ingredient by id or exact name (also drops its recipe links)."""
    return _safe(_client().delete, path=f"/ingredients/{urllib.parse.quote(ref)}")


@mcp.tool()
def delete_recipe(ref: str) -> dict:
    """Delete a recipe by id or exact name (also drops its ingredient links)."""
    return _safe(_client().delete, path=f"/recipes/{urllib.parse.quote(ref)}")


@mcp.tool()
def plan_add_meal(date: str, meal_type: str, recipe: str, servings: int = 0) -> dict:
    """Place a recipe in a plan slot (replaces whatever is there)."""
    body = {"date": date, "meal_type": meal_type, "recipe": recipe}
    if servings:
        body["servings"] = servings
    return _safe(_client().post, path="/plan/meals", body=body)


@mcp.tool()
def plan_swap_meal(entry_id: int, recipe: str, servings: int = 0) -> dict:
    """Replace a plan entry's recipe (marks it manual)."""
    body = {"recipe": recipe}
    if servings:
        body["servings"] = servings
    return _safe(_client().post, path=f"/plan/entries/{entry_id}/swap", body=body)


@mcp.tool()
def plan_remove_meal(entry_id: int) -> dict:
    """Delete a plan entry (continuations become standalone meals)."""
    return _safe(_client().delete, path=f"/plan/entries/{entry_id}")


@mcp.tool()
def plan_generate(confirm: bool = False) -> dict:
    """Preview regeneration counts; with confirm=true, regenerate auto entries."""
    return _safe(_client().post, path="/plan/generate", body={"confirm": confirm})


@mcp.tool()
def tick_item(item_id: int, checked: bool = True) -> dict:
    """Tick or untick a shopping-list row."""
    return _safe(_client().post, path="/shopping-list/tick",
                 body={"item_ids": [item_id], "checked": checked})


@mcp.tool()
def tick_items(item_ids: list, checked: bool = True) -> dict:
    """Tick or untick many shopping-list rows at once."""
    return _safe(_client().post, path="/shopping-list/tick",
                 body={"item_ids": item_ids, "checked": checked})


@mcp.tool()
def add_shopping_item(name: str, quantity: float, unit: str = "each",
                      category: str = "other") -> dict:
    """Add a free-text row to the shopping list."""
    return _safe(_client().post, path="/shopping-list/items",
                 body={"name": name, "quantity": quantity, "unit": unit,
                       "category": category})


@mcp.tool()
def add_pantry_stock(ingredient: str, quantity: float, unit: str = "",
                     expiry_date: str = "", location: str = "pantry") -> dict:
    """Add pantry stock for an ingredient (id or exact name)."""
    body = {"ingredient": ingredient, "quantity": quantity, "location": location}
    if unit:
        body["unit"] = unit
    if expiry_date:
        body["expiry_date"] = expiry_date
    return _safe(_client().post, path="/pantry/stock", body=body)


@mcp.tool()
def use_pantry(item_id: int, amount: float) -> dict:
    """Use up pantry stock; reports what remains."""
    return _safe(_client().post, path=f"/pantry/items/{item_id}/use",
                 body={"amount": amount})


@mcp.tool()
def link_spend_product(product: str, ingredient: str = "", update_price: bool = True,
                       new_category: str = "other") -> dict:
    """Link a product to an ingredient (id/name, \"new\", or \"\" to unlink)."""
    return _safe(_client().post, path="/spend/links",
                 body={"product": product, "ingredient": ingredient,
                       "update_price": update_price, "new_category": new_category})


@mcp.tool()
def import_files(paths: list, store: str = "Woolworths") -> dict:
    """Import local CSV or Woolworths PDF files into the app (auto-commit).

    Each path is read, base64-encoded and sent to the API, which parses,
    dedupes and commits. Safe to re-run: already-imported lines are skipped.
    Returns per-file added/skipped counts and warnings.
    """
    try:
        files = [{"filename": os.path.basename(p), "content_base64": _b64(p)}
                 for p in paths]
    except OSError as exc:
        return {"error": {"code": "file_error", "message": str(exc)}}
    return _safe(_client().post, path="/import",
                 body={"files": files, "store": store})


def main() -> None:
    if not os.environ.get("GROCERIES_URL"):
        sys.exit("GROCERIES_URL is not set — see mcp_server/README.md.")
    if not os.environ.get("GROCERIES_API_KEY"):
        sys.exit("GROCERIES_API_KEY is not set — see mcp_server/README.md.")
    mcp.run()


if __name__ == "__main__":
    main()
