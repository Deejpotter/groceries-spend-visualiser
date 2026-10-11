# Groceries MCP server

Agent-friendly face of the app: the same pure services the web UI uses,
exposed over MCP (stdio). Agents and humans always see the same numbers.

## Tools

| Tool | What it returns |
|---|---|
| `get_week_plan` | Plan dates + each day's slots (meal or null) |
| `get_shopping_list` | List grouped by category, with picks, costs, prefs |
| `get_pantry_status` | Expired/expiring/low alerts + watch list |
| `get_spend_summary` | Totals, averages, top products |
| `get_rules` | Meal rules in evaluation order |
| `search_ingredients` | Ingredients, optionally filtered by name |
| `read_recipe` | One recipe with ingredients, by id or exact name |
| `plan_add_meal` | Place a recipe in a slot (replaces; continuations handled) |
| `plan_swap_meal` | Replace an entry's recipe (marks it manual) |
| `plan_remove_meal` | Delete an entry (continuations go standalone) |
| `plan_generate` | Preview counts; `confirm=true` regenerates auto entries |
| `tick_item` | Tick/untick a shopping-list row |
| `add_shopping_item` | Free-text row on the shopping list |
| `use_pantry` | Use pantry stock; reports what remains |
| `get_server_info` | Version, plan dates, table counts — orient in one call |
| `add_pantry_stock` | Add pantry stock for an ingredient |
| `tick_items` | Tick/untick many rows at once |

Writes share `services/mutations.py` with the web routes, so both faces
stay consistent. Imports stay review-gated: there is no tool for them —
stage in the UI and confirm there.

## Run it

```bash
set GROCERIES_API_KEY=<key from `flask --app app set-api-key`>
python mcp_server/server.py
```

The first run adopts the provided key; afterwards the env key must match
the stored one or the server refuses to start. Rotate with `set-api-key`.

## Register it

OpenCode (`opencode.json`, `mcp` section):

```json
"groceries": {
  "type": "local",
  "command": ["C:/Users/Deej/Repos/groceries-spend-visualiser/myenv/Scripts/python.exe",
              "C:/Users/Deej/Repos/groceries-spend-visualiser/mcp_server/server.py"],
  "environment": { "GROCERIES_API_KEY": "<key>" },
  "enabled": true,
  "timeout": 15000
}
```

Claude Code (`~/.claude.json`, `mcpServers`):

```json
"groceries": {
  "type": "stdio", "command": "C:/Users/Deej/Repos/groceries-spend-visualiser/myenv/Scripts/python.exe",
  "args": ["C:/Users/Deej/Repos/groceries-spend-visualiser/mcp_server/server.py"],
  "env": { "GROCERIES_API_KEY": "<key>" }
}
```

## Tests

`tests/test_mcp_server.py` calls the query functions directly against a
throwaway database — no running server needed.
