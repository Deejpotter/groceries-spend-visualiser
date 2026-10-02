"""Shopping list generation service — pure functions for aggregating ingredients.

Routes load data from the DB and call generate_shopping_list(); keeping this
module free of Flask/SQL makes the aggregation logic easy to test.
"""

import math
import re
from typing import Dict, List, Optional, Tuple, Union

from models import convert_unit, label_of, to_display_unit, units_compatible

# Pantry stock keyed by ingredient id (preferred) or name: either a bare quantity
# (assumed to be in the same unit as the list line) or a (quantity, unit) pair.
PantryStock = Dict[Union[int, str], Union[float, Tuple[float, str]]]


def convert_to_display_unit(quantity: float, unit: str, preference: str) -> Dict[str, float]:
    """Convert a quantity+unit to the preferred display system.

    Returns dict with 'quantity' and 'unit' keys. Count units are not converted.
    """
    qty, display_unit = to_display_unit(quantity, unit, preference)
    return {"quantity": qty, "unit": display_unit}


def _tidy(quantity: float):
    """Round to 2dp and drop a trailing .0 for display."""
    quantity = round(quantity, 2)
    return int(quantity) if quantity == int(quantity) else quantity


def _pantry_amount(stock, unit: str) -> float:
    """How much of `unit` the pantry holds (0 when units can't be compared)."""
    if isinstance(stock, (tuple, list)):
        qty, stock_unit = stock
        if not units_compatible(stock_unit, unit):
            return 0.0
        return convert_unit(qty, stock_unit, unit)
    return float(stock)


def _line_cost(quantity: float, unit: str, ingredient: Dict) -> Optional[float]:
    """Estimated cost: ingredient price is per ingredient unit."""
    price = ingredient.get("price")
    if not price:
        return None
    base_unit = ingredient.get("unit") or unit
    if not units_compatible(unit, base_unit):
        return None
    return round(convert_unit(quantity, unit, base_unit) * price, 2)


def generate_shopping_list(
    plan_entries: List[Dict],                   # [{date, meal_type, recipe_id, servings}]
    recipes: Dict[int, Dict],                   # {id: {name, servings, ...}}
    recipe_ingredients: Dict[int, List[Dict]],  # {recipe_id: [{quantity, unit_override, ingredient_id}]}
    ingredients: Dict[int, Dict],               # {id: {name, unit, category, price}}
    unit_preference: str = "metric",
    subtract_pantry: bool = False,
    pantry_items: Optional[PantryStock] = None,
) -> Dict[str, List[Dict]]:
    """Generate a grouped shopping list from meal plan entries.

    Returns {category: [{ingredient_id, ingredient_name, quantity, unit, category,
    recipe_refs, meal_date, estimated_cost}]} with categories and items sorted.
    """
    if not plan_entries:
        return {}

    aggregated: Dict[tuple, dict] = {}

    for entry in plan_entries:
        if entry.get("is_continuation"):
            continue  # continuation meals share the original recipe's ingredients
        recipe = recipes.get(entry["recipe_id"])
        if not recipe:
            continue
        scale_factor = entry["servings"] / (recipe.get("servings") or 1)

        for ri in recipe_ingredients.get(entry["recipe_id"], []):
            ingredient = ingredients.get(ri["ingredient_id"])
            if not ingredient:
                continue

            unit = ri.get("unit_override") or ingredient["unit"]
            qty = ri["quantity"] * scale_factor
            # Merge compatible units (g + kg) into one line in the ingredient's own unit.
            if units_compatible(unit, ingredient["unit"]):
                qty = convert_unit(qty, unit, ingredient["unit"])
                unit = ingredient["unit"]
            key = (ri["ingredient_id"], unit)

            if key in aggregated:
                item = aggregated[key]
                item["quantity"] += qty
                if recipe["name"] not in item["recipe_refs"]:
                    item["recipe_refs"].append(recipe["name"])
                item["meal_date"] = min(item["meal_date"], entry.get("date", "")) or item["meal_date"]
            else:
                aggregated[key] = {
                    "ingredient_id": ri["ingredient_id"],
                    "ingredient_name": ingredient["name"],
                    "quantity": qty,
                    "unit": unit,
                    "category": ingredient.get("category") or "other",
                    "recipe_refs": [recipe["name"]],
                    "meal_date": entry.get("date", ""),
                }

    # Subtract pantry stock (in the recipe's unit, before display conversion)
    if subtract_pantry and pantry_items:
        for key in list(aggregated):
            item = aggregated[key]
            stock = pantry_items.get(item["ingredient_id"], pantry_items.get(item["ingredient_name"]))
            if stock is None:
                continue
            item["quantity"] -= _pantry_amount(stock, item["unit"])
            if round(item["quantity"], 2) <= 0:
                del aggregated[key]

    for item in aggregated.values():
        ingredient = ingredients[item["ingredient_id"]]
        item["estimated_cost"] = _line_cost(item["quantity"], item["unit"], ingredient)
        qty, unit = to_display_unit(item["quantity"], item["unit"], unit_preference)
        item["quantity"], item["unit"] = _tidy(qty), unit

    grouped: Dict[str, list] = {}
    for item in aggregated.values():
        grouped.setdefault(item["category"], []).append(item)

    return {
        cat: sorted(grouped[cat], key=lambda x: x["ingredient_name"])
        for cat in sorted(grouped)
    }


SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(kg|g|lb|oz|ml|l|gal|fl\s*oz|fl_oz)\b", re.I)
COUNT_RE = re.compile(r"\b(\d+)\s*(?:pack|pk)\b|\bx\s*(\d+)\b|\b(\d+)\s*x(?=\s*\d)", re.I)
SIZE_UNITS = {"kg": "kg", "g": "g", "lb": "lb", "oz": "oz", "ml": "mL", "l": "L",
              "gal": "gal", "fl_oz": "fl_oz", "fl oz": "fl_oz", "floz": "fl_oz"}


def parse_pack_size(product_name: Optional[str], unit: str) -> Optional[float]:
    """Pack size in `unit` read from a product name ('Mince 500g' -> 0.5 for kg).

    Handles multipacks ('Yoghurt 140g x 4 pack', 'Cans 30x375ml'); for 'each'
    ingredients only a count ('6 pack') is used. None when nothing usable is found.
    """
    if not product_name:
        return None
    count_match = COUNT_RE.search(product_name)
    count = int(next(g for g in count_match.groups() if g)) if count_match else None
    if unit == "each":
        return float(count) if count else None
    sizes = SIZE_RE.findall(product_name)
    if not sizes:
        return None
    amount, size_unit = sizes[-1]
    key = re.sub(r"[\s_]+", "", size_unit.lower())
    size_unit = SIZE_UNITS.get(key) or SIZE_UNITS.get(size_unit.lower())
    if size_unit is None:
        return None
    if not units_compatible(size_unit, unit):
        return None
    return convert_unit(float(amount), size_unit, unit) * (count or 1)


def packs_to_buy(quantity: float, unit: str, pack_size: Optional[float], pack_unit: str) -> Optional[int]:
    """Whole packs needed to cover `quantity`; None when the pack size is unknown or incompatible."""
    if not pack_size or pack_size <= 0 or not quantity or not units_compatible(unit, pack_unit):
        return None
    needed = convert_unit(quantity, unit, pack_unit)
    return max(1, math.ceil(round(needed / pack_size, 6)))


def _display_size(size: float, unit: str) -> Tuple[float, str]:
    """Show packs under 1 kg / L / lb / gal in the smaller unit ('500 g', not '0.5 kg')."""
    small = {"kg": "g", "L": "mL", "lb": "oz", "gal": "fl_oz"}
    if unit in small and size < 1:
        return round(convert_unit(size, unit, small[unit]), 2), small[unit]
    return size, unit


def pack_candidates(ingredient: Dict, products: List[Dict]) -> List[Dict]:
    """Pack sizes you can buy for an ingredient, in the ingredient's unit.

    The ingredient's own pack (pack_size or parsed from its name, priced per unit) comes
    first; each linked purchased product adds its size (parsed from its name) at the
    shelf price last paid. Products with no readable size are skipped.
    """
    unit = ingredient.get("unit")
    candidates, seen = [], set()
    own = ingredient.get("pack_size") or parse_pack_size(ingredient.get("name"), unit)
    if own:
        price = ingredient.get("price")
        candidates.append({"name": ingredient.get("name"), "size": own, "unit": unit,
                           "price": round(own * price, 2) if price else None, "url": ingredient.get("url")})
        seen.add(ingredient.get("name"))
    for product in products:
        name = product.get("product_name")
        size = parse_pack_size(name, unit)
        if not size or name in seen:
            continue
        seen.add(name)
        candidates.append({"name": name, "size": size, "unit": unit,
                           "price": product.get("unit_price"), "url": product.get("url")})
    return candidates


def pack_options(quantity: float, unit: str, candidates: List[Dict]) -> List[Dict]:
    """Every way to cover a need with one pack size, least spare first (cheaper wins a tie).

    Each option: name, url, count, size/unit (display), cost, spare (in the candidate's unit).
    """
    options = []
    for c in candidates:
        count = packs_to_buy(quantity, unit, c["size"], c["unit"])
        if not count:
            continue
        needed = convert_unit(quantity, unit, c["unit"])
        spare = max(0.0, round(count * c["size"] - needed, 4))
        size, size_unit = _display_size(c["size"], c["unit"])
        spare_shown, spare_shown_unit = _display_size(spare, c["unit"])
        options.append({
            "name": c["name"], "url": c.get("url"), "count": count, "size": size, "unit": size_unit,
            "cost": round(count * c["price"], 2) if c.get("price") else None,
            "spare": spare, "spare_unit": c["unit"], "pack_size": c["size"],
            "spare_shown": spare_shown, "spare_shown_unit": spare_shown_unit,
        })
    return sorted(options, key=lambda o: (o["spare"], o["cost"] if o["cost"] is not None else math.inf))


