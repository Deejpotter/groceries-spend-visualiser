# Beginner-friendly charts for spend and bills

- **Date:** 2026-10-25
- **Investigator:** OpenCode (DeepSeek V4.1 Flash)
- **Status:** Implemented on `dev`, deployed to staging (`faf17d6`), PR #62 open

## 1. What was asked

Graphs and other visualisations of the household's data, pitched at a beginner.

## 2. What was checked

- `templates/dashboard.html` — home KPI tiles, spend sparkline and monthly line chart
- `templates/spend/dashboard.html` — monthly bar chart, category progress bars, product tables
- `templates/subscriptions/list.html` — bill list with no visual summary
- `services/spend_analysis.py` — what `analyze_purchases()` already returns
- `services/subscriptions.py` — schedule helpers, no cost comparison helpers
- `services/budget.py` — monthly target and pace
- `static/css/style.css` — chart box, KPI tiles, theme variables
- Tests for spend, subscriptions, integration and smoke pages

## 3. What the evidence showed

1. The home page already had a monthly spend line chart and a sparkline; the Spend
   page had a monthly bar chart. Both were unlabelled beyond a title.
2. **Store was never aggregated.** The data was there (`purchases.store`) but the
   only breakdowns were by category and by product.
3. **Products had tables but no chart.** The "Highest total spend" table was a
   wall of numbers for a beginner.
4. **Subscriptions had no numbers beyond the list.** No monthly-equivalent cost,
   no category split, and no view of which weeks are expensive.
5. Category bars existed but carried no explanation of why most spend can sit in
   "Not linked yet".

## 4. What was changed

### Analytics (pure Python)

- `analyze_purchases()` now returns `store_spend`: total and percentage share per
  store, largest first.
- `services/subscriptions.py` gained `monthly_cost()`,
  `monthly_cost_total()`, `monthly_cost_by_category()` and `upcoming_weeks()`.
  Weekly, fortnightly, quarterly and yearly bills are converted to a monthly
  figure so they can be compared on one chart.

### Spend page

- **Spending over time** — existing bar chart, now with a dashed monthly target
  line when a budget is set, a plain-language caption and a "How to read this"
  disclosure.
- **Where your money goes** — new doughnut by store, shown only when there is
  more than one store; otherwise the card is omitted rather than showing a
  pointless single slice.
- **Products that cost the most** — new horizontal bar chart of the top six,
  with the existing tables kept for exact numbers.
- Added a short "start with the tiles" sentence and an explanation on the
  category chart.
- Product names are truncated on the axis; full names appear in the tooltip.

### Bills page

- **Bills per month** and **biggest monthly cost** KPI tiles.
- **Where your bill money goes** — doughnut by category, monthly-equivalent.
- **Bills in the next 8 weeks** — one bar per week, with bill names in the
  tooltip.
- Both charts explain annual-to-monthly conversion and how to spot expensive
  weeks.

All charts use the existing CSS theme variables, the same Chart.js version and
SRI hash, and carry `aria-label` text with an adjacent written summary.

## 5. What was deliberately not changed

- **No new charting dependency.** Chart.js was already used; the CDN tag and SRI
  are unchanged.
- **Home dashboard stays as-is.** It already has KPI tiles, a spend line and a
  sparkline; adding more there would crowd it.
- **Product tables stay.** The charts give shape; the tables give exact values.
- **No per-product price-history chart.** It would need a product picker and a
  unit-conversion decision, which is a separate feature.
- **No new API fields beyond `store_spend`.** The API response builder only
  exposes selected fields, so existing API clients are unaffected.

## 6. Verification

- Full suite: **601 passed** (`python -m pytest -q`).
- New unit tests: store split, monthly conversion for all five cadences, category
  shares, upcoming-week bucketing, inactive filtering.
- New integration tests: spend page renders the product chart and explanations,
  budget line appears when a target is set, a second store renders the doughnut;
  Bills page renders both charts and the monthly KPI.
- Visual check: seeded a throwaway SQLite database with six months of multi-store
  purchases and six bills, ran the app on port 5099, logged in and inspected both
  pages in a headless browser. The monthly chart, doughnut and product bars all
  rendered with correct values; the budget line sat at $200 as expected.
  Full-page screenshots in headless Chromium showed partially painted canvases,
  so the canvases were read directly via `toDataURL()` to confirm the rendering —
  the page capture was the artifact, not the app.
- Environment cleaned up: temp database, seed script, screenshots and browser
  session removed; port 5099 confirmed free.
- Staging deploy (`faf17d6`) verified with an authenticated session: `/spend`
  rendered the monthly, product and store charts and the budget line;
  `/subscriptions` was empty, so a clearly named temporary bill was added,
  both bill charts and the monthly KPI were confirmed, then the bill was purged
  and the page returned to its empty state.

## 7. Open items

- Read the new captions with real data and adjust wording if anything is still
  unclear.
- Consider a dedicated "Insights" page if the Spend page grows further.
- Per-product price history is the obvious next visualisation.

## 8. Commands to reproduce

```bash
myenv/Scripts/python.exe -m pytest tests/test_spend.py tests/test_subscriptions.py -q
myenv/Scripts/python.exe -m pytest -q
flask --app app run --port 5099
```

Seed a database, open `/spend` and `/subscriptions`.
