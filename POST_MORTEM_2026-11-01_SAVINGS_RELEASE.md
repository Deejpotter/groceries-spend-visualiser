# Post-mortem: Suggested savings, all spending and the staging check

**Date:** 31 Oct to 1 Nov 2026
**Author:** Claude Code (cloud session)
**Repo:** `groceries-spend-visualiser` (Larder)
**Scope:** Release the payday split, build savings suggestions from real income and costs,
add an automated staging check, show all spending on the Spend page, and ship it all to
production.

---

## Summary

Goal: Deej wanted Larder to work out savings levels from income and costs, with as much
automation as possible, and to test changes on staging automatically before production.

Outcome: **achieved.** `main` is at `f20a781` (PR #83) with suggested savings, all
spending on the Spend page, and the staging check. 871 tests pass.

It took **9 PRs** (#75 to #83). Along the way it hit **one real bug** that the tests could
not see, **two deploy-pipeline faults**, **one infrastructure outage**, and **two process
slips of my own**.

The headline lesson is the same as the 18 Oct post-mortem: **the tests passed every time,
and the real data still broke the feature.** Synthetic test rows had `direction = 'credit'`,
but BankSync stores money in as `'unknown'`. Only a logged-in check on staging with real
bank history showed that the suggestion never appeared.

---

## Final state

| Item | State |
|---|---|
| `main` | `f20a781`, merge of PR #83. At the time of writing (09:20 UTC) the production deploy was queued in Coolify behind other builds. Confirm with `curl https://groceries.deejpotter.com/health`: `commit` should start `f20a781`. |
| `dev` / staging | `cae9975`. Staging `/health` reports the commit. |
| Tests | 871 passing. |
| Coolify | "Include Source Commit in Build" on for staging and production. |
| Staging check | Commit and `/login` checks pass. API checks blocked on the `STAGING_API_KEY` secret. |

On staging, with real data, the suggestion detects pay of $2,037.25 a fortnight against
usual costs of $2,141.72, so it suggests no savings yet. All spending shows $4,608 a month
over the last 3 months, 26% groceries, leaving out $6,923 of moves between own accounts.

---

## Timeline (UTC)

| When | PR / event | Result |
|---|---|---|
| 31 Oct 21:22 | #75 payday split to `main` | Merged at Deej's request without a staging check (staging not reachable from the session). |
| 21:27 | #76 suggested savings to `dev` | Deployed to staging. 846 tests. |
| 22:31 | #77 staging check, `/health` commit, `GET /api/v1/budget/savings` | Merged. Its trigger (`workflow_run`) never fired. |
| 22:35 | #78 run the check on push to `dev` | Fixed the trigger. First run failed: no commit on `/health`. |
| 22:31 to 23:00 | Staging deploys | **All three failed in Coolify:** "Webhook URL host could not be resolved." |
| 23:00 | #79 clearer failure messages | Second run said staging was still on old code, which was true. |
| 1 Nov ~00:30 to ~04:00 | Cloudflare tunnel down | Staging, production and Coolify all returned error 1033. |
| 04:21 | Manual Coolify deploy via API | Succeeded. Turned on "Include Source Commit" for staging. Check passed commit and `/login`. |
| 07:44 | #80 release plan for a local agent | Merged. |
| 07:51 | #81 BankSync credits fix | Found by a logged-in test on staging: no suggestion with real data. |
| 08:38 | #82 all spending on Spend, sample descriptions | "transfer $793/pay" turned out to be payments to a person, so the numbers were right. |
| 09:12 to 09:20 | #83 `dev` to `main` | Tests and Docker green; staging check red only on the missing secret. Merged. |

---

## What went wrong

### 1. Real bank data broke a feature that tests said worked (bug, #81)

`map_banksync_transaction()` stores a negative amount as `debit` and everything else as
`unknown`, because some providers don't sign amounts. `detect_payday()` only reads
`credit` rows, so a BankSync salary was invisible. Every test built rows with
`direction = 'credit'`, so 846 tests passed while staging showed "Import bank transactions
with at least three paydays".

**Fix:** `resolve_directions()`: when a source has any `debit` rows, it signs amounts, so
its `unknown` rows are money in. The Consumer Data Right standard that BankSync uses
defines a negative amount as money out. Four new tests use BankSync-shaped rows, and all
four fail on the old code.

**Lesson:** write fixtures in the shape the production importer stores, not in the shape the
new code expects. A pure service and a green suite say nothing about the data's shape.

### 2. `workflow_run` never fired (pipeline, #78)

The staging check was triggered by `workflow_run` after CI on `dev`. GitHub only runs
`workflow_run` workflows from the default branch, which is `main`, so it could not run
until it was released. Switched to `push` on `dev`.

**Lesson:** check how a trigger is evaluated before relying on it, not just its syntax.

### 3. One message for three different failures (pipeline, #79)

The first failure said "Turn on Include Source Commit", but the same message covered
"site down", "old code" and "setting off". The second run, with separate messages, showed
the real state: staging had not deployed anything since 21:27.

**Lesson:** a check is only useful if its failure points at one cause.

### 4. Coolify deploys failed with "Webhook URL host could not be resolved" (infrastructure)

Three staging deploys failed before building. Other apps had deployed fine minutes earlier.
The cause was never confirmed. It matched a window of unstable networking on the server,
and the tunnel went down soon after. Deploys after the tunnel came back worked, including
automatic ones.

**Open:** root cause unknown. Step 3 of
`docs/plans/2026-11-01-finish-savings-release.md` lists what to check.

### 5. Cloudflare tunnel outage (infrastructure)

Error 1033 on every host behind the tunnel for about three hours, including production.
Nothing in the repo caused it. Deej restarted it.

### 6. Slow Coolify build queue

Coolify built one app at a time, with up to 8 deploys queued, so each staging deploy took
20 to 40 minutes. Two of my deploy watchers timed out before the deploy landed, and one
check hit a 502 while the new container was starting.

**Lesson:** waits on this server need 45 minutes or more, and a check should retry once on
502 just after a deploy.

### 7. My process slips

- **#75 went to `main` without a staging check.** Deej asked for it, and staging was not
  reachable from the session then. It worked out, but it is the kind of release the staging
  check now exists to prevent.
- **I pushed to an extra branch** (`claude/top-spending-examples`) instead of the session's
  branch. I moved the commit across, but the proxy refused to delete the stray branch, so it
  is still on GitHub.
- **I asked for permission to merge to `dev` after Deej had already given it.** Deej
  corrected this: once CI is green, merge to `dev`; once staging is verified, merge to
  `main`.

---

## What went well

- **The logged-in staging test** (using the admin login stored in Coolify, never printed)
  caught bug 1 before production, and answered the "transfer" question with real
  descriptions instead of guesses.
- **Coolify API access** turned a blind wait into facts: deploy history, failure logs, the
  build queue, and settings changed without the UI.
- **Showing the latest description for top spending** settled a judgement call
  ($793/pay of transfers) with data: they are payments to a person, so the suggestion was
  correct to count them.
- **Every change came with tests that fail on the old code** where it was a fix, and the
  full suite ran before each push.
- **Sources were checked before relying on them:** CDR amount signs, Coolify
  `SOURCE_COMMIT`, the meaning of Cloudflare 1033, emergency-fund and 20% guidance.

---

## What shipped

| Feature | Where |
|---|---|
| Payday split | Fortnightly Budget |
| Suggested savings: pay, costs, buffer, long term and everyday savings, emergency fund goal, 20% benchmark, money pulled back from savings, one-click apply | Fortnightly Budget, `services/savings_advice.py`, `GET /api/v1/budget/savings` |
| Savings account endings setting | Settings |
| BankSync money in read as credits | `services/bank_import.py` `resolve_directions()` |
| All spending by category, month and merchant | Spend, `services/bank_spending.py` |
| `/health` reports the deployed commit | `app.py`, `routes/api.py`, `Dockerfile` |
| Staging check | `scripts/staging_check.py`, `.github/workflows/staging-check.yml` |
| Release plan for a local agent | `docs/plans/2026-11-01-finish-savings-release.md` |

---

## Open items

1. **Add the `STAGING_API_KEY` repository secret.** Until then the staging check can't go
   green. See step 2 of the release plan.
2. **Find the Coolify "Webhook URL host could not be resolved" cause.** See step 3 of the
   release plan.
3. **Delete the stray branch** `claude/top-spending-examples`.
4. **Decide about the Kelly transfers.** About $793 a fortnight counts as other spending.
   If part is a household contribution, Larder could let a payee be labelled as a bill or
   a shared cost.
5. **Merchant names:** grouping by the first word gives keys like `u` (U GO Carrum Downs)
   and `transfer`. Use two words, or a display name, before relying on them.
6. **Bump GitHub Actions** (`checkout` v5+, `setup-python` v6) to clear the Node 20 warning.
7. **Set real savings amounts** on production once the numbers are agreed.
8. **Roadmap:** suggested savings and all spending are done. Next is `STE SUPERMARKET` and
   `ALDIMOBILE` in the merchant patterns, then savings balances from BankSync.
