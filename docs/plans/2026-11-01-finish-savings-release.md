# Finish the suggested savings release

- **Date:** 2026-11-01
- **For:** a local agent on Deej's machine, with `gh`, a clone of this repo, and access to
  the Coolify UI or API.
- **Goal:** get suggested savings and the staging check from `dev` onto production, safely,
  and leave the deploy pipeline in a state where every future release is checked
  automatically.

Read `AGENTS.md` first. Run commands from the repo root. Never paste secret values into
commits, PRs, issues or chat.

## 1. Where things stand

| Item | State |
|---|---|
| `dev` | `00016b8`. 4 commits ahead of `main`, listed below. |
| `main` / production | `6613ffb` (payday split, PR #75). Production `/health` has no `commit` field yet. |
| Staging | Running `00016b8`. `/health` returns `{"commit": "00016b8…", "status": "ok"}`. |
| Staging check | Last run [41273305749](https://github.com/Deejpotter/groceries-spend-visualiser/actions/runs/41273305749): commit and `/login` passed, then stopped on `STAGING_API_KEY is not set`. |
| Tests | 861 pass locally and in CI. |

Commits on `dev` that are not on `main`:

1. `4de13a7` feat(budget): suggest savings amounts from bank history (PR #76)
2. `ab7d00e` ci: check staging runs the right commit and works before main (PR #77)
3. `c432762` ci: run the staging check on pushes to dev (PR #78)
4. `66ab919` fix(ci): say why staging has not reached the expected commit (PR #79)

Coolify (v4.5.3) applications:

| App | UUID | Branch | Include Source Commit in Build |
|---|---|---|---|
| `groceries-spend-visualiser-staging` | `rnn2vq0pgdw6xidwf3iaxppn` | `dev` | On (set 2026-11-01) |
| `groceries-spend-visualiser` (production) | `shzajzgrzju2eupzsks1zatm` | `main` | **Off** |

### What happened on 31 Oct (so it isn't repeated)

- Suggested savings deployed to staging at 21:27 UTC (`fd802c9`).
- The next three staging deploys (22:31, 22:35, 23:00) failed in Coolify before building,
  each with `Deployment failed: Webhook URL host could not be resolved.` Production
  (21:22) and Day Planner Staging (21:34) had deployed fine just before.
- Between about 00:30 and 04:00 UTC on 1 Nov, the Cloudflare tunnel was down. Staging,
  production and Coolify all returned Cloudflare error 1033.
- After the tunnel came back, a manual deploy through the Coolify API
  (`POST /api/v1/deploy`) succeeded at 04:21 UTC.
- The cause of the webhook error is unknown. See step 3.

## 2. Set the staging API key (blocking)

The staging check calls the read-only API with the `STAGING_API_KEY` repository secret.
It currently arrives empty.

1. Find out whether staging already has a key. If the MCP server or another tool already
   points at staging with a key, reuse that value. `set-api-key` replaces the key and
   breaks anything using the old one.
2. If there is no key, open the staging app in Coolify, go to Terminal, and run:

   ```bash
   flask --app app set-api-key
   ```

   It prints the new key once.
3. Save it as a **repository** secret (not an environment secret, not a variable):

   ```bash
   gh secret set STAGING_API_KEY --repo Deejpotter/groceries-spend-visualiser
   ```

   Paste the key when prompted.
4. Confirm the secret exists. Only names are shown:

   ```bash
   gh secret list --repo Deejpotter/groceries-spend-visualiser
   ```

**Done when:** `STAGING_API_KEY` appears in the list.

## 3. Find the cause of the Coolify webhook error

The next push to `dev` will fail the same way if the cause is still there. Check in this
order and stop at the first finding:

1. In Coolify, go to Notifications and then Webhook, for the team and the staging app. If
   a webhook URL is set, check its host resolves (`getent hosts <host>` on the server). If
   it was added by mistake, remove it. If it is wanted, fix the URL.
2. On the Coolify server, check DNS:

   ```bash
   getent hosts github.com
   ```

   ```bash
   curl -sI https://github.com
   ```

   ```bash
   cat /etc/resolv.conf
   ```

3. In the staging app's settings in Coolify, check for a deploy webhook or a "manual
   webhook" URL that points at a host that no longer exists.
4. Prove it with a no-op push: make a docs-only commit to `dev` (for example, tick an item
   in this plan) and confirm the Coolify deploy finishes. The API equivalent:

   ```bash
   curl -sS -H "Authorization: Bearer $COOLIFY_API_TOKEN" "$COOLIFY_URL/api/v1/deployments/applications/rnn2vq0pgdw6xidwf3iaxppn?take=1"
   ```

   The latest deployment should be `finished`, not `failed`.

Note: the Coolify API returns webhook secrets in app details. Don't copy those into logs or
docs.

**Done when:** a push to `dev` deploys to staging by itself, with no manual redeploy.

## 4. Run the staging check until it is green

```bash
gh workflow run staging-check.yml --ref dev --repo Deejpotter/groceries-spend-visualiser
```

```bash
gh run watch --repo Deejpotter/groceries-spend-visualiser $(gh run list --workflow staging-check.yml --repo Deejpotter/groceries-spend-visualiser --limit 1 --json databaseId -q '.[0].databaseId')
```

If it fails, the last lines of the log name the cause:

| Message | Fix |
|---|---|
| `/health returned 530 …` or `down or unreachable` | Cloudflare tunnel or server is down. Restart `cloudflared` and check the Zero Trust tunnel is Healthy. |
| `no commit field … from before the staging check` | Staging didn't deploy the latest `dev`. Read the Coolify deploy log (step 3). |
| `runs the new code but /health reports no commit` | "Include Source Commit in Build" is off for staging. Turn it on and redeploy. |
| `staging runs X, expected Y` | Deploy still in progress or failed. Check Coolify. |
| `STAGING_API_KEY is not set` | Step 2. |
| `/api/v1/... returned 401` | Secret doesn't match the key in the staging database. Redo step 2. |
| `/api/v1/... returned 500` | Real bug. Read the staging container logs, reproduce with a test, fix on a branch, PR to `dev`. |
| `suggested savings are more than the surplus` (or another savings problem) | Real bug in `services/savings_advice.py`. Add a failing test with the same numbers first. |

**Done when:** the run is green.

## 5. Check suggested savings by hand on staging

The check proves the page and API work, not that the numbers make sense for Deej's data.

1. Log in to staging. In Settings, under Budget, set **Savings account endings** to `9910`
   and save.
2. Open **Bills, then Fortnightly Budget**. In **Suggested savings**, check:
   - **Pay** matches the real fortnightly salary.
   - **Usual costs** look right: bills, groceries and other spending. Open "How this is
     worked out" and read the biggest other spending. Transfers between Deej's own accounts
     must not appear there.
   - "moved back out of savings" appears if there were transfers out of `xx9910`.
   - "Based on only N days" does not appear if there are 4+ weeks of bank data.
3. Compare with the API:

   ```bash
   curl -sS -H "Authorization: Bearer <staging key>" https://groceries-staging.deejpotter.com/api/v1/budget/savings
   ```

4. Don't press **Use these amounts** on staging unless Deej wants staging settings changed.
   It writes `savings_per_pay`, `long_term_per_pay` and possibly `fortnightly_income`.

If a number looks wrong, write down the input (anonymised) and the expected result, add a
failing test in `tests/test_savings_advice.py`, fix `services/savings_advice.py`, and PR
to `dev`. Known limits that are **not** bugs: credit card repayments count as moves, and
there are no balances (see `docs/investigations/2026-10-31-suggested-savings.md`).

**Done when:** Deej agrees the numbers look right, or the fixes are merged to `dev` and
steps 4 to 5 pass again.

## 6. Prepare production

1. Turn on "Include Source Commit in Build" for the production app, either in the Coolify
   UI (Advanced) or through the API:

   ```bash
   curl -sS -X PATCH -H "Authorization: Bearer $COOLIFY_API_TOKEN" -H "Content-Type: application/json" -d '{"include_source_commit_in_build": true}' "$COOLIFY_URL/api/v1/applications/shzajzgrzju2eupzsks1zatm"
   ```

   Without it, production `/health` reports `"commit": null` and its deploy can't be
   checked.
2. Back up the production database before the release (Coolify Terminal on the production
   app):

   ```bash
   flask --app app backup-db
   ```

   ```bash
   flask --app app db-status
   ```

   Save the row counts from `db-status` for step 7.

## 7. Release to production

1. Open the PR from `dev` to `main`:

   ```bash
   gh pr create --repo Deejpotter/groceries-spend-visualiser --base main --head dev --title "Suggest savings amounts and check staging before release" --body-file <file>
   ```

   The body should list the four commits from section 1, the verification (staging check
   run link, the manual check in step 5) and that there are no schema changes. New settings
   keys (`savings_accounts`) are created on first save.
2. Wait for CI (`test`, `docker`) and the **Staging check** on the PR. The PR trigger
   checks staging runs the PR's head commit. All must be green. Don't merge on red.
3. Merge only with Deej's go-ahead (a merge commit, like earlier releases).
4. After Coolify deploys production, check:

   ```bash
   curl -sS https://groceries.deejpotter.com/health
   ```

   `commit` must equal the merge commit on `main` (`git rev-parse origin/main`).
5. Run `flask --app app db-status` again on production. Row counts must be the same or
   higher than in step 6.
6. Open the production Fortnightly Budget page and check that Suggested savings renders.

**Done when:** production reports the new commit, row counts are intact and the page works.

## 8. Set the real savings amounts (Deej's call)

1. Set **Savings account endings** to `9910` in production Settings.
2. Review the suggestion on the Fortnightly Budget page with Deej.
3. Press **Use these amounts** only after Deej agrees, or have Deej enter different
   amounts in Settings.
4. Set up the matching automatic transfers in CommBank on payday (from the budget plan in
   `docs/plans/2026-10-31-budgeting-roadmap.md`). Larder doesn't move money.

## 9. Tidy-ups (small PRs to `dev`, after the release)

1. **Node 20 warning in Actions.** Bump `actions/checkout@v4` to v5 or later and
   `actions/setup-python@v5` to v6 in `ci.yml` and `staging-check.yml`. Both newer majors
   run on Node 24. Confirm the current majors on their release pages first.
2. **AGENTS.md** says "~800 tests". Update it to the current count.
3. **Production check (optional):** add a `push: branches: [main]` job to
   `staging-check.yml`, or a second workflow, that runs `scripts/staging_check.py` against
   `https://groceries.deejpotter.com` with the expected commit and a `PRODUCTION_API_KEY`
   secret. This makes step 7.4 automatic.
4. **Roadmap.** Update `docs/plans/2026-10-31-budgeting-roadmap.md`: move suggested savings
   to Done, record the webhook root cause, and set "Where this stopped" to the next
   improvement.
5. **Day Planner.** Add a short note pointing at this plan and the roadmap. Day Planner is
   the shared knowledge base.

## 10. Next improvements (from the roadmap, in order)

1. Add spending beyond groceries to the Spend page. Reuse `cash_flow()` from
   `services/savings_advice.py` so the totals match the savings suggestion.
2. Add `STE SUPERMARKET` to the grocery merchant patterns and exclude `ALDIMOBILE`
   (`services/bank_import.py`, `DEFAULT_MERCHANT_PATTERNS`).
3. Track savings balances from BankSync to show progress to the emergency fund goal.
4. Recalculate the suggestion each payday and flag changes over 10%.
5. Use Inter for Chart.js labels.
6. Decide on a Larder domain and redirect the old one.

Each one ships the same way: tests in `services/` first, the full suite green, PR to `dev`,
staging check green, then a PR to `main` with Deej's go-ahead.

## Rules for the agent

- Business logic goes in `services/`. Routes only load, call and render.
- Run `python -m pytest -q` before every push. All tests must pass.
- Never merge to `main` without Deej's explicit go-ahead, and never while the staging check
  is red.
- Ask before anything that changes or deletes production data, or spends money.
- Don't commit secrets. Coolify API responses contain webhook secrets.
- Commit style: Conventional Commits with a body listing the changes.
