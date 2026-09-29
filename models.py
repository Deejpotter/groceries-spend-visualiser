"""Constants, validation, and helper functions for Grocery Visualiser."""

import os
from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

DEFAULT_TIMEZONE = "Australia/Sydney"


def is_http_url(value) -> bool:
    """True for absolute http(s) URLs; blocks javascript: and other schemes in links."""
    return isinstance(value, str) and value.lower().startswith(("http://", "https://"))


def today() -> date:
    """Today's date in the household's timezone (APP_TIMEZONE), not the server's UTC clock."""
    return datetime.now(ZoneInfo(os.getenv("APP_TIMEZONE") or DEFAULT_TIMEZONE)).date()


# ---------------------------------------------------------------------------
# Category constants
# ---------------------------------------------------------------------------

CATEGORIES = [
    ("produce", "Produce"),
    ("meat", "Meat & Seafood"),
    ("dairy", "Dairy & Eggs"),
    ("bakery", "Bakery"),
    ("pantry", "Pantry Staples"),
    ("frozen", "Frozen"),
    ("household", "Household"),
    ("beverages", "Beverages"),
    ("snacks", "Snacks"),
    ("other", "Other"),
]

CATEGORY_LOOKUP = {code: label for code, label in CATEGORIES}


def category_label(code):
    """Human-readable label for a category code (falls back to the code)."""
    return CATEGORY_LOOKUP.get(code, code)


# ---------------------------------------------------------------------------
# Unit constants
# ---------------------------------------------------------------------------

UNITS = [
    ("kg", "Kilogram (kg)"),
    ("g", "Gram (g)"),
    ("lb", "Pound (lb)"),
    ("oz", "Ounce (oz)"),
    ("L", "Litre (L)"),
    ("mL", "Millilitre (mL)"),
    ("gal", "Gallon (gal)"),
    ("fl_oz", "Fluid Ounce (fl oz)"),
    ("each", "Each"),
    ("pack", "Pack"),
    ("bunch", "Bunch"),
    ("roll", "Roll"),
    ("can", "Can"),
    ("jar", "Jar"),
    ("box", "Box"),
    ("bag", "Bag"),
    ("tube", "Tube"),
    ("carton", "Carton"),
    ("slice", "Slice"),
    ("cup", "Cup"),
    ("tbsp", "Tablespoon"),
    ("tsp", "Teaspoon"),
]

UNIT_LOOKUP = {code: label for code, label in UNITS}


# ---------------------------------------------------------------------------
# Meal type constants
# ---------------------------------------------------------------------------

MEAL_TYPES = ["breakfast", "lunch", "dinner", "snack"]
MEAL_TYPE_LABELS = {
    "breakfast": "Breakfast",
    "lunch": "Lunch",
    "dinner": "Dinner",
    "snack": "Snack",
}


# ---------------------------------------------------------------------------
# Day of week constants
# ---------------------------------------------------------------------------

DAYS_OF_WEEK = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DAY_LABELS = {
    "mon": "Monday",
    "tue": "Tuesday",
    "wed": "Wednesday",
    "thu": "Thursday",
    "fri": "Friday",
    "sat": "Saturday",
    "sun": "Sunday",
    "weekday": "Weekday",
    "weekend": "Weekend",
    "all": "Every day",
}


# ---------------------------------------------------------------------------
# Unit conversion (metric <-> imperial)
# ---------------------------------------------------------------------------

# Base unit: metric (kg, L). Conversion factors to base.
TO_BASE = {
    "kg": 1.0,
    "g": 0.001,
    "lb": 0.453592,
    "oz": 0.0283495,
    "L": 1.0,
    "mL": 0.001,
    "gal": 3.78541,
    "fl_oz": 0.0295735,
}

# Derived units that are counts (not convertible)
COUNT_UNITS = {"each", "pack", "bunch", "roll", "can", "jar", "box", "bag", "tube", "carton", "slice", "cup", "tbsp", "tsp"}


def convert_to_base(value: float, unit: str) -> float:
    """Convert a value from the given unit to the base (metric) unit."""
    if unit in COUNT_UNITS:
        return value
    factor = TO_BASE.get(unit)
    if factor is None:
        return value
    return value * factor


def convert_from_base(value: float, unit: str) -> float:
    """Convert a value from the base (metric) unit to the given unit."""
    if unit in COUNT_UNITS:
        return value
    factor = TO_BASE.get(unit)
    if factor is None:
        return value
    if factor == 0:
        return value
    return value / factor


# Metric unit -> (imperial unit) used when the user prefers imperial display.
IMPERIAL_EQUIVALENT = {"kg": "lb", "g": "oz", "L": "gal", "mL": "fl_oz"}


def to_display_unit(quantity: float, unit: str, preference: str):
    """Return (quantity, unit) converted for display in the preferred system."""
    if preference == "imperial" and unit in IMPERIAL_EQUIVALENT:
        target = IMPERIAL_EQUIVALENT[unit]
        return round(convert_unit(quantity, unit, target), 2), target
    return quantity, unit


def units_compatible(a: str, b: str) -> bool:
    """True if two units can be converted between (same unit or same weight/volume family)."""
    if a == b:
        return True
    weight = {"kg", "g", "lb", "oz"}
    volume = {"L", "mL", "gal", "fl_oz"}
    return (a in weight and b in weight) or (a in volume and b in volume)


def convert_unit(value: float, from_unit: str, to_unit: str) -> float:
    """Convert a value between compatible units; incompatible units are returned unchanged."""
    if not units_compatible(from_unit, to_unit):
        return value
    base = convert_to_base(value, from_unit)
    return convert_from_base(base, to_unit)


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def validate_date_format(date_str: str) -> bool:
    """Check if a string is a valid YYYY-MM-DD date."""
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def parse_date(date_str: str) -> Optional[datetime]:
    """Parse a YYYY-MM-DD string to a datetime, or None if invalid."""
    if not validate_date_format(date_str):
        return None
    return datetime.strptime(date_str, "%Y-%m-%d")


def date_range(start_str: str, end_str: str):
    """Generate dates from start to end inclusive."""
    start = parse_date(start_str)
    end = parse_date(end_str)
    if start is None or end is None:
        return []
    if end < start:
        return []
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def get_day_key(date: datetime) -> str:
    """Get the day-of-week key for a date (mon, tue, ..., weekday, weekend)."""
    day = date.strftime("%a").lower()
    return day


def is_weekday(date: datetime) -> bool:
    """Check if a date is a weekday (Mon-Fri)."""
    return date.weekday() < 5
