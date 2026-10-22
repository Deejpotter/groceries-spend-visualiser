"""Recipe portion scaling — pure functions over plain dicts."""

MIN_SERVINGS = 1
MAX_SERVINGS = 100


def scale_factor(base_servings, target_servings):
    """Multiplier that turns base_servings portions into target_servings portions.

    Returns 1.0 when either value is missing, non-positive, or unchanged, so
    callers can always multiply safely.
    """
    try:
        base = float(base_servings)
        target = float(target_servings)
    except (TypeError, ValueError):
        return 1.0
    if base <= 0 or target <= 0 or base == target:
        return 1.0
    return target / base


def scale_ingredients(ingredients, base_servings, target_servings):
    """Return new ingredient dicts with quantities scaled to target_servings.

    Each input dict must have a ``quantity`` key. The source list is not
    mutated.
    """
    factor = scale_factor(base_servings, target_servings)
    scaled = []
    for item in ingredients or []:
        row = dict(item)
        try:
            quantity = float(row.get("quantity") or 0)
        except (TypeError, ValueError):
            quantity = 0
        row["quantity"] = round(quantity * factor, 4)
        scaled.append(row)
    return scaled


def normalise_servings(value, default):
    """Clamp a requested serving count to a sane, positive integer."""
    try:
        servings = int(value)
    except (TypeError, ValueError):
        return default
    if servings < MIN_SERVINGS:
        return MIN_SERVINGS
    if servings > MAX_SERVINGS:
        return MAX_SERVINGS
    return servings
