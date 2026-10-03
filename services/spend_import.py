"""Import purchase history (Woolworths order-history CSV export) into the purchases table."""

import csv
import io
import re
import unicodedata
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Tuple

from models import convert_unit, units_compatible

REQUIRED_COLUMNS = {"date", "product_name", "quantity"}

# Cup price units as printed by Woolworths -> our unit codes
CUP_UNITS = {"G": "g", "KG": "kg", "ML": "mL", "L": "L", "EA": "each"}
CUP_PRICE_RE = re.compile(r"\$\s*([\d.]+)\s*/\s*([\d.]*)\s*([A-Za-z]+)")

PRODUCT_URLS = {"woolworths": "https://www.woolworths.com.au/shop/productdetails/{}"}


def product_url(store: Optional[str], stockcode) -> Optional[str]:
    """Store product page for a stockcode, or None when the store or code is unknown."""
    template = PRODUCT_URLS.get((store or "").strip().lower())
    code = str(stockcode or "").strip()
    return template.format(code) if template and code.isdigit() else None


def parse_cup_price(text: Optional[str]) -> Optional[Tuple[float, float, str]]:
    """Parse '$1.51 / 100G' -> (1.51, 100.0, 'g'). Returns None if unrecognised."""
    if not text:
        return None
    m = CUP_PRICE_RE.search(text)
    if not m:
        return None
    unit = CUP_UNITS.get(m.group(3).upper())
    try:
        price, amount = float(m.group(1)), float(m.group(2) or 1)
    except ValueError:  # e.g. "$1 / .G"
        return None
    if not unit or amount <= 0 or price < 0:
        return None
    return price, amount, unit


def price_per_unit(cup_price: Optional[str], unit_price: Optional[float], target_unit: str) -> Optional[float]:
    """Best estimate of the price for one `target_unit`, using the cup price when units allow.

    Falls back to the pack price for count units (each/pack/...), since a pack is one unit.
    """
    parsed = parse_cup_price(cup_price)
    if parsed:
        price, amount, cup_unit = parsed
        if units_compatible(cup_unit, target_unit):
            one_target_in_cup_units = convert_unit(1, target_unit, cup_unit)
            return round(price / amount * one_target_in_cup_units, 6)
        if cup_unit == "each" and target_unit == "each":
            return round(price / amount, 6)
    if unit_price is not None and not units_compatible(target_unit, "kg") and not units_compatible(target_unit, "L"):
        return round(unit_price, 6)
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

    rows, errors, unpriced = [], [], 0
    for line_no, raw in enumerate(reader, start=2):
        r = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
        order_date = _date(r.get("date"))
        name = r.get("product_name")
        qty = _num(r.get("quantity"))
        unit_price = _num(r.get("unit_price"))
        line_total = _num(r.get("line_total"))
        if qty is None or qty <= 0:
            errors.append(f"Line {line_no}: skipped (quantity must be a positive number).")
            continue
        if line_total is None and unit_price is not None:
            line_total = round(qty * unit_price, 2)
        if not order_date or not name:
            errors.append(f"Line {line_no}: skipped (needs a valid date and product name).")
            continue
        if line_total is None:
            # Woolworths exports unavailable/unsupplied items with a "null" price.
            unpriced += 1
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
    if unpriced:
        errors.append(f"{unpriced} line(s) had no price (usually items that weren't supplied) and were skipped.")
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


# ---------------------------------------------------------------------------
# Woolworths PDF tax invoices
# ---------------------------------------------------------------------------
# Woolworths has no CSV export; the real source is the PDF tax invoice emailed
# (or downloadable) per order. Layout seen across 2023-2025 invoices:
# header ("Invoice/Order Number: 155431528", "Date: 08 Apr 2023"), then item
# lines ending "<ordered> <supplied> $<price> $<amount>". Items can span two
# lines; substituted items carry a "(Sub)" marker with ordered 0 / supplied 1;
# unavailable items show supplied 0; freebies show $0.00. Category and header
# lines render text-doubled ("BakeryBakery") so they are never trusted.

INVOICE_NUMBER_RE = re.compile(r"Invoice/Order Number:\s*(\d+)")
INVOICE_DATE_RE = re.compile(r"Date:\s*(\d{1,2} [A-Za-z]{3} \d{4})")
INVOICE_QTY_RE = re.compile(
    r"^(?:(\d+)\s+)?(.*?)\s+(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)"
    r"(?:\s+(?:kg|g|ml|l|lb|oz|each))?\s+\$([\d,]+\.\d{2})\s+\$([\d,]+\.\d{2})\s*$",
    re.I,
)
INVOICE_MONEY_RE = re.compile(r"^\$[\d,]+\.\d{2}$")
INVOICE_LEAD_RE = re.compile(r"^\d+\s+")
INVOICE_SUB_RE = re.compile(r"\(\s*Sub\s*\)", re.I)
INVOICE_TOTAL_LABELS = [
    (r"Sub Total\s*:", "subtotal"),
    (r"Reusable bags\s*:", "bags"),
    (r"Service Fee", "fee"),
    (r"Delivery Fee", "fee"),
    (r"Invoice Total\s*:", "total"),
    (r"includes GST of", "gst"),
    (r"Paid Amount\s*:", "paid"),
    (r"Refund Amount\s*:", "refund"),
]
# Lines that must never become part of a product name.
INVOICE_SKIP_PREFIXES = ("Tax Invoice Page", "ABN ", "Need help with")


