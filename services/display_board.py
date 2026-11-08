"""Fridge display board: the next N days of planned meals, small enough for an ESP32.

Pure: takes plan rows, skipped slots and today's date, returns the JSON the
larder-cyd device draws. The device only lays it out, so every label and note is
decided here.
"""

from datetime import date, timedelta
from typing import Dict, Iterable, List, Optional

MAX_DAYS = 31
NAME_LIMIT = 40
DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def clamp_days(value, default: int = 14) -> int:
    try:
        days = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(MAX_DAYS, days))


def meal_note(entry: Dict) -> str:
    """Short note under a meal: leftovers, already made, already bought."""
    origin = entry.get("origin") or ""
    if origin == "made":
        return "Already made"
    if entry.get("is_continuation"):
        return "Leftovers"
    if origin == "rollover":
        return "Already bought"
    return ""


def _short(name: str) -> str:
    name = (name or "").strip()
    return name if len(name) <= NAME_LIMIT else name[:NAME_LIMIT - 3].rstrip() + "..."


def build_board(entries: Iterable[Dict], skips: Iterable[Dict], today: date, days: int = 14,
                meal_types: Optional[List[str]] = None, info: Optional[Dict] = None) -> Dict:
    """{today, days: [{date, dow, day, mon, today, meals: {type: {name, note}}}], info}.

    ``entries`` are plan rows with date, meal_type, recipe_name, is_continuation and
    origin. ``skips`` are ``plan_skips`` rows (ate out). Days with nothing planned
    still appear so the device shows gaps.
    """
    days = clamp_days(days)
    wanted = set(meal_types) if meal_types else None
    slots: Dict[tuple, Dict] = {}
    for entry in entries:
        key = (entry.get("date"), entry.get("meal_type"))
        if wanted and key[1] not in wanted:
            continue
        slots[key] = {"name": _short(entry.get("recipe_name")), "note": meal_note(entry)}
    for skip in skips:
        key = (skip.get("date"), skip.get("meal_type"))
        if wanted and key[1] not in wanted:
            continue
        slots.setdefault(key, {"name": "Eating out", "note": ""})

    out = []
    for offset in range(days):
        day = today + timedelta(days=offset)
        iso = day.isoformat()
        meals = {meal: slot for (d, meal), slot in slots.items() if d == iso}
        out.append({"date": iso, "dow": DOW[day.weekday()], "day": day.day,
                "mon": MONTHS[day.month - 1], "today": offset == 0, "meals": meals})
    return {"today": today.isoformat(), "days": out, "info": info or {}}


def board_info(cycle: Optional[Dict], shopping_left: Optional[int], today: date) -> Dict:
    """Footer facts: days to payday and unticked shopping list lines."""
    info: Dict = {}
    if cycle and cycle.get("next_payday"):
        info["next_payday"] = cycle["next_payday"]
        info["days_to_payday"] = (date.fromisoformat(cycle["next_payday"]) - today).days
    if shopping_left is not None:
        info["shopping_left"] = shopping_left
    return info


def make_board_entry(date_str: str = "2026-11-08", meal_type: str = "dinner", recipe_name: str = "Spag bol",
                     is_continuation: int = 0, origin: str = "ui") -> Dict:
    """Create a plan row dict for testing."""
    return {"date": date_str, "meal_type": meal_type, "recipe_name": recipe_name,
            "is_continuation": is_continuation, "origin": origin}
