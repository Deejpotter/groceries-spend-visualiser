# Post-mortem: Agent CRUD for ingredients & recipes (API + MCP)

**Date:** 18 Oct 2026
**Author:** OpenCode agent
**Repo:** `groceries-spend-visualiser`
**Scope:** Making the app easy for AI agents to populate via the API/MCP, and seeding production with real ingredients and recipes.

---

## Summary

Goal: get the user's ingredients and recipes into the app. The app is meant to be "really easy for AI agents to use", so the intended path was the **MCP server**, not scripts or the UI.

Outcome: **achieved** — production now holds 33 ingredients and 14 recipes (45 links), all created through the MCP write tools. But it took **7 PRs** and exposed **four distinct bugs**, three of which I had introduced or failed to catch in the same session.

The headline lesson: **every failure was a contract mismatch that a single end-to-end check against the real target would have caught.** I repeatedly verified against the wrong environment (local DB instead of production, tests with CSRF disabled instead of enabled, MCP tool signatures instead of the API route it calls).

---

## Final state (verified live)

```
GET /api/v1/server-info → ingredients: 33, recipes: 14, purchases: 3304
484 tests passing
```

All 14 recipes linked to ingredients (e.g. Beef Burgers → 0.5 kg mince, 4 cheese slices, 4 bread, 0.5 onion).

---

## Timeline

| # | Change | Result |
|---|--------|--------|
| #44 | Seed script (`scripts/seed_data.py`) + local DB seed | Seeded **local only** — production untouched. Wrong target. |
| #45 | `POST /ingredients`, `POST /recipes`, `POST /recipes/<id>/ingredients` | Endpoints added. |
| #46 | CSRF exemption via `request.csrf_exempt = True` | **Broken.** Not a Flask-WTF hook. Merged anyway. |
| #47 | MCP write tools (`create_ingredient`, `create_recipe`, `add_recipe_ingredient`) | Tools added — but pointed at a broken API. |
| #48 | Correct fix: `csrf.exempt(api_bp)`; default `covers_days` | API POSTs finally work. |
| #49 | Accept recipe **name or id** in `POST /recipes/<ref>/ingredients` | MCP linking works. |
| #50 | `delete_ingredient`, `delete_recipe` (cascade) | Full CRUD lifecycle. |
| #51 | Docs: full `/api/v1` surface | — |

---

## Root causes

### 1. Seeded the wrong database (PR #44)
I ran the seed against `./data/groceries.db` (local) and reported success. The user was looking at `groceries.deejpotter.com` (production), which is a separate SQLite file on a separate volume.

**Cause:** assumed "done" meant "done everywhere". The app is deployed; local ≠ deployed.

### 2. CSRF exemption that did nothing (PR #46)
`request.csrf_exempt = True` in a `before_request` hook is not how Flask-WTF works. Flask-WTF checks `app.blueprints[request.blueprint] in csrf._exempt_blueprints`. The correct call is `csrf.exempt(api_bp)`.

**Cause:** guessed at an API instead of reading it. **Worse:** the test suite passed, because `create_app(testing=True)` sets `WTF_CSRF_ENABLED = False` — so the tests could never catch it. I merged a "fix" with no test that exercised the failure mode.

### 3. `covers_days` NOT NULL (PR #48)
`POST /recipes` passed `None` for `covers_days`. The column is `INTEGER NOT NULL DEFAULT 1` — the default only applies when the column is **omitted**, not when `NULL` is supplied. Result: `IntegrityError` → HTTP 500.

**Cause:** assumed a DEFAULT would cover an explicit `None`.

### 4. MCP sends a name, API expected an int (PR #49)
The MCP tool posted to `/recipes/<name>/ingredients`, but the route was `/recipes/<int:recipe_id>/ingredients`. Every one of 45 link calls returned **404**.

**Cause:** the MCP tool and the API route were written in different PRs against different assumptions and never checked against each other.

---

## What went well

- **Correct diagnosis once I actually tested end-to-end.** Writing a local script that enabled CSRF and hit the real endpoints surfaced bugs #2–#4 immediately.
- **Every bug got a regression test.** Notably `test_api_post_works_with_csrf_enabled`, which flips CSRF back on — the exact condition the old suite missed.
- **The MCP gap is genuinely closed.** Before: read-only for ingredients/recipes. Now: create, read, link, delete. Agents can manage the whole lifecycle without a script.
- **Auto-deploy on merge to `main` works.** The earlier "it hasn't deployed" was actually "the code was broken", not a deployment problem.
- **Honest correction.** When the user pushed back that the MCP should be the easy path, I dropped the script approach and fixed the MCP instead.

---

## What went badly

- **Declared success three times before it was true** (local seed, PR #46, PR #47). Each time the evidence was "tests pass", which was the wrong evidence.
- **Merged a fix I hadn't verified in the failing condition** (PR #46). The CI was green because CI also runs with CSRF off.
- **Left a test row in production** (`ZZ Test Delete Me`) and had to clean it up — minor, but sloppy.
- **Burned several deploy cycles** chasing bugs one at a time that one careful check would have found together.

---

## Lessons

1. **Verify against the real target.** Local DB ≠ production DB. Tests ≠ production config. Check the thing the user will look at.
2. **When a test passes but the feature fails, the test is wrong.** The CSRF bug survived a green suite; that should have been the signal to question the suite, not to trust it.
3. **Read the library, don't guess it.** `request.csrf_exempt` looked plausible and was wrong. Two minutes in the Flask-WTF source would have saved a PR.
4. **Treat MCP tool ↔ API endpoint as a contract.** Write both sides together, or test that the path the tool builds actually resolves.
5. **A DEFAULT does not cover an explicit NULL.**
6. **Don't say "done" until the user's screen shows it.**

---

## Action items

- [x] Regression test with CSRF **enabled** for API routes (PR #48)
- [x] Delete tools so agents can clean up their own mistakes (PR #50)
- [x] Document the full `/api/v1` write surface (PR #51)
- [ ] Consider a contract test asserting every MCP tool path resolves against the API route table (future)
- [ ] Consider a `make verify-prod` style smoke check that hits the deployed API before declaring a data task done (future)

---

## References

- PRs #44–#51 in `Deejpotter/groceries-spend-visualiser`
- `routes/api.py` — API surface, `_resolve_recipe_id`, `_resolve_ingredient_id`
- `mcp_server/server.py` — MCP tools
- `architecture.md` — "HTTP API auth" section
- `tests/test_api.py` — `test_api_post_works_with_csrf_enabled`
