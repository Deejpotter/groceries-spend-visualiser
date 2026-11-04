# Bills page: show only what's due before payday

- **Date:** 2026-11-05
- **Investigator:** Claude Code (for Deej)
- **Status:** Done (shipped dev → main: #116, #117)

## 1. What was asked

Deej gets paid on Monday 9 Nov 2026. The Bills page listed every bill, including ones
due after that payday. They want to see only what needs paying before the next pay.

## 2. What was checked

- `routes/subscriptions.py` `subscription_list()` listed every active bill grouped by
  category. Nothing on the page used the pay cycle.
- The pay cycle already exists: `current_pay_cycle()` in `services/suggestions.py`
  (settings `payday_date` + `pay_frequency`) is used by the meal plan, shopping list and
  spend pages.
- The "Fortnightly view" uses fixed Monday-based fortnights, not paydays, so it doesn't
  answer "what's due before I'm paid".

## 3. What the evidence showed

- The screenshot shows bills due after payday (e.g. 19 Nov) mixed in with ones due
  before it, and several with a `next_due` months in the past (Feb, Apr, Oct 2026).
  Those were never marked paid, so their date never advanced.

## 4. What was changed

- `services/subscriptions.py`: `split_by_payday(subscriptions, today, next_payday)`
  returns `due` (next_due before payday, overdue included, plus recurring spending
  with no fixed date), `later`, `due_total` and `overdue_count`.
- `routes/subscriptions.py`: when payday is set, the list shows only the due bills
  by default; `?show=all` shows everything. Without a payday setting nothing changes.
- `templates/subscriptions/list.html`: a "Due before payday" card with the total, the
  overdue count, and a toggle ("Show all bills (N due after payday)").
- `templates/subscriptions/_sub_row.html`: an "overdue" badge on the date.
- Tests: `TestSplitByPayday` in `tests/test_subscriptions.py`; two page tests in
  `tests/test_integration.py`.

## 5. What was deliberately not changed

- The KPI tiles and charts still cover every bill. They're monthly and 8-week views,
  and hiding later bills there would understate them.
- Overdue bills are shown, not hidden. They may already be paid (the date never
  advanced) or genuinely unpaid; only Deej can tell, and **Mark paid** fixes the date.
- The fortnightly view was left on calendar fortnights.

## 6. Verification

- Full suite: 982 passed. CI and staging check green on `680836f`.
- Staging pages weren't checked: Chrome isn't signed in there and no credentials were
  entered.
- Production (`066d539`): `payday_date` was empty, so the new view would have fallen
  back to all bills. Set it to 2026-11-09 (Deej: "I get paid this monday"), frequency
  left at fortnightly; budget and income values unchanged after saving. The Bills page
  then showed the card: 20 bills due before Mon 9 Nov ($790.50), 15 overdue, and
  "Show all bills (6 due after payday)". Screenshot checked.

## 7. Open items

- 15 of the 20 due bills are overdue only because `next_due` never advanced. Most are
  bank auto-debits (Belong, Spotify, Microsoft) that have probably been paid, which
  inflates the due total. Next step: auto-advance a bill when a matching bank payment
  is seen on or after its due date.

## 8. Commands to reproduce

```bash
python -m pytest tests/test_subscriptions.py -k Payday
python -m pytest tests/test_integration.py -k payday
```