def _clean_invoice_name(text: str) -> str:
    """'23 * (Sub) Vevelle ... 6 pack' -> 'Vevelle ... 6 pack'."""
    text = INVOICE_SUB_RE.sub("", text.replace(" ", " "))
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"^\*\s*", "", text)
    return text


def parse_woolworths_invoice_text(text: str, store: str = "Woolworths") -> Tuple[Dict, List[Dict], List[str]]:
    """Parse Woolworths tax-invoice text into purchase dicts.

    Returns (meta, rows, warnings). meta holds invoice_number, order_date and
    the printed subtotal/invoice_total when found; rows match the CSV shape so
    import_purchases() dedupes them identically. Unavailable (supplied 0) and
    free ($0.00) lines are skipped, never imported.
    """
    text = unicodedata.normalize("NFKC", text or "")
    number = INVOICE_NUMBER_RE.search(text)
    invoice_number = number.group(1) if number else None
    day = INVOICE_DATE_RE.search(text or "")
    order_date = None
    if day:
        try:
            order_date = datetime.strptime(day.group(1), "%d %b %Y").date().isoformat()
        except ValueError:
            order_date = None
    if not invoice_number or not order_date:
        return {}, [], ["That doesn't look like a Woolworths tax invoice (no order number or date found)."]

    rows, warnings = [], []
    not_supplied = free = 0
    buffer: List[str] = []
    for raw in (text or "").splitlines():
        line = raw.replace(" ", " ").strip()
        if not line or INVOICE_MONEY_RE.match(line) or line.startswith(INVOICE_SKIP_PREFIXES):
            continue
        match = INVOICE_QTY_RE.match(line)
        if not match:
            buffer.append(line)
            continue
        _, prefix, ordered, supplied, price, amount = match.groups()
        first_item = next((i for i, b in enumerate(buffer) if INVOICE_LEAD_RE.match(b)), len(buffer))
        name = _clean_invoice_name(" ".join(buffer[first_item:] + [prefix]))
        buffer = []
        if not name:
            continue
        if float(supplied) <= 0:
            not_supplied += 1
            continue
        if float(amount.replace(",", "")) <= 0:
            free += 1
            continue
        rows.append({
            "order_date": order_date,
            "basket_id": invoice_number,
            "store": store,
            "channel": None,
            "product_name": name,
            "quantity": float(supplied),
            "unit_price": float(price.replace(",", "")),
            "line_total": float(amount.replace(",", "")),
            "cup_price": None,
            "stockcode": None,
        })
    if not_supplied:
        warnings.append(f"{not_supplied} unavailable item(s) were skipped (not supplied).")
    if free:
        warnings.append(f"{free} free item(s) at $0.00 were skipped.")

    meta: Dict = {"invoice_number": invoice_number, "order_date": order_date}
    totals = _invoice_totals(text)
    meta.update(totals)
    if totals.get("subtotal") is not None:
        read = round(sum(r["line_total"] for r in rows), 2)
        if abs(read - totals["subtotal"]) > 0.05:
            warnings.append(
                f"Read {len(rows)} lines totalling ${read:.2f} but the invoice subtotal is "
                f"${totals['subtotal']:.2f} — please check the import."
            )
    if not rows:
        warnings.append("No priced items could be read from that invoice.")
    return meta, rows, warnings


def _invoice_totals(text: str) -> Dict:
    """Map the printed totals run ({subtotal, total, ...}) by label order.

    Values print as consecutive bare-money lines after the last item; labels
    print (possibly duplicated) near the top. Falls back to positional mapping.
    """
    lines = [(raw.replace(" ", " ").strip()) for raw in (text or "").splitlines()]
    values: List[float] = []
    i = 0
    while i < len(lines):
        if INVOICE_MONEY_RE.match(lines[i]):
            run = []
            j = i
            while j < len(lines) and INVOICE_MONEY_RE.match(lines[j]):
                run.append(float(lines[j].replace("$", "").replace(",", "")))
                j += 1
            if len(run) >= 4 and len(run) > len(values):
                values = run
            i = j
        else:
            i += 1
    if not values:
        return {}
    labelled = []
    for pattern, key in INVOICE_TOTAL_LABELS:
        at = next((i for i, line in enumerate(lines) if re.search(pattern, line, re.I)), None)
        if at is not None and key not in {k for _, k in labelled}:
            labelled.append((at, key))
    labelled.sort()
    if len(labelled) == len(values):
        return {key: values[i] for i, (_, key) in enumerate(labelled)}
    return {"subtotal": values[0], "invoice_total": values[3] if len(values) > 3 else values[0]}


def parse_woolworths_invoice_pdf(data: bytes, store: str = "Woolworths") -> Tuple[Dict, List[Dict], List[str]]:
    """Parse a Woolworths PDF tax invoice (raw bytes) into purchase dicts."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        text = "\n".join([(page.extract_text() or "") for page in reader.pages])
    except Exception:
        return {}, [], ["That PDF couldn't be read — it may be encrypted or corrupted."]
    if not text.strip():
        return {}, [], ["No readable text found in that PDF."]
    return parse_woolworths_invoice_text(text, store)
