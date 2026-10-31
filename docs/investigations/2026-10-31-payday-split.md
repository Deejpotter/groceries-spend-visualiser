# Payday split

- **Date:** 2026-10-31
- **Investigator:** Claude Code
- **Status:** Done, shipped through PR

## 1. What was asked

Deej asked for a continuing loop of improvements. This cycle supports the fortnightly
budget plan: show how each pay should be divided between accounts.

## 2. What was checked

`services/budget.py`, the Fortnightly Budget route and page, and the budget settings
form.

## 3. What the evidence showed

The page already worked out income, groceries, bills and spare money. It did not say
how much to move anywhere, and savings had no place in the budget.

## 4. What was changed

- Added `payday_split` to `services/budget.py`.
- Added "Savings each payday" and "Long term savings each payday" to Settings.
- Added a Payday split section to the Fortnightly Budget page, which warns when the
  plan is more than the pay.

## 5. What was deliberately not changed

- No money moves. Larder only shows amounts, and transfers stay manual in CommBank.
- Savings amounts stay blank until Deej chooses them.
- Accounts are not mapped by number, because the split only needs the bucket names.

## 6. Verification

- `pytest`: 824 passed, including 6 new tests.
- The first push did not reach staging, so a docs commit was pushed to redeploy it. Staging check recorded in the PR.

## 7. Open items

- Warn when transfers show savings being pulled back into spending.

## 8. Commands to reproduce

```bash
myenv/Scripts/python.exe -m pytest tests/test_budget.py -q
```
