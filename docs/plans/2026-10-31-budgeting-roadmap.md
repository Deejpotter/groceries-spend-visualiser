# Larder budgeting roadmap

- **Date:** 2026-10-31
- **Status:** In progress. Resume from "Where this stopped".

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
- Payday split, merged to `main` in PR #75 (not checked on staging first, at Deej's request).

## Where this stopped

Released to `main` on 1 Nov (PR #83): suggested savings, all spending on the Spend page and
the staging check. See `POST_MORTEM_2026-11-01_SAVINGS_RELEASE.md` for what happened and
the open items. The remaining release steps (API key secret, Coolify webhook cause) are in
`docs/plans/2026-11-01-finish-savings-release.md`.

## Next improvements

1. Done in suggested savings: warn when money is moved back out of savings.
2. Done in suggested savings: treat own-account transfers as moves.
3. Done: all spending on the Spend page (#82).
4. Done: `STE SUPERMARKET` is a grocery store and `ALDIMOBILE` is not.
5. Use Inter for Chart.js labels.
6. Decide whether to add a Larder domain and redirect the old one.

## Later ideas for automation

- Track savings balances (BankSync balances) to show progress to the emergency fund goal.
- Recalculate the suggestion each payday and flag when it changes by more than 10%.
- Use account endings to set the roles in the account table automatically.
