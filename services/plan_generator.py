"""Plan generation service — pure functions for rule-based meal plan creation.

Extracted from routes/meal_plan.py so generation logic is testable without Flask.
"""

from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional, Set, Tuple

from models import MEAL_TYPES, parse_date, date_range, get_day_key, is_weekday


# ---------------------------------------------------------------------------
# Rule matching
# ---------------------------------------------------------------------------

def rule_matches_day(rule_day_of_week: str, date: datetime) -> bool:
    """Check if a rule's day_of_week matches the given date.

    Supports comma-separated days (e.g. 'mon,wed,fri') and the special
    values 'all', 'weekday', 'weekend', and single day codes.
    """
    day_key = get_day_key(date)
    days = {d.strip() for d in rule_day_of_week.split(",") if d.strip()}
    if "all" in days:
        return True
    if "weekday" in days and is_weekday(date):
        return True
    if "weekend" in days and not is_weekday(date):
        return True
    return day_key in days


def parse_tags(tags: Optional[str]) -> List[str]:
    """Split a comma-separated tag string into lower-cased, trimmed tags."""
    return [t.strip().lower() for t in (tags or "").split(",") if t.strip()]


def recipe_matches_filter(recipe: Dict, tag_filter: Optional[str]) -> bool:
    """A recipe matches if it has ANY of the filter's tags (no filter = match)."""
    wanted = parse_tags(tag_filter)
    if not wanted:
        return True
    return bool(set(wanted) & set(parse_tags(recipe.get("tags"))))


def first_choice(candidates: List[Dict]) -> Dict:
    """Deterministic chooser (used by tests); the app passes random.choice."""
    return candidates[0]


SKIP_TAGS = {"skip", "eat out", "eat-out", "none", "no cook", "no-cook", "takeaway"}
TOP_CHOICES = 3


def is_skip_rule(rule: Dict) -> bool:
    """A rule whose tag filter is 'skip' / 'eat out' marks the slot as intentionally empty."""
    return (rule.get("tag_filter") or "").strip().lower() in SKIP_TAGS


def select_recipe_for_rule(
    tag_filter: Optional[str],
    recipes: List[Dict],
    used_ids: Optional[Set[int]] = None,
    chooser: Callable[[List[Dict]], Dict] = first_choice,
    scorer=None,
    date=None,
    meal_type: Optional[str] = None,
) -> Optional[Dict]:
    """Pick a recipe for a rule, preferring recipes not already used in the plan.

    With a ``scorer`` (see MealScorer), candidates are ranked and the chooser
    picks among the best few, so plans follow what the household eats while
    still varying. Returns None if no recipe matches the tag filter.
    """
    candidates = [r for r in recipes if recipe_matches_filter(r, tag_filter)]
    if not candidates:
        return None
    if used_ids:
        fresh = [r for r in candidates if r["id"] not in used_ids]
        if fresh:
            candidates = fresh
    if scorer is not None:
        candidates = scorer.rank(candidates, date, meal_type)[:TOP_CHOICES]
    return chooser(candidates)


# ---------------------------------------------------------------------------
# Suggestion scoring
# ---------------------------------------------------------------------------

DAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


