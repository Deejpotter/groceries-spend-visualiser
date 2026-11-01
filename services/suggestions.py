"""Load-and-call glue for meal suggestions and pay-cycle lists.

Like ``repository``, this module reads the database; the decisions all live in
the pure services it calls (purchase_patterns, meal_patterns, plan_generator,
pay_cycle, cycle_list). The web routes and the HTTP API both go through here so
they always agree.
"""

from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

from database import get_db, get_setting
from models import parse_ymd
from services.cycle_list import generate_cycle_list, totals_by_source, trim_to_budget
from services.meal_patterns import history_slots, meal_stats, median_cost
from services.pay_cycle import pay_cycle
from services.plan_generator import MealScorer
from services.purchase_patterns import HISTORY_DAYS, build_patterns, predict_needs, prediction_accuracy
from services.repository import (
    load_feedback, load_ingredient_map, load_meal_history, load_pantry_stock, load_product_decisions,
    load_purchase_rows, load_recipe_links, load_recipes, load_shopping_inputs, shopping_preferences,
)
from services.shopping_list_generator import generate_shopping_list


def current_pay_cycle(today: date) -> Optional[Dict]:
    cycle = pay_cycle(get_setting("payday_date"), get_setting("pay_frequency", "fortnightly"), today)
    if cycle:
        budget = get_setting("cycle_grocery_budget")
        cycle["budget"] = float(budget) if budget else None
    return cycle


def patterns_for(today: date) -> List[Dict]:
    since = (today - timedelta(days=HISTORY_DAYS)).isoformat()
    return build_patterns(load_purchase_rows(since), today, load_ingredient_map(), load_product_decisions())


def meal_scorer(today: date) -> Tuple[MealScorer, Dict[tuple, int]]:
    """A scorer primed with meal history, costs and swap feedback, plus the usual meal slots."""
    recipes = load_recipes()
    by_id = {r["id"]: r for r in recipes}
    links = load_recipe_links()
    history = load_meal_history(today.isoformat())
    since = (today - timedelta(days=HISTORY_DAYS)).isoformat()
    stats = meal_stats(history, today, by_id, links, load_ingredient_map(), load_purchase_rows(since))
    swapped: Dict[int, int] = {}
    for row in load_feedback("meal"):
        if row["action"] == "swapped_out" and str(row["item_key"]).isdigit():
            swapped[int(row["item_key"])] = swapped.get(int(row["item_key"]), 0) + 1
    scorer = MealScorer(
        stats, links, feedback=swapped, median_cost=median_cost(stats),
        favourites={r["id"] for r in recipes if r.get("is_favourite")},
        effort={r["id"]: (r.get("prep_time") or 0) + (r.get("cook_time") or 0) for r in recipes},
    )
    return scorer, history_slots(history, today)


def meal_lines(start: str, end: str) -> Dict[str, List[Dict]]:
    unit_pref, subtract = shopping_preferences()
    entries, recipes, recipe_ingredients, ingredients = load_shopping_inputs(start, end)
    return generate_shopping_list(entries, recipes, recipe_ingredients, ingredients,
                                  unit_preference=unit_pref, subtract_pantry=subtract,
                                  pantry_items=load_pantry_stock() if subtract else None)


def build_cycle_list(start: str, end: str, mode: str, today: date, topup_days: int = 4) -> Dict:
    """Lines and totals for a list covering ``start``..``end`` in the given mode."""
    patterns = patterns_for(today)
    first, last = parse_ymd(start), parse_ymd(end)
    needs = predict_needs(patterns, first, last)
    in_stock = {iid for iid, (qty, _) in load_pantry_stock(today).items() if qty > 0}
    due_by = first + timedelta(days=topup_days) if mode == "topup" else None
    lines = generate_cycle_list(meal_lines(start, end), needs, mode, in_stock,
                                load_feedback("staple"), patterns, due_by=due_by)
    cycle = current_pay_cycle(today)
    budget = (cycle or {}).get("budget")
    return {
        "lines": lines,
        "totals": totals_by_source(lines, budget),
        "trim": trim_to_budget(lines, budget) if budget else None,
        "start": start,
        "end": end,
        "mode": mode,
    }


