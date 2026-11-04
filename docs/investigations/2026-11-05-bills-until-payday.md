# Bills page: show only what's due before payday

- **Date:** 2026-11-05
- **Investigator:** Claude Code (for Deej)
- **Status:** In progress

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

- Full suite: 982 passed.

## 7. Open items

- None yet.

## 8. Commands to reproduce

```bash
python -m pytest tests/test_subscriptions.py -k Payday
python -m pytest tests/test_integration.py -k payday
```