def spares(lines: List[Dict], min_share: float = 0.2) -> List[Dict]:
    """Leftovers worth planning a meal around: [{ingredient_id, name, quantity, unit, shown, shown_unit}].

    Spares smaller than `min_share` of a pack (a few grams of cheese) are left out.
    """
    result = []
    for line in lines:
        best = (line.get("pack_choice") or {}).get("best")
        if best and best["spare"] >= best["pack_size"] * min_share and line.get("ingredient_id"):
            result.append({"ingredient_id": line["ingredient_id"], "name": label_of(line),
                           "quantity": best["spare"], "unit": best["spare_unit"],
                           "shown": best["spare_shown"], "shown_unit": best["spare_shown_unit"]})
    return result


def recipes_using_spares(spare_items: List[Dict], recipes: Dict[int, Dict],
                         recipe_ingredients: Dict[int, List[Dict]], ingredients: Dict[int, Dict]) -> List[Dict]:
    """Recipes that would use up spare ingredients, most spares used first.

    Each result: {recipe_id, name, uses: [ingredient names]}. A recipe 'uses' a spare when it
    needs that ingredient in a compatible unit and the spare covers at least half of it.
    """
    by_id = {s["ingredient_id"]: s for s in spare_items}
    found = []
    for recipe_id, links in recipe_ingredients.items():
        uses = []
        for link in links:
            spare = by_id.get(link["ingredient_id"])
            ingredient = ingredients.get(link["ingredient_id"]) or {}
            need_unit = link.get("unit_override") or ingredient.get("unit")
            if not spare or not need_unit or not units_compatible(need_unit, spare["unit"]):
                continue
            need = convert_unit(link["quantity"], need_unit, spare["unit"])
            if need and spare["quantity"] >= need / 2:
                uses.append(spare["name"])
        if uses and recipe_id in recipes:
            found.append({"recipe_id": recipe_id, "name": recipes[recipe_id]["name"], "uses": uses})
    return sorted(found, key=lambda r: (-len(r["uses"]), r["name"]))


def plan_list_lines(rows: List[Dict], products: Dict[int, List[Dict]]) -> List[Dict]:
    """Attach the best pack choice and its cost to each shopping-list row.

    Rows carry ingredient fields as ing_name / ing_unit / ing_pack_size / ing_price / product_url.
    Adds 'pack_choice' ({'best', 'others'} or None) and 'line_cost' (whole packs, else exact cost).
    """
    lines = []
    for row in rows:
        line = dict(row)
        choice = None
        if line.get("ing_name"):
            ingredient = {"name": line["ing_name"], "unit": line["ing_unit"], "pack_size": line.get("ing_pack_size"),
                          "price": line.get("ing_price"), "url": line.get("product_url")}
            options = pack_options(line["quantity"], line["unit"],
                                   pack_candidates(ingredient, products.get(line.get("ingredient_id"), [])))
            if options:
                choice = {"best": options[0], "others": options[1:]}
        line["pack_choice"] = choice
        line["line_cost"] = (choice["best"]["cost"] if choice else None) or line.get("estimated_cost")
        lines.append(line)
    return lines


def list_totals(items: List[Dict]) -> Dict[str, float]:
    """Totals for list lines carrying 'line_cost' (whole packs) and 'estimated_cost' (exact amount).

    Manual lines are left out, as they have no price.
    """
    priced = [i for i in items if not i.get("is_manual")]
    return {
        "packs_total": round(sum(i.get("line_cost") or 0 for i in priced), 2),
        "exact_total": round(sum(i.get("estimated_cost") or 0 for i in priced), 2),
    }


def total_cost(grouped: Dict[str, List[Dict]]) -> float:
    """Sum of estimated line costs (lines without a price are ignored)."""
    return round(sum(i.get("estimated_cost") or 0 for items in grouped.values() for i in items), 2)


# ---------------------------------------------------------------------------
# Helpers for building test data
# ---------------------------------------------------------------------------

def make_plan_entry(
    date: str = "2026-10-01",
    meal_type: str = "dinner",
    recipe_id: int = 1,
    servings: int = 4,
) -> Dict:
    """Create a plan entry dict for testing."""
    return {"date": date, "meal_type": meal_type, "recipe_id": recipe_id, "servings": servings}


def make_recipe_dict(id: int = 1, name: str = "Test Recipe", servings: int = 4) -> Dict:
    """Create a recipe dict for testing."""
    return {"id": id, "name": name, "servings": servings}


def make_recipe_ingredient(
    ingredient_id: int = 1,
    quantity: float = 1.0,
    unit_override: Optional[str] = None,
) -> Dict:
    """Create a recipe ingredient link dict for testing."""
    return {"ingredient_id": ingredient_id, "quantity": quantity, "unit_override": unit_override}


def make_ingredient(
    id: int = 1,
    name: str = "Test Ingredient",
    unit: str = "each",
    category: str = "pantry",
    price: Optional[float] = None,
) -> Dict:
    """Create an ingredient dict for testing."""
    return {"id": id, "name": name, "unit": unit, "category": category, "price": price}