def save_cycle_list(lines: List[Dict]) -> int:
    """Replace the generated (non-manual) shopping list rows, keeping ticks."""
    db = get_db()
    ticked = {(r["ingredient_id"], r["ingredient_name"], r["unit"]) for r in db.execute(
        "SELECT ingredient_id, ingredient_name, unit FROM shopping_list_items WHERE checked = 1 AND is_manual = 0")}
    db.execute("DELETE FROM shopping_list_items WHERE is_manual = 0")
    for line in lines:
        db.execute(
            """INSERT INTO shopping_list_items
               (ingredient_id, ingredient_name, quantity, unit, category, checked, is_manual, meal_date,
                recipe_ref, estimated_cost, source, reason)
               VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?)""",
            (line.get("ingredient_id"), line["ingredient_name"], line["quantity"], line["unit"],
             line.get("category") or "other",
             1 if (line.get("ingredient_id"), line["ingredient_name"], line["unit"]) in ticked else 0,
             line.get("meal_date") or "", ", ".join(line.get("recipe_refs") or []),
             line.get("estimated_cost"), line.get("source"), line.get("reason")),
        )
    db.commit()
    return len(lines)


def accuracy_last_cycle(today: date) -> Optional[Dict]:
    """How well the patterns predicted the last completed pay cycle (or last fortnight)."""
    cycle = current_pay_cycle(today)
    if cycle:
        end = parse_ymd(cycle["cycle_start"]) - timedelta(days=1)
        start = parse_ymd(pay_cycle(get_setting("payday_date"), cycle["frequency"], end)["cycle_start"])
    else:
        end = today - timedelta(days=1)
        start = end - timedelta(days=13)
    rows = load_purchase_rows((start - timedelta(days=HISTORY_DAYS)).isoformat())
    if not any(start.isoformat() <= r["order_date"][:10] <= end.isoformat() for r in rows):
        return None
    result = prediction_accuracy(rows, start, end, load_ingredient_map(), load_product_decisions())
    return {**result, "start": start.isoformat(), "end": end.isoformat()}


def staple_key_for_row(row: Dict) -> Optional[str]:
    """The pattern key a shopping-list row was generated from, for feedback."""
    if row.get("source") not in ("staple", "treat", "household"):
        return None
    if row.get("ingredient_id"):
        return f"ing:{row['ingredient_id']}"
    return f"prod:{row['ingredient_name']}"


def record_removed_line(row: Dict) -> None:
    """A generated staple line deleted by hand counts against suggesting it again."""
    key = staple_key_for_row(row)
    if key:
        get_db().execute("INSERT INTO suggestion_feedback (kind, item_key, action) VALUES ('staple', ?, 'removed')",
                         (key,))


def fortnightly_income() -> Optional[float]:
    """Fortnightly income from settings, or None when blank or unreadable."""
    raw = (get_setting("fortnightly_income") or "").strip()
    try:
        return float(raw.replace("$", "").replace(",", "")) if raw else None
    except ValueError:
        return None


def savings_suggestion() -> Optional[Dict]:
    """Suggested savings per payday from bank history and active bills, or None."""
    from services.bank_import import load_merchant_patterns, resolve_directions, restamp_grocery
    from services.pay_cycle import detect_payday
    from services.savings_advice import cash_flow, parse_accounts, suggest_savings
    from services.subscriptions import monthly_cost_total

    db = get_db()
    rows = restamp_grocery(resolve_directions([dict(r) for r in db.execute(
        "SELECT source, txn_date, description, merchant_key, amount, direction, status"
        " FROM bank_transactions ORDER BY txn_date")]), load_merchant_patterns(db))
    pay = detect_payday(rows)
    if not pay:
        return None
    subs = [dict(r) for r in db.execute("SELECT * FROM subscriptions WHERE active = 1")]
    flow = cash_flow(rows, pay["merchant_key"], parse_accounts(get_setting("savings_accounts")))
    override = fortnightly_income() if pay["frequency"] == "fortnightly" else None
    return suggest_savings(flow, pay, monthly_cost_total(subs), override)
