"""Bank statement import: NetBank CSV parsing, merchant classification, dedupe.

Bank rows are coarser than Woolworths invoices — one line per shop — so
they land as single-line purchases. Nothing is imported until it matches a
grocery merchant pattern; the rest is reported as unclassified.
"""

import csv
import hashlib
import io
from datetime import datetime
from typing import Dict, List, Optional, Tuple

DEFAULT_MERCHANT_PATTERNS = [
    ("woolworths", "Woolworths"),
    ("coles", "Coles"),
    ("aldi", "ALDI"),
    ("iga", "IGA"),
    ("foodland", "Foodland"),
    ("drakes", "Drakes"),
    ("foodworks", "Foodworks"),
    ("spar", "Spar"),
    ("costco", "Costco"),
]


def _num(value, default=None):
    text = str(value or "").replace("$", "").replace(",", "").strip()
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    try:
        return float(text)
    except ValueError:
        return default


def _date(value) -> Optional[str]:
    text = str(value or "").strip()[:10]
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def parse_netbank_csv(text: str) -> Tuple[List[Dict], List[str]]:
    """Parse a NetBank transaction CSV. Returns (rows, errors).

    Accepts either layout: a single signed Amount column, or separate
    Debit and Credit columns. Money out comes back positive either way.
    """
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    names = {c.strip().lower() for c in (reader.fieldnames or [])}
    desc_key = next((k for k in ("description", "details") if k in names), None)
    date_key = next((k for k in ("date", "transaction date") if k in names), None)
    amount_key = "amount" if "amount" in names else None
    debit_key = "debit" if "debit" in names else None
    credit_key = "credit" if "credit" in names else None
    if not desc_key or not date_key:
        return [], ["CSV is missing required column(s): Date and Description."]
    if not amount_key and not (debit_key or credit_key):
        return [], ["CSV is missing required column(s): Amount, or Debit/Credit."]

    rows, errors = [], []
    for line_no, raw in enumerate(reader, start=2):
        r = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
        order_date = _date(r.get(date_key))
        description = r.get(desc_key, "")
        if amount_key:
            amount = _num(r.get(amount_key))
        else:
            debit = _num(r.get(debit_key), 0) if debit_key else 0
            credit = _num(r.get(credit_key), 0) if credit_key else 0
            amount = round(debit - credit, 2)
        if not order_date or not description:
            errors.append(f"Line {line_no}: skipped (needs a valid date and description).")
            continue
        if amount is None:
            errors.append(f"Line {line_no}: skipped (amount isn't a number).")
            continue
        if amount == 0:
            continue
        rows.append({
            "order_date": order_date,
            "description": description,
            "amount": round(abs(amount), 2),
        })
    return rows, errors


def load_merchant_patterns(db) -> List[Tuple[str, str]]:
    """Grocery merchant patterns from settings, or the defaults.

    One per line: ``keyword=Store Name`` or a bare keyword (the store name
    is then the keyword, title-cased).
    """
    row = db.execute(
        "SELECT value FROM settings WHERE key = 'bank_merchant_patterns'"
    ).fetchone()
    raw = (row["value"] if row else "") or ""
    if not raw:
        return list(DEFAULT_MERCHANT_PATTERNS)
    patterns = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            keyword, name = line.split("=", 1)
            patterns.append((keyword.strip().lower(), name.strip()))
        else:
            patterns.append((line.lower(), line.strip().title()))
    return patterns or list(DEFAULT_MERCHANT_PATTERNS)


def classify_merchant(description: str, patterns) -> Optional[str]:
    """The first grocery merchant matching the description, or None."""
    text = (description or "").lower()
    for keyword, name in patterns:
        if keyword and keyword in text:
            return name
    return None


def _basket_id(source: str, order_date: str, amount: float, description: str) -> str:
    """Stable id for a bank row: same file re-imports to the same id."""
    key = f"{order_date}|{amount:.2f}|{description}"
    return f"{source}:{hashlib.sha1(key.encode()).hexdigest()[:16]}"


def to_purchases(rows: List[Dict], patterns,
                 source: str = "csv") -> Tuple[List[Dict], List[Dict]]:
    """Normalise bank rows to purchase dicts. Returns (purchases, unclassified).

    Only rows matching a grocery merchant become purchases; the rest are
    reported so the patterns can be extended and the file re-imported.
    """
    purchases, unclassified = [], []
    for row in rows:
        store = classify_merchant(row["description"], patterns)
        if not store:
            unclassified.append(dict(row))
            continue
        purchases.append({
            "order_date": row["order_date"],
            "basket_id": _basket_id(source, row["order_date"], row["amount"],
                                    row["description"]),
            "store": store,
            "product_name": row["description"],
            "quantity": 1,
            "unit_price": row["amount"],
            "line_total": row["amount"],
            "origin": source,
        })
    return purchases, unclassified


def _dedupe_key(row) -> Tuple[str, str, float]:
    return row["store"], row["order_date"], round(row["line_total"], 2)


def import_bank_transactions(db, purchases: List[Dict]) -> Tuple[int, List[Dict]]:
    """Insert bank purchases, flagging rows that already exist. Returns (added, duplicates).

    A row is a duplicate when its store, date and total match an existing
    purchase — the same shop on the same day for the same amount, from any
    import source (invoice, bank CSV or BankSync).
    """
    from services.spend_import import _linked_ingredients
    links = _linked_ingredients(db)
    existing = {
        (r["store"], r["order_date"], round(r["line_total"], 2))
        for r in db.execute("SELECT store, order_date, line_total FROM purchases")
    }
    added, duplicates = 0, []
    for row in purchases:
        key = _dedupe_key(row)
        if key in existing:
            duplicates.append(row)
            continue
        existing.add(key)
        cur = db.execute(
            """INSERT OR IGNORE INTO purchases
               (order_date, basket_id, store, product_name, quantity, unit_price,
                line_total, ingredient_id, origin)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (row["order_date"], row["basket_id"], row["store"], row["product_name"],
             row["quantity"], row["unit_price"], row["line_total"],
             links.get(row["product_name"]), row["origin"]),
        )
        if cur.rowcount:
            added += 1
    db.commit()
    return added, duplicates


def force_import_duplicate(db, order_date: str, description: str, amount,
                           source: str = "csv") -> bool:
    """Insert one duplicate the user confirmed is a separate shop."""
    from services.spend_import import _linked_ingredients
    amount = _num(amount)
    if amount is None:
        return False
    amount = round(amount, 2)
    store = classify_merchant(description, load_merchant_patterns(db)) or "Bank"
    basket_id = _basket_id(source, order_date, amount, description)
    suffix = 2
    while db.execute(
        "SELECT 1 FROM purchases WHERE store = ? AND basket_id = ? AND product_name = ?",
        (store, basket_id, description),
    ).fetchone():
        basket_id = f"{basket_id}#{suffix}"
        suffix += 1
    db.execute(
        """INSERT INTO purchases
           (order_date, basket_id, store, product_name, quantity, unit_price,
            line_total, ingredient_id, origin)
           VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?)""",
        (order_date, basket_id, store, description, amount, amount,
         _linked_ingredients(db).get(description), source),
    )
    db.commit()
    return True
