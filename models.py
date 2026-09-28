"""Constants, validation, and helper functions for Grocery Visualiser."""

from datetime import datetime, timedelta
from typing import Optional


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


def convert_unit(value: float, from_unit: str, to_unit: str) -> float:
    """Convert a value from one unit to another (metric/imperial only)."""
    if from_unit in COUNT_UNITS or to_unit in COUNT_UNITS:
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