class MealScorer:
    """Scores recipes for a slot from meal history, cost, pack use-up and feedback.

    ``stats`` comes from ``meal_patterns.meal_stats``; ``recipe_ingredients``
    maps recipe id to ingredient links; ``feedback`` maps recipe id to how
    often it was swapped out of a plan. The scorer remembers what it has
    already placed so later slots favour variety and shared ingredients.
    """

    def __init__(self, stats: Dict[int, Dict], recipe_ingredients: Optional[Dict[int, List[Dict]]] = None,
                 feedback: Optional[Dict[int, int]] = None, median_cost: Optional[float] = None,
                 favourites: Optional[Set[int]] = None, effort: Optional[Dict[int, int]] = None):
        self.stats = stats or {}
        self.links = {rid: {l["ingredient_id"] for l in links}
                      for rid, links in (recipe_ingredients or {}).items()}
        self.feedback = feedback or {}
        self.median_cost = median_cost
        self.favourites = favourites or set()
        self.effort = effort or {}
        self.placed: Dict[int, List] = {}
        self.plan_ingredients: Set[int] = set()

    def score(self, recipe: Dict, date=None, meal_type: Optional[str] = None) -> Tuple[float, List[str]]:
        rid = recipe["id"]
        s = self.stats.get(rid) or {}
        score, reasons = 1.0, []
        if rid in self.favourites or recipe.get("is_favourite"):
            score += 0.5
            reasons.append("Favourite")
        eaten = s.get("eaten") or 0
        if eaten:
            score += min(eaten, 10) / 10 * 0.5
            reasons.append(f"Eaten {eaten} time{'s' if eaten != 1 else ''} recently")
        elif s.get("cooked_baskets"):
            score += min(s["cooked_baskets"], 5) / 5 * 0.3
            reasons.append("Ingredients often bought together")
        if meal_type and eaten and s.get("meal_types", {}).get(meal_type):
            score += 0.3 * s["meal_types"][meal_type] / eaten
        if date is not None and eaten:
            day = DAY_KEYS[date.weekday()]
            if s.get("weekdays", {}).get(day):
                score += 0.2 * s["weekdays"][day] / eaten
                if s["weekdays"][day] * 2 >= eaten:
                    reasons.append(f"Usually a {day.title()} meal")
        days_since = s.get("days_since")
        placed = self.placed.get(rid)
        if placed:
            score -= 1.5
        elif days_since is not None and days_since < 7:
            score -= 1.0
            reasons.append("Eaten in the last week")
        elif days_since is not None and days_since < 14:
            score -= 0.4
        elif days_since is not None and days_since >= 21:
            score += 0.2
            reasons.append(f"Not had for {days_since // 7} weeks")
        cost = s.get("cost_per_serving")
        if cost and self.median_cost:
            if cost <= self.median_cost * 0.8:
                score += 0.2
                reasons.append(f"Cheap (${cost:.2f}/serve)")
            elif cost >= self.median_cost * 1.3:
                score -= 0.2
        shared = len(self.links.get(rid, set()) & self.plan_ingredients)
        if shared:
            score += min(shared, 3) * 0.15
            reasons.append(f"Shares {shared} ingredient{'s' if shared != 1 else ''} with the plan")
        swapped = self.feedback.get(rid, 0)
        if swapped:
            score -= min(swapped, 3) * 0.3
            reasons.append(f"Swapped out {swapped} time{'s' if swapped != 1 else ''}")
        minutes = self.effort.get(rid) or 0
        if date is not None and meal_type == "dinner" and date.weekday() < 5 and minutes > 60:
            score -= 0.3
            reasons.append("Long cook for a weeknight")
        return round(score, 3), reasons

    def rank(self, candidates: List[Dict], date=None, meal_type: Optional[str] = None) -> List[Dict]:
        """Candidates best first (ties keep their original order)."""
        scored = [(self.score(r, date, meal_type)[0], i, r) for i, r in enumerate(candidates)]
        return [r for _, _, r in sorted(scored, key=lambda t: (-t[0], t[1]))]

    def alternatives(self, recipes: List[Dict], date=None, meal_type: Optional[str] = None,
                     exclude: Optional[Set[int]] = None, limit: int = 3) -> List[Dict]:
        """Best recipes for a slot with their reasons: [{recipe, score, reasons}]."""
        exclude = exclude or set()
        scored = []
        for r in recipes:
            if r["id"] in exclude:
                continue
            value, reasons = self.score(r, date, meal_type)
            scored.append({"recipe": r, "score": value, "reasons": reasons})
        return sorted(scored, key=lambda x: -x["score"])[:limit]

    def record(self, recipe: Dict, date=None) -> None:
        self.placed.setdefault(recipe["id"], []).append(date)
        self.plan_ingredients |= self.links.get(recipe["id"], set())


# ---------------------------------------------------------------------------
# Plan generation (pure function — no DB)
# ---------------------------------------------------------------------------

