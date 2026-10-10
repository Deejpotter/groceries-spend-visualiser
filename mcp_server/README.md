# Groceries MCP server

Agent-friendly face of the app: the same pure services the web UI uses,
exposed over MCP (stdio). Agents and humans always see the same numbers.

## Tools (read-only for now; writes are a later slice)

| Tool | What it returns |
|---|---|
| `get_week_plan` | Plan dates + each day's slots (meal or null) |
| `get_shopping_list` | List grouped by category, with picks, costs, prefs |
| `get_pantry_status` | Expired/expiring/low alerts + watch list |
| `get_spend_summary` | Totals, averages, top products |
| `get_rules` | Meal rules in evaluation order |
| `search_ingredients` | Ingredients, optionally filtered by name |
| `read_recipe` | One recipe with ingredients, by id or exact name |

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
