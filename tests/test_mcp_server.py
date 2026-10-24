"""MCP server: the stdio client shape over the app's HTTP API.

The server is a thin HTTP client now, so these tests verify the client
behaviour (request shaping, bearer auth, error mapping) and the tool
registry — the app-side logic is covered by tests/test_api.py.
"""

import base64
import io
import json
import urllib.error

import pytest

from mcp_server.server import (
    GroceriesApi, ApiError, _b64, _safe, mcp,
)


class FakeResponse:
    """Minimal context-manager stand-in for an HTTP response."""

    def __init__(self, payload, status=200):
        self._body = json.dumps(payload).encode() if payload is not None else b""
        self.status = status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeOpener:
    """Records requests, replies from a queue (or raises queued errors)."""

    def __init__(self, responses=None, errors=None):
        self.requests = []
        self.responses = list(responses or [])
        self.errors = list(errors or [])

    def __call__(self, req, timeout=None):
        self.requests.append((req, timeout))
        if self.errors:
            raise self.errors.pop(0)
        return self.responses.pop(0)


def _http_error(status, payload):
    """A urllib HTTPError carrying a JSON body."""
    return urllib.error.HTTPError(
        "http://x/api", status, "err", {}, io.BytesIO(json.dumps(payload).encode()))


def _api(**kw):
    kw.setdefault("base_url", "https://groceries.example")
    kw.setdefault("api_key", "gx_test")
    return GroceriesApi(**kw)


# ---------------------------------------------------------------------------
# Client: request shaping and auth
# ---------------------------------------------------------------------------

def test_get_sends_bearer_and_path():
    opener = FakeOpener([FakeResponse({"ok": True})])
    api = _api(opener=opener)
    assert api.get("/plan") == {"ok": True}
    req, _ = opener.requests[0]
    assert req.get_method() == "GET"
    assert req.full_url == "https://groceries.example/api/v1/plan"
    assert req.get_header("Authorization") == "Bearer gx_test"


def test_base_url_trailing_slash_is_trimmed():
    opener = FakeOpener([FakeResponse({})])
    api = _api(base_url="https://groceries.example/", opener=opener)
    api.get("/plan")
    assert opener.requests[0][0].full_url == "https://groceries.example/api/v1/plan"


def test_post_sends_json_body():
    opener = FakeOpener([FakeResponse({"added": 1})])
    api = _api(opener=opener)
    assert api.post("/shopping-list/tick", {"item_ids": [1], "checked": True}) == {"added": 1}
    req, _ = opener.requests[0]
    assert req.get_method() == "POST"
    assert json.loads(req.data.decode()) == {"item_ids": [1], "checked": True}


def test_delete_method():
    opener = FakeOpener([FakeResponse({"removed": 3})])
    api = _api(opener=opener)
    assert api.delete("/plan/entries/3") == {"removed": 3}
    assert opener.requests[0][0].get_method() == "DELETE"


def test_missing_env_vars_raise_config_error():
    with pytest.raises(ApiError) as excinfo:
        GroceriesApi("", "key")
    assert excinfo.value.code == "config"
    with pytest.raises(ApiError):
        GroceriesApi("https://x", "")


# ---------------------------------------------------------------------------
# Client: error mapping
# ---------------------------------------------------------------------------

def test_api_error_body_maps_code_and_message():
    opener = FakeOpener(errors=[_http_error(404, {"error": {"code": "not_found",
                                                            "message": "No recipe."}})])
    with pytest.raises(ApiError) as excinfo:
        _api(opener=opener).get("/recipes/nope")
    assert excinfo.value.code == "not_found"
    assert excinfo.value.message == "No recipe."


def test_error_body_without_structure_falls_back():
    opener = FakeOpener(errors=[_http_error(500, {})])
    with pytest.raises(ApiError) as excinfo:
        _api(opener=opener).get("/plan")
    assert excinfo.value.code == "http_error"
    assert "500" in excinfo.value.message