def generate_plan_entries(
    start_date_str: str,
    end_date_str: str,
    rules: List[Dict],
    recipes: List[Dict],
    existing_entries: List[Dict],
    chooser: Callable[[List[Dict]], Dict] = first_choice,
    scorer: Optional[MealScorer] = None,
    history_slots: Optional[Dict[tuple, int]] = None,
) -> Tuple[List[Dict], Optional[str]]:
    """Generate meal plan entries for the given date range.

    existing_entries are slots that must not be overwritten (manual entries).
    With a ``scorer``, recipes are ranked by what the household eats. With
    ``history_slots`` ({(day, meal_type): count}), slots no rule covers are
    still filled when history shows that meal is normally eaten, using recipes
    eaten at that meal before. Returns (entries, error_message).
    """
    start = parse_date(start_date_str)
    end = parse_date(end_date_str)
    if not start or not end or end < start:
        return [], "Invalid date range."

    # Build lookup for existing manual entries
    existing_set = {
        (e["date"], e["meal_type"]) for e in existing_entries
    }

    entries = []
    used_ids: Set[int] = {e["recipe_id"] for e in existing_entries if "recipe_id" in e}
    if scorer is not None:
        by_id = {r["id"]: r for r in recipes}
        for e in existing_entries:
            if e.get("recipe_id") in by_id:
                scorer.record(by_id[e["recipe_id"]])

    for date in date_range(start_date_str, end_date_str):
        for meal_type in MEAL_TYPES:
            # Find matching ACTIVE rules, sorted by sort_order
            matching_rules = sorted(
                [r for r in rules
                 if r["meal_type"] == meal_type
                 and r.get("is_active", 1)
                 and rule_matches_day(r["day_of_week"], date)],
                key=lambda r: r.get("sort_order", 0),
            )

            entry_key = (date.strftime("%Y-%m-%d"), meal_type)
            slot_rules = list(matching_rules)
            if not slot_rules and history_slots and scorer is not None \
                    and (DAY_KEYS[date.weekday()], meal_type) in history_slots:
                slot_rules = [{"id": None, "tag_filter": None, "history": True}]

            for rule in slot_rules:
                if entry_key in existing_set:
                    break  # Manual override blocks auto-generation
                if is_skip_rule(rule):
                    break  # Eat-out / no-cook slot stays empty on purpose

                pool = recipes
                if rule.get("history"):
                    pool = [r for r in recipes
                            if (scorer.stats.get(r["id"]) or {}).get("meal_types", {}).get(meal_type)]
                recipe = select_recipe_for_rule(rule.get("tag_filter"), pool, used_ids, chooser,
                                                scorer=scorer, date=date, meal_type=meal_type)
                if not recipe:
                    continue
                if scorer is not None:
                    scorer.record(recipe, date)

                recipe_servings = recipe.get("servings") or 1
                servings = (
                    rule["servings_override"]
                    if rule.get("servings_override")
                    else recipe_servings
                )

                entries.append({
                    "date": date.strftime("%Y-%m-%d"),
                    "meal_type": meal_type,
                    "recipe_id": recipe["id"],
                    "servings": servings,
                    "is_auto_generated": 1,
                    "is_continuation": 0,
                    "source_rule_id": rule["id"],
                    "origin": "history" if rule.get("history") else None,
                })
                used_ids.add(recipe["id"])

                existing_set.add(entry_key)
                covers_days = max(1, int(recipe.get("covers_days") or 1))
                continuation_date = date
                continuations_added = 0
                while continuations_added < covers_days - 1:
                    continuation_date += timedelta(days=1)
                    if continuation_date > end:
                        break
                    continuation_key = (continuation_date.strftime("%Y-%m-%d"), meal_type)
                    if continuation_key in existing_set:
                        continue
                    entries.append({
                        "date": continuation_key[0],
                        "meal_type": meal_type,
                        "recipe_id": recipe["id"],
                        "servings": servings,
                        "is_auto_generated": 1,
                        "is_continuation": 1,
                        "source_rule_id": rule["id"],
                        "origin": "history" if rule.get("history") else None,
                        "continuation_of": entry_key,
                    })
                    existing_set.add(continuation_key)
                    continuations_added += 1
                break  # First matching rule wins

    return entries, None


def plan_summary(days: List[Dict], meal_types: List[str]) -> Dict:
    """Headline numbers for the plan dashboard from the day rows built for the view."""
    entries = [d["meals"].get(mt) for d in days for mt in meal_types]
    planned = [e for e in entries if e]
    today = next((d for d in days if d.get("is_today")), None)
    return {
        "slots": len(entries),
        "planned": len(planned),
        "open": len(entries) - len(planned),
        "recipes": len({e["recipe_id"] for e in planned}),
        "manual": sum(1 for e in planned if not e.get("is_auto_generated", 1)),
        "today": [(mt, today["meals"][mt]) for mt in meal_types if today["meals"].get(mt)] if today else None,
    }


# ---------------------------------------------------------------------------
# Helpers for building test data
# ---------------------------------------------------------------------------

def make_rule(
    id: int = 1,
    day_of_week: str = "mon",
    meal_type: str = "dinner",
    tag_filter: Optional[str] = None,
    servings_override: Optional[int] = None,
    is_active: int = 1,
    sort_order: int = 0,
) -> Dict:
    """Create a rule dict for testing."""
    return {
        "id": id,
        "day_of_week": day_of_week,
        "meal_type": meal_type,
        "tag_filter": tag_filter,
        "servings_override": servings_override,
        "is_active": is_active,
        "sort_order": sort_order,
    }


def make_recipe(
    id: int = 1,
    name: str = "Test Recipe",
    servings: int = 4,
    covers_days: int = 1,
    tags: Optional[str] = None,
) -> Dict:
    """Create a recipe dict for testing."""
    return {
        "id": id,
        "name": name,
        "servings": servings,
        "covers_days": covers_days,
        "tags": tags or "",
    }
