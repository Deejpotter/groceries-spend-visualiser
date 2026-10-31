# Larder budgeting roadmap

- **Date:** 2026-10-31
- **Status:** Paused. Resume from "Where this stopped".

## Goal

Move Larder from a grocery tracker to a household budgeting app that helps break the
save-then-spend pattern.

## How each improvement ships

1. Pick the improvement and plan it.
2. Build it with tests in `services/` first, and run the full suite.
3. Push to `dev`, then check it on staging.
4. Open a PR from `dev` to `main`, and merge once CI passes.
5. Confirm production health and row counts, then pick the next improvement.

Changes that alter or delete production data, or spend money, need Deej's OK first.

## Account roles

| Account | Intended role | Actual use |
|---|---|---|
| Smart Access xx1139 | Bills | Main account |
| NetBank Saver xx9910 | Everyday savings | Saved, then spent |
| Smart Access xx9781 | Spending | Empty |
| GoalSaver | Long term emergency savings | Unused |

## Budget plan

This is proposed and not yet set up.

1. Pay into Spending if payroll allows. Otherwise pay into Bills and sweep the rest out
   on payday.
2. On payday, automatically transfer a fixed amount to Bills and to Savings.
3. Keep GoalSaver separate as the emergency fund, even with a small amount.
4. Pay for groceries from Spending, so its balance shows what is left.

## Done

- Rename to Larder.
- Count each shop once across invoices and bank rows.
- Aurora theme, live on production.

## Where this stopped

The payday split is on `dev` but is not on production.

1. Confirm the commit reached staging. Coolify's build queue was backed up on 31 Oct,
   with `78f8103` queued behind other apps.
2. Set an income and savings amounts on staging, check the Fortnightly Budget page, then
   restore the staging settings.
3. Open the PR from `dev` to `main` and merge it.
4. Set real savings amounts in production Settings.

## Next improvements

1. Warn when transfers show savings being pulled back into spending.
2. Treat transfers between Deej's own accounts as moves, using the account roles.
3. Add spending beyond groceries to the Spend page.
4. Add `STE SUPERMARKET` to the merchant patterns and exclude `ALDIMOBILE`.
5. Use Inter for Chart.js labels.
6. Decide whether to add a Larder domain and redirect the old one.