def test_connection_failure_maps_to_connection_code():
    opener = FakeOpener(errors=[urllib.error.URLError("no route")])
    with pytest.raises(ApiError) as excinfo:
        _api(opener=opener).get("/plan")
    assert excinfo.value.code == "connection"


# ---------------------------------------------------------------------------
# _safe: ApiError becomes a structured {error: ...} payload (never raises)
# ---------------------------------------------------------------------------

def test_safe_wraps_api_error():
    def boom():
        raise ApiError("validation", "bad input")
    assert _safe(boom) == {"error": {"code": "validation", "message": "bad input"}}


def test_safe_wraps_unexpected_error():
    def boom():
        raise ValueError("kaboom")
    result = _safe(boom)
    assert result["error"]["code"] == "internal"
    assert "ValueError" in result["error"]["message"]


# ---------------------------------------------------------------------------
# _b64 + import_files file handling
# ---------------------------------------------------------------------------

def test_b64_round_trip(tmp_path):
    f = tmp_path / "invoice.pdf"
    f.write_bytes(b"%PDF-1.4 fake")
    assert base64.b64decode(_b64(str(f))) == b"%PDF-1.4 fake"


def test_import_files_reads_and_posts(tmp_path, monkeypatch):
    from mcp_server import server
    f = tmp_path / "history.csv"
    f.write_text("date,product_name,quantity,unit_price\n2026-01-01,Milk,1,3.50\n")
    opener = FakeOpener([FakeResponse({"added": 1, "skipped": 0, "files": []})])
    monkeypatch.setenv("GROCERIES_URL", "https://groceries.example")
    monkeypatch.setenv("GROCERIES_API_KEY", "gx_test")
    monkeypatch.setattr(server, "_client", lambda: _api(opener=opener))
    result = server.import_files([str(f)])
    assert result == {"added": 1, "skipped": 0, "files": []}
    req, _ = opener.requests[0]
    body = json.loads(req.data.decode())
    assert body["files"][0]["filename"] == "history.csv"
    assert body["store"] == "Woolworths"
    assert base64.b64decode(body["files"][0]["content_base64"]).startswith(b"date,product_name")


def test_import_files_missing_path_returns_error(tmp_path, monkeypatch):
    from mcp_server import server
    monkeypatch.setenv("GROCERIES_URL", "https://groceries.example")
    monkeypatch.setenv("GROCERIES_API_KEY", "gx_test")
    result = server.import_files([str(tmp_path / "nope.csv")])
    assert result["error"]["code"] == "file_error"


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------

def test_tool_registry_matches_expectation():
    import asyncio
    names = sorted(t.name for t in asyncio.run(mcp.list_tools()))
    assert names == sorted([
        "get_server_info", "get_week_plan", "get_shopping_list", "get_pantry_status",
        "get_spend_summary", "list_purchases", "get_rules", "search_ingredients",
        "read_recipe",
        "create_ingredient", "create_recipe", "add_recipe_ingredient",
        "delete_ingredient", "delete_recipe",
        "plan_add_meal", "plan_swap_meal", "plan_remove_meal", "plan_generate",
        "tick_item", "tick_items", "add_shopping_item", "add_pantry_stock",
        "use_pantry", "link_spend_product", "import_files",
    ])


def test_list_purchases_builds_the_query(monkeypatch):
    from mcp_server import server
    opener = FakeOpener([FakeResponse({"purchases": [], "count": 0})])
    monkeypatch.setenv("GROCERIES_URL", "https://groceries.example")
    monkeypatch.setenv("GROCERIES_API_KEY", "gx_test")
    monkeypatch.setattr(server, "_client", lambda: _api(opener=opener))
    server.list_purchases(start="2026-10-01", end="2026-10-31", search="milk")
    req, _ = opener.requests[0]
    assert "/purchases?" in req.full_url
    assert "from=2026-10-01" in req.full_url
    assert "to=2026-10-31" in req.full_url
    assert "search=milk" in req.full_url
    assert "limit=50" in req.full_url
