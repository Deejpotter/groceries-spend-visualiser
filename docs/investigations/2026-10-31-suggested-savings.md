# Suggested savings

- **Date:** 2026-10-31
- **Goal:** Larder suggests savings amounts from income and costs, instead of Deej choosing them blind.

## 1. How it works

`services/savings_advice.py`, shown on the Fortnightly Budget page.

1. **Pay** is the regular salary credit found by `detect_payday()`. A fortnightly
   income set in Settings wins over the detected amount.
2. **Costs** are bank debits from the last 90 days of history, scaled to one pay:
   groceries (rows marked `grocery`), and everything else. Bills from the Bills page
   count once: if they are higher than the other bank spending, they are assumed to be
   paid from an account that is not imported.
3. **Moves:** transfers between your own accounts (`Transfer to xx9910`, and the
   existing internal transfer patterns) are not costs or income.
4. **Surplus** is pay minus costs. A fifth stays unallocated as a buffer.
5. The rest is split in half, rounded down to $10: long term savings and savings.
6. **Emergency fund goal:** three months of bills and groceries.
7. **Benchmark:** 20% of pay. The page shows the gap if the suggestion is below it.
8. **Savings pulled back:** credits from the account endings set in Settings are shown
   per pay.

"Use these amounts" saves the suggestion, and the detected pay when no income is set.

## 2. Sources for the rules

- Three months of essential costs as an emergency fund: Mozo and MyBudget guides.
- 20% of take-home pay as savings: the common 50/30/20 rule.
- Both are starting points, not fixed rules. The buffer and the 50/50 split are Larder's
  own choices and are constants at the top of the service.

## 3. Limits

- Credit card repayments match the internal transfer patterns, so card spending only
  counts if the card account is imported too.
- Bank rows have no account column. Moves are found from the description.
- No balances, so the emergency fund goal shows a target and pays to reach it, not
  progress.

## 4. Verification

- `pytest`: 846 passed, including 22 new tests in `tests/test_savings_advice.py`.
- Rendered the section locally with sample history: $3,000 pay, $1,400 costs gave $630
  long term and $640 savings, and flagged $100 a pay moved back out of savings.
- Not checked on staging with real bank rows yet.
