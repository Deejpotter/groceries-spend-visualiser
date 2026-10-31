# Rename the app to Larder

- **Date:** 2026-10-31
- **Investigator:** Claude Code
- **Status:** Visible rename done; infrastructure names deliberately unchanged

## 1. What was asked

Deej said the app is now more of a general budgeting app than a grocery tool, and chose
the name Larder.

## 2. What was checked

The old name appeared 93 times across 50 files, split between display text and
identifiers tied to deployed data.

## 3. What the evidence showed

Display text is safe to change. The database path, the volume and the environment
variables are not: AGENTS.md records that a wrong volume setup silently starts the app
with an empty database.

## 4. What was changed

- Added `APP_NAME` in `models.py` and passed it to every template as `app_name`.
- Templates use `{{ app_name }}` instead of hard-coded text.
- Updated docstrings, README, `.env.example`, the MCP README and the CI page check.
- Added `test_pages_show_app_name`.

## 5. What was deliberately not changed

- The repo name, `groceries.db`, the `grocery-data` volume, `GROCERIES_URL`,
  `GROCERIES_API_KEY`, the `groceries` MCP server name and the domains. Renaming them
  risks data loss or breaks clients, and most are not visible to the user.
- The `grocery-visualiser` container, process and CI image names, for the same reason.
- The "Grocery Spend" page heading, because that page still only measures groceries.
- The cart icon in the navbar, which is a design choice for later.

## 6. Verification

- Before rebasing onto newer `dev` commits: 690 passed.
- After rebasing: 799 passed, 5 failed, all in `tests/test_suggestions_routes.py::TestCycleList`
  with `ValueError: Invalid format string`.
- The same 5 fail on `dd4d6ce`, the commit before the rename, so the rename did not
  cause them. That baseline run also failed 2 AI-key tests, which pass in the main checkout.
- The rename was pushed before these failures were read, because the test command's exit
  code was hidden by a pipe to `tail`.
- Staging `/login` shows `<title>Login — Larder</title>` and `/health` returns 200.
  Logged-in pages were not checked on staging; the test client covers the dashboard.

## 7. Open items

- Decide whether to add a new domain and redirect the old one.
- Fixed the same day: `short_date` in `models.py` replaces the Linux-only `%-d` format, and all tests pass on Windows.

## 8. Commands to reproduce

```bash
grep -rn "Grocery Visualiser" templates app.py models.py
myenv/Scripts/python.exe -m pytest -q
```
