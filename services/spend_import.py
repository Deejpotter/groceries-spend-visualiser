"""Import purchase history (Woolworths order-history CSV export) into the purchases table."""

import csv
import io
import re
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Tuple

from models import convert_unit, units_compatible

REQUIRED_COLUMNS = {"date", "product_name", "quantity"}

# Cup price units as printed by Woolworths -> our unit codes
CUP_UNITS = {"G": "g", "KG": "kg", "ML": "mL", "L": "L", "EA": "each"}
CUP_PRICE_RE = re.compile(r"\$\s*([\d.]+)\s*/\s*([\d.]*)\s*([A-Za-z]+)")


def parse_cup_price(text: Optional[str]) -> Optional[Tuple[float, float, str]]:
    """Parse '$1.51 / 100G' -> (1.51, 100.0, 'g'). Returns None if unrecognised."""
    if not text:
        return None
    m = CUP_PRICE_RE.search(text)
    if not m:
        return None
    unit = CUP_UNITS.get(m.group(3).upper())
    if not unit:
        return None
    return float(m.group(1)), float(m.group(2) or 1), unit


def price_per_unit(cup_price: Optional[str], unit_price: Optional[float], target_unit: str) -> Optional[float]:
    """Best estimate of the price for one `target_unit`, using the cup price when units allow.

    Falls back to the pack price for count units (each/pack/...), since a pack is one unit.
    """
    parsed = parse_cup_price(cup_price)
    if parsed:
        price, amount, cup_unit = parsed
        if units_compatible(cup_unit, target_unit):
            one_target_in_cup_units = convert_unit(1, target_unit, cup_unit)
            return round(price / amount * one_target_in_cup_units, 4)
        if cup_unit == "each" and target_unit == "each":
            return round(price / amount, 4)
    if unit_price is not None and not units_compatible(target_unit, "kg") and not units_compatible(target_unit, "L"):
        return round(unit_price, 4)
    return None


def _num(value, default=None):
    try:
        return float(str(value).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return default


def _date(value) -> Optional[str]:
    text = str(value or "").strip()[:10]
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def parse_purchase_csv(text: str, store: str = "Woolworths") -> Tuple[List[Dict], List[str]]:
    """Parse CSV text into purchase dicts. Returns (rows, errors).

    Required columns: date, product_name, quantity, and unit_price or line_total.
    Optional: basket_id (defaults to one basket per date), channel, cup_price, stockcode.
    """
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    columns = {c.strip().lower() for c in (reader.fieldnames or [])}
    missing = REQUIRED_COLUMNS - columns
    if missing or not ({"unit_price", "line_total"} & columns):
        need = sorted(missing | ({"unit_price or line_total"} if not ({"unit_price", "line_total"} & columns) else set()))
        return [], [f"CSV is missing required column(s): {', '.join(need)}"]

    rows, errors = [], []
    for line_no, raw in enumerate(reader, start=2):
        r = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
        order_date = _date(r.get("date"))
        name = r.get("product_name")
        qty = _num(r.get("quantity"), 1.0)
        unit_price = _num(r.get("unit_price"))
        line_total = _num(r.get("line_total"))
        if line_total is None and unit_price is not None:
            line_total = round(qty * unit_price, 2)
        if not order_date or not name or line_total is None:
            errors.append(f"Line {line_no}: skipped (needs a valid date, product name and price).")
            continue
        rows.append({
            "order_date": order_date,
            "basket_id": r.get("basket_id") or f"{store}-{order_date}",
            "store": store,
            "channel": r.get("channel") or None,
            "product_name": name,
            "quantity": qty,
            "unit_price": unit_price,
            "line_total": line_total,
            "cup_price": r.get("cup_price") or None,
            "stockcode": r.get("stockcode") or None,
        })
    return rows, errors


def import_purchases(db, rows: Iterable[Dict]) -> Tuple[int, int]:
    """Insert purchases, skipping lines already imported. Returns (added, skipped).

    New lines for a product that's already linked to an ingredient inherit that link.
    """
    links = {r["product_name"]: r["ingredient_id"] for r in db.execute(
        "SELECT product_name, MAX(ingredient_id) AS ingredient_id FROM purchases "
        "WHERE ingredient_id IS NOT NULL GROUP BY product_name"
    )}
    added = skipped = 0
    for row in rows:
        cur = db.execute(
            """INSERT OR IGNORE INTO purchases
               (order_date, basket_id, store, channel, product_name, quantity, unit_price,
                line_total, cup_price, stockcode, ingredient_id)
               VALUES (:order_date, :basket_id, :store, :channel, :product_name, :quantity, :unit_price,
                       :line_total, :cup_price, :stockcode, :ingredient_id)""",
            {**row, "ingredient_id": links.get(row["product_name"])},
        )
        if cur.rowcount:
            added += 1
        else:
            skipped += 1
    db.commit()
    return added, skipped


def import_csv_file(db, path: str, store: str = "Woolworths") -> Tuple[int, int, List[str]]:
    with open(path, encoding="utf-8-sig") as f:
        rows, errors = parse_purchase_csv(f.read(), store)
    added, skipped = import_purchases(db, rows)
    return added, skipped, errors
