# Aurora theme

- **Date:** 2026-10-31
- **Investigator:** Claude Code
- **Status:** Done, shipped through PR

## 1. What was asked

Deej asked for a way more modern, animated and colourful design.

## 2. What was checked

`static/css/style.css`, `templates/base.html`, the chart colours in templates, and the
logged-in pages on staging at desktop width, plus five pages at 390px wide.

## 3. What the evidence showed

- The old palette was warm and muted, and many surfaces had hard-coded light colours.
- Charts read CSS tokens, so new tokens reach them without chart changes, apart from
  a fixed palette list in two templates.
- At 390px, a Bills table overflowed the page by 77px.

## 4. What was changed

- New light and dark tokens on a violet base, with neon accents and semantic colours.
- A fixed animated sky whose colours follow the nav section: Plan, Shop or Track.
- Inter Variable from jsDelivr with SRI, large gradient page titles and a text wordmark.
- Spring hover and entrance motion, page crossfades and KPI count-up.
- The navbar collapses below 1200px and its links no longer wrap.
- Cards that hold a table now scroll sideways instead of widening the page.

## 5. What was deliberately not changed

- Page titles stay at 2.3 to 3.9rem, not the 15 to 25vw of a pure poster treatment,
  because this is a data app that needs content above the fold.
- Cards are translucent without `backdrop-filter`, to avoid repainting blur over an
  animated background on phones. The navbar and toolbars do use blur.
- No logic, routes or data changed.

## 6. Verification

- `pytest`: 818 passed.
- Checked in Chrome on staging: spend, dashboard, meal plan, recipes, bills, shopping
  list and light mode. No console errors on the spend page.
- Phone width was tested with a 390px same-origin iframe, because Chrome would not
  shrink the window. No page overflowed after the Bills fix was verified below.

## 7. Open items

- Chart.js labels still use its default font.

## 8. Commands to reproduce

```bash
myenv/Scripts/python.exe -m pytest -q
```
