# Groceries MCP server

Agent-friendly face of the Grocery Visualiser app. It is a **thin stdio
client over the app's HTTP API** (`/api/v1`): the server itself holds no
database connection and no business logic, so the same binary works
against a local dev instance, staging or production by changing
`GROCERIES_URL`.

## Setup

1. Get an API key: `flask --app app set-api-key` (prints the key once —
   store it in your secrets store, e.g. `krasus/secrets/`).
2. On the **remote** app, the key must exist in its database. For a
   Coolify container: `docker exec <container> flask --app app set-api-key`.
3. Point the server at the app:

```sh
export GROCERIES_URL="https://groceries-staging.deejpotter.com"
export GROCERIES_API_KEY="gx_..."
python mcp_server/server.py
```

Dependencies: the `mcp` package (`pip install mcp`) — stdlib handles the
rest (urllib, json, base64).

## Client config

OpenClaw / Claude Code / Claude Desktop (`mcpServers`), and Hermes
(`config.yaml` → `mcp_servers`) all share the same shape:

```json
{
  "groceries": {
    "command": "python",
    "args": ["C:/Users/Deej/Repos/groceries-spend-visualiser/mcp_server/server.py"],
    "env": {
      "GROCERIES_URL": "https://groceries-staging.deejpotter.com",
      "GROCERIES_API_KEY": "gx_..."
    }
  }
}
```

Auth is a bearer key: the app is single-admin, so there is one shared key
(no per-user tokens). Rotate it with `flask set-api-key` and update every
client that uses it.

## Tools

| Tool | Endpoint | What it does |
|------|----------|--------------|
| `get_server_info` | `GET /server-info` | Version, plan dates, table counts — orient first |
| `get_week_plan` | `GET /plan` | Plan days with each slot's meal |
| `get_shopping_list` | `GET /shopping-list` | List by category, picks, costs |
| `get_pantry_status` | `GET /pantry` | Expired/expiring/low alerts |
| `get_spend_summary` | `GET /spend/summary` | Totals, averages, top products |
| `get_rules` | `GET /rules` | Meal rules in order |
| `search_ingredients` | `GET /ingredients` | Filter by name |
| `read_recipe` | `GET /recipes/<ref>` | One recipe with ingredients (id or name) |
| `plan_add_meal` | `POST /plan/meals` | Place a recipe in a slot |
| `plan_swap_meal` | `POST /plan/entries/<id>/swap` | Replace an entry's recipe |
| `plan_remove_meal` | `DELETE /plan/entries/<id>` | Remove an entry |
| `plan_generate` | `POST /plan/generate` | Preview counts; `confirm=true` regenerates |
| `tick_item` / `tick_items` | `POST /shopping-list/tick` | Tick rows (one or many) |
| `add_shopping_item` | `POST /shopping-list/items` | Free-text list row |
| `add_pantry_stock` | `POST /pantry/stock` | Add pantry stock (id or name) |
| `use_pantry` | `POST /pantry/items/<id>/use` | Consume stock; reports remainder |
| `link_spend_product` | `POST /spend/links` | Link product to ingredient (`new` / `""`) |
| `import_files` | `POST /import` | Import local CSV/PDF files (auto-commit) |

Imports are **auto-commit**: the API parses, dedupes (by store + basket +
product) and applies the skip rules (unavailable and free items are
skipped), so re-running an import is safe. Per-file `added`/`skipped`
counts and warnings come back in the result.

Errors return `{"error": {"code", "message"}}` — codes `unauthorized`,
`not_found`, `validation`, `no_plan_dates`, `connection`, `internal`.
The server never raises on tool failure; the shape is always returned.

## Tests

```sh
python -m pytest tests/test_mcp_server.py
```

Covers request shaping, bearer auth, error mapping, the `import_files`
file path, and the tool registry. The app-side logic behind the API is
covered by `tests/test_api.py`.
