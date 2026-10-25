# Bills and chart hardening after release

- **Date:** 2026-10-25
- **Investigator:** OpenCode (DeepSeek V4.1 Flash)
- **Status:** Fixes implemented on `dev`, full suite green (608 passed)

## 1. What was asked

Test the newly released bill detection and charts against realistic data, then improve what testing exposed.

## 2. What was checked

- Seeded a throwaway database with six months of two-store grocery purchases and 48 realistic bank transactions: Netflix, Spotify, AGL, Telstra, AAMI, Jetts, salary, a savings transfer, Woolworths shops and sporadic Amazon purchases.
- Ran the detector directly, then the real pages in a browser at desktop and 390 px mobile widths.
- Exercised Add, Dismiss, Pause, Resume, Mark paid, theme toggle, budget line and chart rendering.
- Left the local test app and database in place only for the session, then deleted them.

## 3. What the evidence showed

### Detection defects

1. **Savings transfers were bills.** `TRANSFER TO SAVINGS` met the cadence and amount rules and appeared as a $500 monthly subscription.
2. **Annual bills were never detected.** The minimum was three sightings, so a yearly insurance payment would need three years of history. AAMI Insurance was invisible with two payments 365 days apart.
3. **A generic keyword beat the real brand.** `JETTS FITNESS 001` matched the generic `fitness` entry first and displayed as "Gym/Fitness".
4. **Repeat groceries could be added as bills.** Purchase-history suggestions (for example weekly milk) sat in the same list with the same Add button, which would create a nonsense subscription.

### UI defects

5. **Pause and Remove looked broken.** The HTMX responses are 204, and HTMX does not swap on 204, so the row stayed on screen. The Paused section and the charts were server-rendered and never refreshed.
6. **Resume and Mark paid returned 500.** `_sub_row.html` and `_inactive_row.html` call `csrf()` but did not import the macro. They only worked when included from the full page, not when rendered standalone as HTMX fragments. This was pre-existing and not covered by tests.
7. **Theme toggles left charts in the old palette.** Charts read CSS variables once at creation. Switching to light mode left dark-mode bar colours and axis text.
8. **Mobile was sound.** At 390 px the charts fit at 332 px wide and the page had no horizontal overflow.

## 4. What was changed

### Detection (`services/recurring.py`, `services/subscriptions.py`)

- Added internal-transfer patterns (`transfer to savings/offset/credit`, `between`, `own account`, card payments) while leaving a generic transfer eligible, since it can be rent.
- Two sightings are now enough when the interval lands on a year; three remains the rule for every other cadence.
- Reordered known merchants so specific brands (`Jetts`, `Anytime`, `Snap Fitness`) resolve before the generic `fitness` keyword.
- Added `MIN_OCCURRENCES`-aware tests for the yearly exception.

### Suggestions UI

- Bill suggestions and repeat grocery purchases are now separate. Groceries appear under **Often bought — not bills**, with usual price, cadence and last bought, and no Add button.
- Variable bills show **avg** before the amount, because the stored figure is the mean.
- The empty state mentions the two-sighting year rule.

### Bills page

- A successful HTMX mutation now reloads the page. Paused/active sections and both charts stay correct after pause, resume, mark paid, remove and purge.
- `_sub_row.html` and `_inactive_row.html` import the `csrf` macro, fixing the 500s.
- Both chart scripts respond to the new `gv:themechange` event: axis text, grid lines, legend text, bar colours and doughnut palettes are re-read and redrawn without a reload.

### Spend page

- Same theme handling for the monthly, store and product charts.

## 5. What was deliberately not changed

- **Fixed-amount utilities remain "subscriptions".** The badge reflects amount stability, not the merchant category; the category badge still says Utilities.
- **Next due stays last-seen-plus-one-cycle.** If that date is past, it shows as overdue so `Mark paid` can catch up.
- **No chart library change or new dependency.**
- **No per-product price history chart**; still a separate feature.
- **The purchase suggestion algorithm itself is unchanged**; only its placement changed.

## 6. Verification

- Full suite: **608 passed** (7 new tests).
- Browser checks on a seeded local app:
  - savings transfer absent; AAMI present; Jetts named correctly;
  - repeat groceries rendered separately with no Add button;
  - Pause reloads, shows Paused, removes charts when no active bills;
  - Resume and Mark paid return normally and refresh the charts;
  - theme toggle changes bar colours, axis text, grid lines and doughnut borders on both pages;
  - mobile 390 px: no horizontal overflow, charts fit.
- Temp database, seed script, screenshots and browser session removed; port 5099 free.

## 7. Open items

- Real bank data in staging is still needed to calibrate utility variance and merchant grouping.
- A detected bill whose next due is already past could be explained on the list (for example "overdue when added").
- `suggest_from_purchases` still returns items that are displayed but not actionable; consider whether it belongs on the Receipts/Spend page instead.

## 8. Commands to reproduce

```bash
myenv/Scripts/python.exe -m pytest tests/test_recurring.py tests/test_integration.py::TestSubscriptions -q
myenv/Scripts/python.exe -m pytest -q
```

Seed a database, open `/subscriptions/suggest` and `/subscriptions`, then exercise Add, Pause, Resume, Mark paid and the theme toggle.
