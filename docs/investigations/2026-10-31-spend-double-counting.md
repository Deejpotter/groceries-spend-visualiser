# Spend page double counting and the 98% Woolworths figure

- **Date:** 2026-10-31
- **Investigator:** Claude Code
- **Status:** Fix on `dev`. Not yet on production. Open items in section 7.

## 1. What was asked

Deej said the `/spend` page was wrong: "Woolworths took 98%" of spend is not true.
The page showed $25,871.80 over 12 months, $2,163.56 a month and 175 shops.

## 2. What was checked

All queries were read-only against the production database (`mode=ro`), 12 months
to 2026-10-28.

- `purchases` by store and origin, month by month.
- Each itemised basket against the bank rows within 7 days.
- Same-day baskets, by shared product lines.
- Bank rows with the same store and amount within 4 days.
- Grocery-like merchants in `bank_transactions` that were not classified.
- Bank credits at Woolworths, Coles and ALDI.

## 3. What the evidence showed

| Hypothesis | Outcome | Evidence |
|---|---|---|
| Invoices and bank payments are the same shops, counted twice | **Confirmed** | Oct 2025 to Aug 2026: invoices $11,082, bank $12,319, with near-identical monthly sizes. Bank rows land 1 to 2 days after the shop. No import has run since 24 Aug, and the chart fell to about $1.3k from then on, which is what bank-only looks like. |
| The importer's dedupe catches this | **Rejected** | It needs an exact store, date and amount. 0 of 69 nearest matches had the same amount. |
| Amended orders are also counted twice | **Confirmed** | 5 same-day pairs share 11 to 42 lines, with first-of-pair totals of $934.07. 2025-11-04: invoices $95.41 and $103.31, bank $94.68 + $8.45 = $103.13. 2025-12-22: invoices $166.68 and $174.18 differ by $7.50, and the bank has a $7.50 top-up. |
| A pending bank row is counted beside its posted row | **Confirmed**, once | $210.44 on 26 and 28 Oct 2026. Only the posted row has a "Value date". |
| Removing duplicates fixes the 98% | **Rejected** | Woolworths is 96.6%, shown as 97%, of $14,954.26. |
| Refunds are ignored | **Rejected** | No grocery credits exist in the bank data. |
| Grocery shops are missed by the merchant patterns | **Minor** | `STE SUPERMARKET` $169.27 is unclassified. Adding it changes the share by about 1 point. |
| Money spent from another account is invisible | **Likely, unverified** | 28 transfers labelled "Groceries" total $2,230 (including one typed "Groceeies") to another account. The bank data only shows them leaving. If all of it was spent at other stores, Woolworths would be about 84%. |
| The figure reads as a share of all spending | **Confirmed** as a wording fault | "of it" sat next to the plain "you spent". Woolworths is about 39% of the $37,298 non-transfer bank debits. |
| Invoices of about 42 to 45 lines lost items | **Likely, unverified** | Those baskets sit $80 to $190 below their bank charge, while small baskets are within about $10. The source invoices were not available to check. Totals no longer depend on this, but product figures may be low. |

## 4. What was changed

- `services/spend_analysis.py`: `mark_counted()` flags every row as `counted`
  (money) and `itemised` (a real product line). Inside a store's bank coverage the
  bank is the money. Coverage runs from 3 days before that store's first bank row
  to its last one. Outside it, invoices are the money.
- Replaced amended invoices are dropped: same store and day, at least 3 shared
  lines and 80% of the smaller basket, keeping the later order number.
- Pending bank copies are dropped: no "Value date", with a posted row for the same
  store and amount within 3 days.
- Products, categories and line counts now come from itemised rows only.
- `load_purchases()` marks the whole table before applying the date range, so a
  range edge cannot split a shop from its payment.
- `load_spend()` gives money rows to the budget tile and the home dashboard.
  `average_spend_per_shop()` uses the same logic.
- `templates/spend/dashboard.html`: the insight says the share is of the grocery
  stores tracked, and a note says what the totals include.

| 12 months | Before | After |
|---|---|---|
| Total | $25,871.80 | $14,954.26 |
| Per month | $2,163.56 | $1,250.57 |
| Per shop | $147.84 | $146.61 |
| Shops | 175 | 102 |
| Between shops | 2.1 days | 3.6 days |
| Woolworths share | 98% | 97% of tracked stores |
| Oct 2026 month to date | $1,141.54 | $931.10 |

Reconciliation: bank rows of $15,164.70 less the $210.44 pending copy is $14,954.26.

## 5. What was deliberately not changed

- **No production rows were changed or deleted.** The fix is at read time, so
  invoices keep their product detail and a re-import cannot bring the problem back.
- **The importer's exact-match dedupe stays.** A wider match would throw away
  invoices that products need.
- **Amount-based basket matching was rejected.** Only 36 of 69 matches were within
  $20, so it would leave shops double counted.
- **Pattern changes were left out.** `aldi` also matches `ALDIMOBILE` ($2.00), which
  is a phone plan, but that is a settings decision.
- **`load_purchase_rows()` is unchanged.** Purchase patterns are about products and
  do not report money totals.

## 6. Verification

- New tests in `tests/test_spend.py` cover each duplicate type, bank coverage per
  store, range edges, the budget and the guards against false matches.
- The new code was run read-only against production rows and matched the table above.
- Full test results are in section 8.

## 7. Open items

1. What is the account receiving the "Groceries" transfers? If it pays other
   supermarkets, connect it, or the store share stays overstated.
2. Are invoices of about 43 lines losing items on import? This needs one original
   invoice to compare.
3. Should `STE SUPERMARKET` be added to the merchant patterns, and `ALDIMOBILE`
   excluded?
4. The "All time" monthly average spreads across the gap from Oct 2023 to Sep 2025,
   which has no data.

## 8. Commands to reproduce

```powershell
python -m pytest tests/test_spend.py -q
```

On Deej's Windows PC the full suite gives 810 passed and 7 failed. The same 7 fail
on `origin/dev` without this change. Five are `ValueError: Invalid format string`
in `TestCycleList`, and two are the AI review "needs a key" tests. Clearing
`OPENROUTER_API_KEY` in the shell did not fix those two, and their cause was not
found. Linux CI is the check for these.
